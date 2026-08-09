# -*- coding: utf-8 -*-
"""A qué tópico de categoría pertenece un producto: Electrónicos u Hogar.

PARA QUÉ EXISTE
------------------------------------------------------------------------------
El canal tiene dos tópicos por categoría, aparte de los dos de siempre:

    🚨 Errores de precio   caída 70%-99%   (cualquier producto)
    🏷️ Ofertas reales      caída 50%-69%   (cualquier producto)
    📱 Electrónicos        caída 35%-69%   (solo si es electrónica)
    🏠 Hogar               caída 35%-69%   (solo si es de hogar)

O sea: los tópicos de categoría bajan el piso del 50% al 35%, pero SOLO para
lo que calce con su categoría. Un 40% en unas zapatillas no se avisa en
ninguna parte; un 40% en un notebook sí, en Electrónicos.

POR NOMBRE DE PRODUCTO, NO POR TIENDA
------------------------------------------------------------------------------
La tentación es usar el `rubro` que ya trae `tiendas.py`. NO SIRVE, y el
motivo es de datos, no de estilo: Falabella, Paris, Ripley, Hites y ABC están
marcadas como `retail` porque venden de todo. Filtrar por rubro dejaría los
dos tópicos nuevos sin nada de Falabella — que son 1,5 millones de fichas, el
catálogo más grande que tenemos.

Así que se clasifica por el NOMBRE del producto, con el mismo enfoque que ya
funciona en `caliente.py` (IMANES/LASTRE). El rubro de la tienda queda como
desempate para los nombres ambiguos: si spdigital.cl vende algo que no calzó
con ningún patrón, casi seguro es electrónica igual.

DÓNDE VA LA LÍNEA BLANCA (decisión, no accidente)
------------------------------------------------------------------------------
Refrigeradores, lavadoras y secadoras van a HOGAR, no a Electrónicos. Son
aparatos con enchufe, sí, pero quien entra a "Electrónicos" busca celulares,
notebooks, TV y consolas — no un lavavajillas. Si algún día se prefiere al
revés, se mueven los patrones de LINEA_BLANCA de un lado al otro y listo.

LO QUE ENSEÑARON LOS NOMBRES REALES (8-ago-2026)
------------------------------------------------------------------------------
Se probó contra los 520 productos con nombre y precio que había en la base, y
los ejemplos inventados escondían tres fallos que los datos de verdad
mostraron de inmediato:

  · "Sosten Encaje Copa C"      -> caía en Hogar por `copa` (la de vino)
  · "8 Bitbox Juego De Mesa"    -> caía en Hogar por `mesa` (la de comedor)
  · "Funda Con Teclado", "Carcasa Silicona", "Lámina De Vidrio Para iPad"
                                -> caían en Electrónicos siendo accesorios
                                   de $5.000, que es justo el ruido que
                                   hace que alguien silencie el canal

Por eso hay un RUIDO que se evalúa ANTES que todo lo demás, los patrones
demasiado sueltos se ataron a su contexto (`copa de vino`, `mesa de centro`),
y hay un piso de precio: un 40% sobre $3.000 no es un hallazgo.
"""
import re

ELECTRONICOS = "electronicos"
HOGAR = "hogar"

# Bajo esto no se clasifica en ningún tópico de categoría, por mucho que
# calce. Los tópicos de categoría bajan el piso al 35% de descuento, así que
# sin un piso de precio se llenarían de rebajas de tres lucas.
PRECIO_MINIMO = 100_000

# Se evalúa PRIMERO y descarta. Son cosas que calzan con los patrones de
# abajo pero no son el producto: accesorios baratos, repuestos, consumibles.
# Un 40% en una carcasa de celular no le sirve a nadie.
RUIDO = re.compile(
    r"\b("
    r"funda|carcasa|case\b|cover\b|mica\b|"
    r"l[áa]mina\s*(?:de\s*)?(?:vidrio|hidrogel)|protector\s*de\s*pantalla|"
    r"vidrio\s*templado|"
    r"cable\b|adaptador|conector|enchufe|alargador|zapatilla\s*el[ée]ctrica|"
    r"soporte\s*(?:para|de)\s*(?:tv|celular|notebook|monitor)|"
    r"repuesto|filtro\s*de\s*repuesto|bolsa\s*de\s*aspiradora|"
    r"cartucho|t[óo]ner|tinta\b|resma|"
    r"pila[s]?\b|bater[íi]a\s*aa|"
    r"juego\s*de\s*mesa|juego\s*de\s*cartas|rompecabezas|puzzle|"
    r"tenis\s*de\s*mesa|ping\s*pong"
    r")\b", re.I)

