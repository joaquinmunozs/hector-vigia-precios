# -*- coding: utf-8 -*-
"""Decide si un hallazgo se puede publicar en abierto. Ver INTEGRACIONES.md §5.

    python verificador.py --url https://...        # revisa una ficha suelta
    python verificador.py --recientes 12           # revisa las alertas de 12 h

POR QUÉ ESTO EXISTE, Y POR QUÉ ES LA PIEZA MÁS IMPORTANTE
------------------------------------------------------------------------------
Telegram y una publicación abierta no tienen el mismo costo de error.

El 11-ago-2026 Héctor alertó una manteca de cacao a $681 cuando valía $34.030:
había leído el precio POR GRAMO. En el grupo eso se arregla con un mensaje al
tiro. En Instagram queda público, la gente entra a comprar, no existe, y quedan
capturas. Y como nadie revisa a mano antes de publicar, la revisión tiene que
ser código.

La regla es incómoda a propósito: **ante la duda, no se publica.** Un carrusel
que no salió no le cuesta nada al negocio. Uno con un precio falso le cuesta la
credibilidad, que es literalmente lo único que Rat.IA vende.

LO QUE SE EXIGE
------------------------------------------------------------------------------
  1. El precio se relee EN VIVO de la ficha y tiene que seguir estando.
  2. Historial de verdad (5+ lecturas). Nada medido contra la foto del día uno.
  3. Nombre legible y foto disponible — sin eso no hay carrusel que armar.
  4. Que no sea de una categoría vetada.
  5. Que haya stock.
"""
import argparse
import re
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import alertas
import baseprecios
import vigia

# Cuánto puede haber subido el precio desde la alerta y seguir siendo
# publicable. No es una tolerancia de "más o menos": si BAJÓ más, se publica
# con el precio nuevo (el número sigue siendo cierto y todavía mejor). Lo que
# no se perdona es que haya SUBIDO, porque ahí el post estaría mintiendo.
#
# El 1% cubre el redondeo y los ajustes de un peso que hacen algunas tiendas,
# no un cambio de precio real.
TOLERANCIA_SUBIDA = 0.01

# Mínimo de lecturas para publicar. Es el mismo que usa `baseprecios.evaluar`
# para dejar de depender de la línea base: sin historial la referencia es la
# foto del día que se descubrió el producto, y si ese día estaba inflado el
# "descuento" nunca existió.
MIN_LECTURAS = baseprecios.MIN_OBSERVACIONES

# Caída mínima para que valga publicarlo, ya releído. Se recalcula porque el
# precio pudo cambiar entre la alerta y la publicación (el retraso es de horas,
# a propósito: primero se enteran los que pagan).
CAIDA_MINIMA = baseprecios.UMBRAL_CATEGORIA


