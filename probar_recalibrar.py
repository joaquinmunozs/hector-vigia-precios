# -*- coding: utf-8 -*-
"""La recalibración se decide por el historial, no por el número del día.

    python probar_recalibrar.py

QUÉ SE ROMPIÓ (16-ago-2026)
------------------------------------------------------------------------------
Las 190.317 líneas base de producción decían `inicial`. Ninguna decía
`recalculada`. O sea: la referencia con la que Héctor compara cada precio es
el PRIMER precio que vio en su vida, no el precio normal del producto.

Eso apaga dos de los tres canales de aviso. `evaluar()` exige historial para
anunciar ofertas (40%-70%) y rebajas de categoría (35%-50%); sin historial
solo pasan los errores (70%+). Se ve en los avisos reales:

    11-ago   160 avisos   (130 ofertas + 29 rebajas + 1 error)
    12-ago     4 avisos   (solo errores)
    13-ago     6 avisos   (solo errores)
    14-ago     0 avisos
    15-ago     2 avisos   (solo errores)
    16-ago     0 avisos

La causa no era un bug de cálculo: `recalcular_bases` necesita productos con
7 días de historial (DIAS_MINIMOS_HISTORIAL), y solo se la llamaba los días
1 y 15 del mes. El historial de Héctor arranca el 10-ago, así que el 15-ago
tenía 5 días y actualizó 0 referencias — el log lo dice textual:
"recalibración: 0 referencias actualizadas", en las 4 corridas de ese día.
La siguiente oportunidad caía recién el 1-sep.

Atar una condición sobre DÍAS DE HISTORIAL a un número de CALENDARIO no
funciona: el historial no arranca el día 1 del mes, arranca cuando arranca —
y se reinicia cada vez que se pierde la base.

QUÉ SE ARREGLA
------------------------------------------------------------------------------
Se recalibra cuando pasaron 7+ días desde la ÚLTIMA recalibración, contado en
la tabla `marcadores`. El espaciado de una semana se mantiene tal cual: era a
propósito, porque una referencia que se refresca muy seguido se "acostumbra"
al precio bajo y deja de verlo como caída. Lo que cambia es contra qué se
mide ese espaciado.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_recalibrar.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA

import baseprecios                      # noqa: E402
import correr                           # noqa: E402

DIA = 86400


def main():
    fallos = 0
    con = baseprecios.abrir()
    ahora = int(time.time())

    # ── 1. Base recién nacida: no toca ────────────────────────────────────
    print("1. base sin historial suficiente → NO recalibra")
    if correr._toca_recalibrar(con, ahora):
        print("  ❌ recalibra con 0 días de historial")
        fallos += 1
    else:
        print("  ✅ no recalibra")

    # ── 2. Con 7 días de historial y nunca recalibrada: SÍ toca ───────────
    print("\n2. 7 días de historial y nunca recalibrada → SÍ recalibra")
    baseprecios.guardar(con, "hites.com", "http://x/1", "P", 20000,
                        cuando=ahora - 8 * DIA)
    con.commit()
    if not correr._toca_recalibrar(con, ahora):
        print("  ❌ NO recalibra pese a tener 8 días de historial — "
              "es el bug que dejó 190.317 referencias en 'inicial'")
        fallos += 1
    else:
        print("  ✅ recalibra")

    # ── 3. Recién recalibrada: no repite al otro día ──────────────────────
    print("\n3. recalibrada hoy → NO repite mañana")
    correr._anotar_recalibracion(con, ahora)
    if correr._toca_recalibrar(con, ahora + 1 * DIA):
        print("  ❌ recalibra al día siguiente — la referencia se "
              "'acostumbra' al precio bajo y deja de ver la caída")
        fallos += 1
    else:
        print("  ✅ no repite")

    # ── 4. A los 7 días de la última, vuelve a tocar ──────────────────────
    print("\n4. 7 días después de la última → vuelve a recalibrar")
    if not correr._toca_recalibrar(con, ahora + 7 * DIA):
        print("  ❌ no vuelve a recalibrar nunca")
        fallos += 1
    else:
        print("  ✅ recalibra de nuevo")

    # ── 5. No depende del número del día del mes ──────────────────────────
    print("\n5. no depende de que sea día 1 o 15")
    # ahora+7 días cae en un día cualquiera del mes; que funcione ya lo probó
    # el caso 4. Acá se comprueba lo inverso: ser día 1 no alcanza por sí solo.
    con2_ruta = os.path.join(tempfile.gettempdir(), "probar_recalibrar2.db")
    if os.path.exists(con2_ruta):
        os.remove(con2_ruta)
    import sqlite3
    con2 = sqlite3.connect(con2_ruta)
    con2.row_factory = sqlite3.Row
    con2.executescript(baseprecios.ESQUEMA)
    import datetime
    primero = datetime.datetime(2026, 9, 1, 3, 0).timestamp()
    if correr._toca_recalibrar(con2, int(primero)):
        print("  ❌ recalibra un día 1 sin historial que lo justifique")
        fallos += 1
    else:
        print("  ✅ el número del día ya no decide nada")
    con2.close()

    print()
    if fallos:
        print("❌ %d fallos" % fallos)
    else:
        print("✅ la recalibración sigue al historial, no al calendario")
    con.close()
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
