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
_PRECIOS = re.compile(r'"price"\s*:\s*\[\s*"([\d.,]+)"')


def _num(txt):
    """'1.299.990' -> 1299990. El punto es separador de miles en Chile."""
    s = re.sub(r"[^\d]", "", str(txt))
    if not s:
        return None
    n = int(s)
    return n if PRECIO_MIN <= n <= PRECIO_MAX else None


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

    entero = json.dumps(datos, ensure_ascii=False)
    precios = [p for p in (_num(x) for x in _PRECIOS.findall(entero)) if p]
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
