# -*- coding: utf-8 -*-
"""Saca (nombre, precio, stock) de la página de un producto.

POR QUÉ MULTI-ESTRATEGIA
------------------------------------------------------------------------------
Se probaron los sitios chilenos uno por uno el 6-ago-2026 y no hay un método
único que sirva para todos:

  · Ninguno de los candidatos expone la API pública de VTEX (se probó el
    endpoint /api/catalog_system/pub/products/search en 15 tiendas: cero).
  · jumbo, santaisabel, easy y buscalibre traen JSON-LD en la portada.
  · easy y casaideas corren Next.js (__NEXT_DATA__).
  · La ficha de producto de buscalibre llega en 9 KB: el precio lo pinta
    JavaScript después, así que ahí el HTML crudo NO alcanza.

Por eso se prueban 4 estrategias en orden de confiabilidad y se usa la primera
que devuelva un precio creíble. Si ninguna funciona, el sitio queda marcado
como "necesita navegador" y se deja fuera hasta la fase 2 — nunca se inventa
un precio ni se adivina con una regex floja, porque un precio mal leído
dispara una alerta falsa y eso quema la confianza del suscriptor al instante.

ORDEN:
  1. JSON-LD (schema.org/Product)  → es un estándar, el más confiable
  2. __NEXT_DATA__ (Next.js)       → JSON limpio incrustado
  3. Open Graph / meta tags        → product:price:amount
  4. Regex de microdatos           → itemprop="price"
"""
import json
import re
import urllib.parse

# Precio mínimo creíble en CLP. Bajo esto casi siempre es basura del HTML
# (un "12" de una fecha, un id, un contador), no un precio de verdad.
PRECIO_MIN = 500
PRECIO_MAX = 20_000_000


class SinPrecio(Exception):
    """No se pudo leer un precio confiable de esa página."""


