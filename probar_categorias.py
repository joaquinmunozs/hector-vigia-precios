# -*- coding: utf-8 -*-
"""Prueba la cadena completa: clasificar -> evaluar -> a qué tópicos va.

    python probar_categorias.py

POR QUÉ ESTA PRUEBA EXISTE
------------------------------------------------------------------------------
La regla de los tópicos de categoría (8-ago-2026) tiene tres partes que se
pueden romper por separado y en silencio:

  1. `categorias.clasificar` decide si es electrónica u hogar
  2. `baseprecios.evaluar` baja el piso al 35% SOLO para esos
  3. `alertas.destinos` decide a qué tópicos va, y si se duplica

Un error en cualquiera de las tres no rompe nada visible: simplemente el
tópico queda vacío para siempre, o se llena de ruido. Por eso se prueba con
una base de verdad, no con mocks.

EL CASO QUE MÁS IMPORTA
------------------------------------------------------------------------------
Un -60% en un notebook tiene que salir DOS VECES: en Ofertas reales y en
Electrónicos. Ese duplicado es lo que se pidió explícitamente, y es lo más
fácil de romper sin darse cuenta al tocar el ruteo.

HAY QUE SEMBRAR HISTORIAL (13-ago-2026)
------------------------------------------------------------------------------
Esta prueba llevaba días fallando 5 de 12 casos y no era un bug del producto:
sólo llamaba a `fijar_base` y no guardaba ni una lectura. Sin lecturas,
`evaluar` calcula `con_historial=False`, y ahí manda la regla del 11-ago —
"una oferta sin historial no se avisa, sólo los errores". Resultado: todo lo
que caía menos del 70% devolvía `None` y la prueba lo leía como "el ruteo
está roto". Los únicos casos que pasaban eran el error de -80% y los que
esperaban "(nada)", o sea pasaban por el motivo equivocado.

Ahora cada caso siembra 30 días de historial al precio base, que es la
situación en la que el ruteo por categoría de verdad corre. La regla de "sin
historial" se prueba aparte, al final, que es donde corresponde.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

# Los tópicos se leen del entorno. Se fijan ANTES de importar alertas para
# que no queden cacheados con otro valor.
os.environ["VIGIA_TOPICO_ERRORES"] = "2"
os.environ["VIGIA_TOPICO_OFERTAS"] = "4"
os.environ["VIGIA_TOPICO_ELECTRONICOS"] = "36"
os.environ["VIGIA_TOPICO_HOGAR"] = "38"

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["VIGIA_DB"] = _tmp.name

import alertas          # noqa: E402
import baseprecios      # noqa: E402

NOMBRE = {"2": "Errores", "4": "Ofertas", "36": "Electrónicos", "38": "Hogar"}

# (nombre, tienda, precio_base, precio_nuevo, tópicos esperados)
CASOS = [
    # ── Electrónica ──────────────────────────────────────────────────────
    ("Apple iPhone 16 Pro Max 256GB", "falabella.com",
     1_000_000, 600_000, ["36"]),                    # -40%: solo su categoría
    ("Notebook ASUS ROG RTX 4070", "spdigital.cl",
     1_000_000, 400_000, ["4", "36"]),               # -60%: DUPLICADO
    ("Smart TV LG 55'' 4K UHD", "paris.cl",
     1_000_000, 200_000, ["2"]),                     # -80%: error, sin duplicar

    # ── Hogar ────────────────────────────────────────────────────────────
    ("Sofá Seccional 3 Cuerpos Gris", "falabella.com",
     500_000, 300_000, ["38"]),                      # -40%: solo su categoría
    ("Refrigerador No Frost 400L", "abc.cl",
     500_000, 200_000, ["4", "38"]),                 # -60%: DUPLICADO

    # ── Sin categoría: el piso es 40%, no 50% ────────────────────────────
    #
    # Estos cuatro esperaban "(nada)" y llevaban días marcando falso fallo.
    # El piso de Ofertas bajó de 0,50 a 0,40 el 11-ago-2026 (commit 9912b94,
    # "ropa, zapatillas y todo lo que no es electrónica ni hogar entra desde
    # ahí"), y ni esta tabla ni la de rangos de `alertas.py` se actualizaron.
    ("Zapatillas Nike Air Max 90", "falabella.com",
     100_000, 60_000, ["4"]),                        # -40%: justo en el piso
    ("Zapatillas Nike Air Max 90", "falabella.com",
     100_000, 40_000, ["4"]),                        # -60%: solo ofertas
    ("Cien años de soledad", "antartica.cl",
     20_000, 12_000, ["4"]),                         # -40% en un libro

    # ── Los que fallaron contra datos reales ─────────────────────────────
    #
    # Acá lo que se prueba es que NO caigan en un tópico de categoría: si
    # `clasificar` se equivocara, la funda saldría en Electrónicos ("36") y
    # el sostén en Hogar ("38"). Que además vayan a Ofertas es correcto —
    # son caídas de 40% de productos sin categoría.
    ("Funda Con Teclado Para Samsung S9", "falabella.com",
     45_000, 27_000, ["4"]),                         # accesorio, no electrónica
    ("Sosten Encaje Copa C", "falabella.com",
     20_000, 12_000, ["4"]),                         # "copa" no es cristalería
    ("Zapatillas de Running Galaxy 7", "falabella.com",
     54_990, 33_000, []),                            # no es un Samsung, y -39,99%
    ("Cargador Rápido 45W", "falabella.com",
     8_990, 5_000, []),                              # ahorro de $3.990: bajo el piso
]


DIA = 86400


def _sembrar(con, tienda, url, nombre, base):
    """30 días de lecturas al precio base.

    Hacen falta al menos `DIAS_MINIMOS_HISTORIAL` días de observación real
    para que `evaluar` se crea la referencia; con menos, sólo salen errores
    y el ruteo por categoría nunca llega a ejecutarse. Todas al mismo precio
    a propósito: así el mínimo de 30 días ES el precio base y la caída que
    mide la prueba es exactamente la que dice la tabla.
    """
    ahora = int(time.time())
    for d in range(30, 0, -1):
        baseprecios.guardar(con, tienda, url, nombre, base,
                            cuando=ahora - d * DIA)


def main():
    con = baseprecios.abrir()
    fallos = 0

    print("%-40s %8s %8s  %-22s %s" % (
        "PRODUCTO", "ANTES", "AHORA", "ESPERADO", "OBTENIDO"))
    print("-" * 104)

    for i, (nombre, tienda, base, nuevo, esperado) in enumerate(CASOS):
        # Cada caso usa su propia URL: así no se pisan entre ellos con la
        # ventana anti-repetición de 12 h.
        url = "https://%s/p/%d" % (tienda, i)
        baseprecios.fijar_base(con, url, base)
        _sembrar(con, tienda, url, nombre, base)

        det = baseprecios.evaluar(con, url, nuevo, nombre=nombre, tienda=tienda)
        obtenido = [d for d in alertas.destinos(det) if d] if det else []

        ok = sorted(obtenido) == sorted(esperado)
        fallos += not ok
        caida = 100 * (1 - nuevo / base)

        print("%s %-38s %8s %8s  %-22s %s" % (
            "  " if ok else "✗ ",
            nombre[:38],
            format(base, ",d").replace(",", "."),
            format(nuevo, ",d").replace(",", "."),
            " + ".join(NOMBRE[t] for t in esperado) or "(nada)",
            " + ".join(NOMBRE[t] for t in obtenido) or "(nada)"))

        if not ok:
            print("     caída %.0f%% · tipo=%s · categoría=%s" % (
                caida, det["tipo"] if det else "—",
                (det or {}).get("categoria") or "—"))

    con.close()
    os.unlink(_tmp.name)

    print("-" * 104)
    if fallos:
        print("✗ %d de %d casos fallaron" % (fallos, len(CASOS)))
    else:
        print("✅ %d casos, todos correctos" % len(CASOS))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
