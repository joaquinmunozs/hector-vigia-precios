# -*- coding: utf-8 -*-
"""La lista caliente: pocos productos, vigilados cada pocos segundos.

EL PROBLEMA QUE RESUELVE
------------------------------------------------------------------------------
La barrida normal recorre 250.000 productos cada 4 horas. Eso significa que un
error de precio que dura 20 minutos SE NOS ESCAPA: cuando volvemos a mirar ese
producto, la tienda ya lo corrigió. Detectar tarde es no detectar.

Subir la frecuencia de TODO el catálogo es imposible — serían millones de
peticiones por segundo, que además de carísimo es indistinguible de un ataque
y nos bloquearían en minutos.

LA SOLUCIÓN: DOS VELOCIDADES
------------------------------------------------------------------------------
Es como operan los "cook groups" de zapatillas, que detectan un restock en
120-300 ms: NO vigilan millones de productos, vigilan una lista corta de
productos calientes a toda velocidad, y el resto ni lo miran.

  🔥 lista caliente  ~1.500 productos · cada ~5 seg  · detección < 10 seg
  🐢 barrida normal  250.000          · cada 4 h     · detección < 4 h

Un error en un iPhone de $1.200.000 se agarra en segundos. Un error en un paño
de cocina de $2.000 se agarra en horas — y está bien, porque nadie hace fila
por un error de $2.000.

QUÉ ENTRA EN LA LISTA CALIENTE
------------------------------------------------------------------------------
No es "los más caros" a secas. Un sillón de $900.000 es caro pero nadie corre
por él; un iPhone de $900.000 se agota en minutos.

REGLA DURA, DESDE EL 8-ago-2026: HAY QUE SER UN "IMÁN" PARA ENTRAR.
------------------------------------------------------------------------------
Antes, cualquier producto sobre $80.000 entraba con un puntaje base aunque no
calzara con ninguna categoría deseable (`factor = 1.0` de todos modos). Eso
dejaba pasar de todo — un libro caro, una enciclopedia, lo que fuera— con tal
de que costara lo suficiente. Y como antartica.cl (librería) tiene 56.549
productos en el catálogo, ese volumen se colaba solo: Héctor terminó
"vigilando" y avisando libros que a nadie le importan.

Se vio en vivo el 7-ago-2026: Joaquín reportó que las alertas reales que
mandó Héctor fueron de libros, no de los productos que valía la pena avisar.

La corrección es estructural, no un parche de palabras: si el nombre NO
calza con IMANES, el puntaje es CERO — no entra, sin importar el precio. La
lista de IMANES no es un bonus, es el portón de entrada.
"""
import re

# Marcas y categorías donde un error de precio se convierte en plata rápido.
# Salen de mirar qué se revende de verdad en Chile, no de un ranking genérico.
# ESTE ES EL PORTÓN DE ENTRADA — si nada de esto calza, el producto no entra
# a la lista caliente, sin importar cuánto cueste. Ver la nota del 8-ago-2026
# arriba: antes bastaba con ser caro, y eso dejaba pasar libros y similares.
IMANES = re.compile(
    r"\b("
    # Apple — el imán más fuerte que hay en reventa chilena
    r"iphone|ipad|macbook|apple\s*watch|airpods|imac|mac\s*mini|"
    # consolas y gaming
    r"playstation|ps[45]|xbox|nintendo|switch|steam\s*deck|"
    r"joystick|control(?:ador)?\s*(?:ps[45]|xbox)|"
    # celulares y tablets de marca (no solo Apple)
    r"samsung\s*galaxy|galaxy\s*(?:s2[0-9]|tab|z\s*fold|z\s*flip)|"
    r"xiaomi|redmi|poco\s*x|huawei|motorola\s*edge|"
    # componentes PC de alta gama — se revenden solos
    r"rtx|geforce|radeon\s*rx|ryzen|core\s*i[579]|"
    r"notebook|laptop|thinkpad|rog\b|legion\b|"
    # electro de marca fuerte
    r"dyson|gopro|dji|drone|roomba|"
    r"airfryer|freidora\s*de\s*aire|"
    # ropa y calzado deportivo de marca — reventa clásica
    r"nike|adidas|jordan|yeezy|new\s*balance|asics|puma|under\s*armour|"
    # relojes y audio de marca
    r"garmin|apple\s*watch|galaxy\s*watch|g-?shock|"
    r"sony\s*wh|bose|jbl\s*(?:flip|charge|boombox)|beats\b|"
    # coleccionables con reventa real (no libros comunes)
    r"lego|funko\s*pop|"
    # pantallas grandes
    r"televisor|smart\s*tv|oled|qled|monitor\s*gamer|"
    # movilidad
    r"bicicleta|scooter\s*el[ée]ctric|patineta\s*el[ée]ctric|"
    # herramientas profesionales de marca — se revenden rápido entre oficios
    r"bosch\s*professional|makita|dewalt|"
    # perfumes de nicho/diseñador, no genéricos
    r"chanel|dior|carolina\s*herrera|versace|calvin\s*klein"
    r")\b", re.I)

# Cosas caras que NO se revenden ni generan urgencia — una segunda barrera,
# por si un nombre calza con IMANES por casualidad (ej. una novela que
# mencione "Nike" en el título). El portón real es IMANES; esto es respaldo.
LASTRE = re.compile(
    r"\b(sof[áa]|sill[óo]n|comedor|dormitorio|colch[óo]n|closet|"
    r"refrigerador|lavadora|cocina\s*enc|horno\s*emp|campana|"
    r"piso\s*flotante|cer[áa]mic|porcelanato|puerta|ventana|"
    r"servicio|garant[íi]a\s*ext|instalaci[óo]n|"
    r"tratamiento|colegiatura|seguro|"
    r"libro|libros|enciclopedia|diccionario|atlas|novela|"
    r"cuento|c[óo]mic|manga|texto\s*escolar|cuaderno)", re.I)

