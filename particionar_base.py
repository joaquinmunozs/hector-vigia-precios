# -*- coding: utf-8 -*-
"""(codex) Reduce una base monolítica al conjunto de tiendas de un shard."""
import argparse
import os
import sqlite3
import sys

import baseprecios

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


TABLAS_POR_URL = ("linea_base", "fallos", "descartadas", "alertas", "publicaciones")


def particionar(ruta, tiendas, vacuum=True):
    tiendas = sorted({t.strip().lower() for t in tiendas if t.strip()})
    if not tiendas:
        raise ValueError("el shard necesita al menos una tienda")
    con = sqlite3.connect(ruta, timeout=120)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        antes = con.execute("SELECT COUNT(*) FROM precios").fetchone()[0]
        signos = ",".join("?" for _ in tiendas)
        con.execute("CREATE TEMP TABLE conservar_url (url TEXT PRIMARY KEY)")
        con.execute("INSERT INTO conservar_url SELECT DISTINCT url FROM precios "
                    "WHERE lower(tienda) IN (%s)" % signos, tiendas)
        existentes = {f[0] for f in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for tabla in TABLAS_POR_URL:
            if tabla in existentes:
                con.execute("DELETE FROM %s WHERE url NOT IN "
                            "(SELECT url FROM conservar_url)" % tabla)
        con.execute("DELETE FROM precios WHERE lower(tienda) NOT IN (%s)" % signos,
                    tiendas)
        con.commit()
        despues = con.execute("SELECT COUNT(*) FROM precios").fetchone()[0]
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    if vacuum and despues < antes:
        con = sqlite3.connect(ruta, timeout=120)
        try:
            con.execute("VACUUM")
        finally:
            con.close()
    return antes, despues


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=baseprecios.RUTA)
    p.add_argument("--tiendas", default=os.environ.get("HECTOR_TIENDAS", ""))
    p.add_argument("--sin-vacuum", action="store_true")
    args = p.parse_args()
    antes, despues = particionar(args.db, args.tiendas.split(","),
                                  vacuum=not args.sin_vacuum)
    print("base del shard: %d → %d filas de precios" % (antes, despues))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
