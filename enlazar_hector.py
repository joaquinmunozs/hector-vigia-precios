# -*- coding: utf-8 -*-
"""Enlaza a Héctor (@HectorRat_bot) con el grupo y los tópicos YA creados.

DIFERENCIA CON configurar_telegram.py
------------------------------------------------------------------------------
Ese script CREA los tópicos. Este los DESCUBRE. Si el grupo ya tiene sus temas
hechos a mano, crear otros dejaría cuatro tópicos y los avisos irían a los
vacíos — que es peor que no configurar nada, porque parece que funciona.

CÓMO FUNCIONA
------------------------------------------------------------------------------
Telegram no tiene forma de listar los temas de un grupo: no existe un
`getForumTopics`. La única vía es que alguien ESCRIBA en cada tema, porque
cada mensaje trae su `message_thread_id`. Por eso el script se queda
escuchando y va anotando los temas que ve aparecer.

USO
------------------------------------------------------------------------------
    python enlazar_hector.py

Y mientras corre, escribe un mensaje cualquiera en CADA uno de los dos temas
del grupo. El script los detecta, los muestra y deja las variables listas.
"""
import json
import os
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ESPERA_MAX = 300          # 5 min escuchando; de sobra para escribir dos mensajes


def api(metodo, **params):
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/%s" % (TOKEN, metodo),
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read().decode())


def main():
    if not TOKEN:
        print("Falta TELEGRAM_BOT_TOKEN en el entorno.")
        return 1

    yo = api("getMe")
    if not yo.get("ok"):
        print("Token inválido: %s" % yo)
        return 1
    print("Bot: @%s\n" % yo["result"]["username"])
    print("Escribe un mensaje en CADA tema del grupo. Escuchando...\n")

    chat_id, titulo = None, ""
    temas = {}                # message_thread_id -> nombre visto
    desplazamiento = 0
    limite = time.time() + ESPERA_MAX

    while time.time() < limite:
        try:
            # long polling: la petición se queda abierta hasta que llega algo,
            # así no hay que machacar la API con consultas cada segundo.
            u = api("getUpdates", offset=desplazamiento, timeout=25,
                    allowed_updates=["message", "my_chat_member"])
        except Exception as e:                        # noqa: BLE001
            print("  (reintentando: %s)" % str(e)[:50])
            continue

        for x in u.get("result", []):
            desplazamiento = x["update_id"] + 1
            msg = x.get("message") or {}
            chat = msg.get("chat") or (x.get("my_chat_member") or {}).get("chat") or {}
            if not chat:
                continue
            if chat.get("type") in ("supergroup", "group"):
                chat_id, titulo = chat["id"], chat.get("title", "")

            hilo = msg.get("message_thread_id")
            if hilo and hilo not in temas:
                # El nombre del tema solo viene en el mensaje de creación. Si
                # no está, se usa el texto que escribió la persona como pista.
                nombre = ((msg.get("forum_topic_created") or {}).get("name")
                          or (msg.get("reply_to_message", {})
                                 .get("forum_topic_created", {}) or {}).get("name")
                          or (msg.get("text") or "")[:30] or "(sin nombre)")
                temas[hilo] = nombre
                print("  tema detectado: id %-6s  %s" % (hilo, nombre))

        if chat_id and len(temas) >= 2:
            break

    if not chat_id:
        print("\nNo vi ningún mensaje. Revisa que Héctor esté en el grupo")
        print("y que hayas escrito DESPUÉS de agregarlo.")
        return 1

    print("\nGrupo: %s  (%s)" % (titulo, chat_id))
    if len(temas) < 2:
        print("Solo vi %d tema(s). Faltó escribir en el otro." % len(temas))
        return 1

    ids = list(temas)
    print("\n" + "=" * 62)
    print("Asigna cuál es cuál (el primero será ERRORES):")
    for i, t in enumerate(ids, 1):
        print("  %d) id %-6s  %s" % (i, t, temas[t]))
    print("=" * 62)

    lineas = [
        "TELEGRAM_BOT_TOKEN=%s" % TOKEN,
        "VIGIA_CHAT_ID=%s" % chat_id,
        "VIGIA_TOPICO_ERRORES=%s" % ids[0],
        "VIGIA_TOPICO_OFERTAS=%s" % ids[1],
    ]
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    print("\nGuardado en %s:" % ruta)
    for l in lineas:
        print("   " + (l[:34] + "..." if l.startswith("TELEGRAM") else l))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
