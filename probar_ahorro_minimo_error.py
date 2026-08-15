# -*- coding: utf-8 -*-
"""Un error de precio no debe apagarse por ser barato. Base temporal.

    python probar_ahorro_minimo_error.py

QUÉ SE ROMPIÓ (15-ago-2026)
------------------------------------------------------------------------------
Cero alertas en 24 h seguidas de barrida, con cientos de miles de cambios de
precio detectados. Verificado contra la base real de producción (descargada
del artifact `precios-db`): 4 caídas reales de 70%-86% en productos de precio
bajo (una broca de $7.290 a $1.000, una cortina de $36.990 a $10.990, unas
zapatillas Hush Puppies de $6.299 a $1.299) pasaban el piso porcentual
(`UMBRAL_ERROR`) pero `evaluar()` igual devolvía `None`.

La causa: `AHORRO_MINIMO = 8_000` exige $8.000 de ahorro EN PESOS, sin
excepción — y en un producto de precio bajo, un 80%+ de caída porcentual
sigue sin alcanzar $8.000 de diferencia absoluta. El propio docstring del
módulo ya decía que un 75% de caída "se entiende solo, no hace falta
historial" — pero nadie había dicho que tampoco debía depender de cuánta
plata en pesos representa. El piso de plata tiene sentido para ofertas (que
son un descuento, y ahí sí importa cuánto se ahorra el suscriptor); no tiene
sentido para errores (la tienda vendiendo por accidente), donde el porcentaje
YA es la señal.

Esta prueba fija el escenario real (broca $7.290 → $1.000, -86%, sin
historial) y comprueba que ahora SÍ sale como error, y que el piso de plata
sigue aplicando sin cambios a ofertas y categoría (no se sacó la protección
entera, solo se acotó a dónde corresponde).
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_ahorro_minimo_error.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA           # ← antes del import, a propósito

import baseprecios                       # noqa: E402

assert baseprecios.RUTA == RUTA, (
    "baseprecios apunta a %s y no a la base de prueba" % baseprecios.RUTA)

HORA = 3600


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def main():
    con = baseprecios.abrir()
    ahora = int(time.time())
    fallos = 0

    # ── Caso real: la broca de la base de producción ──────────────────────
    url_broca = "https://www.falabella.com/falabella-cl/product/999/broca/1"
    baseprecios.guardar(con, "falabella.com", url_broca, "Broca Expert Hex-9",
                        7_290, cuando=ahora - 48 * HORA)
    baseprecios.fijar_base(con, url_broca, 7_290, origen="inicial",
                           cuando=ahora - 48 * HORA)

    det = baseprecios.evaluar(con, url_broca, 1_000, ahora=ahora,
                              nombre="Broca Expert Hex-9", tienda="falabella.com")
    ahorro = 7_290 - 1_000
    print("Broca: %s -> %s (-%.0f%%, ahorro %s, bajo el piso de $8.000)" % (
        _plata(7_290), _plata(1_000), 100 * (1 - 1_000 / 7_290), _plata(ahorro)))
    if det and det["tipo"] == baseprecios.ERROR:
        print("  ✅ sale como ERROR pese a que el ahorro en pesos no llega a $8.000")
    else:
        print("  ❌ NO sale — el piso de plata sigue tapando errores de precio")
        fallos += 1

    # ── El piso de plata SIGUE aplicando a ofertas (caída bajo UMBRAL_ERROR) ─
    url_oferta = "https://www.falabella.com/falabella-cl/product/999/oferta/2"
    baseprecios.guardar(con, "falabella.com", url_oferta, "Cargador 45W",
                        10_000, cuando=ahora - 48 * HORA)
    baseprecios.fijar_base(con, url_oferta, 10_000, origen="inicial",
                           cuando=ahora - 48 * HORA)

    # -50%: cruza el piso porcentual de oferta (40%) pero el ahorro es $5.000,
    # bajo AHORRO_MINIMO. Como no es un error (caída < UMBRAL_ERROR=0.70),
    # el piso de plata tiene que seguir bloqueándolo.
    det = baseprecios.evaluar(con, url_oferta, 5_000, ahora=ahora,
                              nombre="Cargador 45W", tienda="falabella.com")
    print("\nCargador: %s -> %s (-50%%, ahorro $5.000, bajo el piso de $8.000, "
          "NO es error)" % (_plata(10_000), _plata(5_000)))
    if det:
        print("  ❌ SALE — el piso de plata dejó de proteger a las ofertas chicas")
        fallos += 1
    else:
        print("  ✅ no sale — el piso de plata sigue aplicando a ofertas, como antes")

    print()
    if fallos:
        print("❌ %d fallos" % fallos)
    else:
        print("✅ los errores de precio ya no dependen de cuánto cuesta el producto, "
              "y las ofertas siguen protegidas por el piso de $8.000")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
