# -*- coding: utf-8 -*-
"""Crea los tópicos del grupo de Telegram y deja los IDs listos para usar.

QUÉ HAY QUE HACER ANTES (esto NO lo puede hacer un bot)
------------------------------------------------------------------------------
Telegram no permite que un bot cree un grupo. Los tres primeros pasos son
manuales, una sola vez:

  1. En Telegram: menú ☰ → Nuevo grupo → nómbralo (ej. "Vigía de Precios").
  2. Entra al grupo → Editar → activa **Temas** (Topics).
  3. Editar → Administradores → agrega el bot de Steve como administrador,
     con el permiso **Gestionar temas** activado.
  4. Escribe cualquier mensaje en el grupo (el bot necesita "verlo" una vez).

Después corre:

    python configurar_telegram.py

El script encuentra el grupo solo, crea los dos tópicos y te imprime las
variables de entorno que hay que guardar.
"""
import json
import os
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

TOPICOS = [
    ("errores", "🚨 Errores de precio", 0xE74C3C),   # rojo
    ("ofertas", "🏷️ Ofertas reales 40%+", 0x2ECC71),  # verde
]


def _api(token, metodo, **params):
    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/%s" % (token, metodo),
        data=json.dumps(params).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())


def _token():
    t = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if t:
        return t
    # Reusa el token que ya tiene configurado Steve.
    ruta = (r"C:\Users\HP Pavilion\Desktop\01 · MercadoLibre — Planeta Shop"
            r"\steve-bot\.env")
    if os.path.exists(ruta):
        for linea in open(ruta, encoding="utf-8"):
            if linea.strip().startswith("TELEGRAM_BOT_TOKEN"):
                return linea.split("=", 1)[1].strip().strip('"\'')
    return ""


def main():
    token = _token()
    if not token:
        print("No encuentro TELEGRAM_BOT_TOKEN.")
        return 1

    yo = _api(token, "getMe")
    if not yo.get("ok"):
        print("El token no sirve: %s" % yo)
        return 1
    print("Bot: @%s\n" % yo["result"]["username"])

    # Buscar el grupo con temas activados en los mensajes recientes.
    upd = _api(token, "getUpdates", limit=100)
    grupos = {}
    for u in upd.get("result", []):
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "supergroup" and chat.get("is_forum"):
            grupos[chat["id"]] = chat.get("title", "(sin título)")

    if not grupos:
        print("No encontré ningún grupo con TEMAS activados.")
        print("\nRevisa que:")
        print("  · el grupo tenga 'Temas' activado en Editar")
        print("  · el bot sea administrador con permiso 'Gestionar temas'")
        print("  · hayas escrito un mensaje en el grupo DESPUÉS de agregarlo")
        return 1

    if len(grupos) > 1:
        print("Hay más de un grupo con temas. Elige cuál:")
        for i, (cid, t) in enumerate(grupos.items(), 1):
            print("  %d) %s  (%s)" % (i, t, cid))
        return 1

    chat_id, titulo = next(iter(grupos.items()))
    print("Grupo: %s  (%s)\n" % (titulo, chat_id))

    creados = {}
    for clave, nombre, color in TOPICOS:
        r = _api(token, "createForumTopic", chat_id=chat_id,
                 name=nombre, icon_color=color)
        if r.get("ok"):
            tid = r["result"]["message_thread_id"]
            creados[clave] = tid
            print("  ✅ %-26s id %s" % (nombre, tid))
        else:
            desc = str(r.get("description", ""))
            # Si ya existe, no es un error: se avisa y se sigue.
            print("  ⚠️  %-26s %s" % (nombre, desc[:60]))

    if not creados:
        return 1

    print("\n" + "=" * 62)
    print("GUARDA ESTAS VARIABLES (secreto 'vigia-env' en Modal):")
    print("=" * 62)
    print("VIGIA_CHAT_ID=%s" % chat_id)
    for clave, tid in creados.items():
        print("VIGIA_TOPICO_%s=%s" % (clave.upper(), tid))
    print("=" * 62)

    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    with open(ruta, "w", encoding="utf-8") as f:
        f.write("VIGIA_CHAT_ID=%s\n" % chat_id)
        for clave, tid in creados.items():
            f.write("VIGIA_TOPICO_%s=%s\n" % (clave.upper(), tid))
    print("\nTambién quedaron en %s" % ruta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
