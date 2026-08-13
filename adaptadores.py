# -*- coding: utf-8 -*-
"""Lectores a medida para tiendas que no se dejan leer del HTML.

POR QUÉ HACE FALTA
------------------------------------------------------------------------------
El extractor genérico saca el precio del HTML. Sirve para casi todas, pero hay
tiendas donde el HTML simplemente NO trae el precio: lo pide el navegador
aparte, por XHR, y lo pinta después. Ahí no hay regex que valga y tampoco
sirve pagar un proxy — el problema no es que te bloqueen, es que el dato no
está en lo que te entregan.

La salida es pedirle el precio a la MISMA API que usa la página. Es la vía
más limpia y más barata: un JSON de 50 KB en vez de un HTML de 1,1 MB, sin
navegador y sin proxy.

CÓMO SE ENCONTRÓ (6-ago-2026)
------------------------------------------------------------------------------
Falabella devolvía 403 a todo y estaba clasificada como "necesita proxy de
US$180/mes". Dos hallazgos la rescataron:

  1. El 403 se cae solo con mandar `Referer`. Con esa cabecera la ficha
     responde 200 y entrega 1,1 MB de HTML.
  2. Pero ese HTML tiene `"offers": []` — el precio no viene. Sí viene de
     `/s/browse/v1/product/cl?productId=N`, que responde 200 sin bloqueo.

O sea: nunca hizo falta el proxy, hacía falta mirar de dónde saca el precio la
propia página.
"""
import json
import re

PRECIO_MIN = 500
PRECIO_MAX = 20_000_000

CABECERAS_FALABELLA = {
    # Sin Referer, Falabella responde 403 a la ficha. Con él, 200. Es la
    # comprobación más barata que hay antes de pensar en pagar un proxy.
    "Referer": "https://www.falabella.com/falabella-cl/",
    "Accept": "application/json",
}

_ID_FALABELLA = re.compile(r"/product/(\d+)/")

# El JSON anida los precios en varios sitios según el tipo de oferta
# (eventPrice, internetPrice, normalPrice...). Se recogen todos y se toma el
# MENOR: es el que el cliente termina pagando, y es contra ese que tiene
# sentido medir una caída.
#
# ── PERO NO TODO "price" ES UN PRECIO (13-ago-2026) ───────────────────────
#
# La ley chilena obliga a mostrar el precio por unidad de medida junto al
# precio: "$34.030  ($681 el gr)". Falabella lo publica DENTRO del mismo
# bloque, en una llave `pum`:
#
#     "price": ["34.030"],  "pum": {"label": "gr", "price": ["681"]}
#
# Esto se recogía con una regex sobre el JSON entero, así que el pum entraba
# a la bolsa como un precio más — y como por definición es más chico que el
# precio, `min()` lo elegía SIEMPRE. El vigía leía $681 donde el producto
# vale $34.030 y anunciaba un -98% que no existía.
#
# De los 11 avisos de "🚨 ERROR DE PRECIO" que salieron entre el 11 y el
# 13-ago, NUEVE eran esto. Le pasa a todo lo que se vende por peso, volumen
# o en pack: media tienda de supermercado y hogar.
#
# Por eso se recorre el árbol en vez de barrerlo con una regex: es la única
# forma de saber si un "price" cuelga de un `pum` o no. Ver `probar_pum.py`.
def _num(txt):
    """'1.299.990' -> 1299990. El punto es separador de miles en Chile."""
    if isinstance(txt, bool):
        return None
    if isinstance(txt, (int, float)):
        n = int(txt)
        return n if PRECIO_MIN <= n <= PRECIO_MAX else None
    s = re.sub(r"[^\d]", "", str(txt))
    if not s:
        return None
    n = int(s)
    return n if PRECIO_MIN <= n <= PRECIO_MAX else None


def _recolectar_precios(nodo, salida):
    """Todos los precios del árbol, saltándose los de unidad de medida.

    Se descarta por DOS señales, no una: la llave `pum` y el `"type": "pum"`
    del propio bloque. La segunda es la que aguanta que Falabella lo mueva de
    sitio o lo renombre — si sólo se mirara el nombre de la llave, el aviso
    falso volvería solo el día que cambien el JSON, y nadie se enteraría
    hasta ver un -98% en el tópico.
    """
    if isinstance(nodo, dict):
        if str(nodo.get("type") or "").lower() == "pum":
            return
        for clave, valor in nodo.items():
            if str(clave).lower() == "pum":
                continue
            if str(clave).lower() == "price":
                for x in (valor if isinstance(valor, list) else [valor]):
                    n = _num(x)
                    if n:
                        salida.append(n)
                continue
            _recolectar_precios(valor, salida)
    elif isinstance(nodo, list):
        for v in nodo:
            _recolectar_precios(v, salida)


def falabella(url, bajar):
    """Precio de una ficha de Falabella, vía su propia API interna.

    `bajar(url, cabeceras)` lo inyecta quien llama, para no atar este módulo a
    una librería de red concreta.
    """
    m = _ID_FALABELLA.search(url)
    if not m:
        return None

    api = "https://www.falabella.com/s/browse/v1/product/cl?productId=" + m.group(1)
    crudo = bajar(api, CABECERAS_FALABELLA)
    try:
        datos = json.loads(crudo)
    except Exception:                      # noqa: BLE001
        return None

    precios = []
    _recolectar_precios(datos, precios)
    if not precios:
        return None

    d = datos.get("data") or {}
    nombre = d.get("displayName") or d.get("name") or ""

    # El estado de stock se DEVUELVE, no se usa para descartar la lectura.
    #
    # Antes esto devolvía None cuando el producto estaba agotado, con la idea
    # de no avisar. El efecto era el contrario: quien llama trata None como
    # "el adaptador no supo leerlo" y cae al extractor genérico, que sí saca
    # un precio del HTML — - o sea el filtro de stock quedaba anulado.
    #
    # Devolviendo `hay_stock: False` el precio entra al historial (sirve para
    # la referencia) pero NO dispara alerta, que es lo que se quería.
    return {
        "nombre": str(nombre)[:120],
        "precio": min(precios),
        "hay_stock": datos.get("responseType") != "OUT_OF_STOCK",
        "fuente": "api-falabella",
    }


# Qué tienda usa qué lector. Se consulta ANTES del extractor genérico.
POR_DOMINIO = {
    "falabella.com": falabella,
}


def para(dominio):
    return POR_DOMINIO.get(dominio)