def _num(v):
    """Convierte a entero un precio en cualquiera de los formatos que se ven.

    OJO con el formato chileno: '1.299.990' son 1.299.990 pesos, NO 1.29.
    El punto es separador de miles, no decimal. Confundirlos es como se
    genera una alerta falsa de 'bajó 99%'.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = int(v)
        return n if PRECIO_MIN <= n <= PRECIO_MAX else None

    s = str(v).strip().replace("$", "").replace(" ", "")
    if not s:
        return None
    # "1.299.990,50" (europeo) -> se elimina el punto de miles y la coma decimal
    if "," in s and "." in s:
        s = s.replace(".", "").split(",")[0]
    elif "," in s:
        # Puede ser decimal ("1299.99" escrito como "1299,99") o miles.
        partes = s.split(",")
        s = "".join(partes[:-1]) if len(partes[-1]) == 3 else partes[0]
    elif "." in s:
        partes = s.split(".")
        # Si el último bloque tiene 3 dígitos es separador de miles (chileno).
        s = "".join(partes) if len(partes[-1]) == 3 else partes[0]
    s = re.sub(r"[^0-9]", "", s)
    if not s:
        return None
    n = int(s)
    return n if PRECIO_MIN <= n <= PRECIO_MAX else None


def _de_jsonld(html):
    for bloque in re.findall(
            r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            datos = json.loads(bloque.strip())
        except Exception:            # noqa: BLE001 — JSON-LD roto es común
            continue
        pila = datos if isinstance(datos, list) else [datos]
        # Los sitios suelen anidar el producto dentro de @graph.
        #
        # Y las tiendas sobre Shopify publican un `ProductGroup` cuyo precio NO
        # está en el nodo de arriba (su `offers` viene en null) sino dentro de
        # `hasVariant`, una por talla o color, cada una un `Product` completo.
        # Sin descender ahí, hushpuppies.cl y vans.cl descubrían 3.190 fichas y
        # medían CERO: el precio estaba en el HTML y se descartaba el bloque
        # entero por el `@type`. Verificado en vivo el 11-ago-2026.
        for d in list(pila):
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("@graph"), list):
                pila.extend(d["@graph"])
            if isinstance(d.get("hasVariant"), list):
                pila.extend(d["hasVariant"])
        for o in pila:
            if not isinstance(o, dict):
                continue
            tipo = o.get("@type")
            tipos = tipo if isinstance(tipo, list) else [tipo]
            if not any(str(t).lower() in ("product", "book") for t in tipos):
                continue
            ofertas = o.get("offers") or {}
            ofertas = ofertas[0] if isinstance(ofertas, list) and ofertas else ofertas
            if not isinstance(ofertas, dict):
                continue
            precio = _num(ofertas.get("price") or ofertas.get("lowPrice"))
            if not precio:
                # Tricot y otros Salesforce Commerce anidan el precio en
                # `priceSpecification`, no directo en `offers.price`.
                espec = ofertas.get("priceSpecification") or []
                espec = espec if isinstance(espec, list) else [espec]
                for e in espec:
                    if isinstance(e, dict):
                        precio = _num(e.get("price"))
                        if precio:
                            break
            if precio:
                disp = str(ofertas.get("availability") or "")
                return {
                    "nombre": str(o.get("name") or "")[:120],
                    "precio": precio,
                    "hay_stock": "outofstock" not in disp.lower().replace("_", ""),
                    "fuente": "json-ld",
                    "imagen": _url_imagen(o.get("image")),
                }
    return None


def _url_imagen(valor):
    """Normaliza el campo `image` de JSON-LD, que llega de cuatro formas.

    schema.org lo permite como string, como lista de strings, como ImageObject
    (un dict con `url`), o como lista de ImageObject. Asumir una sola forma
    dejaba sin foto a la mitad de las tiendas.
    """
    if isinstance(valor, list):
        valor = valor[0] if valor else None
    if isinstance(valor, dict):
        valor = valor.get("url") or valor.get("contentUrl")
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    # Protocolo relativo (`//cdn.tienda.cl/x.jpg`): Instagram baja la imagen por
    # su cuenta, así que una URL sin esquema le falla.
    if valor.startswith("//"):
        valor = "https:" + valor
    if not valor.startswith("http"):
        return None

    # "Empieza con http" NO alcanza. spdigital.cl publica, de verdad,
    # `<meta property="og:image" content="https:undefined">` — un bug de su
    # JavaScript que deja una URL con forma válida y host inexistente. Sin esta
    # comprobación se guarda como si fuera una foto y el carrusel falla recién
    # al publicar, cuando Instagram no puede bajarla.
    try:
        host = urllib.parse.urlsplit(valor).netloc.lower()
    except ValueError:
        return None
    if "." not in host or host.startswith(".") or host.endswith("."):
        return None
    if any(b in host for b in ("undefined", "null", "localhost", "example.")):
        return None
    return valor


_OG_IMAGEN = (
    r'property="og:image"\s+content="([^"]+)"',
    r'content="([^"]+)"\s+property="og:image"',
    r'name="twitter:image"\s+content="([^"]+)"',
)


def _imagen_de_meta(html):
    """La foto principal desde las meta tags.

    Se usa como respaldo para las estrategias que no traen imagen propia. Es
    justamente la que la tienda eligió para que se vea al compartir el link,
    así que es la mejor candidata disponible sin escarbar el HTML.
    """
    for patron in _OG_IMAGEN:
        m = re.search(patron, html, re.I)
        if m:
            u = _url_imagen(m.group(1))
            if u:
                return u
    return None


# Claves cuyo contenido es un precio, pero no EL precio: por unidad de medida
# (kilo, gramo, litro), por cuota, o de referencia.
_RUIDO = re.compile(
    r"unit|unidad|kilo|gram|litro|liter|medida|measure|ppum|cuota|"
    r"installment|shipping|envio|env[íi]o", re.I)


def _de_nextdata(html):
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        datos = json.loads(m.group(1))
    except Exception:                # noqa: BLE001
        return None

    hallazgo = {}

    def hurgar(nodo, prof=0):
        # Ramas que contienen precios que NO son el precio del producto: el
        # precio por kilo/gramo/litro que muestran los supermercados, y el
        # valor de la cuota. Entrar ahí es como se leyó $681 (por gramo) en
        # vez de $34.030 (el tarro de 500 g).
        if isinstance(nodo, dict):
            for clave in list(nodo):
                if _RUIDO.search(str(clave)):
                    nodo = {k: v for k, v in nodo.items() if not _RUIDO.search(str(k))}
                    break
        # Tope de profundidad: estos árboles son enormes y sin límite el
        # recorrido se come el tiempo del job.
        if prof > 12 or hallazgo.get("precio"):
            return
        if isinstance(nodo, dict):
            for clave in ("price", "salePrice", "finalPrice", "currentPrice",
                          "precio", "bestPrice"):
                if clave in nodo:
                    p = _num(nodo[clave])
                    if p:
                        hallazgo["precio"] = p
                        for nk in ("name", "productName", "title", "nombre"):
                            if isinstance(nodo.get(nk), str):
                                hallazgo["nombre"] = nodo[nk][:120]
                                break
                        return
            for v in nodo.values():
                hurgar(v, prof + 1)
        elif isinstance(nodo, list):
            for v in nodo[:40]:
                hurgar(v, prof + 1)

    hurgar(datos)
    if hallazgo.get("precio"):
        return {"nombre": hallazgo.get("nombre", ""), "precio": hallazgo["precio"],
                "hay_stock": True, "fuente": "next-data"}
    return None


def _de_meta(html):
    for patron in (r'property="product:price:amount"\s+content="([^"]+)"',
                   r'content="([^"]+)"\s+property="product:price:amount"',
                   r'name="twitter:data1"\s+content="([^"]+)"'):
        m = re.search(patron, html, re.I)
        if m:
            p = _num(m.group(1))
            if p:
                t = re.search(r'property="og:title"\s+content="([^"]+)"', html, re.I)
                return {"nombre": (t.group(1)[:120] if t else ""), "precio": p,
                        "hay_stock": True, "fuente": "og-meta"}
    return None


def _de_microdatos(html):
    for patron in (r'itemprop="price"[^>]*content="([^"]+)"',
                   r'content="([^"]+)"[^>]*itemprop="price"'):
        m = re.search(patron, html, re.I)
        if m:
            p = _num(m.group(1))
            if p:
                return {"nombre": "", "precio": p, "hay_stock": True,
                        "fuente": "microdatos"}
    return None


def _de_atributos(html):
    """Precio en atributos HTML tipo `data-price="12990.0"`.

    Lo usan las tiendas sobre Salesforce Commerce (Tricot, Hites). Va al final
    porque es el menos estándar: se prefiere siempre un dato declarado en
    JSON-LD antes que uno leído de un atributo suelto.
    """
    for patron in (r'data-price="([\d.,]+)"',
                   r"data-price='([\d.,]+)'",
                   r'data-product-price="([\d.,]+)"'):
        m = re.search(patron, html, re.I)
        if m:
            p = _num(m.group(1))
            if p:
                t = re.search(r'property="og:title"\s+content="([^"]+)"', html, re.I)
                return {"nombre": (t.group(1)[:120] if t else ""), "precio": p,
                        "hay_stock": True, "fuente": "data-attr"}
    return None



# El bloque escapado de Next.js streaming:
#     "children":"<json con \" adentro>","id":"jsonld-producto-123"
_NEXT_STREAM = re.compile(r'"children":"((?:[^"\\]|\\.)*)","id":"(jsonld-[^"]+)"')


def _de_next_streaming(html):
    """JSON-LD inyectado por el streaming de Next.js (Paris y similares).

    Paris publica su JSON-LD, pero NO como un `<script application/ld+json>`
    normal: lo mete como STRING ESCAPADO dentro de otro JSON, vía
    `self.__next_s.push([...])`. Por eso su HTML pesa 1,9 MB, contiene el
    precio, y aun así ninguna de las otras estrategias lo veía: hay que
    desescapar una vez ANTES de parsear.
    """
    for m in _NEXT_STREAM.finditer(html):
        crudo, ident = m.group(1), m.group(2)
        if "product" not in ident.lower():
            continue
        try:
            datos = json.loads(json.loads('"' + crudo + '"'))
        except Exception:                    # noqa: BLE001
            continue
        if "Product" not in str(datos.get("@type")):
            continue

        ofertas = datos.get("offers") or {}
        if isinstance(ofertas, list):
            ofertas = ofertas[0] if ofertas else {}
        if not isinstance(ofertas, dict):
            continue

        precio = _num(ofertas.get("price") or ofertas.get("lowPrice"))
        if not precio:
            espec = ofertas.get("priceSpecification") or []
            espec = espec if isinstance(espec, list) else [espec]
            for e in espec:
                if isinstance(e, dict):
                    precio = _num(e.get("price"))
                    if precio:
                        break
        if not precio:
            continue

        disp = str(ofertas.get("availability") or "")
        return {"nombre": str(datos.get("name") or "")[:120],
                "precio": precio,
                "hay_stock": "outofstock" not in disp.lower().replace("_", ""),
                "fuente": "next-streaming"}
    return None


ESTRATEGIAS = (_de_jsonld, _de_nextdata, _de_next_streaming, _de_meta,
               _de_microdatos, _de_atributos)


# Cuánto pueden discrepar dos estrategias antes de que el precio se considere
# no confiable. Se calibró con el caso que lo destapó (11-ago-2026): una
# "Manteca de cacao pura 500 g" que vale $34.030 se alertó como $681, porque
# $681 era el PRECIO POR GRAMO que la ficha muestra al lado. La razón entre
# ambos es 50×, así que un tope de 3× lo caza sin ambigüedad.
#
# Por qué 3× y no algo más fino: las estrategias apuntan todas al precio
# VIGENTE, no al precio de lista. Un "antes $99.990 / ahora $19.990" no las
# hace discrepar, porque ninguna lee el "antes". Si dos igual difieren más de
# 3×, es que una está leyendo otra cosa — precio por unidad de medida, un
# producto relacionado, una cuota — y ahí no hay forma de saber cuál.
DESACUERDO_MAX = 3.0


def extraer(html):
    """Devuelve {nombre, precio, hay_stock, fuente} o lanza SinPrecio.

    Se corren TODAS las estrategias, no solo hasta la primera que responda.
    El precio que se devuelve sigue siendo el de la estrategia más confiable
    (el orden de ESTRATEGIAS no cambió); las demás corren para contrastar.

    Si dos se contradicen de forma grosera, se prefiere NO medir el producto
    antes que medirlo mal. Una ficha sin dato es invisible para el suscriptor;
    una alerta falsa de -98% la ve entero, entra al link, se da cuenta de que
    no existe, y deja de creerle al canal. Ese es el error caro.
    """
    if not html or len(html) < 2000:
        # Una ficha de producto real nunca pesa tan poco. Si llega así, es una
        # página de desafío del WAF o un esqueleto que rellena JavaScript.
        raise SinPrecio("respuesta muy corta (%d bytes): bloqueo o render por JS"
                        % len(html or ""))

    hallazgos = []
    for f in ESTRATEGIAS:
        try:
            r = f(html)
        except Exception:            # noqa: BLE001 — una estrategia rota no
            continue                 # puede tumbar a las otras
        if r:
            hallazgos.append(r)

    if not hallazgos:
        raise SinPrecio("ninguna estrategia encontró precio")

    precios = [h["precio"] for h in hallazgos]
    if len(precios) > 1 and max(precios) > min(precios) * DESACUERDO_MAX:
        raise SinPrecio(
            "precios contradictorios (%s): %s" % (
                ", ".join("%s=%d" % (h["fuente"], h["precio"]) for h in hallazgos),
                "probable precio por unidad de medida"))

    elegido = hallazgos[0]

    # LA FOTO ES UN REQUISITO DEL CARRUSEL, NO UN ADORNO
    #
    # Los carruseles de Instagram se armarán con la foto real del producto (ver
    # INTEGRACIONES.md §5, que exige "foto disponible" para publicar). Nada de
    # esto se guardaba: el extractor devolvía solo nombre y precio, así que no
    # había imagen que poner y el requisito era imposible de cumplir.
    #
    # No se falla si no hay foto. Un producto sin imagen sigue sirviendo para el
    # historial y para el aviso de Telegram, que es el producto que se cobra;
    # solo queda fuera del carrusel.
    if not elegido.get("imagen"):
        # Se busca en OTRO hallazgo antes de caer a las meta tags: si el JSON-LD
        # de una variante trajo la foto, es más específica que la de og:image
        # (que a veces es el logo de la tienda).
        for h in hallazgos:
            if h.get("imagen"):
                elegido["imagen"] = h["imagen"]
                break
    if not elegido.get("imagen"):
        elegido["imagen"] = _imagen_de_meta(html)

    return elegido