# ── Categorías vetadas ───────────────────────────────────────────────────
#
# No es moralismo: son las categorías que hacen que Meta restrinja o baje una
# cuenta, y perder la cuenta es perder el canal completo. Además varias exigen
# autorización para publicitarse en Chile.
#
# Se compara contra el nombre del producto, que es lo único que se tiene. Es
# deliberadamente amplio: un falso positivo cuesta un post que no salió, un
# falso negativo puede costar la cuenta.
VETADAS = {
    "medicamentos": re.compile(
        r"\b(paracetamol|ibuprofeno|aspirina|amoxicilina|omeprazol|losart[áa]n|"
        r"metformina|sertralina|clonazepam|diazepam|tramadol|codeina|"
        # `comprimidos` va con número delante: a secas cazaba "pistola de aire
        # comprimido" y la clasificaba como medicamento.
        r"antibi[óo]tico|analg[ée]sico|jarabe|\d+\s*comprimidos?|c[áa]psulas?\s+de|"
        r"receta\s+m[ée]dica|mg\b.*\b(comprimido|c[áa]psula)|"
        r"anticonceptiv|insulina|inhalador|ventol[íi]n)\b", re.I),
    "alcohol": re.compile(
        r"\b(whisky|whiskey|vodka|ron\b|tequila|gin\b|ginebra|pisco|cerveza|"
        r"vino\s+(tinto|blanco|ros[ée]|espumante)|champagne|champa[ñn]a|"
        r"espumante|licor|aperitivo|vermouth|bourbon|coñac|cognac|brandy|"
        r"ale\b|lager|stout|ipa\b|malbec|cabernet|carm[ée]n[èe]re|sauvignon)\b",
        re.I),
    "armas": re.compile(
        # "pistola" sola cazaba herramientas: la de silicona, la de pintura, la
        # de calor. Son productos de ferretería perfectamente publicables y
        # construmart tiene el catálogo lleno de ellas.
        r"\b(pistola(?!\s+de\s+(silicona|pintura|calor|agua|impacto|engrase|"
        r"masilla|clavos|pegamento|aire\s+caliente|lavado))|"
        r"rev[óo]lver|escopeta|rifle|carabina|municion|munici[óo]n|"
        r"cartuchos?|balas?|airsoft|postones?|aire\s+comprimido|"
        r"cuchillo\s+(t[áa]ctico|de\s+combate|mariposa)|navaja\s+t[áa]ctica|"
        r"manopla|taser|gas\s+pimienta|ballesta)\b", re.I),
    "adulto": re.compile(
        r"\b(vibrador|dildo|consolador|juguete\s+sexual|sexshop|sex\s+shop|"
        r"lencer[íi]a\s+er[óo]tica|lubricante\s+[íi]ntimo|preservativos?|"
        r"cond[óo]n|condones|anillo\s+vibrador|plug\s+anal|masturbador)\b", re.I),
    "tabaco": re.compile(
        r"\b(cigarrillos?|tabaco|vapeador|vape\b|vaper\b|pod\s+desechable|"
        r"nicotina|puros?\s+habanos?|narguile|shisha|hookah)\b", re.I),
}


def categoria_vetada(nombre):
    """Devuelve el nombre de la categoría vetada, o None."""
    n = alertas._limpiar_nombre(nombre, "")
    for etiqueta, patron in VETADAS.items():
        if patron.search(n):
            return etiqueta
    return None


# ── El verificador ───────────────────────────────────────────────────────
def _lecturas(con, url):
    f = con.execute(
        "SELECT COUNT(*) n FROM precios WHERE url=? AND precio>0", (url,)).fetchone()
    return f["n"] if f else 0


def _imagen(con, url):
    """La foto más reciente que se haya visto de este producto."""
    f = con.execute(
        "SELECT imagen FROM precios WHERE url=? AND imagen IS NOT NULL "
        "AND imagen <> '' ORDER BY visto_en DESC LIMIT 1", (url,)).fetchone()
    return f["imagen"] if f else None


def verificar(con, det, leer=None):
    """¿Se puede publicar? Devuelve (bool, motivo, datos_confirmados).

    `leer` se inyecta para poder probar sin red. Por defecto usa el mismo lector
    que la barrida, así que hereda los adaptadores por tienda y el TLS imitado:
    duplicar esa lógica acá sería garantizar que un día queden en desacuerdo.
    """
    leer = leer or vigia.leer
    url = det["url"]
    tienda = det.get("tienda") or ""
    nombre = det.get("nombre") or ""

    # Lo barato primero: todo lo que se responde con la base ya en memoria va
    # antes de gastar una petición contra la tienda.
    vetada = categoria_vetada(nombre)
    if vetada:
        return False, "categoría vetada (%s)" % vetada, None

    limpio = alertas._limpiar_nombre(nombre, url)
    # Un nombre de menos de 10 caracteres, o que quedó como el slug de la URL,
    # da un carrusel que no se entiende. `_limpiar_nombre` inventa uno desde la
    # URL cuando falta: sirve para Telegram, no para una pieza pública.
    if not nombre or len(limpio) < 10:
        return False, "nombre ilegible o ausente", None

    lecturas = _lecturas(con, url)
    if lecturas < MIN_LECTURAS:
        return False, "historial insuficiente (%d de %d lecturas)" % (
            lecturas, MIN_LECTURAS), None

    foto = _imagen(con, url)
    if not foto:
        return False, "sin foto del producto", None

    # Y recién ahora la relectura en vivo, que es la parte caras.
    try:
        d = leer(tienda, url)
    except Exception as e:                       # noqa: BLE001
        return False, "no se pudo releer la ficha (%s)" % str(e)[:80], None

    if not d.get("hay_stock", True):
        return False, "sin stock", None

    vivo = int(d["precio"])
    alertado = int(det["precio"])
    if vivo > alertado * (1 + TOLERANCIA_SUBIDA):
        return False, "la tienda ya corrigió el precio (%s → %s)" % (
            alertas._plata(alertado), alertas._plata(vivo)), None

    referencia = int(det["referencia"])
    caida = 1 - (vivo / referencia) if referencia > 0 else 0
    if caida < CAIDA_MINIMA:
        return False, "la caída ya no alcanza (%.0f%%)" % (caida * 100), None

    # Se devuelve el precio EN VIVO, no el de la alerta. Si bajó todavía más
    # entre el aviso y la publicación, el número correcto es el de ahora — y
    # publicar el viejo sería publicar un dato falso, que es justo lo que este
    # módulo existe para evitar.
    confirmado = dict(det)
    confirmado.update({
        "precio": vivo,
        "caida": caida,
        "nombre": alertas._limpiar_nombre(d.get("nombre") or nombre, url),
        "imagen": foto,
        "lecturas": lecturas,
        "verificado_en": int(time.time()),
    })
    return True, "ok", confirmado


