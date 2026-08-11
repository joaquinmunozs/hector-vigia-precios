import os
# -*- coding: utf-8 -*-
"""Las tiendas chilenas a vigilar, clasificadas por su defensa anti-bot.

CÓMO SE ARMÓ ESTA LISTA
------------------------------------------------------------------------------
No es una lista a ojo: cada dominio se consultó en vivo el 6-ago-2026 y se
clasificó leyendo sus CABECERAS HTTP reales, buscando la firma de cada WAF:

    __cf_bm / cf_clearance  -> Cloudflare Bot Management (el bravo)
    x-akamai / akamai       -> Akamai
    incap_ses / incapsula   -> Imperva
    cf-ray sin __cf_bm      -> Cloudflare básico (CDN, sin bot management)

LIMITACIÓN CONOCIDA: se miró la PORTADA de cada sitio, no sus páginas de
producto ni el checkout. Una tienda puede verse blanda en la home y tener
protección fuerte en la ficha de producto. Por eso `probar_extraccion.py`
verifica producto por producto antes de darla por buena.

ORDEN DE ATAQUE
------------------------------------------------------------------------------
LIMPIA  -> Fase 1: se consultan desde Modal sin proxy. Costo $0.
MEDIA   -> Fase 1b: Cloudflare de CDN. Probablemente pasan con tráfico bajo y
           espaciado; si empiezan a devolver desafíos, se sube la pausa antes
           de pensar en pagar proxy.
DIFICIL -> Fase 2: necesitan proxy residencial de pago. NO se tocan hasta que
           el proyecto genere ingresos.

CUÁNTO COSTARÍA LA FASE 2 (medido, no estimado, el 6-ago-2026)
------------------------------------------------------------------------------
Se bajaron páginas reales para medir el peso EN EL CABLE, que es lo que cobra
un proxy — no el HTML descomprimido:

    spdigital.cl  560 KB de HTML ->  87 KB transferidos (gzip)
    abc.cl      1.226 KB         -> 100 KB
    hites.com   2.420 KB         -> 203 KB

O sea ~150 KB por ficha, ~6.800 fichas por GB. El extractor solo lee HTML: no
baja imágenes ni JS, y eso divide el costo por diez frente al cálculo ingenuo.

Los proxies de datacenter YA NO SIRVEN para esto: pasaron de 42-48% de bloqueo
(2024) a 71-78% (2026) contra el top-1.000 de e-commerce, y bajo Cloudflare Bot
Management o Akamai rinden menos del 20%. Akamai usa el fingerprint TLS
(JA3/JA4) como señal primaria, así que cambiar de IP no basta: hay que imitar
el handshake del navegador. Residenciales rinden 85-99%.

Por eso lo que de verdad funciona no es el proxy suelto sino el "unlocker"
(resuelven el anti-bot y solo cobran los éxitos): Bright Data Web Unlocker a
US$1,50 por 1.000 peticiones, sin cobrar los fallos.

LA CONCLUSIÓN QUE IMPORTA: no hay que barrer Falabella entero. Barrerlo cada
2 h serían ~US$2.700/mes (2,5 millones CLP), inviable. Pero como los errores
de precio duran HORAS, no minutos, basta con 4 pasadas al día sobre los ~1.000
productos CAROS: US$180/mes (~$165.000 CLP), que se paga con 25 suscriptores.
La fase 2 tiene que ser quirúrgica — iPhone, notebooks sobre $500.000,
zapatillas premium — no el catálogo completo.
"""

LIMPIA = "limpia"
MEDIA = "media"
DIFICIL = "dificil"

