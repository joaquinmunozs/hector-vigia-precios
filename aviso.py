# -*- coding: utf-8 -*-
"""Manda el aviso a Telegram, sin depender del proyecto de Steve.

En el PC, `vigia.py` importa el módulo `telegram` de steve-bot. Dentro del
contenedor de Modal ese proyecto no existe, así que acá va una versión mínima
que habla directo con la API de Telegram usando las mismas variables de
entorno del secreto `steve-env`.
"""
import json
import os
import urllib.request


def mensaje(texto, topico=None):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("[sin telegram]\n%s" % texto)
        return

    cuerpo = {"chat_id": chat, "text": texto, "parse_mode": "HTML",
              "disable_web_page_preview": False}
    hilo = os.environ.get("TELEGRAM_TOPICO_GENERAL")
    if hilo:
        cuerpo["message_thread_id"] = int(hilo)

    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
        if not r.get("ok"):
            print("telegram rechazó:", str(r)[:200])
    except Exception as e:      # noqa: BLE001 — un aviso fallido no debe
        print("telegram falló:", str(e)[:120])   # tumbar la pasada entera
