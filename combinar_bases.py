# -*- coding: utf-8 -*-
"""(codex) Une las bases disjuntas de los shards para análisis y respaldo."""
import argparse
import glob
import os
import shutil
import sqlite3
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


TABLAS = ("precios", "linea_base", "fallos", "descartadas", "alertas",
          "publicaciones", "marcadores")


def _columnas(con, esquema, tabla):
    return [f[1] for f in con.execute("PRAGMA %s.table_info(%s)" % (esquema, tabla))]


def combinar(rutas, salida):
    rutas = [os.path.abspath(r) for r in rutas if os.path.exists(r)]
    if not rutas:
        raise ValueError("no se encontraron bases de shards")
    shutil.copy2(rutas[0], salida)
    con = sqlite3.connect(salida, timeout=180)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        for numero, ruta in enumerate(rutas[1:], 1):
            alias = "s%d" % numero
            con.execute("ATTACH DATABASE ? AS %s" % alias, (ruta,))
            tablas_origen = {f[0] for f in con.execute(
                "SELECT name FROM %s.sqlite_master WHERE type='table'" % alias)}
            tablas_destino = {f[0] for f in con.execute(
                "SELECT name FROM main.sqlite_master WHERE type='table'")}
            for tabla in TABLAS:
                if tabla not in tablas_origen or tabla not in tablas_destino:
                    continue
                comunes = [c for c in _columnas(con, "main", tabla)
                           if c in _columnas(con, alias, tabla)
                           and not (tabla in ("precios", "alertas") and c == "id")]
                if not comunes:
                    continue
                cols = ",".join('"%s"' % c for c in comunes)
                modo = "OR REPLACE" if tabla != "precios" else ""
                con.execute("INSERT %s INTO main.%s (%s) SELECT %s FROM %s.%s"
                            % (modo, tabla, cols, cols, alias, tabla))
            con.commit()
            con.execute("DETACH DATABASE %s" % alias)
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        total = con.execute("SELECT COUNT(*) FROM precios").fetchone()[0]
    finally:
        con.close()
    return total


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entrada", required=True)
    p.add_argument("--salida", default="precios.db")
    args = p.parse_args()
    rutas = sorted(glob.glob(os.path.join(args.entrada, "**", "precios.db"),
                             recursive=True))
    total = combinar(rutas, args.salida)
    print("base consolidada: %d shards · %d filas de precios" % (len(rutas), total))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
