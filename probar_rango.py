# -*- coding: utf-8 -*-
"""¿Se puede leer el precio bajando solo el principio de la página?

LA IDEA
------------------------------------------------------------------------------
Muchas tiendas ponen el precio en las meta etiquetas del <head>
(`product:price:amount`) o en un JSON-LD que va arriba del todo. En SPDigital
el precio está en el BYTE 1.323 de una página de 373.540 — el 0,4% inicial.

Bajar los otros 372 KB es puro desperdicio: tiempo, memoria y, si algún día se
usan proxies, plata (se cobra por GB).

Con la cabecera HTTP `Range` se puede pedir solo los primeros N bytes. Si el
servidor la respeta, la lectura se vuelve ~40 veces más liviana, y eso sube
directamente cuántos productos caben en la lista caliente.

QUÉ SE VERIFICA
------------------------------------------------------------------------------
  1. ¿El servidor respeta Range? (responde 206 Partial Content)
  2. ¿Alcanza a venir el precio en ese trozo?
  3. ¿Cuánto más rápido es de verdad?

Si una tienda no respeta Range, devuelve la página entera y se lee igual: no
se pierde nada por intentarlo.
"""
import re
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import extractor
from curl_cffi import requests as cffi

TROZO = 16_384          # 16 KB: cubre <head> completo con margen

OBJETIVOS = {
    "spdigital": "https://www.spdigital.cl/001r00623-corvo-xfer-belt/",
    "paris": "https://www.paris.cl/plumon-all-season-2-plazas-916167999.html",
    "ripley": "https://simple.ripley.cl/control-ps5-dualsense-sony-blanco-2000380632868p",
    "adidas": "https://www.adidas.cl/zapatos-de-futbol-copa-mundial/015110.html",
}


def medir(url, rango=None, veces=3):
    """Devuelve (bytes, ms_mediana, precio o None)."""
    cab = {"Accept-Language": "es-CL,es;q=0.9"}
    if rango:
        cab["Range"] = "bytes=0-%d" % (rango - 1)

    tiempos, largo, precio, codigo = [], 0, None, None
    for _ in range(veces):
        t0 = time.time()
        try:
            r = cffi.get(url, impersonate="chrome", timeout=30, headers=cab)
        except Exception:                     # noqa: BLE001
            return 0, 0, None, "error"
        tiempos.append(time.time() - t0)
        largo, codigo = len(r.text), r.status_code
        if precio is None:
            try:
                precio = extractor.extraer(r.text)["precio"]
            except Exception:                 # noqa: BLE001
                pass
        time.sleep(0.4)
    return largo, statistics.median(tiempos) * 1000, precio, codigo


if __name__ == "__main__":
    print("%-11s %-9s %9s %8s %10s  %s"
          % ("TIENDA", "MODO", "BYTES", "MS", "PRECIO", "HTTP"))
    print("-" * 62)
    for nombre, url in OBJETIVOS.items():
        b1, t1, p1, c1 = medir(url)
        b2, t2, p2, c2 = medir(url, rango=TROZO)
        print("%-11s %-9s %9d %8.0f %10s  %s" % (nombre, "completo", b1, t1, p1 or "-", c1))
        marca = "✅" if p2 else "✗"
        print("%-11s %-9s %9d %8.0f %10s  %s %s"
              % ("", "rango", b2, t2, p2 or "-", c2, marca))
        if p2 and b1:
            print("%-11s %-9s ahorro de %.0f%% de bytes, %.1fx más rápido\n"
                  % ("", "", 100 * (1 - b2 / b1), t1 / max(t2, 1e-6)))
        else:
            print("%-11s %-9s (no sirve el rango acá)\n" % ("", ""))
