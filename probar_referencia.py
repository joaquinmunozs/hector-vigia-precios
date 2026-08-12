# -*- coding: utf-8 -*-
"""Verifica contra qué precio se anuncia la caída. Base temporal, no toca nada.

    python probar_referencia.py

Reproduce el escenario que la Directiva Omnibus existe para castigar: un
producto que vale $100.000 y que se infla a $150.000 antes de "rebajarlo".

OJO CON `VIGIA_DB`: `baseprecios` la lee AL IMPORTARSE, no al abrir. Por eso se
define arriba de todo, antes del import. La primera versión de este script la
ponía dentro de `main()` y terminó escribiendo filas de prueba en la base real
—se borraron a mano—, que es justo lo que un script de prueba no debe hacer.
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")

RUTA = os.path.join(tempfile.gettempdir(), "probar_referencia.db")
if os.path.exists(RUTA):
    os.remove(RUTA)
os.environ["VIGIA_DB"] = RUTA           # ← antes del import, a propósito

import baseprecios                       # noqa: E402

assert baseprecios.RUTA == RUTA, (
    "baseprecios apunta a %s y no a la base de prueba" % baseprecios.RUTA)

DIA = 86400


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def _sembrar(con, url, ahora, dias_inflado):
    """30 días de historia con `dias_inflado` a $150.000 y el resto a $100.000.

    El reparto importa: la mediana está PONDERADA POR TIEMPO, así que sólo se
    va a $150.000 si el precio inflado ocupó más de la mitad de la ventana.
    Con 5 días inflados la mediana sigue dando $100.000 y el error no aparece
    — es lo que pasó en el primer intento de esta prueba.
    """
    for t in ("precios", "alertas"):
        con.execute("DELETE FROM %s WHERE url=?" % t, (url,))
    normal = 30 - dias_inflado
    con.execute(
        "INSERT INTO precios (tienda, url, nombre, precio, visto_en, visto_hasta)"
        " VALUES (?,?,?,?,?,?)",
        ("tienda.cl", url, "Producto de prueba", 100_000,
         ahora - 30 * DIA, ahora - dias_inflado * DIA))
    con.execute(
        "INSERT INTO precios (tienda, url, nombre, precio, visto_en, visto_hasta)"
        " VALUES (?,?,?,?,?,?)",
        ("tienda.cl", url, "Producto de prueba", 150_000,
         ahora - dias_inflado * DIA, ahora - 60))
    con.commit()
    return normal


def _evaluar(con, url, precio, ahora):
    con.execute("DELETE FROM alertas WHERE url=?", (url,))
    con.commit()
    return baseprecios.evaluar(con, url, precio, ahora=ahora,
                               nombre="Producto de prueba", tienda="tienda.cl")


def main():
    con = baseprecios.abrir()
    ahora = int(time.time())
    url = "https://tienda.cl/producto-de-prueba"

    # 20 de los 30 días inflado: así la mediana ponderada SÍ se va a $150.000
    # y se puede ver la diferencia entre las dos referencias.
    _sembrar(con, url, ahora, dias_inflado=20)

    mediana, _ = baseprecios._mediana_ponderada(con, url, ahora=ahora)
    minimo = min(p for p, _ in baseprecios.historial(con, url, ahora=ahora))
    print("Historia: 10 días a $100.000 y 20 días inflado a $150.000\n")
    print("  mediana ponderada : %s   ← lo que se usaba antes" % _plata(mediana))
    print("  mínimo de 30 días : %s   ← lo que manda Omnibus\n" % _plata(minimo))

    if mediana <= minimo:
        print("❌ el escenario no reproduce el problema: la mediana no quedó "
              "por encima del mínimo")
        return 1

    fallos = 0
    casos = [
        (110_000, False, "está sobre el mínimo real: no es un hallazgo"),
        (38_000,  True,  "oferta real"),
        (25_000,  True,  "error de precio de verdad"),
    ]

    for precio, debe_avisar, nota in casos:
        det = _evaluar(con, url, precio, ahora)
        print("Precio de hoy %s — %s" % (_plata(precio), nota))
        if not det:
            print("  → no avisa")
            if debe_avisar:
                print("  ❌ debería haber avisado")
                fallos += 1
            print()
            continue
        if not debe_avisar:
            print("  ❌ no debería haber avisado (%.1f%%)" % (det["caida"] * 100))
            fallos += 1
            print()
            continue

        contra_mediana = (1 - precio / mediana) * 100
        print("  → %s · %.1f%% contra %s"
              % (det["tipo"].upper(), det["caida"] * 100,
                 _plata(det["referencia"])))
        print("     antes habría dicho %.1f%% (contra la mediana)"
              % contra_mediana)
        if det["referencia"] != minimo:
            print("  ❌ la referencia no es el mínimo de 30 días")
            fallos += 1
        if det.get("habitual") != int(mediana):
            print("  ❌ no se está pasando el precio habitual para el mensaje")
            fallos += 1
        print()

    # EL CASO QUE MOTIVÓ EL CAMBIO: $38.000 es una oferta real (-62%), pero
    # contra la mediana inflada daba -74,7% y cruzaba UMBRAL_ERROR. Un aviso
    # así ensucia el tópico donde el producto se juega la credibilidad.
    det = _evaluar(con, url, 38_000, ahora)
    if not det:
        print("❌ el caso de control no avisa")
        fallos += 1
    elif det["tipo"] == baseprecios.ERROR:
        print("❌ $38.000 SIGUE clasificando como error de precio")
        fallos += 1
    else:
        print("✅ $38.000 clasifica como '%s', no como error de precio"
              % det["tipo"])

    con.close()
    os.remove(RUTA)
    print("\n%s" % ("TODO BIEN" if not fallos else "%d FALLO(S)" % fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
