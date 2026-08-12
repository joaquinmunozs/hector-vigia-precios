# -*- coding: utf-8 -*-
"""Encuentra URLs de producto de cada tienda, por la vía pública.

CÓMO Y POR QUÉ ASÍ
------------------------------------------------------------------------------
Se usa el **sitemap.xml**, que es el archivo que las propias tiendas publican
para que Google las indexe. Es la vía limpia: es contenido que ellos exponen a
propósito, no hay que rastrear el sitio categoría por categoría (mucho más
tráfico, mucho más sospechoso, mucho más lento).

Probado en vivo el 6-ago-2026:
  · spdigital.cl  → 66.267 URLs de producto directas en el sitemap raíz
  · jumbo.cl      → sitemap de CATEGORÍAS; las fichas hay que sacarlas
                    entrando a una categoría
  · easy.cl       → sitemap índice (apunta a otros sitemaps por rubro)
  · paris.cl      → 404, no publica sitemap en esa ruta
  · dafiti.cl     → sitemap vacío

Por eso hay dos estrategias: sitemap directo y, si eso no da fichas, entrar a
las categorías que el propio sitemap lista.
"""
import gzip
import os
import re
import threading
import urllib.parse
import urllib.request
import urllib.robotparser

TIEMPO_LIMITE = 25

# ── Tiendas que hay que pedir a través del proxy de Cloudflare ─────────────
#
# paris.cl y easy.cl contestan 403 al runner de GitHub Actions, que sale por
# IPs de Azure (medido el 11-ago-2026: 20.169.74.50). Desde el edge de
# Cloudflare contestan 200 con el XML de verdad. El Worker está en
# `proxy-tiendas/` de este mismo repo.
#
# Sin `HECTOR_PROXY_URL` en el entorno, esto no hace NADA y todo sigue como
# antes: en local, desde la casa de Joaquín, esas tiendas responden bien
# directo y no hay por qué gastar peticiones del Worker.
POR_PROXY = {"paris.cl", "www.paris.cl", "easy.cl", "www.easy.cl"}
PROXY_URL = os.environ.get("HECTOR_PROXY_URL", "").strip()
PROXY_TOKEN = os.environ.get("HECTOR_PROXY_TOKEN", "").strip()


def _por_proxy(url):
    """La URL a pedir y las cabeceras extra, si esta tienda va por el proxy."""
    if not PROXY_URL or not PROXY_TOKEN:
        return url, {}
    try:
        host = urllib.parse.urlsplit(url).hostname or ""
    except ValueError:
        return url, {}
    if host.lower() not in POR_PROXY:
        return url, {}
    return (PROXY_URL.rstrip("/") + "/?u=" + urllib.parse.quote(url, safe=""),
            {"x-hector-token": PROXY_TOKEN})


CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"),
    "Accept-Language": "es-CL,es;q=0.9",
}

# Cómo se ve la URL de una ficha de producto en cada tienda. Sale de mirar los
# sitemaps de verdad, no de suponer.
PATRONES_FICHA = (
    r"/p$",           # VTEX y varios: .../nombre-producto/p
    r"/p/",
    r"/producto/",
    r"/product/",
    r"-p-\d+",        # Dafiti y similares
)


# curl_cffi replica el saludo TLS de Chrome (orden de cifrados, extensiones,
# frame SETTINGS de HTTP/2). Varias tiendas miran esa huella ANTES de leer una
# sola cabecera: adidas.cl devuelve 403 a urllib y 200 a curl_cffi con la
# misma IP. Es opcional a propósito — si no está instalado, todo sigue
# funcionando con urllib, solo se pierden las tiendas quisquillosas.
try:
    from curl_cffi import requests as _cffi
except ImportError:                        # pragma: no cover
    _cffi = None

NAVEGADOR = "chrome"


