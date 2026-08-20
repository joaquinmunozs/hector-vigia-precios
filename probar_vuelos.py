# -*- coding: utf-8 -*-
"""Los vuelos avisan desde el 40%, no desde el 35%. Base temporal.

    python probar_vuelos.py

QUÉ PROTEGE ESTA PRUEBA (20-ago-2026)
------------------------------------------------------------------------------
Al sumar Vuelos como tópico de categoría, heredaba gratis el piso de las
categorías (`UMBRAL_CATEGORIA`, 35%) — el mismo que usan Electrónicos y
Hogar. Eso NO era lo pedido: los vuelos tienen que avisar desde el 40%.

La diferencia no es cosmética. En pasajes, un 35% es una promoción de martes
cualquiera; el tópico se habría llenado de ruido, que es la forma más rápida
de que alguien lo silencie — y un tópico silenciado no sirve de nada.

Se comprueban cuatro cosas:

  1. Un vuelo con 40% justo SÍ avisa.
  2. Un vuelo con 38% NO avisa. Este es el caso que se rompería si alguien
     borra `UMBRAL_VUELOS` y deja que los vuelos caigan en el 35% general.
  3. Un error de precio en un vuelo ($4.000 el Santiago-Madrid) avisa, aunque
     su precio esté MUY por debajo de `PRECIO_MINIMO` ($20.000). Este es el
     hallazgo más valioso del rubro y el piso de precio lo habría tapado.
  4. Electrónicos y Hogar siguen avisando desde el 35%, sin cambios.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_vuelos.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA           # ← antes del import, a propósito

import baseprecios                       # noqa: E402
import categorias                        # noqa: E402

assert baseprecios.RUTA == RUTA, (
    "baseprecios apunta a %s y no a la base de prueba" % baseprecios.RUTA)

DIA = 86400


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def _con_historial(con, tienda, url, nombre, precio, ahora):
    """Deja el historial que `evaluar` exige para tratar una caída como oferta.

    Sin esto todo caso que no sea error de precio devuelve None por la regla
    de "una oferta sin historial no se avisa", y la prueba mediría otra cosa.
    Son 10 lecturas repartidas en 20 días: cubre `MIN_OBSERVACIONES` (5) y
    `DIAS_MINIMOS_HISTORIAL` (7) con margen.
    """
    for i in range(10):
        baseprecios.guardar(con, tienda, url, nombre, precio,
                            cuando=ahora - (20 - i * 2) * DIA)
    baseprecios.fijar_base(con, url, precio, origen="inicial",
                           cuando=ahora - 20 * DIA)


def main():
    con = baseprecios.abrir()
    ahora = int(time.time())
    fallos = 0

    casos = [
        # (titulo, tienda, nombre, referencia, precio_ahora, debe_avisar, categoria_esperada)
        ("Vuelo al 40% justo",
         "www.latamairlines.com", "Santiago - Madrid ida y vuelta",
         900_000, 540_000, True, categorias.VUELOS),
        ("Vuelo al 38% (bajo el piso de vuelos)",
         "jetsmart.com", "Santiago - Antofagasta",
         100_000, 62_000, False, categorias.VUELOS),
        ("Error de precio en vuelo, bajo PRECIO_MINIMO",
         "www.skyairline.com", "Santiago - Lima",
         400_000, 4_000, True, categorias.VUELOS),
        ("Electronica al 36% (el 35% de siempre)",
         "falabella.com", "Notebook ASUS ROG RTX 4070",
         1_000_000, 640_000, True, categorias.ELECTRONICOS),
    ]

    print("%-42s %10s %10s %7s  %s" % ("CASO", "ANTES", "AHORA", "CAIDA", "RESULTADO"))
    print("-" * 96)

    for i, (titulo, tienda, nombre, ref, ahora_precio, debe, cat) in enumerate(casos):
        url = "https://%s/prueba/%d" % (tienda, i)
        _con_historial(con, tienda, url, nombre, ref, ahora)
        det = baseprecios.evaluar(con, url, ahora_precio, ahora=ahora,
                                  nombre=nombre, tienda=tienda)
        caida = 100 * (1 - ahora_precio / ref)
        aviso = bool(det)

        if aviso != debe:
            estado = "❌ %s (se esperaba %s)" % (
                "avisa" if aviso else "NO avisa", "avisar" if debe else "callar")
            fallos += 1
        else:
            estado = "✅ %s" % ("avisa como " + det["tipo"] if aviso else "no avisa")

        print("%-42s %10s %10s %6.0f%%  %s" % (
            titulo[:42], _plata(ref), _plata(ahora_precio), caida, estado))

        # Cuando avisa, además tiene que ir a la categoría correcta.
        if aviso and det.get("categoria") != cat:
            print("   ❌ categoria %r, se esperaba %r" % (det.get("categoria"), cat))
            fallos += 1

    # El piso de vuelos tiene que ser un número propio, no el de categorías.
    print()
    if getattr(baseprecios, "UMBRAL_VUELOS", None) != 0.40:
        print("❌ UMBRAL_VUELOS no es 0.40")
        fallos += 1
    elif baseprecios.UMBRAL_VUELOS == baseprecios.UMBRAL_CATEGORIA:
        print("❌ el piso de vuelos quedó pegado al de categorías")
        fallos += 1
    else:
        print("✅ UMBRAL_VUELOS = %.2f, separado del %.2f de las otras categorías"
              % (baseprecios.UMBRAL_VUELOS, baseprecios.UMBRAL_CATEGORIA))

    print()
    if fallos:
        print("❌ %d fallos" % fallos)
    else:
        print("✅ los vuelos avisan desde el 40%, el error de precio barato pasa, "
              "y el resto quedó como estaba")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
