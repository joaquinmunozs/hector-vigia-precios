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
        for d in list(pila):
            if isinstance(d, dict) and isinstance(d.get("@graph"), list):
                pila.extend(d["@graph"])
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
                }
    return None


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


def extraer(html):
    """Devuelve {nombre, precio, hay_stock, fuente} o lanza SinPrecio."""
    if not html or len(html) < 2000:
        # Una ficha de producto real nunca pesa tan poco. Si llega así, es una
        # página de desafío del WAF o un esqueleto que rellena JavaScript.
        raise SinPrecio("respuesta muy corta (%d bytes): bloqueo o render por JS"
                        % len(html or ""))
    for f in ESTRATEGIAS:
        try:
            r = f(html)
        except Exception:            # noqa: BLE001 — una estrategia rota no
            continue                 # puede tumbar a las otras tres
        if r:
            return r
    raise SinPrecio("ninguna estrategia encontró precio")