def _bajar_urllib(url, tiempo, cabeceras):
    req = urllib.request.Request(url, headers=cabeceras)
    datos = urllib.request.urlopen(req, timeout=tiempo).read()
    if datos[:2] == b"\x1f\x8b":          # sitemaps suelen venir comprimidos
        datos = gzip.decompress(datos)
    return datos.decode("utf-8", "replace")


# ── Una sesión por hilo, para no repetir el handshake TLS ──────────────────
#
# `_cffi.get()` suelto abre conexión TCP + handshake TLS en CADA petición. Un
# handshake son dos vueltas de red antes de pedir el primer byte, y contra
# tiendas chilenas eso es la mayor parte del tiempo de una lectura.
#
# MEDIDO el 12-ago-2026 (mediana de 6 lecturas por tienda, conexión nueva vs
# sesión reutilizada):
#
#     construmart.cl   124 ms -> 57 ms   2,16x
#     tricot.cl        504 ms -> 319 ms  1,58x
#     salcobrand.cl    279 ms -> 197 ms  1,42x
#     hites.com        625 ms -> 506 ms  1,23x
#     antartica.cl     144 ms -> 158 ms  0,91x  (dentro del ruido)
#     ------------------------------------------
#     promedio         335 ms -> 248 ms  1,35x
#
# Es la mejora más barata que hay: como el throughput por hilo es 1/latencia,
# bajar la latencia sube el ritmo en la misma proporción SIN pedirle una sola
# petición más a la tienda. No toca `RITMO_SEGURO` ni el riesgo de bloqueo.
#
# Va por hilo (`threading.local`) porque una sesión de curl_cffi no es segura
# de compartir entre hilos, y acá corren hasta 200 en paralelo. Cada hilo paga
# UN handshake por tienda y reusa esa conexión el resto de su vida.
_sesiones = threading.local()


def _sesion():
    s = getattr(_sesiones, "s", None)
    if s is None:
        s = _cffi.Session(impersonate=NAVEGADOR)
        _sesiones.s = s
    return s


def bajar(url, tiempo=TIEMPO_LIMITE, cabeceras=None):
    """Baja una página. Prueba urllib y, si lo rechazan, imita a Chrome.

    Se intenta urllib PRIMERO por ser mucho más liviano: la mayoría de las
    tiendas no necesitan el disfraz, y montar un handshake completo de Chrome
    en cada una de las 165.000 peticiones costaría tiempo y memoria de más.

    `cabeceras` agrega o pisa las de base. Falabella, por ejemplo, responde
    403 sin `Referer` y 200 con él.
    """
    todas = dict(CABECERAS)
    if cabeceras:
        todas.update(cabeceras)
    # Las tiendas bloqueadas se piden al Worker de Cloudflare en vez de
    # directo. Va acá adentro, y no en cada llamador, porque `bajar` es el
    # único punto por donde salen TODAS las peticiones: descubrimiento,
    # barrida y vigilante. Si se hiciera arriba habría que acordarse en tres
    # lugares distintos y uno se olvidaría.
    url, extra = _por_proxy(url)
    todas.update(extra)
    # Las URLs con tildes o ñ (spdigital tiene "caja-abierta-dañada/...") hay
    # que percent-encodearlas: urllib intenta mandarlas como ASCII y revienta
    # con "'ascii' codec can't encode character". `safe` conserva la
    # estructura de la URL para no romperla.
    url = urllib.parse.quote(url, safe=":/?&=#%+~,;@!$'()*[]")
    try:
        return _bajar_urllib(url, tiempo, todas)
    except Exception:                      # noqa: BLE001
        if _cffi is None:
            raise
    r = _sesion().get(url, timeout=tiempo, headers=todas)
    if r.status_code >= 400:
        raise urllib.error.HTTPError(url, r.status_code, "rechazado", None, None)
    return r.text


def _locs(xml):
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml)


def _es_ficha(url):
    return any(re.search(p, url, re.I) for p in PATRONES_FICHA)