# Electrónica de consumo: lo que alguien busca cuando entra a "Electrónicos".
RE_ELECTRONICOS = re.compile(
    r"\b("
    # celulares y tablets. `galaxy` va ATADO a Samsung: suelto atrapaba
    # "Zapatillas de Running Galaxy 7", que es una adidas.
    r"iphone|ipad|celular|smartphone|tablet|"
    r"samsung\s*galaxy|galaxy\s*(?:s\d|a\d{2}|m\d{2}|note|tab|"
    r"z\s*(?:fold|flip)|watch|buds)|"
    r"xiaomi|redmi|poco\s*[xmf]|"
    r"huawei|motorola|oppo|honor\b|nokia|"
    # computación
    r"notebook|laptop|macbook|imac|mac\s*mini|computador|"
    r"pc\s*(?:gamer|escritorio|all\s*in\s*one)|thinkpad|chromebook|"
    r"monitor|teclado|mouse\b|impresora|esc[áa]ner|"
    r"disco\s*(?:duro|s[óo]lido)|ssd\b|nvme|memoria\s*ram|pendrive|"
    r"tarjeta\s*(?:de\s*)?(?:video|gr[áa]fica)|rtx|geforce|radeon|"
    r"procesador|ryzen|core\s*i[3579]|placa\s*madre|"
    # TV, audio y video
    r"televisor|smart\s*tv|pantalla\s*led|oled|qled|proyector|"
    r"audifonos|aud[íi]fonos|parlante|soundbar|barra\s*de\s*sonido|"
    r"home\s*theater|subwoofer|amplificador|"
    r"airpods|jbl\b|bose\b|sony\s*wh|beats\b|marshall\b|"
    # gaming
    r"playstation|ps[45]\b|xbox|nintendo|switch\b|steam\s*deck|"
    r"consola|joystick|control\s*(?:inal[áa]mbrico|dualsense|dualshock)|"
    # foto, drones y wearables
    r"c[áa]mara|gopro|dji\b|drone|dron\b|"
    r"smartwatch|smart\s*band|apple\s*watch|galaxy\s*watch|garmin|fitbit|"
    # redes y energía
    r"router|repetidor\s*wifi|access\s*point|"
    r"power\s*bank|bater[íi]a\s*externa|cargador\s*(?:r[áa]pido|inal)"
    r")\b", re.I)

# Línea blanca: enchufe sí, pero es hogar. Ver la nota de arriba.
LINEA_BLANCA = (
    r"refrigerador|freezer|congelador|lavadora|secadora|lavavajillas|"
    r"lava\s*seca|cocina\s*(?:a\s*gas|el[ée]ctrica|encimera)|"
    r"horno\s*(?:el[ée]ctrico|empotrable)|microondas|campana\s*extractora|"
    r"calefont|termo\s*el[ée]ctrico|estufa|aire\s*acondicionado|"
    r"hervidor|licuadora|batidora|cafetera|tostador|airfryer|"
    r"freidora\s*de\s*aire|juguera|sandwichera|"
    r"aspiradora|roomba|enceradora|"
)

RE_HOGAR = re.compile(
    r"\b(" + LINEA_BLANCA +
    # muebles
    r"sof[áa]|sill[óo]n|sillon|poltrona|butaca|silla\b|mesa\b|"
    r"comedor|velador|c[óo]moda|closet|clóset|repisa|estante|librero|"
    r"escritorio\b|mueble|ropero|cajonera|"
    # dormitorio
    r"colch[óo]n|somier|cama\s*(?:americana|box|nido)|box\s*spring|"
    r"almohada|plumón|plum[óo]n|edred[óo]n|s[áa]bana|cubrecama|"
    r"funda\s*de\s*(?:almohada|colch[óo]n)|"
    # cocina y mesa. `copa` y `vaso` van ATADOS a su contexto: sueltos
    # atrapaban "Sosten Encaje Copa C", que es ropa interior, no cristalería.
    r"olla\b|sart[ée]n|bater[íi]a\s*de\s*cocina|"
    r"vajilla|plato\s*(?:hondo|llano)|cubiertos|"
    r"cop[ao]s?\s*(?:de\s*)?(?:vino|champ[áa]n|agua|cristal)|"
    r"vasos?\s*(?:de\s*)?(?:vidrio|cristal|whisky)|"
    r"set\s*de\s*(?:ollas|cuchillos|vasos|copas)|"
    # baño, orden y decoración
    r"toalla|cortina|alfombra|espejo\b|l[áa]mpara|"
    r"organizador|canasto|contenedor\s*pl[áa]stico|"
    r"cuadro\s*decorativo|florero|maceter[oa]|"
    # exterior y jardín
    r"parrilla|quincho|toldo|piscina\b|"
    # herramientas de casa
    r"taladro|atornillador|esmeril|sierra\s*(?:circular|caladora)|"
    r"herramienta|caja\s*de\s*herramientas"
    r")\b", re.I)

