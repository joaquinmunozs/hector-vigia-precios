# -*- coding: utf-8 -*-
"""Arma y envía los avisos a Telegram, con la estructura de las capturas.

FORMATO
------------------------------------------------------------------------------
Copiado de los canales que ya funcionan en Chile:

    🔥 SRank  spdigital  Apple iPhone 16 Pro Max 🔍
    $1.489.990 -> $16.989 (98,9%)
    PRODUCTO                       <- enlace directo a la ficha
    Precio histórico 📉
    06/08/2026  $1.489.990
    05/08/2026  $1.349.990

El "rank" no es decoración: ordena de un vistazo qué tan grande es el hallazgo,
y es lo que hace que alguien abra el mensaje en vez de ignorarlo. Sale del
porcentaje de caída, no se asigna a mano.

CUATRO TÓPICOS, Y UN MENSAJE PUEDE IR A DOS
------------------------------------------------------------------------------
  🚨 Errores de precio  -> caída 70% a 99%   (cualquier producto)
  🏷️ Ofertas reales     -> caída 50% a 70%   (cualquier producto)
  📱 Electrónicos       -> caída 35% a 70%   (solo electrónica)
  🏠 Hogar              -> caída 35% a 70%   (solo hogar)

Los dos primeros van separados a propósito: quien paga por errores de precio
no quiere que le llegue una oferta del 55% mezclada, y quien busca ofertas no
necesita que le suene el teléfono a las 4 AM por un error que dura 20 minutos.

EL DUPLICADO ES INTENCIONAL (8-ago-2026)
------------------------------------------------------------------------------
Un hallazgo de categoría entre 50% y 70% sale DOS VECES: en Ofertas reales y
en su tópico de categoría. No es un bug ni un descuido — es lo que se pidió:
quien sigue solo Electrónicos no debería perderse un -60% en un notebook por
no estar mirando el tópico general, y quien sigue solo Ofertas tampoco.

Sobre el 70% NO se duplica: ahí ya es error de precio y va únicamente al
tópico de errores. El tope de los tópicos de categoría es 69%.

Nada bajo 35% llega acá, y entre 35% y 50% solo llega si es de categoría:
ese corte lo pone `baseprecios.evaluar`, no este archivo.
"""
import json
import os
import re
import time
import urllib.request

import baseprecios
import categorias

# Rango de caída -> (emoji, etiqueta). El orden importa: se evalúa de mayor a
# menor y se usa el primero que calce.
#   70%-99% caen en los rangos de ERROR     (van al tópico de errores)
#   50%-70% caen en los de OFERTA           (ofertas + su categoría si tiene)
#   35%-50% caen en el de CATEGORIA         (solo su tópico de categoría)
RANGOS = (
    (0.90, "🔥", "SRank"),      # 90-99%: el error grande, el que vuela
    (0.80, "🅰️", "ARank"),
    (0.70, "🅱️", "BRank"),      # 70-80%: error de precio más leve
    (0.60, "🏷️", "Oferta+"),    # 60-70%: oferta muy fuerte
    (0.50, "🏷️", "Oferta"),     # 50-60%: el piso general, oferta real
    (0.35, "📉", "Rebaja"),     # 35-50%: solo en Electrónicos u Hogar
)


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def _rango(caida):
    for minimo, emoji, etiqueta in RANGOS:
        if caida >= minimo:
            return emoji, etiqueta
    return "📉", "Rebaja"


# Categoría -> variable de entorno con el id de su tópico.

# ── Variantes del mismo producto ─────────────────────────────────────────
#
# Una cortina publicada en ocho colores son ocho URLs distintas con el mismo
# precio y la misma caída: ocho avisos idénticos que saturan el tópico y
# hacen que la gente lo silencie. Para el suscriptor es UN producto.
#
# Se agrupan por (tienda, título sin el color/talla, precio). El color se
# quita del título en vez de compararlo: dos publicaciones que solo difieren
# en "Azul" / "Rojo" colapsan a la misma clave, y se manda una sola con el
# número de variantes.
_COLOR = re.compile(
    r"\b(negro|blanco|gris|plata|dorado|beige|crema|caf[ée]|marr[óo]n|"
    r"azul|celeste|marino|verde|oliva|amarillo|naranj[ao]|rojo|burdeo|"
    r"rosa|rosado|fucsia|morado|lila|violeta|turquesa|coral|vino|"
    r"multicolor|transparente|natural|"
    r"talla\s*\w{1,4}|\b(?:xs|s|m|l|xl|xxl)\b|"
    r"\d+\s*(?:plazas?|cm|mm|ml|lts?|litros?)|"
    r"unitalla|surtido)\b", re.I)


def _clave_variante(det):
    base = _COLOR.sub("", det.get("nombre") or "")
    base = re.sub(r"[\s\-–—,/]+", " ", base).strip().lower()
    return (det.get("tienda"), base, int(det.get("precio") or 0))


def agrupar_variantes(detecciones):
    """Un aviso por producto, no por color. Devuelve (deteccion, cuantas)."""
    grupos = {}
    for d in detecciones:
        grupos.setdefault(_clave_variante(d), []).append(d)
    salida = []
    for lista in grupos.values():
        # Se avisa la de mayor caída: si alguna variante bajó más, esa es la
        # que vale la pena mostrar.
        mejor = max(lista, key=lambda x: x.get("caida") or 0)
        salida.append((mejor, len(lista)))
    return salida