# Sitios cuyo HTML llega bien pero SIN el precio: lo pinta JavaScript después.
# No es bloqueo — la petición pasa — pero leerlos exige un navegador de verdad
# (Playwright/CDP), que es mucho más caro en tiempo y memoria. Se dejan
# marcados para no reintentarlos en vano en cada pasada.
# Verificado en vivo el 6-ago-2026 producto por producto.
SOLO_CON_NAVEGADOR = {
    "pcfactory.cl",   # 227 KB de HTML, cero marcadores de precio
    "wei.cl",         # "price":0 en el HTML; el real llega por XHR
    "kmarket.cl",
    "preunic.cl",
    "buscalibre.cl",  # la ficha llega en 9 KB: es un esqueleto
}

TIENDAS = [
    # ── Sin firma de WAF fuerte — por acá se parte ───────────────────────
    # PARIS: resuelto el 7-ago-2026 tras varios intentos fallidos. Su HTML
    # pesa 1,9 MB y SÍ trae el precio, pero ninguna estrategia lo veía: publica
    # el JSON-LD como string ESCAPADO dentro del streaming de Next.js
    # (`self.__next_s.push`), no como un <script application/ld+json> normal.
    # Lo lee la estrategia `_de_next_streaming` del extractor. Verificado 8/8.
    {"dominio": "paris.cl",         "nivel": LIMPIA, "rubro": "retail"},
    {"dominio": "easy.cl",          "nivel": LIMPIA, "rubro": "hogar"},
    {"dominio": "jumbo.cl",         "nivel": LIMPIA, "rubro": "supermercado"},
    {"dominio": "santaisabel.cl",   "nivel": LIMPIA, "rubro": "supermercado"},
    {"dominio": "casaideas.cl",     "nivel": LIMPIA, "rubro": "hogar"},
    {"dominio": "construmart.cl",   "nivel": LIMPIA, "rubro": "ferreteria"},
    {"dominio": "dinsa.cl",         "nivel": LIMPIA, "rubro": "importadora"},
    {"dominio": "wei.cl",           "nivel": LIMPIA, "rubro": "electro"},
    {"dominio": "dafiti.cl",        "nivel": LIMPIA, "rubro": "moda"},
    {"dominio": "bata.cl",          "nivel": LIMPIA, "rubro": "calzado"},
    {"dominio": "crandon.cl",       "nivel": LIMPIA, "rubro": "accesorios"},
    {"dominio": "converse.cl",      "nivel": LIMPIA, "rubro": "calzado"},
    {"dominio": "kmarket.cl",       "nivel": LIMPIA, "rubro": "retail"},
    {"dominio": "buscalibre.cl",    "nivel": LIMPIA, "rubro": "libros"},
    {"dominio": "mall.cl",          "nivel": LIMPIA, "rubro": "marketplace"},
    {"dominio": "dijon.com",        "nivel": LIMPIA, "rubro": "hogar"},
    {"dominio": "tecnored.cl",      "nivel": LIMPIA, "rubro": "tecnologia"},

    # ── Cloudflare de CDN, sin bot management activo ─────────────────────
    {"dominio": "hites.com",        "nivel": MEDIA, "rubro": "retail"},
    # ABCDin y La Polar se fusionaron: los dos dominios redirigen (301) a
    # abc.cl y comparten el mismo sitemap. Vigilarlos por separado duplicaría
    # 27.000 productos y haría el doble de peticiones al mismo servidor.
    {"dominio": "abc.cl",           "nivel": MEDIA, "rubro": "retail"},
    {"dominio": "tricot.cl",        "nivel": MEDIA, "rubro": "moda"},
    {"dominio": "preunic.cl",       "nivel": MEDIA, "rubro": "farmacia"},
    {"dominio": "rosen.cl",         "nivel": MEDIA, "rubro": "hogar"},
    {"dominio": "doite.cl",         "nivel": MEDIA, "rubro": "outdoor"},
    {"dominio": "salcobrand.cl",    "nivel": MEDIA, "rubro": "farmacia"},
    {"dominio": "farmaciasahumada.cl", "nivel": MEDIA, "rubro": "farmacia"},
    {"dominio": "spdigital.cl",     "nivel": MEDIA, "rubro": "tecnologia"},
    {"dominio": "pcfactory.cl",     "nivel": MEDIA, "rubro": "tecnologia"},
    {"dominio": "reifschneider.cl", "nivel": MEDIA, "rubro": "tecnologia"},
    {"dominio": "reuse.cl",         "nivel": MEDIA, "rubro": "tecnologia"},
    {"dominio": "hushpuppies.cl",   "nivel": MEDIA, "rubro": "calzado"},
    {"dominio": "vans.cl",          "nivel": MEDIA, "rubro": "calzado"},
    {"dominio": "underarmour.cl",   "nivel": MEDIA, "rubro": "deporte"},
    {"dominio": "antartica.cl",     "nivel": MEDIA, "rubro": "libros"},
    {"dominio": "sportline.cl",     "nivel": MEDIA, "rubro": "deporte"},
    {"dominio": "homy.cl",          "nivel": MEDIA, "rubro": "hogar"},
    {"dominio": "winnerchile.cl",   "nivel": MEDIA, "rubro": "deporte"},

    # ── Marcas propias ───────────────────────────────────────────────────
    # Se probaron una por una el 6-ago-2026. Solo Puma tiene tienda chilena
    # con catálogo real y precio legible (8/8 en la prueba, 5.000 fichas).
    #
    # Las demás NO sirven, y el motivo es de negocio, no técnico:
    #   samsung.com / asus.com → sitio corporativo GLOBAL: el sitemap trae
    #       noticias y fichas técnicas, no precios de Chile
    #   motorola.cl → 108 URLs, todas categorías: vitrina, no tienda
    #   xiaomi.cl / newbalance.cl → no publican sitemap
    #   reebok.cl → el sitemap trae contenido, no fichas
    #
    # Conclusión: perseguir marcas individuales rinde poco. Esas marcas se
    # venden en Paris, Falabella, Ripley e Hites, que sí tienen catálogo
    # grande con precio — ahí está el volumen de verdad.
    {"dominio": "puma.cl",          "nivel": MEDIA, "rubro": "deporte"},

    # ── Rescatadas de la lista "imposible" el 6-ago-2026 ─────────────────
    # NO estaban bloqueadas: fallaba el recorrido de sitemaps de descubrir.py,
    # que solo bajaba dos niveles. Con el recorrido recursivo se leen gratis.
    {"dominio": "tottus.cl",        "nivel": MEDIA, "rubro": "supermercado"},

    # RIPLEY — funciona desde una casa, NO desde Modal.
    # Comprobado el 7-ago-2026 con `diagnosticar_tienda` corriendo DENTRO de
    # Modal: la ficha de producto devuelve `HTTP 403` y el sitemap 0 fichas,
    # mientras el MISMO código desde el PC de Joaquín saca más de un millón de
    # URLs y extrae 5/5. O sea no es el código ni la huella TLS: Ripley
    # bloquea las IP de centro de datos y punto.
    #
    # Es el ÚNICO caso donde un proxy residencial se justifica de verdad, y el
    # premio es grande (1,2 millones de fichas). Queda en DIFICIL hasta que el
    # proyecto genere ingresos para pagarlo.
    {"dominio": "ripley.cl",        "nivel": DIFICIL, "rubro": "retail"},

    # ── Ganadas el 6-ago-2026, sin pagar un peso ─────────────────────────
    # FALABELLA: 1,5 millones de fichas (63 sitemaps × 25.000). Se daba por
    # perdida con un 403 de Cloudflare. Dos hallazgos la rescataron:
    #   1. El 403 se cae con solo mandar `Referer`. Con eso responde 200.
    #   2. Pero ese HTML trae `"offers": []`: el precio NO viene ahí. Sí
    #      viene de su propia API, /s/browse/v1/product/cl?productId=N,
    #      que responde 200 sin bloqueo ninguno.
    # Se lee con el adaptador de `adaptadores.py`.
    {"dominio": "falabella.com",    "nivel": MEDIA, "rubro": "retail"},

    # ADIDAS: 7.394 fichas, 5/5. Nunca fue bloqueo por IP — rechaza a urllib
    # por su huella TLS y acepta a curl_cffi con la MISMA IP. Corre sobre
    # Salesforce Commerce, igual que Tricot y Hites.
    {"dominio": "adidas.cl",        "nivel": MEDIA, "rubro": "deporte"},

    # ── Fase 2: acá sí haría falta proxy. No tocar todavía ───────────────
    {"dominio": "cruzverde.cl",     "nivel": DIFICIL, "rubro": "farmacia"},
    {"dominio": "decathlon.cl",     "nivel": DIFICIL, "rubro": "deporte"},
    {"dominio": "apple.com/cl",     "nivel": DIFICIL, "rubro": "tecnologia"},

    # ── Probadas a fondo y descartadas por ahora ─────────────────────────
    # nike.cl     -> 403 "Just a moment" de Cloudflare en TODO, incluido
    #                robots.txt, aun con el TLS imitado. Su API pública
    #                (api.nike.com/product_feed/threads/v2) SÍ responde 200
    #                para marketplace(CL), pero ese feed no trae precios.
    # lider.cl    -> 638 sitemaps × 5.000 = 3,19 millones de fichas, pero la
    #                ficha llega en 14 KB, sin precio ni estado embebido.
    #                Habría que dar con su XHR interno. Por volumen, es el
    #                próximo mejor candidato.
    # sodimac.cl  -> su sitemap trae UNA sola URL, la portada. No publican.
    # imperial.cl -> sin sitemap de fichas.
]