# Desempate: si el nombre no calza con nada, el rubro de la tienda decide.
# Solo para tiendas de UNA sola categoría — las multirubro (`retail`) no
# aparecen acá a propósito, porque de ellas no se puede inferir nada.
RUBRO_A_CATEGORIA = {
    "electro": ELECTRONICOS,
    "tecnologia": ELECTRONICOS,
    "hogar": HOGAR,
}

_rubros = None


def _rubro_de(tienda):
    """El rubro que `tiendas.py` le asigna a ese dominio. Se cachea."""
    global _rubros
    if _rubros is None:
        import tiendas
        _rubros = {t["dominio"]: t["rubro"] for t in tiendas.TIENDAS}
    return _rubros.get(tienda or "")


def _limpio(nombre):
    """Los nombres llegan con entidades HTML, a veces dobles.

    Antártica guarda "Una&#x20;Novela&#x20;En&#x20;La&#x20;Crisis". Sin
    deshacer eso, ningún patrón con `\\b` calza como debería, porque las
    palabras quedan pegadas por basura en vez de por espacios.
    """
    n = (nombre or "").replace("&amp;#x20;", " ").replace("&#x20;", " ")
    n = n.replace("&amp;", "&").replace("&#x28;", "(").replace("&#x29;", ")")
    return " ".join(n.split())


def clasificar(nombre, tienda=None, precio=None):
    """Devuelve ELECTRONICOS, HOGAR o None.

    El orden importa:
      1. El piso de precio — bajo PRECIO_MINIMO no entra nada.
      2. RUIDO — accesorios y consumibles se descartan aunque calcen.
      3. El nombre manda.
      4. El rubro de la tienda, solo si el nombre no dijo nada, y solo para
         tiendas de una sola categoría.
    """
    if precio is not None and precio < PRECIO_MINIMO:
        return None

    n = _limpio(nombre)
    if n:
        if RUIDO.search(n):
            return None
        # Si calzan ambos (ej. "smart tv para living"), manda Electrónicos:
        # es la categoría más específica y la que de verdad mueve a la gente.
        if RE_ELECTRONICOS.search(n):
            return ELECTRONICOS
        if RE_HOGAR.search(n):
            return HOGAR
    return RUBRO_A_CATEGORIA.get(_rubro_de(tienda))


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    # Los casos con ✗ son los que fallaron contra datos reales el 8-ago-2026
    # y ahora tienen que dar "—". Están acá para que no vuelvan a colarse.
    ejemplos = [
        ("falabella.com", "Apple iPhone 16 Pro Max 256GB", 1_299_990),
        ("falabella.com", "Refrigerador No Frost 400L Samsung", 549_990),
        ("falabella.com", "Sofá Seccional 3 Cuerpos Gris", 899_990),
        ("falabella.com", "Zapatillas Nike Air Max 90", 89_990),
        ("paris.cl", "Smart TV LG 55'' 4K UHD", 399_990),
        ("paris.cl", "Perfume Carolina Herrera Good Girl 80ml", 79_990),
        ("spdigital.cl", "Cooler Master Hyper 212", 34_990),
        ("easy.cl", "Taladro Percutor Bosch 750W", 89_990),
        ("easy.cl", "Producto sin nombre reconocible", 45_000),
        ("antartica.cl", "Cien años de soledad", 18_990),
        ("jumbo.cl", "Detergente Ariel 3L", 12_990),
        # ✗ los que fallaban antes
        ("falabella.com", "Sosten Encaje Copa C", 19_990),
        ("antartica.cl", "8 Bitbox Juego De Mesa", 24_990),
        ("falabella.com", "Funda Con Teclado Negro Para Samsung S9", 45_990),
        ("falabella.com", "Lámina De Vidrio Templado Para iPad Air 13", 9_990),
        ("antartica.cl", "0000&#x20;Una&#x20;Novela&#x20;En&#x20;La&#x20;Crisis", 15_990),
        # piso de precio
        ("falabella.com", "Cargador Rápido 45W", 8_990),
    ]
    print("%-14s %-44s %10s  %s" % ("TIENDA", "PRODUCTO", "PRECIO", "CATEGORÍA"))
    print("-" * 84)
    for t, n, p in ejemplos:
        print("%-14s %-44s %10s  %s" % (
            t, _limpio(n)[:44], format(p, ",d").replace(",", "."),
            clasificar(n, t, p) or "—"))
