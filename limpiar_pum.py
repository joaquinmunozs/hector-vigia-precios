# -*- coding: utf-8 -*-
"""Saca de la base los precios que en realidad eran precio por unidad de medida.

    python limpiar_pum.py                  # sólo mira y cuenta, no toca nada
    python limpiar_pum.py --confirmar      # borra lo confirmado contra la API

POR QUÉ HACE FALTA LIMPIAR Y NO BASTA CON ARREGLAR EL ADAPTADOR
------------------------------------------------------------------------------
El bug del `pum` (ver `adaptadores.py` y `probar_pum.py`) no sólo mandó avisos
falsos: dejó el precio equivocado GUARDADO. Y una vez guardado hace daño al
revés — `evaluar` mide la caída contra el mínimo de 30 días, así que una ficha
con un $681 en el historial pasa a tener $681 como "lo más barato jamás visto".

Efecto: esa ficha ya no puede volver a alertar nunca. El bug empieza gritando
y termina dejando mudo justo lo que más se mueve (lo que se vende por peso,
volumen o en pack). Arreglar el adaptador detiene la hemorragia; esto limpia.

SE LE PREGUNTA A LA TIENDA. NO SE ADIVINA.
------------------------------------------------------------------------------
El primer intento de este script usaba un criterio offline: "si el precio
guardado cabe un número entero de veces en otro precio de la misma ficha, es
un pum" ($12.990 / 16 piezas = $812). Verificado contra la API el 13-ago-2026:
**0 aciertos de 12**. El criterio se llenaba de cocientes de 2 — que no son
packs, son ofertas de 50% de descuento, o sea justo lo que este bot existe
para encontrar. Habría borrado 243 lecturas buenas.

Tampoco sirve "el precio no termina en 0": acertaba 3 de 8, porque Falabella
tiene precios legítimos no redondos de vendedores del marketplace ($17.955).

Así que el candidato se elige barato y se CONFIRMA caro:

  1. candidatos = fichas cuyo mínimo guardado es menos de la mitad de su
     máximo (son 369 en la base del 13-ago: el universo entero del daño
     posible, porque un pum siempre queda muy por debajo del precio real).
  2. a cada una se le pide su JSON a la API y se leen sus `pum` de verdad.
  3. se borra una lectura sólo si coincide con un pum real de esa ficha.

Under-limpia a propósito: si el pum cambió desde que se guardó, esa lectura
sobrevive. Preferible a borrar el historial de una oferta real, que es el
activo del negocio.
"""
import argparse
import concurrent.futures as futuros
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

import descubrir                                        # noqa: E402
from adaptadores import CABECERAS_FALABELLA, _num       # noqa: E402

_ID = re.compile(r"/product/(\d+)/")
# El pum de hoy puede diferir en unos pesos del que se guardó (el precio se
# movió un poco). 2% cubre eso sin llegar a tocar el precio real, que está
# siempre a un múltiplo de distancia.
TOLERANCIA = 0.02
HILOS = 12


def candidatos(con):
    """Fichas cuyo mínimo guardado es menos de la mitad de su máximo."""
    return list(con.execute(
        "SELECT url, MIN(precio) mn, MAX(precio) mx FROM precios "
        "WHERE tienda='falabella.com' AND precio > 0 GROUP BY url "
        "HAVING COUNT(DISTINCT precio) > 1 AND MIN(precio) < 0.5 * MAX(precio)"))


