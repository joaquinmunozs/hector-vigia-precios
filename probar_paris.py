# -*- coding: utf-8 -*-
"""Extrae el precio de Paris, que lo esconde en el streaming de Next.js.

POR QUÉ NO SERVÍA EL EXTRACTOR NORMAL
------------------------------------------------------------------------------
Paris publica su JSON-LD, pero NO como un `<script type="application/ld+json">`
normal. Usa el streaming de Next.js, que inyecta el bloque así:

    self.__next_s.push([0,{"type":"application/ld+json",
                           "children":"{\\"@type\\":\\"Product\\",...}",
                           "id":"jsonld-product-916167999"}])

O sea el JSON-LD va como un STRING ESCAPADO dentro de otro JSON. Buscar
`application/ld+json` lo encuentra, pero el contenido está doblemente
escapado: hay que desescapar una vez antes de parsearlo.

Por eso el HTML pesa 1,9 MB, contiene el precio, y aun así ninguna de las
cinco estrategias del extractor lo veía.
"""
import json
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from curl_cffi import requests as cffi

# El bloque escapado: "children":"<json con \" adentro>","id":"jsonld-..."
# El cuerpo se captura tolerando escapes para no cortar en la primera comilla.
BLOQUE = re.compile(r'"children":"((?:[^"\\]|\\.)*)","id":"(jsonld-[^"]+)"')


def desde_next_streaming(html):
    """Precio y nombre desde el JSON-LD escapado de Next.js."""
    for m in BLOQUE.finditer(html):
        crudo, ident = m.group(1), m.group(2)
        if "product" not in ident.lower():
            continue
        try:
            # Doble desescape: primero se resuelve el string, después el JSON.
            datos = json.loads(json.loads('"' + crudo + '"'))
        except Exception:                    # noqa: BLE001
            continue

        tipos = datos.get("@type")
        if "Product" not in str(tipos):
            continue

        ofertas = datos.get("offers") or {}
        if isinstance(ofertas, list):
            ofertas = ofertas[0] if ofertas else {}

        precio = ofertas.get("price") or ofertas.get("lowPrice")
        if precio is None:
            espec = ofertas.get("priceSpecification") or []
            espec = espec if isinstance(espec, list) else [espec]
            for e in espec:
                if isinstance(e, dict) and e.get("price") is not None:
                    precio = e["price"]
                    break
        if precio is None:
            continue

        return {"nombre": str(datos.get("name") or "")[:120],
                "precio": precio,
                "disponible": str(ofertas.get("availability") or ""),
                "id": ident}
    return None


if __name__ == "__main__":
    import descubrir

    urls = sys.argv[1:]
    if not urls:
        urls = descubrir.fichas_de("paris.cl", tope=8)

    ok = 0
    for u in urls[:8]:
        try:
            html = cffi.get(u, impersonate="chrome", timeout=30).text
            d = desde_next_streaming(html)
        except Exception as e:               # noqa: BLE001
            print("  ✗ %s  (%s)" % (u[-50:], str(e)[:40]))
            continue
        if d:
            ok += 1
            print("  ✓ %-46s $%s" % (d["nombre"][:46], d["precio"]))
        else:
            print("  ✗ sin precio: %s" % u[-58:])
    print("\nextrae %d/%d" % (ok, len(urls[:8])))
