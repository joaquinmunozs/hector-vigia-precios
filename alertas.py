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

DOS TÓPICOS, Y SOLO DOS
------------------------------------------------------------------------------
  🚨 Errores de precio  -> caída 70% a 99%
  🏷️ Ofertas reales     -> caída 50% a 70%

Van separados a propósito: quien paga por errores de precio no quiere que le
llegue una oferta del 55% mezclada, y quien busca ofertas no necesita que le
suene el teléfono a las 4 AM por un error que dura 20 minutos.

Nada bajo 50% se avisa: ese corte lo pone `baseprecios.evaluar` con
UMBRAL_OFERTA, así que a este archivo NUNCA le llega un hallazgo menor. Las
etiquetas de abajo empiezan en 50% por lo mismo.
"""
import json
import os
import time
import urllib.request

import baseprecios

# Rango de caída -> (emoji, etiqueta). El orden importa: se evalúa de mayor a
# menor y se usa el primero que calce. El piso es 50%: nada menor llega acá.
#   70%-99% caen en los rangos de ERROR (van al tópico de errores)
#   50%-70% caen en los de OFERTA       (van al tópico de ofertas)
RANGOS = (
    (0.90, "🔥", "SRank"),      # 90-99%: el error grande, el que vuela
    (0.80, "🅰️", "ARank"),
    (0.70, "🅱️", "BRank"),      # 70-80%: error de precio más leve
    (0.60, "🏷️", "Oferta+"),    # 60-70%: oferta muy fuerte
    (0.50, "🏷️", "Oferta"),     # 50-60%: el piso, oferta real
)


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def _rango(caida):
    for minimo, emoji, etiqueta in RANGOS:
        if caida >= minimo:
            return emoji, etiqueta
    return "🏷️", "Oferta"


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
    """Manda cada hallazgo a su tópico y lo registra para no repetirlo."""
    topico_error = os.environ.get("VIGIA_TOPICO_ERRORES")
    topico_oferta = os.environ.get("VIGIA_TOPICO_OFERTAS")

    enviados = 0
    # Los más grandes primero: si hay muchos, los que importan salen antes.
    for det in sorted(hallazgos, key=lambda d: -d["caida"]):
        destino = topico_error if det["tipo"] == baseprecios.ERROR else topico_oferta
        if _enviar(armar_texto(det, det.get("tienda", "")), destino):
            baseprecios.anotar_alerta(con, det)
            enviados += 1
            # Telegram tumba al bot si se le mandan más de ~20 mensajes por
            # minuto al mismo chat.
            time.sleep(3.5)
    con.commit()
    return enviados