def por_nivel(*niveles):
    return [t for t in TIENDAS if t["nivel"] in niveles]


def resumen():
    from collections import Counter
    c = Counter(t["nivel"] for t in TIENDAS)
    return dict(c)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("tiendas registradas: %d" % len(TIENDAS))
    for k, v in resumen().items():
        print("  %-8s %d" % (k, v))


# ── Reparto entre instancias ─────────────────────────────────────────────
#
# Varias instancias de Héctor pueden correr a la vez desde IPs distintas —la
# de Joaquín, la de Alejandro, la de Max— y eso SUMA capacidad, pero solo si
# cada una se hace cargo de tiendas DISTINTAS.
#
# Dos instancias con la misma lista no duplican volumen: duplican trabajo.
# Barren las mismas fichas, gastan el doble de peticiones contra el mismo
# servidor y avisan dos veces el mismo hallazgo. Ya pasó el 6-ago-2026, con
# un deploy viejo de Modal corriendo en paralelo con GitHub Actions: el
# throughput se desplomó de 40/seg a 0,2/seg.
#
# Repartidas, en cambio, no hay duplicado posible: un hallazgo pertenece a
# una tienda, y esa tienda tiene un solo dueño. Por eso todas pueden
# compartir el mismo bot de Telegram y el mismo grupo sin pisarse.
#
#   HECTOR_TIENDAS=falabella.com,hites.com    -> solo esas dos
#   (sin la variable)                         -> todas
#
# Cada instancia mantiene su propia base: son catálogos distintos.
_SOLO = [d.strip().lower() for d in os.environ.get("HECTOR_TIENDAS", "").split(",") if d.strip()]
if _SOLO:
    _CONOCIDAS = {t["dominio"] for t in TIENDAS}
    _faltan = [d for d in _SOLO if d not in _CONOCIDAS]
    if _faltan:
        # Falla fuerte a propósito: un dominio mal escrito dejaría a esa
        # tienda sin dueño y nadie lo notaria hasta echar de menos sus avisos.
        raise SystemExit("HECTOR_TIENDAS nombra tiendas que no existen: %s" % ", ".join(_faltan))
    TIENDAS = [t for t in TIENDAS if t["dominio"] in _SOLO]
