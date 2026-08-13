# -*- coding: utf-8 -*-
"""El precio por unidad de medida NO es el precio. No toca la red ni la base.

    python probar_pum.py

QUÉ SE ROMPIÓ (13-ago-2026)
------------------------------------------------------------------------------
La ley chilena obliga a mostrar, junto al precio, cuánto sale la unidad de
medida: "$34.030  ($681 el gr". Falabella lo publica en su API dentro de cada
bloque de precio, en una llave `pum`:

    "price": ["34.030"],  "pum": {"label": "gr", "price": ["681"]}

`adaptadores.falabella` buscaba `"price": ["..."]` con una regex sobre el JSON
ENTERO y se quedaba con el `min()`. El pum siempre es más chico que el precio
—esa es su definición— así que ganaba siempre, y el vigía leía $681 donde el
producto vale $34.030.

Efecto medido sobre la base de producción del 13-ago: de los 11 avisos de
"🚨 ERROR DE PRECIO" emitidos, NUEVE eran esto. Todos con la misma firma: el
precio anunciado era el del gramo, la pieza o la toalla suelta.

    Manteca De Cacao 500g   $34.030 -> se avisó $681    ($681 los 10 gr)
    Matcha Puro 400g        $27.590 -> se avisó $690    ($690 los 10 gr)
    Vajilla de 16 Piezas    $12.990 -> se avisó $812    ($812 la pieza)
    Pack 6 Toallas          $28.990 -> se avisó $4.832  ($4.832 la toalla)

No es un caso de borde: le pasa a todo lo que se vende por peso, volumen o
en pack, que es media tienda de supermercado y hogar.

El fixture es la respuesta REAL de la API para la manteca de cacao
(productId 143907533), recortada a lo que importa.
"""
import sys

import adaptadores

sys.stdout.reconfigure(encoding="utf-8")


# Respuesta real de la API, capturada el 13-ago-2026.
MANTECA = {
    "data": {
        "displayName": "Manteca De Cacao Pura 500g 100% Natural Bio V",
        "variants": [{
            "prices": [
                {"type": "eventPrice", "crossed": False, "price": ["34.030"],
                 "pum": {"label": "gr", "type": "pum", "price": ["681"]}},
                {"type": "normalPrice", "crossed": True, "price": ["36.990"]},
            ],
        }],
    },
}

# Un producto de una sola unidad: el pum ES el precio. No debe estorbar.
CORTINA = {
    "data": {
        "displayName": "Cortina Roller Duo 075X140 Gris",
        "variants": [{
            "prices": [
                {"type": "eventPrice", "price": ["10.990"],
                 "pum": {"label": "un", "type": "pum", "price": ["10.990"]}},
                {"type": "normalPrice", "crossed": True, "price": ["36.990"]},
            ],
        }],
    },
}

# Sin pum en ninguna parte: el comportamiento de siempre, la oferta más barata.
SIN_PUM = {
    "data": {
        "displayName": "Notebook",
        "variants": [{
            "prices": [
                {"type": "eventPrice", "price": ["499.990"]},
                {"type": "normalPrice", "crossed": True, "price": ["899.990"]},
            ],
        }],
    },
}

# El pum escondido en otro lado del árbol, sin la llave `pum` de por medio.
# Se defiende por `"type": "pum"`, no sólo por el nombre de la llave: si
# Falabella lo mueve de sitio, el aviso falso vuelve solo.
PUM_MUDADO = {
    "data": {
        "displayName": "Arroz 5 kg",
        "variants": [{
            "prices": [{"type": "eventPrice", "price": ["7.990"]}],
            "medidas": {"unidad": {"type": "pum", "price": ["1.598"]}},
        }],
    },
}

CASOS = (
    ("manteca 500g (el pum es $681 el gr)", MANTECA, 34_030),
    ("cortina, unidad suelta (pum == precio)", CORTINA, 10_990),
    ("notebook, sin pum", SIN_PUM, 499_990),
    ("arroz con el pum en otra rama", PUM_MUDADO, 7_990),
)


def main():
    fallos = 0
    for etiqueta, datos, esperado in CASOS:
        import json
        crudo = json.dumps(datos, ensure_ascii=False)
        d = adaptadores.falabella(
            "https://www.falabella.com/falabella-cl/product/143907533/x/1",
            lambda _url, _cab=None, _c=crudo: _c)
        leido = d and d.get("precio")
        ok = leido == esperado
        fallos += not ok
        print("%s %-42s leyó %s, esperado %s"
              % ("✅" if ok else "❌", etiqueta,
                 format(leido, ",d").replace(",", ".") if leido else "nada",
                 format(esperado, ",d").replace(",", ".")))

    print()
    if fallos:
        print("❌ %d de %d fallan: el pum se sigue colando como precio"
              % (fallos, len(CASOS)))
    else:
        print("✅ %d de %d: el pum ya no se confunde con el precio"
              % (len(CASOS), len(CASOS)))
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