# Rutas que NUNCA son una ficha de producto. Todo lo demás del sitemap se
# considera candidato y se deja que el extractor decida: si la página no tiene
# precio, se descarta sola. Filtrar por patrón de URL era demasiado estricto —
# spdigital.cl publica sus fichas como /nombre-del-producto/ sin ningún
# prefijo, y así se perdían 66.000 de sus 66.267 productos.
NO_ES_FICHA = re.compile(
    r"/(blogs?|noticias|ayuda|contacto|nosotros|terminos|politica|privacidad|"
    r"sucursales|tiendas|servicio|garantia|despacho|carrito|cart|checkout|"
    r"login|cuenta|buscar|busqueda|search|categoria|category|marcas?|"
    r"landings?|promocion(es)?|campana|cyber|black|navidad|pages?|collections?|"
    r"proyectos|responsabilidad|sale)(/|$)", re.I)

# Un sitemap paginado se ve como `sitemap_products_2.xml?from=123&to=456`, así
# que `endswith(".xml")` no lo detecta: hay que mirar la ruta sin el query.
_ES_XML = re.compile(r"\.xml(\?|$)", re.I)


# Rutas donde suele vivir el sitemap cuando robots.txt no lo declara.
RUTAS_SITEMAP = ("/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml",
                 "/sitemaps.xml", "/sitemap/sitemap.xml")

# Sitemaps que ya encontramos y que conviene tener anotados, porque su
# robots.txt no siempre se deja leer.
#
# CASO REAL (7-ago-2026): ripley.cl entrega su robots.txt sin problemas desde
# una conexión de casa, pero lo RECHAZA desde Modal — que sale por IP de
# centro de datos, y varios WAF las tratan distinto. Resultado: el
# descubrimiento devolvía 0 fichas en producción y más de un millón en el PC,
# para el mismo código. Anotar el sitemap salta ese paso por completo.
#
# El dominio va en la clave; el valor es la lista de sitemaps raíz. Ojo que
# Ripley los publica en OTRO subdominio (simple.ripley.cl), así que probar
# rutas comunes bajo ripley.cl tampoco lo habría encontrado.
SITEMAPS_CONOCIDOS = {
    "ripley.cl": ["https://simple.ripley.cl/sitemap_ripley_index.xml"],
    "falabella.com": [
        "https://www.falabella.com/static/site/sitemaps/pdp/pdp_cl_FA_COM-index.xml"],
    "adidas.cl": [
        "https://www.adidas.cl/glass/sitemaps/adidas/CL/es/sitemap-index.xml"],
}


def ubicar_sitemap(dominio):
    """Dónde está el sitemap de este sitio.

    Se pregunta primero a robots.txt, que es donde el propio sitio lo declara
    para los buscadores. Solo si ahí no dice nada se prueban rutas comunes.

    Esto importa: paris.cl, hites.com y tricot.cl publican en
    `sitemap_index.xml` y devolvían 404 en `/sitemap.xml`, así que quedaban
    fuera del sistema por completo aunque su catálogo fuera accesible.
    """
    for base in ("https://www.%s" % dominio, "https://%s" % dominio):
        try:
            robots = bajar(base + "/robots.txt", tiempo=12)
            declarados = re.findall(r"(?im)^\s*sitemap:\s*(\S+)", robots)
            if declarados:
                return declarados
        except Exception:                  # noqa: BLE001
            continue

    for base in ("https://www.%s" % dominio, "https://%s" % dominio):
        for ruta in RUTAS_SITEMAP:
            try:
                if "<loc>" in bajar(base + ruta, tiempo=12)[:4000]:
                    return [base + ruta]
            except Exception:              # noqa: BLE001
                continue

    # Último recurso: el sitemap que ya teníamos anotado. Va al final a
    # propósito — si robots.txt responde, se le hace caso, porque una ruta
    # anotada envejece y la declarada por el propio sitio no.
    return SITEMAPS_CONOCIDOS.get(dominio, [])


