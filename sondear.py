# -*- coding: utf-8 -*-
"""Recorre los sitemaps de una tienda BAJANDO TODOS LOS NIVELES y diagnostica.

POR QUÉ EXISTE
------------------------------------------------------------------------------
`descubrir.py` bajaba solo dos niveles de sitemap. Ripley anida tres
(índice -> productos_1P.xml -> productos_1P_1.xml -> las fichas) y por eso
devolvía 25 URLs en vez de 1.100.000: se daba por bloqueada una tienda que en
realidad se lee perfecto y gratis.

Este script recorre en profundidad y después diagnostica una ficha de verdad,
para separar tres casos que se veían iguales desde afuera:
    bloqueo    -> el WAF corta (403/desafío). Solo eso justifica pagar proxy.
    extractor  -> pasa y el precio está en el HTML. Se arregla gratis.
    javascript -> pasa pero el precio lo pinta JS. Hace falta navegador.
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

import descubrir
import diagnosticar as diag
from curl_cffi import requests as cffi


def _bajar(url, tiempo=30):
    r = cffi.get(url, impersonate="chrome", timeout=tiempo,
                 headers={"Accept-Language": "es-CL,es;q=0.9"})
    return r.text


def recolectar(raices, max_nivel=4, tope=400):
    """Baja por el árbol de sitemaps hasta encontrar URLs que no sean XML."""
    pendientes = list(raices)
    fichas, vistos, nivel = [], set(), 0

    while pendientes and nivel < max_nivel and len(fichas) < tope:
        siguiente = []
        for s in pendientes[:4]:            # unos pocos por nivel: es un sondeo
            if s in vistos:
                continue
            vistos.add(s)
            try:
                urls = descubrir._locs(_bajar(s))
            except Exception:               # noqa: BLE001
                continue
            for u in urls:
                if descubrir._ES_XML.search(u):
                    siguiente.append(u)
                elif (not descubrir.NO_ES_FICHA.search(u)
                      and u.count("/") >= 3 and len(u) > 28):
                    fichas.append(u)
        # Si hay sitemaps que dicen "producto/pdp/item", se prefieren esos.
        preferidos = [x for x in siguiente
                      if re.search(r"produc|pdp|item|sku", x, re.I)]
        pendientes = preferidos or siguiente
        nivel += 1

    return list(dict.fromkeys(fichas))


def sondear(dominio, cuantas=2):
    print("\n" + "=" * 74)
    print(dominio)
    print("=" * 74)
    try:
        raices = descubrir.ubicar_sitemap(dominio)
    except Exception:                       # noqa: BLE001
        raices = []
    if not raices:
        for ruta in ("/sitemap.xml", "/sitemap_index.xml"):
            try:
                t = _bajar("https://www.%s%s" % (dominio, ruta), 20)
                if "<loc>" in t[:5000]:
                    raices = ["https://www.%s%s" % (dominio, ruta)]
                    break
            except Exception:               # noqa: BLE001
                continue
    if not raices:
        print("  ✗ sin sitemap")
        return dominio, 0, "sin sitemap"

    fichas = recolectar(raices)
    print("  fichas halladas: %d" % len(fichas))
    if not fichas:
        return dominio, 0, "sin fichas"

    veredictos = [diag.diagnosticar(u) for u in fichas[:cuantas]]
    final = max(set(veredictos), key=veredictos.count)
    return dominio, len(fichas), final


if __name__ == "__main__":
    doms = sys.argv[1:] or ["sodimac.cl", "tottus.cl", "lider.cl",
                            "cruzverde.cl", "nike.cl", "adidas.cl",
                            "decathlon.cl", "imperial.cl"]
    filas = []
    for d in doms:
        try:
            filas.append(sondear(d))
        except Exception as e:              # noqa: BLE001
            print("  error: %s" % str(e)[:60])
            filas.append((d, 0, "error"))

    print("\n\n" + "=" * 74)
    print("%-18s %10s   %s" % ("TIENDA", "FICHAS", "VEREDICTO"))
    print("=" * 74)
    icono = {"extractor": "✅ GRATIS", "javascript": "🟡 navegador",
             "bloqueo": "🔴 proxy", "sin sitemap": "⬜ nada",
             "sin fichas": "⬜ nada", "sin conexion": "⬜ nada", "error": "⬜ nada"}
    for dom, n, v in filas:
        print("%-18s %10d   %s" % (dom, n, icono.get(v, v)))
    gratis = [f for f in filas if f[2] == "extractor"]
    print("\nSe ganan GRATIS (sin proxy): %d de %d" % (len(gratis), len(filas)))
    for g in gratis:
        print("   ✅ %s" % g[0])