PRECIO_MINIMO = 60_000         # bajo esto, el ahorro no justifica la urgencia

# OJO: no es el tope real de producción — `vigilante.cargar_lista` llama a
# `elegir()` con tope=100.000 y después cada tienda se recorta sola por su
# cupo de peticiones seguras (ver vigilante.py). Esto es solo el default
# para pruebas sueltas y para el `--tope` de la CLI.
TOPE_CALIENTE = 1_500

# Cuánto pesa la tasa de error REAL de una tienda (0.0 a ~0.05 en la
# práctica) frente al puntaje de marca+precio. 20 significa que una tienda
# con 5% de su catálogo con errores históricos duplica su puntaje — sube de
# rango, pero no aplasta a un iPhone recién agregado que aún no tiene
# historial. Tope 3.0 para que una tienda muy chica con 1-2 errores tempranos
# (aunque ya pasó el piso de `MIN_CATALOGO_PARA_TASA`) no se dispare sola.
PESO_HISTORIAL = 20.0
TOPE_BOOST_HISTORIAL = 3.0


def puntaje(nombre, precio, tienda="", tasa_error_tienda=0.0):
    """Cuánto merece este producto estar en la lista caliente. Mayor = más.

    Primero el portón: si no es un "imán" reconocido, el puntaje es CERO,
    sin importar el precio. Ser caro ya no basta — hay que ser algo que la
    gente de verdad quiera y revenda rápido.

    `tasa_error_tienda` es la probabilidad EMPÍRICA (no una marca ni un
    precio) de que esa tienda vuelva a tener un error — sale de
    `baseprecios.tasas_error_por_tienda`. Arranca en 0 para todas las
    tiendas (sin historial, no hay señal) y se corrige sola con cada error
    real: la tienda que de verdad se equivoca sube en el ranking aunque sus
    productos no sean más caros ni más "imán" que los de otra.
    """
    nombre = nombre or ""
    if not precio or precio < PRECIO_MINIMO:
        return 0.0
    if not IMANES.search(nombre):
        return 0.0
    if LASTRE.search(nombre):
        return 0.0

    import math
    # El precio en escala logarítmica desempata ENTRE imanes — un iPhone de
    # $1.200.000 pesa más que unas Nike de $70.000, pero ambos ya pasaron el
    # portón por ser deseables; el precio solo ordena, no decide la entrada.
    p = math.log10(precio)             # 60.000 -> 4.8 · 2.000.000 -> 6.3

    # La API de Falabella responde en 131 ms y su HTML en más de 1.000: cabe
    # mucho más Falabella en el mismo presupuesto de tiempo.
    factor = 1.4 if tienda == "falabella.com" else 1.0

    boost = 1.0 + min(tasa_error_tienda * PESO_HISTORIAL, TOPE_BOOST_HISTORIAL)

    return p * factor * boost


def elegir(filas, tope=TOPE_CALIENTE, tasas_error=None):
    """De todo el catálogo con precio conocido, los que entran a la caliente.

    `filas` son dicts o tuplas con (tienda, url, nombre, precio).
    `tasas_error` es el dict de `baseprecios.tasas_error_por_tienda` — si no
    se pasa, nadie recibe refuerzo por historial (equivalente a que todas las
    tiendas tengan 0% de errores conocidos todavía).
    """
    tasas_error = tasas_error or {}
    puntuados = []
    for f in filas:
        tienda, url, nombre, precio = (
            (f["tienda"], f["url"], f["nombre"], f["precio"])
            if isinstance(f, dict) or hasattr(f, "keys") else f)
        p = puntaje(nombre, precio, tienda, tasas_error.get(tienda, 0.0))
        if p > 0:
            puntuados.append((p, tienda, url, nombre, precio))

    puntuados.sort(reverse=True, key=lambda x: x[0])
    return [(t, u, n, pr) for _, t, u, n, pr in puntuados[:tope]]


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ejemplos = [
        ("falabella.com", "u1", "Apple iPhone 16 Pro Max 256GB", 1_299_990),
        ("falabella.com", "u2", "Sofá Seccional 3 Cuerpos Gris", 899_990),
        ("spdigital.cl", "u3", "Notebook ASUS ROG RTX 4070", 1_499_990),
        ("abc.cl", "u4", "Refrigerador No Frost 400L", 799_990),
        ("adidas.cl", "u5", "Zapatillas Adidas Samba OG", 89_990),
        ("jumbo.cl", "u6", "Detergente Ariel 3L", 12_990),
        ("ripley.cl", "u7", "PlayStation 5 Slim Digital", 549_990),
    ]
    print("%-46s %10s %8s" % ("PRODUCTO", "PRECIO", "PUNTAJE"))
    print("-" * 68)
    for t, u, n, p in ejemplos:
        print("%-46s %10s %8.2f" % (n[:46], format(p, ",d").replace(",", "."),
                                    puntaje(n, p, t)))
    print("\nEntran a la caliente (tope 4):")
    for t, u, n, p in elegir(ejemplos, tope=4):
        print("   %-44s %s" % (n[:44], format(p, ",d").replace(",", ".")))
