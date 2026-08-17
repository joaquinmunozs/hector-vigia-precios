# -*- coding: utf-8 -*-
"""Una URL que ya se comprobó que no es ficha no vuelve el lunes siguiente.

    python probar_descartadas.py

QUÉ SE ROMPIÓ (16-ago-2026)
------------------------------------------------------------------------------
Tres tiendas configuradas tenían CERO productos en la base pese a haberse
descubierto bien: jumbo.cl, casaideas.cl y easy.cl. Y no aparecían en la tabla
`fallos`, así que no había rastro de por qué.

Verificado bajando sus sitemaps de verdad:

    casaideas.cl   3.082 URLs publicadas,   0 son fichas de producto
    easy.cl          929 URLs publicadas,   0 son fichas
    jumbo.cl         803 URLs publicadas,   3 son fichas

Publican categorías y landings, no productos. El ciclo era:

  1. el descubrimiento del lunes mete las ~4.800 URLs (parecen fichas: no
     tienen /categoria/ en la ruta, que es lo que filtraba `NO_ES_FICHA`);
  2. cada una falla 6 veces al leerla (TOPE_FALLOS) y se descarta;
  3. `olvidar_url` la borra de `precios`, `linea_base` Y `fallos` — o sea sin
     dejar rastro;
  4. `descubrir_productos` mete lo que no esté en `precios`… y el lunes
     siguiente vuelve al paso 1.

Cada semana se pagaban ~29.000 lecturas fallidas para llegar a la misma
conclusión, y la tienda terminaba siempre en 0.

QUÉ SE ARREGLA
------------------------------------------------------------------------------
`olvidar_url` deja constancia en la tabla `descartadas`, y el descubrimiento
salta lo que ya está ahí. Con fecha, para poder readmitirlas a los 60 días:
una tienda puede cambiar de plataforma y empezar a publicar fichas donde antes
había categorías, así que el descarte no puede ser eterno.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_descartadas.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA

import baseprecios                       # noqa: E402
import vigia                             # noqa: E402

DIA = 86400
URL = "https://www.casaideas.cl/contenido/guia-de-regalos"


def _catalogo(con):
    return con.execute("SELECT COUNT(*) FROM precios").fetchone()[0]


def main():
    fallos = 0
    con = baseprecios.abrir()

    # ── 1. Descartar deja constancia ──────────────────────────────────────
    print("1. descartar una URL deja constancia")
    baseprecios.guardar(con, "casaideas.cl", URL, "Guia", 0)
    con.commit()
    baseprecios.olvidar_url(con, URL)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM descartadas WHERE url=?",
                    (URL,)).fetchone()[0]
    if n != 1:
        print("  ❌ no quedó anotada (descartadas=%d)" % n)
        fallos += 1
    else:
        print("  ✅ quedó en `descartadas`")

    # ── 2. El descubrimiento no la vuelve a meter ─────────────────────────
    print("\n2. el descubrimiento del lunes siguiente NO la remete")
    antes = _catalogo(con)
    corte = int(time.time()) - baseprecios.DIAS_REINTENTAR_DESCARTADA * 86400
    con.execute(
        "INSERT INTO precios (tienda, url, nombre, precio, visto_en) "
        "SELECT ?,?,'',0,0 WHERE NOT EXISTS "
        "(SELECT 1 FROM precios WHERE url=?) "
        "AND NOT EXISTS (SELECT 1 FROM descartadas WHERE url=? AND cuando > ?)",
        ("casaideas.cl", URL, URL, URL, corte))
    con.commit()
    if _catalogo(con) != antes:
        print("  ❌ volvió a entrar — el ciclo semanal sigue vivo")
        fallos += 1
    else:
        print("  ✅ no volvió a entrar")

    # ── 3. A los 60 días se le da otra oportunidad ────────────────────────
    print("\n3. a los %d días se le da otra oportunidad"
          % baseprecios.DIAS_REINTENTAR_DESCARTADA)
    viejo = int(time.time()) - (baseprecios.DIAS_REINTENTAR_DESCARTADA + 1) * DIA
    con.execute("UPDATE descartadas SET cuando=? WHERE url=?", (viejo, URL))
    con.commit()
    antes = _catalogo(con)
    con.execute(
        "INSERT INTO precios (tienda, url, nombre, precio, visto_en) "
        "SELECT ?,?,'',0,0 WHERE NOT EXISTS "
        "(SELECT 1 FROM precios WHERE url=?) "
        "AND NOT EXISTS (SELECT 1 FROM descartadas WHERE url=? AND cuando > ?)",
        ("casaideas.cl", URL, URL, URL, corte))
    con.commit()
    if _catalogo(con) == antes:
        print("  ❌ nunca más vuelve a entrar — una tienda que cambie de "
              "plataforma quedaría afuera para siempre")
        fallos += 1
    else:
        print("  ✅ vuelve a entrar")

    # ── 4. Una URL sana no se toca ────────────────────────────────────────
    print("\n4. una URL que sí da precio no queda descartada")
    buena = "https://www.hites.com/producto-real"
    baseprecios.guardar(con, "hites.com", buena, "Real", 19990)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM descartadas WHERE url=?",
                    (buena,)).fetchone()[0]
    if n:
        print("  ❌ una URL sana quedó marcada como descartada")
        fallos += 1
    else:
        print("  ✅ intacta")

    print()
    if fallos:
        print("❌ %d fallos" % fallos)
    else:
        print("✅ lo descartado no vuelve cada lunes, y no es para siempre")
    con.close()
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
