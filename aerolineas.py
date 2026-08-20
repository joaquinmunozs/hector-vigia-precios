# -*- coding: utf-8 -*-
"""Las aerolíneas con rutas desde Chile, y por dónde se les mira el precio.

QUÉ ES ESTO Y QUÉ NO ES
------------------------------------------------------------------------------
Es el equivalente de `tiendas.py`, pero para vuelos. NO es un buscador de
pasajes: es la lista de páginas a las que Héctor le puede pegar para enterarse
de una tarifa anormalmente baja.

POR QUÉ LOS VUELOS NO SON COMO UNA FICHA DE FALABELLA
------------------------------------------------------------------------------
Todo lo que Héctor vigila hoy tiene una ficha con URL fija y un precio
adentro: `.../notebook-hp-15/p` cuesta $499.990 hoy y $299.990 mañana, y la
caída se calcula contra el historial de ESA url. Un vuelo no tiene eso. El
precio depende de ruta + fecha + cuánto queda vendido, y cambia sin que
cambie ninguna URL.

Por eso acá se vigila la PÁGINA DE OFERTAS de cada aerolínea, que es donde
ellas mismas publican "Santiago-Lima desde $89.000". Esa página sí es una URL
fija con precios adentro, o sea el mismo problema que Héctor ya sabe resolver.
Lo que NO cubre: una tarifa barata puntual en una fecha suelta que la
aerolínea no publicó como oferta. Para eso haría falta una API de búsqueda
(Amadeus, Kiwi, Skyscanner), que es otra arquitectura y se cobra.

Dicho de otro modo: esto detecta LO QUE LA AEROLÍNEA ANUNCIA y los ERRORES DE
PRECIO (la tarifa de US$4 a Europa que alguien cargó mal), que es justamente
el hallazgo que hace famoso a un canal de ofertas.

CÓMO SE ARMÓ ESTA LISTA (20-ago-2026)
------------------------------------------------------------------------------
Se probaron 49 URLs contra las aerolíneas de verdad, no de memoria. Quedaron
las que responden 200 con contenido real. Las que no entraron están al final
con el motivo, para que nadie las vuelva a agregar a ciegas creyendo que se
olvidaron.

`nivel` sale de esa misma prueba:
    LIMPIA  -> responde directo, sin defensa visible
    WAF     -> tiene un muro (Cloudflare/Incapsula) que hay que rodear
"""

LIMPIA = "limpia"
WAF = "waf"

