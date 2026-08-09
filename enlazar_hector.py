# -*- coding: utf-8 -*-
"""Enlaza a Héctor (@HectorRat_bot) con el grupo y los tópicos YA creados.

DIFERENCIA CON configurar_telegram.py
------------------------------------------------------------------------------
Ese script CREA los tópicos. Este los DESCUBRE. Si el grupo ya tiene sus temas
hechos a mano, crear otros dejaría tópicos duplicados y los avisos irían a los
vacíos — que es peor que no configurar nada, porque parece que funciona.

SON CUATRO TÓPICOS DESDE EL 8-ago-2026
------------------------------------------------------------------------------
  🚨 Errores de precio   caída 70%-99%
  🏷️ Ofertas reales      caída 50%-69%
  📱 Electrónicos        caída 35%-69%, solo electrónica
  🏠 Hogar               caída 35%-69%, solo hogar

La versión anterior de este script asumía EXACTAMENTE DOS temas y asignaba
por orden de aparición: el primero que viera era Errores y el segundo
Ofertas. Con cuatro temas eso escribía un `.env` equivocado en silencio —
los avisos de error se habrían ido a Hogar y nadie se habría enterado hasta
revisar el canal. Ahora se identifican POR NOMBRE, y lo que no se reconoce
se reporta en vez de adivinarse.

CÓMO FUNCIONA
------------------------------------------------------------------------------
Telegram no tiene forma de listar los temas de un grupo: no existe un
`getForumTopics`. La única vía es que alguien ESCRIBA en cada tema, porque
cada mensaje trae su `message_thread_id`. Por eso el script se queda
escuchando y va anotando los temas que ve aparecer.

USO
------------------------------------------------------------------------------
    python enlazar_hector.py

Y mientras corre, escribe un mensaje cualquiera en CADA uno de los cuatro
temas del grupo. El script los detecta, los identifica por nombre y deja las
variables listas.
"""
import json
import os
import re
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ESPERA_MAX = 300          # 5 min escuchando; de sobra para escribir los mensajes

# Nombre del tema -> variable de entorno. Se busca por palabra clave y sin
# tildes ni mayúsculas, porque el nombre real del tópico lo escribe una
# persona y puede venir como "ELECTRONICOS", "Electrónicos" o "📱 Electro".
PATRONES = (
    (re.compile(r"error", re.I), "VIGIA_TOPICO_ERRORES"),
    (re.compile(r"oferta", re.I), "VIGIA_TOPICO_OFERTAS"),
    (re.compile(r"electr[oó]", re.I), "VIGIA_TOPICO_ELECTRONICOS"),
    (re.compile(r"hogar", re.I), "VIGIA_TOPICO_HOGAR"),
)


def _variable_de(nombre):
    for patron, var in PATRONES:
        if patron.search(nombre or ""):
            return var
    return None


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

        if chat_id and len(temas) >= len(PATRONES):
            break

    if not chat_id:
        print("\nNo vi ningún mensaje. Revisa que Héctor esté en el grupo")
        print("y que hayas escrito DESPUÉS de agregarlo.")
        return 1

    print("\nGrupo: %s  (%s)" % (titulo, chat_id))

    # Se identifica por NOMBRE, no por orden de aparición. Un tema que no
    # calce con ningún patrón se reporta y NO se escribe: adivinar acá es
    # exactamente cómo los avisos terminan en el tópico equivocado.
    asignados, sin_reconocer = {}, []
    for hilo, nombre in temas.items():
        var = _variable_de(nombre)
        if var and var not in asignados:
            asignados[var] = hilo
        else:
            sin_reconocer.append((hilo, nombre))

    print("\n" + "=" * 62)
    for _patron, var in PATRONES:
        if var in asignados:
            print("  ✅ %-28s id %s" % (var, asignados[var]))
        else:
            print("  ⚠️  %-28s NO SE VIO" % var)
    for hilo, nombre in sin_reconocer:
        print("  ❓ id %-6s %-20s (no calza con ningún tópico conocido)"
              % (hilo, nombre[:20]))
    print("=" * 62)

    faltan = [v for _p, v in PATRONES if v not in asignados]
    if faltan:
        print("\nFaltó escribir en: %s" % ", ".join(faltan))
        print("No se escribe el .env a medias — corre de nuevo y escribe en")
        print("TODOS los temas, o edita el .env a mano.")
        return 1

    lineas = ["TELEGRAM_BOT_TOKEN=%s" % TOKEN, "VIGIA_CHAT_ID=%s" % chat_id]
    lineas += ["%s=%s" % (v, asignados[v]) for _p, v in PATRONES]

    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("\n".join(lineas) + "\n")
    print("\nGuardado en %s:" % ruta)
    for l in lineas:
        print("   " + (l[:34] + "..." if l.startswith("TELEGRAM") else l))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