def precios_de(url):
    """(pums, precios_reales) que la API declara hoy. None si no se pudo.

    HAY QUE MIRAR LAS DOS LISTAS, NO SÓLO LOS PUM
    --------------------------------------------------------------------------
    En un producto de unidad suelta —un sitial, una cortina— el pum ES el
    precio: "$249.990 ($249.990 la unidad)". La primera versión de esto
    marcaba como basura toda lectura que coincidiera con un pum, así que
    proponía borrar el precio bueno de 15 sitiales y cortinas.
    """
    m = _ID.search(url)
    if not m:
        return None
    api = "https://www.falabella.com/s/browse/v1/product/cl?productId=" + m.group(1)
    try:
        datos = json.loads(descubrir.bajar(
            api, tiempo=15, cabeceras=CABECERAS_FALABELLA))
    except Exception:                                    # noqa: BLE001
        return None
    pums, reales = [], []
    for v in (datos.get("data") or {}).get("variants") or []:
        for pr in v.get("prices") or []:
            n = _num((pr.get("price") or [None])[0])
            if n:
                reales.append(n)
            n = _num(((pr.get("pum") or {}).get("price") or [None])[0])
            if n:
                pums.append(n)
    return pums, reales


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="precios.db")
    ap.add_argument("--confirmar", action="store_true",
                    help="borra de verdad (sin esto sólo cuenta)")
    ap.add_argument("--tope", type=int, default=0,
                    help="revisar sólo N candidatas (para probar)")
    args = ap.parse_args()

    con = sqlite3.connect(args.base)
    cand = candidatos(con)
    if args.tope:
        cand = cand[:args.tope]
    print("Base: %s" % args.base)
    print("Fichas candidatas (mínimo < 50%% del máximo): %d" % len(cand))
    print("Preguntándole a la API por cada una...\n")

    confirmadas, sin_respuesta, limpias = [], 0, 0
    with futuros.ThreadPoolExecutor(max_workers=HILOS) as pool:
        for (url, mn, mx), res in zip(
                cand, pool.map(lambda f: precios_de(f[0]), cand)):
            if res is None:
                sin_respuesta += 1
                continue
            pums, reales = res

            def _cerca(a, lista):
                return any(abs(a - b) <= max(2, a * TOLERANCIA) for b in lista)

            # Un pum sólo es basura si NO es además un precio real de la ficha
            # (en los productos de unidad suelta coinciden). Ver `precios_de`.
            malos = [p for p in _lecturas(con, url)
                     if _cerca(p, pums) and not _cerca(p, reales)]
            if malos:
                # El precio real sale de la API, no del máximo guardado: en
                # la perlita de 6 litros el máximo guardado ERA el pum, y el
                # informe se contradecía ("$3.332, siendo pum, real $3.332").
                confirmadas.append((url, sorted(set(malos)),
                                    max(reales) if reales else mx))
            else:
                limpias += 1

    print("  confirmadas como pum: %d" % len(confirmadas))
    print("  limpias:              %d" % limpias)
    print("  sin respuesta de API: %d\n" % sin_respuesta)

    for url, malos, mx in confirmadas[:15]:
        print("  %s  guardado como precio, siendo pum (real ~%s)"
              % (", ".join(_plata(p) for p in malos), _plata(mx)))
        print("     %s" % url.split("/")[-2][:70])

    bases_malas = []
    for url, malos, _ in confirmadas:
        f = con.execute("SELECT precio FROM linea_base WHERE url=?",
                        (url,)).fetchone()
        if f and f[0] in malos:
            bases_malas.append(url)
    print("\nLíneas base fijadas sobre un pum: %d" % len(bases_malas))

    if not args.confirmar:
        print("\n(en seco: no se tocó nada — agregar --confirmar para borrar)")
        return 0

    borradas = 0
    for url, malos, _ in confirmadas:
        for p in malos:
            borradas += con.execute(
                "DELETE FROM precios WHERE url=? AND precio=? "
                "AND tienda='falabella.com'", (url, p)).rowcount
    # Una línea base fijada sobre un pum se borra: `fijar_base` la vuelve a
    # poner con la próxima lectura buena, y dejarla sería seguir midiendo
    # todas las caídas contra un número que nunca existió.
    for url in bases_malas:
        con.execute("DELETE FROM linea_base WHERE url=?", (url,))
    con.commit()
    print("\n✅ borradas %d lecturas y %d líneas base" % (borradas, len(bases_malas)))
    return 0


def _lecturas(con, url):
    return [r[0] for r in con.execute(
        "SELECT DISTINCT precio FROM precios WHERE url=? AND precio>0", (url,))]


if __name__ == "__main__":
    raise SystemExit(main())