AEROLINEAS = [
    # -- Las tres que operan la mayoría de los vuelos desde Chile ----------
    {"clave": "latam", "nivel": LIMPIA, "pais": "CL",
     "url": "https://www.latamairlines.com/cl/es/ofertas-vuelos",
     "nota": "La grande. Nacional + toda Sudamérica + larga distancia."},
    {"clave": "jetsmart", "nivel": LIMPIA, "pais": "CL",
     "url": "https://jetsmart.com/cl/es/",
     "nota": "Low cost. Donde más aparecen los errores de precio."},
    {"clave": "skyairline", "nivel": LIMPIA, "pais": "CL",
     "url": "https://www.skyairline.com/chile/destinos",
     "nota": "Low cost. La portada da desafío WAF, /chile/destinos no."},

    # -- Sudamérica -------------------------------------------------------
    {"clave": "avianca", "nivel": LIMPIA, "pais": "CO",
     "url": "https://www.avianca.com/cl/es/ofertas/",
     "nota": "SCL-BOG y conexiones."},
    # Copa quedó en WAF, no en LIMPIA, y no por descuido: al medirla el
    # 20-ago dio desafío en el PRIMER escalón (2 hilos) y después siguió
    # bloqueando incluso con una sola petición cada 5 segundos. La medición
    # dejó marcada la IP de la casa. Para vigilarla de verdad hay que
    # pasarla por el proxy de Cloudflare, como easy.cl.
    # Ver docs/medicion-aerolineas-2026-08-20.md
    {"clave": "copa", "nivel": WAF, "pais": "PA",
     "url": "https://www.copaair.com/es-cl/",
     "nota": "SCL-PTY, hub al Caribe y USA. NECESITA PROXY: bloquea la IP de casa."},
    {"clave": "aerolineasarg", "nivel": LIMPIA, "pais": "AR",
     "url": "https://www.aerolineas.com.ar/",
     "nota": "SCL-EZE/AEP."},
    {"clave": "gol", "nivel": LIMPIA, "pais": "BR",
     "url": "https://www.voegol.com.br/es",
     "nota": "SCL-GRU/GIG."},
    {"clave": "wingo", "nivel": LIMPIA, "pais": "CO",
     "url": "https://www.wingo.com/es/ofertas",
     "nota": "Low cost de Copa."},
    {"clave": "paranair", "nivel": LIMPIA, "pais": "PY",
     "url": "https://www.paranair.com/",
     "nota": "SCL-ASU."},
    {"clave": "arajet", "nivel": LIMPIA, "pais": "DO",
     "url": "https://www.arajet.com/es/ofertas",
     "nota": "Low cost dominicana, SCL-SDQ."},

    # -- Norteamérica -----------------------------------------------------
    {"clave": "american", "nivel": LIMPIA, "pais": "US",
     "url": "https://www.aa.com/i18n/travel-info/travel-deals.jsp",
     "nota": "SCL-MIA/DFW."},
    {"clave": "delta", "nivel": LIMPIA, "pais": "US",
     "url": "https://www.delta.com/es/es/flight-deals/overview",
     "nota": "SCL-ATL."},
    {"clave": "united", "nivel": LIMPIA, "pais": "US",
     "url": "https://www.united.com/es/cl/fly/deals.html",
     "nota": "SCL-IAH."},

    # -- Europa -----------------------------------------------------------
    {"clave": "iberia", "nivel": LIMPIA, "pais": "ES",
     "url": "https://www.iberia.com/cl/ofertas-vuelos/",
     "nota": "SCL-MAD."},
    {"clave": "airfrance", "nivel": LIMPIA, "pais": "FR",
     "url": "https://wwws.airfrance.cl/",
     "nota": "SCL-CDG."},
    {"clave": "klm", "nivel": LIMPIA, "pais": "NL",
     "url": "https://www.klm.cl/",
     "nota": "SCL-AMS."},
    {"clave": "lufthansa", "nivel": LIMPIA, "pais": "DE",
     "url": "https://www.lufthansa.com/cl/es/homepage",
     "nota": "SCL-FRA vía GRU."},
    {"clave": "britishairways", "nivel": LIMPIA, "pais": "GB",
     "url": "https://www.britishairways.com/travel/offers/public/es_cl",
     "nota": "SCL-LHR vía GRU."},
    {"clave": "aireuropa", "nivel": LIMPIA, "pais": "ES",
     "url": "https://www.aireuropa.com/cl/es/vuelos/ofertas-vuelos.html",
     "nota": "SCL-MAD."},
    {"clave": "level", "nivel": LIMPIA, "pais": "ES",
     "url": "https://www.flylevel.com/es/ofertas/",
     "nota": "SCL-BCN, low cost de larga distancia."},
    {"clave": "iberojet", "nivel": LIMPIA, "pais": "ES",
     "url": "https://www.iberojet.com/",
     "nota": "Chárter/larga distancia a España."},

    # -- Resto del mundo --------------------------------------------------
    {"clave": "turkish", "nivel": LIMPIA, "pais": "TR",
     "url": "https://www.turkishairlines.com/es-cl/",
     "nota": "SCL-IST vía GRU/EZE."},
    {"clave": "qantas", "nivel": LIMPIA, "pais": "AU",
     "url": "https://www.qantas.com/cl/es.html",
     "nota": "SCL-SYD, la ruta transpacífica."},
]

# -- Las que NO entraron, y por qué (probadas el 20-ago-2026) -------------
#
# No están por olvido: se probaron y quedaron fuera. Si alguien las quiere
# sumar, el trabajo pendiente está descrito acá y no hay que redescubrirlo.
#
#   · aircanada  -> HTTP 403 + desafío. Bloquea la IP de casa. Necesita el
#                   mismo tratamiento que tottus.cl, que hoy no tenemos.
#   · emirates   -> HTTP 403 directo. Igual llega a SCL solo por codeshare,
#                   así que la pérdida es chica.
#   · boa        -> desafío WAF en todas sus URLs. Vuela SCL-VVI.
#   · qatar      -> todas las rutas probadas dieron 404; falta encontrar la
#                   URL buena de su portal chileno.
#   · plusultra  -> 404. Revisar si sigue volando a Chile.
#   · estelar    -> el dominio no resuelve desde acá.
#
# skyairline es un caso aparte y vale la pena leerlo: su PORTADA
# (`skyairline.com` y `/chile`) devuelve desafío WAF, pero `/chile/destinos`
# responde 200 con 986 KB. O sea la aerolínea no bloquea: bloquea esa portada.
# Es la misma lección que dejó spdigital en `tiendas.py` -- medir contra la
# portada miente sobre la tienda entera.

POR_CLAVE = {a["clave"]: a for a in AEROLINEAS}


def objetivos():
    """En el formato que espera `medir_limites.OBJETIVOS`: clave -> (url, cabeceras)."""
    return {a["clave"]: (a["url"], {"Accept-Language": "es-CL,es;q=0.9"})
            for a in AEROLINEAS}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print("%d aerolíneas con rutas desde Chile\n" % len(AEROLINEAS))
    print("%-16s %-5s %-8s %s" % ("CLAVE", "PAÍS", "NIVEL", "NOTA"))
    print("-" * 88)
    for a in AEROLINEAS:
        print("%-16s %-5s %-8s %s" % (a["clave"], a["pais"], a["nivel"], a["nota"][:52]))
