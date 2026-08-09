# -*- coding: utf-8 -*-
"""Historial de precios, línea base y clasificación de hallazgos.

EL ACTIVO DEL NEGOCIO ES ESTA BASE, NO EL SCRAPER
------------------------------------------------------------------------------
La competencia muestra "Precio histórico" con 3-4 fechas en cada alerta. Eso no
sale de mirar el precio de hoy: sale de haberlos guardado durante meses. Ese
historial es lo que permite decir "esto NUNCA estuvo tan barato" en vez de
"está más barato que ayer" — que es la diferencia entre un error de precio y
una oferta normal.

LÍNEA BASE: DETECTA DESDE LA SEGUNDA BARRIDA, NO EN SEMANAS
------------------------------------------------------------------------------
Al arrancar no hay historial, así que se fija el precio de HOY como referencia
inicial de cada producto (la "línea base").

Eso significa que **no hay que esperar nada**: la primera barrida fija la
referencia, y la SEGUNDA ya compara contra ella. Si un producto vale $20.000 y
cae a $5.000, se avisa en la siguiente pasada. Un 75% de caída se entiende
solo; no hace falta historial para saber que algo así está mal.

La línea base se refresca cada ~2 semanas (días 1 y 15, ver modal_app.py).
Eso NO es lo que habilita la detección — solo corrige el punto débil de la
foto inicial: si el día que se fijó el producto estaba en oferta, la
referencia quedó baja. Con la mediana de semanas de historial eso se arregla
solo. Va espaciado a propósito: una referencia que se actualiza muy seguido se
"acostumbra" a un precio bajo y deja de verlo como caída.

TRES NIVELES DE HALLAZGO — y NADA fuera de ellos
------------------------------------------------------------------------------
  caída 70% a 99%   -> ERROR DE PRECIO (el retail se equivocó)
  caída 50% a 70%   -> OFERTA REAL     (descuento de verdad, medido por
                                        nosotros, no el "precio referencia"
                                        inflado que publica la tienda)
  caída 35% a 50%   -> SOLO SI ES DE CATEGORÍA (electrónicos u hogar). Es el
                       piso rebajado que alimenta esos dos tópicos, agregado
                       el 8-ago-2026. Ver `categorias.py`.
  caída bajo 35%    -> NO SE AVISA NUNCA.

Y el piso del 35% NO aplica a todo: un -40% en unas zapatillas o en un libro
sigue sin avisarse. Solo baja para lo que `categorias.clasificar` reconoce
como electrónica u hogar, porque son los dos tópicos que lo piden. Sin esa
restricción el canal se llenaría de "-38%" genéricos, que es exactamente
como se consigue que la gente lo silencie.

Esa distinción es el corazón del producto: la tienda dice "70% dcto" sobre un
precio de referencia que nunca cobró. Acá el porcentaje se calcula contra lo
que ESE producto costó de verdad en el tiempo.
"""
import os
import sqlite3
import statistics
import time

import categorias

RUTA = os.environ.get("VIGIA_DB", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "precios.db"))

UMBRAL_ERROR = 0.70        # 70%+ bajo la referencia = error de precio
UMBRAL_OFERTA = 0.50       # 50%-70% = oferta real, para cualquier producto
UMBRAL_CATEGORIA = 0.35    # 35%-50%: SOLO electrónicos u hogar, ver arriba
MIN_OBSERVACIONES = 3      # historial mínimo para no depender de la línea base
VENTANA_REPETIR = 12 * 3600
TOPE_FALLOS = 2        # fallos seguidos antes de descartar una URL

ERROR = "error"
OFERTA = "oferta"
CATEGORIA = "categoria"    # 35%-50%: solo llega a su tópico de categoría

