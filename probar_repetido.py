# -*- coding: utf-8 -*-
"""Un precio que se queda abajo no se avisa todos los días. Base temporal.

    python probar_repetido.py

QUÉ SE ROMPIÓ (13-ago-2026)
------------------------------------------------------------------------------
Medido sobre la base de producción: la manteca de cacao salió TRES veces al
tópico de errores (11, 12 y 13 de agosto) con el mismo precio y el mismo
porcentaje. Las toallas, la vajilla y el matcha, dos veces cada una. De 11
avisos de error, 4 eran repeticiones de uno anterior.

El mecanismo tiene dos piezas y las dos hacían falta:

  · `_aviso_reciente` sólo tapa VENTANA_REPETIR (12 h). Como el vigía vuelve
    a leer la misma ficha al día siguiente, el aviso reaparece pasada esa
    ventana, para siempre, mientras el precio siga abajo.
  · El filtro que lo impediría —"tiene que ser el más barato jamás visto"—
    está escrito `if con_historial and ...`, así que sólo corre cuando el
    producto lleva 7 días observado.

Y hoy NINGÚN producto llega a esos 7 días: las 170 alertas de la base de
producción tienen la referencia en `origen='inicial'`. O sea el filtro existe
pero no protege a nadie todavía, y no lo hará hasta que el catálogo madure.

La regla que se quiere es más simple y no depende del historial: **el mismo
producto no se vuelve a avisar salvo que haya bajado MÁS que la última vez
que se avisó.** Si sigue igual de barato, ya lo dijimos.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_repetido.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA           # ← antes del import, a propósito

import baseprecios                       # noqa: E402

assert baseprecios.RUTA == RUTA, (
    "baseprecios apunta a %s y no a la base de prueba" % baseprecios.RUTA)

HORA = 3600
URL = "https://www.falabella.com/falabella-cl/product/143907533/manteca/1"


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def main():
    con = baseprecios.abrir()
    ahora = int(time.time())
    fallos = 0

    # El escenario real de la manteca: descubierta a $36.990, sin historial
    # (la referencia es la foto del día uno), y a partir de ahí se lee barata.
    baseprecios.guardar(con, "falabella.com", URL, "Manteca De Cacao Pura 500g",
                        36_990, cuando=ahora - 48 * HORA)
    baseprecios.fijar_base(con, URL, 36_990, origen="inicial",
                           cuando=ahora - 48 * HORA)

    print("Ficha a %s, referencia inicial (sin 7 días de historial).\n"
          % _plata(36_990))

    # ── Aviso 1: legítimo. Es la primera vez que se ve el desplome.
    det = baseprecios.evaluar(con, URL, 681, ahora=ahora - 40 * HORA,
                              nombre="Manteca De Cacao Pura 500g",
                              tienda="falabella.com")
    if det and det["tipo"] == baseprecios.ERROR:
        print("✅ 1er aviso a %s: sale, y debe salir" % _plata(681))
        baseprecios.anotar_alerta(con, det, ahora=ahora - 40 * HORA)
        baseprecios.guardar(con, "falabella.com", URL, "Manteca", 681,
                            cuando=ahora - 40 * HORA)
    else:
        print("❌ el 1er aviso no salió — la prueba no está midiendo lo que cree")
        return 1

    # ── Aviso 2: al día siguiente, mismo precio. NO debe salir.
    det = baseprecios.evaluar(con, URL, 681, ahora=ahora - 16 * HORA,
                              nombre="Manteca De Cacao Pura 500g",
                              tienda="falabella.com")
    if det:
        print("❌ 2º aviso a %s (24 h después, mismo precio): SALE y no debería"
              % _plata(681))
        fallos += 1
    else:
        print("✅ 2º aviso a %s (24 h después, mismo precio): no sale"
              % _plata(681))

    # ── Aviso 3: dos días después, un peso más caro. Tampoco.
    det = baseprecios.evaluar(con, URL, 690, ahora=ahora,
                              nombre="Manteca De Cacao Pura 500g",
                              tienda="falabella.com")
    if det:
        print("❌ 3er aviso a %s (más caro que el avisado): SALE y no debería"
              % _plata(690))
        fallos += 1
    else:
        print("✅ 3er aviso a %s (más caro que el avisado): no sale" % _plata(690))

    # ── Pero si baja DE VERDAD por debajo de lo ya avisado, sí se avisa:
    # ese es un hallazgo nuevo, no una repetición.
    det = baseprecios.evaluar(con, URL, 550, ahora=ahora,
                              nombre="Manteca De Cacao Pura 500g",
                              tienda="falabella.com")
    if det:
        print("✅ baja a %s (bajo lo ya avisado): sí sale, es un hallazgo nuevo"
              % _plata(550))
    else:
        print("❌ baja a %s y NO sale: se está tapando una caída real"
              % _plata(550))
        fallos += 1

    # ── Y la ventana de 12 h sigue mandando aunque baje más: dos avisos del
    # mismo producto en la misma tarde son ruido igual.
    baseprecios.anotar_alerta(con, det, ahora=ahora) if det else None
    det = baseprecios.evaluar(con, URL, 520, ahora=ahora + HORA,
                              nombre="Manteca De Cacao Pura 500g",
                              tienda="falabella.com")
    if det:
        print("❌ otro aviso 1 h después: SALE y no debería (ventana de 12 h)")
        fallos += 1
    else:
        print("✅ otro aviso 1 h después: no sale (ventana de 12 h)")

    # ── EL CASO QUE ESTE FILTRO NO PUEDE ROMPER ───────────────────────────
    #
    # La tienda corrige el precio y después se vuelve a equivocar igual. Eso
    # es un hallazgo nuevo —de los que mejor pagan—, no una repetición, y
    # `_ya_se_dijo` NO puede ser quien lo tape.
    #
    # Se prueba el filtro directo, no `evaluar`, a propósito: pasada una
    # semana el producto ya tiene historial, y ahí manda otra regla, anterior
    # a ésta y deliberada — "con historial, tiene que ser el más barato jamás
    # visto" (ver `evaluar`). Mezclar las dos haría que esta prueba dijera
    # "pasa" o "falla" por un motivo que no es el que está midiendo.
    print()
    print("El error se corrige y reaparece:")
    if baseprecios._ya_se_dijo(con, URL, 550):
        print("  ✅ mientras sigue barato, el filtro tapa la repetición")
    else:
        print("  ❌ el filtro no está tapando la repetición")
        fallos += 1

    baseprecios.marcar_si_restablecido(con, URL, 36_990, ahora=ahora + 24 * HORA)
    abiertas = con.execute(
        "SELECT COUNT(*) c FROM alertas WHERE url=? AND restablecido_en IS NULL",
        (URL,)).fetchone()["c"]
    if abiertas:
        print("  ❌ quedaron %d alertas abiertas tras recuperarse el precio "
              "(filas zombi: taparían el próximo error para siempre)" % abiertas)
        fallos += 1
    else:
        print("  ✅ al recuperarse el precio se cerraron TODAS las alertas abiertas")

    if baseprecios._ya_se_dijo(con, URL, 550):
        print("  ❌ tras corregirse, el filtro SIGUE tapando: "
              "un error que reaparece no se avisaría nunca")
        fallos += 1
    else:
        print("  ✅ tras corregirse, el filtro deja pasar el error que reaparece")

    print()
    if fallos:
        print("❌ %d fallos: el mismo producto se sigue repitiendo" % fallos)
    else:
        print("✅ sin repeticiones: sólo se reavisa si bajó más que la vez anterior")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