# ── CLI ──────────────────────────────────────────────────────────────────
def _revisar_recientes(con, horas):
    desde = int(time.time() - horas * 3600)
    filas = con.execute("""
        SELECT a.url, a.tipo, a.precio, a.referencia, a.caida,
               (SELECT nombre FROM precios p WHERE p.url=a.url AND p.nombre<>''
                  ORDER BY p.visto_en DESC LIMIT 1) AS nombre,
               (SELECT tienda FROM precios p WHERE p.url=a.url LIMIT 1) AS tienda
        FROM alertas a WHERE a.avisado_en >= ?
        GROUP BY a.url ORDER BY a.caida DESC
    """, (desde,)).fetchall()

    if not filas:
        print("Sin alertas en las últimas %d h." % horas)
        return 0

    print("Revisando %d alerta(s) de las últimas %d h:\n" % (len(filas), horas))
    aprobados = 0
    for f in filas:
        det = dict(f)
        ok, motivo, conf = verificar(con, det)
        marca = "PUBLICA " if ok else "RECHAZA "
        print("%s %-16s %-42s %s" % (
            marca, det["tienda"] or "?", (det["nombre"] or "")[:42], motivo))
        if ok:
            aprobados += 1
            print("           %s → %s  (-%.0f%%, %d lecturas)" % (
                alertas._plata(conf["referencia"]), alertas._plata(conf["precio"]),
                conf["caida"] * 100, conf["lecturas"]))
    print("\naprobados: %d de %d" % (aprobados, len(filas)))
    return aprobados


def main():
    p = argparse.ArgumentParser(description="¿Se puede publicar este hallazgo?")
    p.add_argument("--recientes", type=int, metavar="HORAS",
                   help="revisa las alertas de las últimas N horas")
    p.add_argument("--url", help="revisa una ficha suelta (relectura en vivo)")
    p.add_argument("--tienda", help="tienda de --url (si no, se saca del host)")
    args = p.parse_args()

    if args.url:
        tienda = args.tienda or args.url.split("/")[2].replace("www.", "")
        d = vigia.leer(tienda, args.url)
        print("%-16s %s" % (tienda, args.url))
        print("  precio : %s" % alertas._plata(d["precio"]))
        print("  nombre : %s" % alertas._limpiar_nombre(d.get("nombre"), args.url))
        print("  stock  : %s" % ("sí" if d.get("hay_stock", True) else "NO"))
        print("  imagen : %s" % (d.get("imagen") or "(ninguna)"))
        vetada = categoria_vetada(d.get("nombre") or "")
        print("  vetada : %s" % (vetada or "no"))
        return 0

    con = baseprecios.abrir()
    _revisar_recientes(con, args.recientes or 24)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
