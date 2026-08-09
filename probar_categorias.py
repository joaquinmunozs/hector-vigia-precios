# -*- coding: utf-8 -*-
"""Prueba la cadena completa: clasificar -> evaluar -> a qué tópicos va.

    python probar_categorias.py

POR QUÉ ESTA PRUEBA EXISTE
------------------------------------------------------------------------------
La regla de los tópicos de categoría (8-ago-2026) tiene tres partes que se
pueden romper por separado y en silencio:

  1. `categorias.clasificar` decide si es electrónica u hogar
  2. `baseprecios.evaluar` baja el piso al 35% SOLO para esos
  3. `alertas.destinos` decide a qué tópicos va, y si se duplica

Un error en cualquiera de las tres no rompe nada visible: simplemente el
tópico queda vacío para siempre, o se llena de ruido. Por eso se prueba con
una base de verdad, no con mocks.

EL CASO QUE MÁS IMPORTA
------------------------------------------------------------------------------
Un -60% en un notebook tiene que salir DOS VECES: en Ofertas reales y en
Electrónicos. Ese duplicado es lo que se pidió explícitamente, y es lo más
fácil de romper sin darse cuenta al tocar el ruteo.
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

# Los tópicos se leen del entorno. Se fijan ANTES de importar alertas para
# que no queden cacheados con otro valor.
os.environ["VIGIA_TOPICO_ERRORES"] = "2"
os.environ["VIGIA_TOPICO_OFERTAS"] = "4"
os.environ["VIGIA_TOPICO_ELECTRONICOS"] = "36"
os.environ["VIGIA_TOPICO_HOGAR"] = "38"

_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["VIGIA_DB"] = _tmp.name

import alertas          # noqa: E402
import baseprecios      # noqa: E402

NOMBRE = {"2": "Errores", "4": "Ofertas", "36": "Electrónicos", "38": "Hogar"}

# (nombre, tienda, precio_base, precio_nuevo, tópicos esperados)
CASOS = [
    # ── Electrónica ──────────────────────────────────────────────────────
    ("Apple iPhone 16 Pro Max 256GB", "falabella.com",
     1_000_000, 600_000, ["36"]),                    # -40%: solo su categoría
    ("Notebook ASUS ROG RTX 4070", "spdigital.cl",
     1_000_000, 400_000, ["4", "36"]),               # -60%: DUPLICADO
    ("Smart TV LG 55'' 4K UHD", "paris.cl",
     1_000_000, 200_000, ["2"]),                     # -80%: error, sin duplicar

    # ── Hogar ────────────────────────────────────────────────────────────
    ("Sofá Seccional 3 Cuerpos Gris", "falabella.com",
     500_000, 300_000, ["38"]),                      # -40%: solo su categoría
    ("Refrigerador No Frost 400L", "abc.cl",
     500_000, 200_000, ["4", "38"]),                 # -60%: DUPLICADO

    # ── Sin categoría: el piso sigue siendo 50% ──────────────────────────
    ("Zapatillas Nike Air Max 90", "falabella.com",
     100_000, 60_000, []),                           # -40%: NO se avisa
    ("Zapatillas Nike Air Max 90", "falabella.com",
     100_000, 40_000, ["4"]),                        # -60%: solo ofertas
    ("Cien años de soledad", "antartica.cl",
     20_000, 12_000, []),                            # -40% en un libro: nada

    # ── Los que fallaron contra datos reales ─────────────────────────────
    ("Funda Con Teclado Para Samsung S9", "falabella.com",
     45_000, 27_000, []),                            # accesorio: nada
    ("Sosten Encaje Copa C", "falabella.com",
     20_000, 12_000, []),                            # no es cristalería
    ("Zapatillas de Running Galaxy 7", "falabella.com",
     54_990, 33_000, []),                            # no es un Samsung
    ("Cargador Rápido 45W", "falabella.com",
     8_990, 5_000, []),                              # bajo el piso de precio
]


def main():
    con = baseprecios.abrir()
    fallos = 0

    print("%-40s %8s %8s  %-22s %s" % (
        "PRODUCTO", "ANTES", "AHORA", "ESPERADO", "OBTENIDO"))
    print("-" * 104)

    for i, (nombre, tienda, base, nuevo, esperado) in enumerate(CASOS):
        # Cada caso usa su propia URL: así no se pisan entre ellos con la
        # ventana anti-repetición de 12 h.
        url = "https://%s/p/%d" % (tienda, i)
        baseprecios.fijar_base(con, url, base)

        det = baseprecios.evaluar(con, url, nuevo, nombre=nombre, tienda=tienda)
        obtenido = [d for d in alertas.destinos(det) if d] if det else []

        ok = sorted(obtenido) == sorted(esperado)
        fallos += not ok
        caida = 100 * (1 - nuevo / base)

        print("%s %-38s %8s %8s  %-22s %s" % (
            "  " if ok else "✗ ",
            nombre[:38],
            format(base, ",d").replace(",", "."),
            format(nuevo, ",d").replace(",", "."),
            " + ".join(NOMBRE[t] for t in esperado) or "(nada)",
            " + ".join(NOMBRE[t] for t in obtenido) or "(nada)"))

        if not ok:
            print("     caída %.0f%% · tipo=%s · categoría=%s" % (
                caida, det["tipo"] if det else "—",
                (det or {}).get("categoria") or "—"))

    con.close()
    os.unlink(_tmp.name)

    print("-" * 104)
    if fallos:
        print("✗ %d de %d casos fallaron" % (fallos, len(CASOS)))
    else:
        print("✅ %d casos, todos correctos" % len(CASOS))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