TOPICO_DE_CATEGORIA = {
    categorias.ELECTRONICOS: "VIGIA_TOPICO_ELECTRONICOS",
    categorias.HOGAR: "VIGIA_TOPICO_HOGAR",
}


def destinos(det):
    """A qué tópicos va este hallazgo. Puede ser más de uno — ver el duplicado.

    Devuelve una lista de ids de tópico (los que estén configurados). Si
    ninguno lo está, devuelve [None], que manda el mensaje al hilo general
    del grupo en vez de perderlo.
    """
    ids = []

    if det["tipo"] == baseprecios.ERROR:
        # Sobre 70% es error de precio y va SOLO ahí: no se duplica a la
        # categoría, porque el tope de esos tópicos es 69%.
        ids.append(os.environ.get("VIGIA_TOPICO_ERRORES"))
    else:
        if det["tipo"] == baseprecios.OFERTA:
            ids.append(os.environ.get("VIGIA_TOPICO_OFERTAS"))
        # El duplicado: 50%-70% de categoría sale también acá. Y 35%-50%
        # de categoría sale ÚNICAMENTE acá.
        var = TOPICO_DE_CATEGORIA.get(det.get("categoria"))
        if var:
            ids.append(os.environ.get(var))

    ids = [i for i in ids if i]
    return ids or [None]


def _escapar(t):
    return (str(t or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _limpiar_nombre(nombre, url):
    """El nombre suele venir con entidades HTML dobles o vacío."""
    n = (nombre or "").replace("&amp;#x20;", " ").replace("&#x20;", " ")
    n = " ".join(n.split())
    if not n:
        # Sin nombre, se arma uno legible desde la URL.
        cola = url.rstrip("/").split("/")[-1].replace("-", " ")
        n = cola[:70].title()
    return n[:90]


def armar_texto(det, tienda):
    """El mensaje listo para Telegram, en HTML."""
    emoji, etiqueta = _rango(det["caida"])
    pct = det["caida"] * 100

    lineas = [
        "%s <b>%s</b>  <i>%s</i>" % (emoji, etiqueta, _escapar(tienda)),
        "<b>%s</b>" % _escapar(_limpiar_nombre(det.get("nombre"), det["url"])),
        "",
        "<s>%s</s> → <b>%s</b>  (<b>%.1f%%</b>)" % (
            _plata(det["referencia"]), _plata(det["precio"]), pct),
        "",
        '<a href="%s">PRODUCTO</a>' % det["url"],
    ]

    if det.get("historico"):
        lineas += ["", "<b>Precio histórico</b> 📉"]
        # El historial viene de más reciente a más antiguo, que es como lo
        # muestran los canales de referencia.
        for p in det["historico"][:4]:
            lineas.append("  %s" % _plata(p))
    elif not det.get("con_historial"):
        # Honestidad: si la referencia es la foto del día uno y no un historial
        # acumulado, el mensaje lo dice. Un "-80%" sin respaldo es justo lo que
        # hace que la gente deje de creerle al canal.
        lineas += ["", "<i>Referencia: precio al registrar el producto "
                   "(historial aún en construcción)</i>"]

    return "\n".join(lineas)


def _enviar(texto, topico_id=None):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("VIGIA_CHAT_ID") or
            os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("[sin telegram configurado]\n%s\n" % texto)
        return False

    cuerpo = {"chat_id": chat, "text": texto, "parse_mode": "HTML",
              "disable_web_page_preview": False}
    if topico_id:
        cuerpo["message_thread_id"] = int(topico_id)

    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"):
            print("telegram rechazó: %s" % str(r)[:200])
            return False
        return True
    except Exception as e:                       # noqa: BLE001 — un aviso que
        print("telegram falló: %s" % str(e)[:150])   # falla no puede tumbar
        return False                                  # el barrido entero


def enviar_hallazgos(con, hallazgos):
    """Manda cada hallazgo a sus tópicos y lo registra para no repetirlo.

    Un hallazgo puede ir a DOS tópicos (ver `destinos`), pero se anota UNA
    sola vez: la anotación existe para no volver a avisar el mismo producto
    dentro de VENTANA_REPETIR, y eso es por producto, no por tópico.
    """
    enviados = 0
    # Las variantes del mismo producto (una cortina en ocho colores) colapsan
    # a UN aviso. Para el suscriptor es un producto; ocho mensajes idénticos
    # son la forma más rápida de que silencie el tópico.
    agrupados = agrupar_variantes(hallazgos)
    # Los más grandes primero: si hay muchos, los que importan salen antes.
    for det, variantes in sorted(agrupados, key=lambda x: -x[0]["caida"]):
        texto = armar_texto(det, det.get("tienda", ""))
        if variantes > 1:
            texto += ("\n\n<i>Disponible en %d variantes "
                      "(color o medida).</i>" % variantes)
        llego = False
        for topico in destinos(det):
            if _enviar(texto, topico):
                llego = True
                enviados += 1
                # Telegram tumba al bot si se le mandan más de ~20 mensajes
                # por minuto al mismo chat.
                time.sleep(3.5)
        if llego:
            for hermana in hallazgos:
                if _clave_variante(hermana) == _clave_variante(det):
                    baseprecios.anotar_alerta(con, hermana)
    con.commit()
    return enviados