# Un sitemap índice separa productos de categorías, y cada tienda le pone otro
# nombre a la carpeta de fichas. Falabella usa `pdp` (Product Detail Page), que
# no contiene "produc" — por eso se le pasaba de largo su sitemap bueno y se la
# daba por bloqueada teniendo 1,5 millones de fichas publicadas.
#
# OJO con el delimitador: la palabra "sitemap" CONTIENE "item" (s-item-ap), así
# que un `item` suelto marcaba como "de producto" absolutamente todos los
# sitemaps — incluidos los de landings y categorías. Por eso el token tiene que
# ir precedido de inicio, barra, guion o guion bajo.
_DE_PRODUCTO = re.compile(r"(?:^|[/_\-])(produc|pdp|item|sku|ficha)", re.I)

# Hasta cuántos niveles de sitemap anidado se baja. Ripley encadena TRES
# (índice -> productos_1P.xml -> productos_1P_1.xml -> fichas); con el tope
# viejo de dos niveles devolvía 25 URLs en vez de 1.100.000.
MAX_NIVEL_SITEMAP = 5


def desde_sitemap(dominio, tope=100000, desplazamiento=0):
    """Todas las URLs candidatas a ficha que publique el sitemap.

    Recorre el árbol de sitemaps en anchura, nivel por nivel, hasta dar con
    las URLs que no son XML. No se puede asumir una profundidad fija: cada
    tienda anida distinto.

    `desplazamiento` ROTA por cuál sitemap hijo se empieza (12-ago-2026).
    --------------------------------------------------------------------------
    Sin esto, una tienda más grande que `tope` queda amputada en silencio y
    siempre por el mismo lado: el árbol se recorre en el mismo orden todas las
    semanas, se corta en el mismo punto, y la parte de atrás del catálogo NO
    EXISTE para el sistema — ni siquiera como URL pendiente.

    Es exactamente lo que pasa con Falabella: `tope_tienda=100000` contra
    ~1,5M de fichas, así que cada lunes se redescubren las mismas 100.000 y
    1,4 millones nunca entran. El síntoma engaña, porque el contador de
    "fichas descubiertas" sube y parece que funciona.

    Rotando el punto de partida, cada descubrimiento muerde una tajada
    distinta y el catálogo completo entra a lo largo de varias pasadas. Es el
    mismo truco de `_con_rotacion` en vigia.py, aplicado un nivel más arriba.
    """
    pendientes = ubicar_sitemap(dominio)
    if not pendientes:
        return []

    planas, vistos, nivel = [], set(), 0
    rotado = False
    while pendientes and nivel < MAX_NIVEL_SITEMAP and len(planas) < tope:
        siguiente = []
        for s in pendientes:
            if len(planas) >= tope:
                break
            if s in vistos:
                continue
            vistos.add(s)
            try:
                urls = _locs(bajar(s))
            except Exception:              # noqa: BLE001
                continue
            for u in urls:
                if _ES_XML.search(u):
                    siguiente.append(u)
                else:
                    planas.append(u)

        # Si entre los hijos hay unos que dicen "producto/pdp/sku", se siguen
        # SOLO esos: mezclarlos con los de categorías y landings llenaba el
        # catálogo de páginas que nunca tienen precio.
        preferidos = [x for x in siguiente if _DE_PRODUCTO.search(x)]
        pendientes = preferidos or siguiente

        # La rotación se aplica UNA vez, en el primer nivel donde ya hay
        # varios sitemaps hijos entre los que elegir. Más abajo daría igual
        # (dentro de un mismo hijo el orden no importa) y más arriba no hay
        # nada que rotar todavía.
        if desplazamiento and not rotado and len(pendientes) > 1:
            k = desplazamiento % len(pendientes)
            pendientes = pendientes[k:] + pendientes[:k]
            rotado = True
            print("      (rotación de sitemaps: se empieza por el %d de %d)"
                  % (k + 1, len(pendientes)))
        nivel += 1

    # Se normaliza el doble slash que publican algunos sitemaps (easy.cl
    # emite https://www.easy.cl//ruta), porque rompe la petición.
    planas = [re.sub(r"(?<!:)//+", "/", u) for u in planas]
    fichas = [u for u in planas
              if not NO_ES_FICHA.search(u) and u.count("/") >= 3 and len(u) > 28]
    return list(dict.fromkeys(fichas))[:tope]


