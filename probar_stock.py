# -*- coding: utf-8 -*-
"""Verifica que un producto SIN STOCK nunca dispare una alerta.

POR QUÉ ES CRÍTICO
------------------------------------------------------------------------------
Un producto agotado suele conservar su último precio publicado, o mostrar uno
raro mientras la tienda lo da de baja. Si eso gatilla una alerta, el
suscriptor corre, no puede comprar, y silencia el canal. Un aviso inútil hace
más daño que diez avisos que no se mandaron.

La regla: sin stock NO se avisa, pase lo que pase con el precio. El precio sí
se guarda para el historial, y cuando el producto reponga stock vuelve a
evaluarse con las reglas normales (50% oferta / 70% error).
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

os.environ["VIGIA_DB"] = os.path.join(tempfile.mkdtemp(), "stock.db")

import baseprecios


def simular(con, url, precio, hay_stock):
    """Repite la lógica de vigia/vigilante: evaluar solo si hay stock."""
    det = baseprecios.evaluar(con, url, precio) if hay_stock else None
    baseprecios.guardar(con, "t", url, "Producto", precio)
    if not baseprecios._base_de(con, url):
        baseprecios.fijar_base(con, url, precio, "inicial")
    con.commit()
    return det


if __name__ == "__main__":
    con = baseprecios.abrir()

    print("Producto que vale $100.000 y se desploma a $10.000 (-90%)\n")

    U1 = "https://t.cl/sin-stock"
    simular(con, U1, 100_000, True)          # referencia
    det = simular(con, U1, 10_000, False)    # cae, pero AGOTADO
    print("  SIN stock  → %s" % ("🚨 AVISA (mal)" if det else "✅ no avisa"))

    U2 = "https://t.cl/con-stock"
    simular(con, U2, 100_000, True)
    det = simular(con, U2, 10_000, True)     # cae, CON stock
    print("  CON stock  → %s" % (
        "✅ avisa (%s, -%.0f%%)" % (det["tipo"], det["caida"] * 100)
        if det else "🚨 no avisa (mal)"))

    # Lo importante: el que estaba agotado debe poder avisar al reponer.
    print("\nEl agotado vuelve a tener stock, sigue a $10.000:")
    det = simular(con, U1, 10_000, True)
    print("  → %s" % ("✅ avisa ahora (%s, -%.0f%%)" % (det["tipo"], det["caida"] * 100)
                      if det else "❌ sigue sin avisar"))

    print("\n--- verificación del adaptador de Falabella ---")
    import adaptadores
    import descubrir

    def bajar(u, cab):
        return descubrir.bajar(u, tiempo=20, cabeceras=cab)

    # 144822972 dio OUT_OF_STOCK en las pruebas del 6-ago.
    d = adaptadores.falabella(
        "https://www.falabella.com/falabella-cl/product/144822972/x/144822972", bajar)
    if d:
        print("  agotado → lee precio $%s con hay_stock=%s  %s"
              % (format(d["precio"], ",d").replace(",", "."), d["hay_stock"],
                 "✅" if not d["hay_stock"] else "❌ debería ser False"))
    else:
        print("  (ese producto ya no responde; probar con otro id)")