ESQUEMA = """
CREATE TABLE IF NOT EXISTS precios (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tienda   TEXT NOT NULL,
    url      TEXT NOT NULL,
    nombre   TEXT,
    precio   INTEGER NOT NULL,
    visto_en INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_precios_url ON precios(url);
CREATE INDEX IF NOT EXISTS ix_precios_tienda ON precios(tienda);

-- Referencia por producto. Se fija al descubrirlo y se recalcula a la semana
-- y a las 3 semanas, cuando ya hay historial de verdad.
CREATE TABLE IF NOT EXISTS linea_base (
    url       TEXT PRIMARY KEY,
    precio    INTEGER NOT NULL,
    fijado_en INTEGER NOT NULL,
    origen    TEXT NOT NULL DEFAULT 'inicial'
);

-- Cuántas veces seguidas una URL no dio precio. Tras TOPE_FALLOS se borra
-- del catálogo: un sitemap trae miles de URLs que no son fichas, y
-- reintentarlas para siempre desperdicia la barrida entera.
CREATE TABLE IF NOT EXISTS fallos (
    url    TEXT PRIMARY KEY,
    veces  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS alertas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    url        TEXT NOT NULL,
    tipo       TEXT NOT NULL,
    precio     INTEGER NOT NULL,
    referencia INTEGER NOT NULL,
    caida      REAL NOT NULL,
    avisado_en INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_alertas_url ON alertas(url);
"""


def _migrar(con):
    """Pone al día una base creada por una versión anterior.

    `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, así que una
    base vieja se queda sin las columnas nuevas y revienta al consultarlas.
    Pasó de verdad con el volumen de Modal, que tenía la tabla `alertas` sin
    la columna `tipo`.
    """
    faltantes = {
        "alertas": [("tipo", "TEXT NOT NULL DEFAULT 'error'")],
    }
    for tabla, columnas in faltantes.items():
        try:
            actuales = {f["name"] for f in
                        con.execute("PRAGMA table_info(%s)" % tabla).fetchall()}
        except sqlite3.OperationalError:
            continue                      # la tabla aún no existe: nada que migrar
        if not actuales:
            continue
        for nombre, definicion in columnas:
            if nombre not in actuales:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s"
                            % (tabla, nombre, definicion))
    con.commit()


