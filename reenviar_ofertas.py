# -*- coding: utf-8 -*-
"""Reenvía al grupo de Rat.IA las ofertas/errores de precio del canal de un
aliado externo (contenido redistribuido con su autorización).

QUÉ HACE Y QUÉ NO
------------------------------------------------------------------------------
Escucha mensajes nuevos en los canales de origen (via cuenta de usuario real,
Telethon — no un bot, porque los bots no pueden "espiar" canales ajenos, solo
los que administran) y los reenvía al tópico de Rat.IA que corresponde, sin
marca de origen (a pedido): el mensaje llega con el formato normal de Rat.IA,
sin decir de qué canal salió.

NO hace nada con cookies, rastreo entre sitios, ni bots de "login" — eso se
descartó a propósito. Esto es un reenvío de texto, plano y simple.

EL RUTEO ES POR CANAL, NO POR EL % DEL TEXTO (20-ago-2026, rediseñado)
------------------------------------------------------------------------------
Cada canal del aliado YA está segmentado por su propio nombre: "Ofertas
Chile 50%" solo publica alrededor de ese piso, "80" son los errores más
grandes, "Tecno"/"Supermercado" son por categoría. Rutear tratando de leer
el % del TEXTO de cada mensaje (como hacía la v1 de este script) es más
frágil: si el formato de un mensaje no calza con el regex, se pierde el
mensaje aunque el canal mismo ya garantice que es un hallazgo válido.

Por eso el mapa canal → tópico es la fuente de verdad (variables
CANALES_ERRORES, CANALES_OFERTAS, CANALES_SUPERMERCADO, CANALES_TECNO,
CANALES_VUELOS, CANALES_HOGAR más abajo). Un canal YA CATEGORIZADO se
reenvía SIEMPRE, sin exigirle ningún formato al texto -- el canal mismo es
la garantía. El filtro de "(NN%)"/"$NNN" (`_parece_oferta`) solo se evalúa
para un canal que llegue en CANALES_ORIGEN pero no esté en NINGUNA lista de
categoría: ahí sí conviene ser cauto antes de reenviar algo a ciegas, y se
avisa en el log "canal sin categoría asignada" para arreglarlo a mano en
vez de mandarlo al tópico equivocado en silencio.

BUG REAL encontrado y corregido el 20-ago-2026: antes el filtro de % se le
exigía a TODOS los mensajes, categorizados o no, y el regex solo aceptaba
UN decimal ("98,9%"). El bot del aliado a veces manda DOS ("95,38%" en el
canal "80", "39.34%" con punto en COCHA) -- verificado leyendo mensajes
reales vía Telethon, no a ciegas. Resultado: se perdían en silencio
hallazgos reales del canal "80", el más importante, y el canal COCHA
(Vuelos) probablemente no había reenviado NADA nunca por el mismo motivo.
El regex ahora acepta cualquier cantidad de decimales, y de todos modos ya
no aplica a canales categorizados.

RUTEO POR CANAL DE ORIGEN (tal como quedaron los canales del aliado)
------------------------------------------------------------------------------
    75%, 80              -> 🚨 Errores de precio   (CANALES_ERRORES)
    50%, 60%, 60% Sin MKP -> 🏷️ Ofertas 50-75%      (CANALES_OFERTAS)
    Tecno                 -> 📱 Electrónicos         (CANALES_TECNO)
    Supermercado          -> 🛒 Supermercado         (CANALES_SUPERMERCADO)
    COCHA                 -> ✈️ Vuelos               (CANALES_VUELOS)
    (ninguno todavía)     -> 🏠 Hogar                (CANALES_HOGAR, por si
                             el aliado suma un canal de esa categoría después)
El canal "... Chat" NO es un canal de ofertas, es conversación: no debe
estar en CANALES_ORIGEN ni en ninguna categoría.

REQUIERE UNA SESIÓN DE USUARIO REAL, NO SE PUEDE AUTOMATIZAR DE UNA
------------------------------------------------------------------------------
Telethon necesita loguearse UNA VEZ como una cuenta de Telegram de verdad,
con el código que llega al teléfono. Eso no lo puede hacer un script solo —
hay que correr `python reenviar_ofertas.py --login` a mano, una sola vez,
y de ahí el archivo `.session` que se genera queda para todas las corridas
siguientes sin volver a pedir código.

Para canales PRIVADOS (a los que se entró por link de invitación, como el
"addlist" del aliado — el caso normal acá) no hay @usuario público, solo un
ID numérico interno. `listar_canales.py` lista todos los chats de la sesión
con su ID.

CORRE COMO PROCESO QUE NO SE APAGA, NO COMO CRON
------------------------------------------------------------------------------
Esto tiene que quedar escuchando todo el tiempo — no sirve un cron de
GitHub Actions de "correr y listo". Necesita un hostname que no se apague:
un VPS chico o un servicio tipo Railway/Fly.io (~$5 USD/mes). GitHub Actions
NO alcanza para esto (está pensado para trabajos que terminan, no para
quedarse escuchando).

    python reenviar_ofertas.py --login     # una sola vez, a mano
    python reenviar_ofertas.py             # queda escuchando

VARIABLES DE ENTORNO
------------------------------------------------------------------------------
    TG_API_ID, TG_API_HASH        de https://my.telegram.org/apps (cuenta que
                                   ya está unida a los canales de origen)
    CANALES_ORIGEN                 @usuario o ID de TODOS los canales a
                                   escuchar, separados por coma. SIN el canal
                                   "... Chat".
    CANALES_ERRORES                subconjunto de arriba -> VIGIA_TOPICO_ERRORES
    CANALES_OFERTAS                subconjunto de arriba -> VIGIA_TOPICO_OFERTAS
    CANALES_SUPERMERCADO           subconjunto de arriba -> VIGIA_TOPICO_SUPERMERCADO
    CANALES_TECNO                  subconjunto de arriba -> VIGIA_TOPICO_ELECTRONICOS
    CANALES_VUELOS                 subconjunto de arriba -> VIGIA_TOPICO_VUELOS
    CANALES_HOGAR                  subconjunto de arriba -> VIGIA_TOPICO_HOGAR
    TELEGRAM_BOT_TOKEN, VIGIA_CHAT_ID          los mismos que ya usa alertas.py
    VIGIA_TOPICO_ERRORES, VIGIA_TOPICO_OFERTAS, VIGIA_TOPICO_ELECTRONICOS,
    VIGIA_TOPICO_SUPERMERCADO, VIGIA_TOPICO_VUELOS, VIGIA_TOPICO_HOGAR
                                   IDs de tópico del grupo de Rat.IA
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

try:
    from telethon import TelegramClient, events
    from telethon.extensions import html as tl_html
except ImportError:
    print("Falta telethon: pip install telethon")
    raise

SESION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reenvio.session")

# Lo que el mensaje original trae para "loguearte"/unirte a MÁS canales del
# aliado — no tiene sentido en el mensaje reenviado, y es justo el tipo de
# instrucción (bots de login, links de invitación) que no queremos empujar
# a los suscriptores de Rat.IA. Se recorta por línea, no se descarta el
# mensaje entero: el precio y el link del producto sí importan.
_LINEAS_A_SACAR = re.compile(
    r"(añadir carpeta|únete a los grupos|t\.me/addlist|t\.me/\w*bot|"
    r"/login|configuraci[oó]n seg[uú]n tu dispositivo|evitar rastreo|"
    r"abrir enlaces en|navegador en app)", re.I)

# "(60%)", "(98,9%)", "(95,38%)", "(39.34%)" -- CUALQUIER cantidad de
# decimales y separador coma O punto (el bot del aliado usa los dos según
# el canal). Antes exigía exactamente UN decimal y perdía en silencio
# los mensajes con dos (ver docstring de _parece_oferta). Se usa SOLO
# como filtro de sanidad, no para decidir el tópico -- eso lo decide el
# canal, ver CATEGORIAS más abajo.
_PORCENTAJE = re.compile(r"\(\s*\d{1,3}(?:[.,]\d+)?\s*%\s*\)")

# "$237.990" -- respaldo del filtro de sanidad para el día que un mensaje
# real no traiga ningún "(NN%)" en absoluto (ej. un precio plano sin
# comparación porcentual).
_PRECIO = re.compile(r"\$\s?\d")

# Orden de prioridad: la primera variable que contenga al canal gana. Errores
# va primero a propósito -- si algún día un canal quedara mal clasificado en
# dos listas a la vez, es preferible que gane el más urgente.
CATEGORIAS = (
    ("CANALES_ERRORES", "VIGIA_TOPICO_ERRORES"),
    ("CANALES_SUPERMERCADO", "VIGIA_TOPICO_SUPERMERCADO"),
    ("CANALES_TECNO", "VIGIA_TOPICO_ELECTRONICOS"),
    ("CANALES_VUELOS", "VIGIA_TOPICO_VUELOS"),
    ("CANALES_HOGAR", "VIGIA_TOPICO_HOGAR"),
    ("CANALES_OFERTAS", "VIGIA_TOPICO_OFERTAS"),
)


def _limpiar(texto):
    lineas = [l for l in (texto or "").split("\n") if not _LINEAS_A_SACAR.search(l)]
    return "\n".join(lineas).strip()


def _parece_oferta(texto):
    """Filtro de sanidad para canales SIN categoría asignada (ver _topico):
    ¿el mensaje trae algún "(NN%)" o un precio en pesos? No decide el
    tópico, solo si vale la pena reenviarlo cuando no hay otra señal.

    BUG REAL encontrado y corregido el 20-ago-2026: el regex de % solo
    aceptaba UN decimal ("98,9%"), y el bot del aliado a veces manda DOS
    ("95,38%" en el canal "80", "39.34%" con punto en COCHA) -- eso hacía
    que se descartaran en silencio hallazgos reales del canal MÁS
    importante. Verificado con mensajes reales vía Telethon antes de
    tocar el regex, no a ciegas.

    Los canales que SÍ tienen categoría (todo lo que hoy está en
    CANALES_ORIGEN) ya NO pasan por acá -- ver _al_llegar: el canal mismo
    es la garantía de que es un hallazgo real, no el formato exacto del
    texto. Esta función queda como red de seguridad para el día que se
    sume un canal sin clasificar todavía."""
    return bool(_PORCENTAJE.search(texto or "") or _PRECIO.search(texto or ""))


def _en_lista(chat_id, chat_username, variable_env):
    valores = {v.strip().lower().lstrip("@") for v in
              (os.environ.get(variable_env) or "").split(",") if v.strip()}
    if str(chat_id) in valores:
        return True
    if chat_username and chat_username.lower() in valores:
        return True
    return False


def _topico(chat_id, chat_username):
    """A qué tópico va este canal, según CATEGORIAS. None si no está
    clasificado en ninguna lista -- ese caso se descarta con aviso."""
    for canales_var, topico_var in CATEGORIAS:
        if _en_lista(chat_id, chat_username, canales_var):
            return os.environ.get(topico_var)
    return None


def _enviar_a_ratia(texto, topico, intentos=3):
    """Manda el mensaje. Reintenta en 429 (límite de envíos de Telegram) --
    el aliado a veces publica el MISMO hallazgo en 2-3 de sus canales a la
    vez (verificado: un "Logitech Teclado Gamer" salió simultáneo en los
    canales 75%/80/Tecno), así que pueden llegar varios reenvíos casi
    juntos al mismo grupo. Sin reintento, el 429 se perdía en silencio."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("VIGIA_CHAT_ID") or "").strip()
    if not token or not chat:
        print("[sin telegram configurado] %s" % texto[:200])
        return
    cuerpo = {"chat_id": chat, "text": texto, "parse_mode": "HTML",
              "disable_web_page_preview": False}
    if topico:
        cuerpo["message_thread_id"] = int(topico)
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    for intento in range(intentos):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            if r.get("ok"):
                return
            print("telegram rechazó: %s" % str(r)[:200])
            return
        except urllib.error.HTTPError as e:
            if e.code == 429 and intento < intentos - 1:
                cuerpo_error = json.loads(e.read().decode())
                espera = (cuerpo_error.get("parameters") or {}).get("retry_after", 2)
                print("429 de Telegram, esperando %ss (intento %d/%d)..."
                      % (espera, intento + 1, intentos))
                time.sleep(espera)
                continue
            print("telegram falló: HTTP %s %s"
                  % (e.code, e.read().decode(errors="replace")[:150]))
            return
        except Exception as e:                                   # noqa: BLE001
            print("telegram falló: %s" % str(e)[:150])
            return


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login", action="store_true",
                   help="solo hace el login interactivo y sale")
    a = p.parse_args()

    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        print("Faltan TG_API_ID / TG_API_HASH (sacarlos de my.telegram.org/apps)")
        return 1

    # Un canal PRIVADO (al que se entró por link de invitación, como el
    # "addlist" del aliado) no tiene @usuario público -- solo un ID numérico,
    # que `listar_canales.py` imprime. Se acepta cualquiera de los dos acá:
    # los valores que son puro dígitos (con o sin signo -) se pasan como int,
    # el resto se deja como string de @usuario.
    def _entidad(c):
        c = c.strip().lstrip("@")
        try:
            return int(c)
        except ValueError:
            return c

    canales = [_entidad(c) for c in (os.environ.get("CANALES_ORIGEN") or "").split(",") if c.strip()]
    if not canales and not a.login:
        print("Falta CANALES_ORIGEN (usernames o IDs separados por coma)")
        return 1

    client = TelegramClient(SESION, int(api_id), api_hash)

    if a.login:
        client.start()  # pide teléfono + código la primera vez, interactivo
        print("Login OK. La sesión quedó guardada en %s" % SESION)
        return 0

    @client.on(events.NewMessage(chats=canales))
    async def _al_llegar(event):
        # `raw_text` es texto plano puro: pierde cualquier link "escondido"
        # bajo una palabra (ej. "PRODUCTO" clickeable hacia la ficha real,
        # que en el mensaje original es un text_link, no una URL visible).
        # `tl_html.unparse` reconstruye el mismo mensaje en HTML a partir del
        # texto + las entidades reales (`event.message.entities`), preservando
        # esos links -- se manda con parse_mode HTML, así llegan clickeables
        # también en Rat.IA. Verificado el 20-ago-2026 (bug reportado: los
        # links de producto no eran clickeables en el reenvío).
        texto = _limpiar(tl_html.unparse(event.message.message or "", event.message.entities))
        if not texto:
            return
        chat = await event.get_chat()
        username = getattr(chat, "username", None)
        topico = _topico(event.chat_id, username)

        # El canal manda: si ya tiene categoría asignada (CANALES_ERRORES,
        # CANALES_OFERTAS, etc.), es de confianza y se reenvía SIEMPRE, sin
        # exigirle un formato exacto al texto. El filtro de "% o precio"
        # queda solo para el canal que todavía no está clasificado en
        # ninguna lista -- ahí sí conviene ser cauto antes de reenviar algo
        # a ciegas. Antes se exigía el % SIEMPRE, y un formato de dos
        # decimales ("95,38%", "39.34%") hacía que se perdieran en
        # silencio hallazgos reales del canal "80" -- el más importante.
        if topico is None:
            if not _parece_oferta(texto):
                print("descartado (canal sin categoría y sin %% ni precio "
                      "legible): %s..." % texto[:60].replace("\n", " "))
                return
            print("⚠️  canal SIN categoría asignada (chat_id=%s, @%s) -- "
                  "revisar CANALES_* en las variables de entorno: %s..."
                  % (event.chat_id, username, texto[:50].replace("\n", " ")))
            return

        _enviar_a_ratia(texto, topico)
        print("reenviado (topico %s): %s..." % (topico, texto[:50].replace("\n", " ")))

    print("Escuchando %d canal(es): %s" % (len(canales), ", ".join(str(c) for c in canales)))
    client.start()
    client.run_until_disconnected()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
