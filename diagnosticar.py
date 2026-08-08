# -*- coding: utf-8 -*-
"""¿Por qué falla una tienda: la BLOQUEA un WAF o el precio lo pinta JavaScript?

La diferencia decide en qué gastar plata:
  · Bloqueo (403, desafío, HTML minúsculo) -> lo arregla un proxy/unlocker. CARO.
  · Render por JS (200, HTML grande, sin precio en el crudo) -> el proxy NO
    sirve de nada; hace falta un navegador o encontrar el XHR interno. GRATIS
    o casi, pero es otro trabajo.

Confundirlas es como se termina pagando US$2.700/mes de proxies para un
problema que el proxy no resuelve.
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from curl_cffi import requests as cffi

# Señales de que el precio SÍ está en el HTML crudo (aunque el extractor no lo
# haya sabido leer), frente a un HTML que simplemente no lo trae.
PISTAS = (
    ("json-ld", r'application/ld\+json'),
    ("schema Product", r'"@type"\s*:\s*"Product"'),
    ("offers", r'"offers"'),
    ("price json", r'"price"\s*:\s*"?\d'),
    ("__NEXT_DATA__", r'__NEXT_DATA__'),
    ("og price", r'product:price:amount'),
    ("itemprop price", r'itemprop="price"'),
    ("data-price", r'data-price'),
    ("$ visible", r'\$\s?\d{1,3}(\.\d{3})+'),
)

DESAFIO = ("just a moment", "checking your browser", "cf-challenge",
           "access denied", "captcha", "incapsula", "pardon our interruption",
           "request unsuccessful")


def diagnosticar(url):
    print("\n" + "-" * 74)
    print(url[:74])
    try:
        r = cffi.get(url, impersonate="chrome", timeout=30,
                     headers={"Accept-Language": "es-CL,es;q=0.9"})
    except Exception as e:                                   # noqa: BLE001
        print("  ✗ ni siquiera conecta: %s" % str(e)[:60])
        return "sin conexion"

    html = r.text
    bajo = html[:6000].lower()
    print("  HTTP %s · %d KB · servidor: %s"
          % (r.status_code, len(html) // 1024,
             (r.headers.get("server") or "?")[:22]))

    if r.status_code in (403, 429, 503):
        print("  → BLOQUEO duro (%s). Acá sí hace falta proxy/unlocker." % r.status_code)
        return "bloqueo"
    if any(d in bajo for d in DESAFIO):
        print("  → DESAFÍO del WAF. Hace falta proxy/unlocker o navegador.")
        return "bloqueo"
    if len(html) < 3000:
        print("  → HTML minúsculo: esqueleto o bloqueo silencioso.")
        return "bloqueo"

    halladas = [n for n, p in PISTAS if re.search(p, html, re.I)]
    if halladas:
        print("  → PASA el WAF y el precio ESTÁ en el HTML: %s" % ", ".join(halladas))
        print("     (falla el extractor, no la red — es arreglable gratis)")
        return "extractor"
    print("  → PASA el WAF pero el HTML NO trae precio: lo pinta JavaScript.")
    print("     (un proxy no sirve de nada acá)")
    return "javascript"


if __name__ == "__main__":
    urls = sys.argv[1:]
    if not urls:
        print("uso: python diagnosticar.py <url> [url...]")
        raise SystemExit(1)
    veredictos = [diagnosticar(u) for u in urls]
    print("\n" + "=" * 74)
    for v in sorted(set(veredictos)):
        print("  %-12s %d" % (v, veredictos.count(v)))