def abrir():
    con = sqlite3.connect(RUTA, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    _migrar(con)
    # WAL: permite que varios hilos escriban sin bloquearse entre sí, que es
    # necesario con el barrido concurrente.
    con.execute("PRAGMA journal_mode=WAL")
    return con


def guardar(con, tienda, url, nombre, precio, cuando=None):
    con.execute(
        "INSERT INTO precios (tienda, url, nombre, precio, visto_en) VALUES (?,?,?,?,?)",
        (tienda, url, nombre or "", int(precio), int(cuando or time.time())))


def fijar_base(con, url, precio, origen="inicial", cuando=None):
    con.execute(
        "INSERT INTO linea_base (url, precio, fijado_en, origen) VALUES (?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET precio=excluded.precio, "
        "fijado_en=excluded.fijado_en, origen=excluded.origen",
        (url, int(precio), int(cuando or time.time()), origen))


def historial(con, url, limite=60):
    filas = con.execute(
        "SELECT precio, visto_en FROM precios WHERE url=? AND precio>0 "
        "ORDER BY visto_en DESC LIMIT ?", (url, limite)).fetchall()
    return [(f["precio"], f["visto_en"]) for f in filas]


def _base_de(con, url):
    f = con.execute("SELECT precio FROM linea_base WHERE url=?", (url,)).fetchone()
    return f["precio"] if f else None


def _aviso_reciente(con, url, ahora):
    f = con.execute(
        "SELECT avisado_en FROM alertas WHERE url=? ORDER BY avisado_en DESC LIMIT 1",
        (url,)).fetchone()
    return bool(f and (ahora - f["avisado_en"]) < VENTANA_REPETIR)


def evaluar(con, url, precio_actual, ahora=None, nombre=None, tienda=None):
    """Clasifica el precio de hoy. Devuelve el detalle o None.

    Llamar SIEMPRE antes de guardar el precio nuevo: si no, el precio de hoy
    entra en su propia referencia y diluye la caída.

    `nombre` y `tienda` son opcionales pero conviene pasarlos: sin ellos no
    se puede clasificar el producto y el piso se queda en el 50% de siempre,
    o sea los tópicos de Electrónicos y Hogar no reciben nada entre 35% y 50%.
    """
    ahora = int(ahora or time.time())
    previos = [p for p, _ in historial(con, url)]

    # Con historial suficiente manda la MEDIANA (resiste que un error viejo
    # quedara registrado y arrastrara el promedio hacia abajo). Sin historial,
    # manda la línea base fijada al descubrir el producto.
    if len(previos) >= MIN_OBSERVACIONES:
        referencia = statistics.median(previos)
        con_historial = True
    else:
        referencia = _base_de(con, url)
        con_historial = False

    if not referencia or referencia <= 0:
        return None

    caida = 1 - (precio_actual / referencia)

    # El piso depende de si el producto alimenta un tópico de categoría: 35%
    # para electrónica y hogar, 50% para todo lo demás.
    categoria = categorias.clasificar(nombre, tienda, precio_actual)
    piso = UMBRAL_CATEGORIA if categoria else UMBRAL_OFERTA
    if caida < piso:
        return None

    # Con historial, además tiene que ser el más barato jamás visto: si ya
    # estuvo así antes, es una oferta que se repite, no un hallazgo.
    if con_historial and previos and precio_actual >= min(previos):
        return None

    if _aviso_reciente(con, url, ahora):
        return None

    if caida >= UMBRAL_ERROR:
        tipo = ERROR
    elif caida >= UMBRAL_OFERTA:
        tipo = OFERTA
    else:
        tipo = CATEGORIA

    return {
        "url": url,
        "precio": precio_actual,
        "referencia": int(referencia),
        "caida": caida,
        "tipo": tipo,
        "categoria": categoria,
        "con_historial": con_historial,
        "historico": previos[:4],
    }


def anotar_alerta(con, det, ahora=None):
    con.execute(
        "INSERT INTO alertas (url, tipo, precio, referencia, caida, avisado_en) "
        "VALUES (?,?,?,?,?,?)",
        (det["url"], det["tipo"], det["precio"], det["referencia"],
         det["caida"], int(ahora or time.time())))


def recalcular_bases(con, ahora=None):
    """Recalcula la línea base usando la mediana del historial acumulado.

    Se llama cada ~2 semanas desde modal_app.py. Solo toca productos que ya
    tengan al menos MIN_OBSERVACIONES lecturas; los demás conservan su base
    inicial, que ya sirve para detectar.
    """
    ahora = int(ahora or time.time())
    filas = con.execute(
        "SELECT url, COUNT(*) n FROM precios WHERE precio>0 "
        "GROUP BY url HAVING n >= ?", (MIN_OBSERVACIONES,)).fetchall()
    tocados = 0
    for f in filas:
        previos = [p for p, _ in historial(con, f["url"])]
        if previos:
            fijar_base(con, f["url"], statistics.median(previos), "recalculada", ahora)
            tocados += 1
    con.commit()
    return tocados


def anotar_fallo(con, url):
    """Suma un fallo. Devuelve True si ya toca descartar esa URL."""
    con.execute(
        "INSERT INTO fallos (url, veces) VALUES (?,1) "
        "ON CONFLICT(url) DO UPDATE SET veces = veces + 1", (url,))
    f = con.execute("SELECT veces FROM fallos WHERE url=?", (url,)).fetchone()
    return bool(f and f["veces"] >= TOPE_FALLOS)


def olvidar_url(con, url):
    for tabla in ("precios", "linea_base", "fallos"):
        con.execute("DELETE FROM %s WHERE url=?" % tabla, (url,))


def limpiar_fallo(con, url):
    """Una URL que volvió a dar precio deja de estar en observación."""
    con.execute("DELETE FROM fallos WHERE url=?", (url,))


def estadisticas(con):
    p = con.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT url) u FROM precios WHERE precio>0").fetchone()
    v = con.execute("SELECT COUNT(DISTINCT url) u FROM precios").fetchone()
    b = con.execute("SELECT COUNT(*) n FROM linea_base").fetchone()
    a = con.execute("SELECT tipo, COUNT(*) n FROM alertas GROUP BY tipo").fetchall()
    return {
        "vigilados": v["u"],
        "con_precio": p["u"],
        "observaciones": p["n"],
        "con_base": b["n"],
        "alertas": {x["tipo"]: x["n"] for x in a},
    }
