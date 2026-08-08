# -*- coding: utf-8 -*-
"""Por qué fallan las fichas de Falabella que fallan.

La tasa de lectura es ~58%. La pregunta es si el 42% restante son productos
SIN STOCK — que es correcto descartar, porque avisar de algo que no se puede
comprar quema la confianza del suscriptor — o si es un fallo real que hay
que arreglar.

Confundir las dos cosas lleva a "arreglar" un comportamiento que ya era el
deseado, o a dar por bueno un catálogo a medias.
"""
import json
import re
import sys
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")

import descubrir
from curl_cffi import requests as cffi

H = {"Accept": "application/json",
     "Referer": "https://www.falabella.com/falabella-cl/"}
API = "https://www.falabella.com/s/browse/v1/product/cl?productId="
ID = re.compile(r"/product/(\d+)/")
PRECIO = re.compile(r'"price"\s*:\s*\[\s*"([\d.,]+)"')


def revisar(url):
    m = ID.search(url)
    if not m:
        return "sin id en la url", None
    try:
        r = cffi.get(API + m.group(1), impersonate="chrome", timeout=20, headers=H)
    except Exception as e:                    # noqa: BLE001
        return "error de red", str(e)[:40]
    if r.status_code != 200:
        return "HTTP %s" % r.status_code, None
    try:
        j = r.json()
    except Exception:                         # noqa: BLE001
        return "respuesta no-JSON", None

    tipo = j.get("responseType")
    entero = json.dumps(j, ensure_ascii=False)
    precios = PRECIO.findall(entero)
    if precios:
        return "OK", precios[0]
    return "sin precio (%s)" % (tipo or "?"), None


if __name__ == "__main__":
    fichas = descubrir.fichas_de("falabella.com", tope=200)
    print("revisando %d fichas de Falabella...\n" % min(60, len(fichas)))

    cuenta = Counter()
    for u in fichas[:60]:
        motivo, dato = revisar(u)
        cuenta[motivo] += 1

    total = sum(cuenta.values())
    print("%-32s %6s %8s" % ("RESULTADO", "N", "%"))
    print("-" * 50)
    for motivo, n in cuenta.most_common():
        print("%-32s %6d %7.0f%%" % (motivo[:32], n, 100 * n / total))

    ok = cuenta.get("OK", 0)
    sin_stock = sum(n for m, n in cuenta.items() if "OUT_OF_STOCK" in m)
    print("\nleíbles: %d/%d (%.0f%%)" % (ok, total, 100 * ok / total))
    if sin_stock:
        vivos = total - sin_stock
        print("descontando los sin stock: %d/%d (%.0f%%)"
              % (ok, vivos, 100 * ok / max(1, vivos)))
        print("\nLos sin stock se descartan a propósito: no tienen precio "
              "publicado\ny avisar de algo que no se puede comprar quema al "
              "suscriptor.")
