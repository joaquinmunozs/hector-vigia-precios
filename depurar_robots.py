# -*- coding: utf-8 -*-
"""Saca del catálogo lo que el robots.txt de la propia tienda diga que no
se debe rastrear (`Disallow`).

`descubrir.fichas_de` ya filtra esto para lo que se descubre DE AHORA EN
ADELANTE (9-ago-2026). `depurar()` es para lo que ya estaba en la base de
ANTES de ese cambio, y para lo que una tienda agregue a su robots.txt más
adelante — por eso `correr.py` la llama sola los lunes, junto con el
descubrimiento semanal (ver `_es_lunes_temprano`), no hace falta acordarse
de correrla a mano.

    python depurar_robots.py            # revisa y borra
    python depurar_robots.py --probar   # solo cuenta, no borra nada
"""
import argparse
import sys

import baseprecios
import descubrir


def depurar(con, probar=False):
    """Revisa cada tienda del catálogo contra su robots.txt y borra (o solo
    cuenta, si `probar`) las fichas que caen en un `Disallow`. Devuelve
    (revisadas, excluidas)."""
    tiendas = [r["tienda"] for r in
               con.execute("SELECT DISTINCT tienda FROM precios").fetchall()]

    total_revisadas, total_excluidas = 0, 0
    for dominio in tiendas:
        urls = [r["url"] for r in con.execute(
            "SELECT url FROM precios WHERE tienda=?", (dominio,)).fetchall()]
        if not urls:
            continue
        total_revisadas += len(urls)
        rp = descubrir._parser_robots(dominio)
        excluidas = [u for u in urls if not rp.can_fetch("*", u)]
        if not excluidas:
            continue
        total_excluidas += len(excluidas)
        print("  %-20s %d de %d fichas violan su robots.txt"
              % (dominio, len(excluidas), len(urls)))
        if not probar:
            for u in excluidas:
                baseprecios.olvidar_url(con, u)
            con.commit()
    return total_revisadas, total_excluidas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--probar", action="store_true",
                   help="solo cuenta cuántas se sacarían, no borra nada")
    args = p.parse_args()

    con = baseprecios.abrir()
    revisadas, excluidas = depurar(con, probar=args.probar)
    print("\n%d revisadas, %d excluidas%s"
          % (revisadas, excluidas,
             " (--probar: no se borró nada)" if args.probar else ""))


if __name__ == "__main__":
    main()
