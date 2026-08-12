# -*- coding: utf-8 -*-
"""Verifica que un rechazo NO cuente como fallo de la URL. No toca nada.

    python probar_desenlace.py

Lo que protege: el 11-ago-2026 el catálogo cayó de 439.375 a 360.863 fichas en
un día porque toda lectura fallida acercaba la URL a que la borraran, sin mirar
POR QUÉ falló. Una tienda que nos bloquea unas horas se llevaba puesto su
catálogo entero.

Es la prueba que faltaba para poder bajar `TOPE_FALLOS` con confianza.
"""
import sys
import urllib.error

sys.stdout.reconfigure(encoding="utf-8")

import vigia


def _http(code):
    return urllib.error.HTTPError("https://t.cl/x", code, "", None, None)


CASOS = [
    # (excepción, desenlace esperado, por qué)
    (_http(404), "muerta", "la ficha no existe"),
    (_http(410), "muerta", "la ficha se fue para siempre"),
    (_http(403), "rechazo", "la tienda nos bloquea — es el caso de tottus"),
    (_http(429), "rechazo", "le estamos pegando muy rápido"),
    (_http(503), "rechazo", "la tienda se cayó"),
    (_http(500), "rechazo", "error de la tienda, no de la URL"),
    (_http(451), "sin_precio", "código raro: se trata como URL sospechosa"),
    (TimeoutError("se acabó el tiempo"), "rechazo", "timeout, no dice nada de la URL"),
    (urllib.error.URLError("dns"), "rechazo", "no se pudo ni resolver el host"),
    (OSError("conexión cortada"), "rechazo", "se cortó la red"),
    (ValueError("sin precio en el HTML"), "sin_precio", "se bajó y no había precio"),
    (AttributeError("extractor"), "sin_precio", "reventó el lector"),
]


def main():
    fallos = 0
    print("%-34s %-12s %-12s" % ("excepción", "esperado", "obtenido"))
    print("-" * 74)
    for exc, esperado, porque in CASOS:
        obtenido = vigia.desenlace(exc)
        marca = "  " if obtenido == esperado else "❌"
        if obtenido != esperado:
            fallos += 1
        nombre = type(exc).__name__
        if isinstance(exc, urllib.error.HTTPError):
            nombre += " %d" % exc.code
        print("%s %-32s %-12s %-12s  %s"
              % (marca, nombre, esperado, obtenido, porque))

    print("-" * 74)

    # Lo que de verdad importa: NINGÚN rechazo puede terminar borrando la URL.
    # `barrida` sólo llama a `anotar_fallo` cuando el desenlace es
    # "sin_precio", así que basta con comprobar el mapeo.
    rechazos = [c for c in CASOS if c[1] == "rechazo"]
    malos = [c for c in rechazos if vigia.desenlace(c[0]) != "rechazo"]
    if malos:
        print("❌ %d rechazo(s) caerían en la cuenta de fallos y borrarían "
              "catálogo bueno" % len(malos))
        fallos += len(malos)
    else:
        print("✅ los %d rechazos quedan fuera de la cuenta de fallos: "
              "no borran catálogo" % len(rechazos))

    print("\n%s" % ("TODO BIEN" if not fallos else "%d FALLO(S)" % fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
