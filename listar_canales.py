# -*- coding: utf-8 -*-
"""Lista tus chats de Telegram con su ID, para canales PRIVADOS sin @usuario.

POR QUÉ HACE FALTA
------------------------------------------------------------------------------
`reenviar_ofertas.py` necesita saber A QUÉ canales escuchar. Si son canales
públicos basta con su @usuario, pero un canal al que entraste por un link de
invitación (como el "addlist" del aliado) normalmente NO tiene @usuario
público — solo un ID numérico interno, que Telethon puede ver una vez que ya
estás suscrito. Este script lo imprime.

USO
------------------------------------------------------------------------------
Necesita la MISMA sesión que ya creaste con `--login` (el archivo
reenvio.session en esta misma carpeta) — no vuelve a pedir código.

    python listar_canales.py

Copia los IDs de los canales de ofertas que te interesan (probablemente
saldrán con "Ofertas Chile" en el nombre) y pégalos en CANALES_ORIGEN,
CANALES_SUPERMERCADO y CANALES_TECNO — reemplazando los @usuario que se
habían pensado usar. reenviar_ofertas.py acepta tanto @usuario como ID
numérico en esas variables, Telethon resuelve cualquiera de los dos.
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from telethon import TelegramClient

SESION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reenvio.session")


async def main():
    api_id = os.environ.get("TG_API_ID")
    api_hash = os.environ.get("TG_API_HASH")
    if not api_id or not api_hash:
        print("Faltan TG_API_ID / TG_API_HASH en el entorno.")
        return 1

    client = TelegramClient(SESION, int(api_id), api_hash)
    await client.start()

    print("%-14s %-8s %s" % ("ID", "TIPO", "NOMBRE"))
    print("-" * 70)
    async for d in client.iter_dialogs():
        entidad = d.entity
        tipo = "canal" if getattr(entidad, "broadcast", False) else (
            "grupo" if getattr(entidad, "megagroup", False) else "chat")
        print("%-14s %-8s %s" % (d.id, tipo, d.name))

    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
