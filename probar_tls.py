# -*- coding: utf-8 -*-
"""¿Se pueden leer las tiendas DIFICIL sin proxy, solo imitando el TLS de Chrome?

LA PREGUNTA QUE RESUELVE
------------------------------------------------------------------------------
Las tiendas grandes (Falabella, Ripley, Sodimac...) quedaron fuera porque su
WAF bloquea. Se asumió que la salida era pagar proxies residenciales
(~US$180-2.700/mes). Pero el bloqueo puede no ser por la IP: Cloudflare y
Akamai miran PRIMERO el fingerprint TLS (JA3/JA4) — la huella que deja el
saludo de tu cliente HTTPS. Python con urllib deja una huella que grita
"soy un script", y eso se detecta ANTES de leer una sola cabecera.

curl_cffi replica el saludo TLS exacto de Chrome (orden de cifrados,
extensiones, frame SETTINGS de HTTP/2). Si el bloqueo era por ahí, esto lo
resuelve GRATIS y no hace falta proxy.

Este script compara las dos vías contra las mismas URLs para saberlo con
datos, no por suposición.
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import descubrir
import extractor

try:
    from curl_cffi import requests as cffi
except ImportError:
    print("Falta curl_cffi:  python -m pip install curl_cffi")
    raise SystemExit(1)

# El navegador a imitar. "chrome" toma la última versión que traiga la
# librería, así no queda clavado a una versión que envejece.
PERFIL = "chrome"

DIFICILES = ["falabella.com", "ripley.cl", "sodimac.cl", "tottus.cl",
             "lider.cl", "cruzverde.cl", "nike.cl", "adidas.cl",
             "decathlon.cl", "imperial.cl"]


def bajar_tls(url, tiempo=25):
    r = cffi.get(url, impersonate=PERFIL, timeout=tiempo,
                 headers={"Accept-Language": "es-CL,es;q=0.9"})
    return r.status_code, r.text


def probar(dominio, cuantas=6):
    print("\n" + "=" * 74)
    print(dominio)
    print("=" * 74)

    # 1. ¿Se puede siquiera descubrir su catálogo? El sitemap suele estar menos
    #    protegido que la ficha, así que se prueba con TLS imitado también.
    try:
        raices = descubrir.ubicar_sitemap(dominio)
    except Exception as e:                                   # noqa: BLE001
        raices = []
        print("  sitemap por urllib falló: %s" % str(e)[:50])

    if not raices:
        for ruta in ("/sitemap.xml", "/sitemap_index.xml"):
            try:
                cod, txt = bajar_tls("https://www.%s%s" % (dominio, ruta), 20)
                if cod == 200 and "<loc>" in txt[:5000]:
                    raices = ["https://www.%s%s" % (dominio, ruta)]
                    print("  sitemap SOLO con TLS imitado: %s" % ruta)
                    break
            except Exception:                                # noqa: BLE001
                continue

    if not raices:
        print("  ✗ sin sitemap accesible por ninguna vía")
        return dominio, 0, 0, 0

    # 2. Sacar unas fichas candidatas.
    fichas = []
    for r in raices[:3]:
        try:
            cod, txt = bajar_tls(r, 25)
            urls = descubrir._locs(txt)
            hijos = [u for u in urls if descubrir._ES_XML.search(u)]
            planas = [u for u in urls if not descubrir._ES_XML.search(u)]
            prod = [h for h in hijos if "produc" in h.lower() or "item" in h.lower()]
            for h in (prod or hijos)[:2]:
                try:
                    _, t2 = bajar_tls(h, 25)
                    planas += [u for u in descubrir._locs(t2)
                               if not descubrir._ES_XML.search(u)]
                except Exception:                            # noqa: BLE001
                    continue
            fichas += [u for u in planas if not descubrir.NO_ES_FICHA.search(u)
                       and u.count("/") >= 3 and len(u) > 28]
        except Exception as e:                               # noqa: BLE001
            print("  error leyendo sitemap: %s" % str(e)[:50])
    fichas = list(dict.fromkeys(fichas))
    print("  fichas candidatas: %d" % len(fichas))
    if not fichas:
        return dominio, 0, 0, 0

    # 3. La prueba de verdad: leer el precio, por las dos vías.
    ok_plano, ok_tls = 0, 0
    muestra = fichas[:cuantas]
    for u in muestra:
        try:
            extractor.extraer(descubrir.bajar(u))
            ok_plano += 1
        except Exception:                                    # noqa: BLE001
            pass
        try:
            cod, html = bajar_tls(u, 25)
            d = extractor.extraer(html)
            ok_tls += 1
            if ok_tls == 1:
                print("  ✓ TLS: %s → $%s" % (d["nombre"][:42] or "(sin nombre)",
                                             format(d["precio"], ",d").replace(",", ".")))
        except Exception as e:                               # noqa: BLE001
            if ok_tls == 0 and u is muestra[0]:
                print("  ✗ TLS: %s" % str(e)[:60])
        time.sleep(0.5)

    print("  RESULTADO  urllib: %d/%d   ·   TLS imitado: %d/%d"
          % (ok_plano, len(muestra), ok_tls, len(muestra)))
    return dominio, len(fichas), ok_plano, ok_tls


if __name__ == "__main__":
    objetivos = sys.argv[1:] or DIFICILES
    filas = []
    for d in objetivos:
        try:
            filas.append(probar(d))
        except Exception as e:                               # noqa: BLE001
            print("  error general: %s" % str(e)[:60])
            filas.append((d, 0, 0, 0))

    print("\n\n" + "=" * 74)
    print("%-18s %9s %9s %9s" % ("TIENDA", "FICHAS", "urllib", "TLS"))
    print("=" * 74)
    for dom, n, a, b in filas:
        marca = "✅" if b > a else ("=" if b and b == a else "❌")
        print("%s %-16s %9d %9d %9d" % (marca, dom, n, a, b))
    ganadas = [f for f in filas if f[3] > f[2]]
    print("\nTiendas que se GANAN solo con TLS imitado (sin proxy): %d" % len(ganadas))