def desde_categorias(dominio, tope=120, max_categorias=6):
    """Cuando el sitemap solo trae categorías (caso Jumbo), se entra a unas
    pocas y se sacan los enlaces de ficha del HTML."""
    try:
        urls = _locs(bajar("https://www.%s/sitemap.xml" % dominio))
    except Exception:                      # noqa: BLE001
        return []

    categorias = [u for u in urls
                  if not u.endswith(".xml") and not _es_ficha(u) and u.count("/") >= 4]
    fichas = []
    for cat in categorias[:max_categorias]:
        try:
            html = bajar(cat)
        except Exception:                  # noqa: BLE001
            continue
        hallados = re.findall(r'https://[a-z0-9.\-]+/[a-z0-9\-/]+/p\b', html, re.I)
        fichas += [u for u in dict.fromkeys(hallados)]
        if len(fichas) >= tope:
            break
    return fichas[:tope]


_ROBOTS_CACHE = {}


def _parser_robots(dominio):
    """El robots.txt de la tienda, parseado — cacheado por dominio, se pide
    una sola vez aunque `fichas_de` revise miles de URLs.

    Si no se puede leer (falla la conexión, da 404, etc.) se devuelve un
    parser vacío, que en la práctica permite todo: sin robots.txt no hay
    regla que respetar, y no vale la pena bloquear el descubrimiento por
    esto — el filtro es para respetar un `Disallow` DECLARADO, no para
    inventar restricciones que la tienda no puso.
    """
    if dominio in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[dominio]
    rp = urllib.robotparser.RobotFileParser()
    try:
        crudo = bajar("https://www.%s/robots.txt" % dominio, tiempo=12)
        rp.parse(crudo.splitlines())
    except Exception:                              # noqa: BLE001
        rp.parse([])                                # vacío = permite todo
    _ROBOTS_CACHE[dominio] = rp
    return rp


def _permitidas_por_robots(dominio, urls):
    """Filtra las URLs que el propio robots.txt de la tienda marca como
    `Disallow` — no leerlas es gratis (ya bajamos el archivo para encontrar
    el sitemap) y es la diferencia entre "leemos lo que publican para
    cualquiera" y "entramos donde ellos mismos pidieron que no".
    """
    rp = _parser_robots(dominio)
    excluidas = [u for u in urls if not rp.can_fetch("*", u)]
    if excluidas:
        print("  %s: %d ficha(s) excluidas por robots.txt (Disallow)"
              % (dominio, len(excluidas)))
    return [u for u in urls if u not in set(excluidas)]


def fichas_de(dominio, tope=100000, desplazamiento=0):
    """Todas las URLs de ficha que se puedan hallar, por cualquiera de las dos
    vías. Devuelve lista sin duplicados, ya filtrada contra `Disallow`."""
    encontradas = desde_sitemap(dominio, tope, desplazamiento)
    if not encontradas:
        encontradas = desde_categorias(dominio, tope)
    unicas = list(dict.fromkeys(encontradas))[:tope]
    return _permitidas_por_robots(dominio, unicas)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    for dom in sys.argv[1:] or ["spdigital.cl", "jumbo.cl", "santaisabel.cl"]:
        f = fichas_de(dom, tope=20)
        print("%-18s %d fichas" % (dom, len(f)))
        for u in f[:3]:
            print("    ", u[:88])
