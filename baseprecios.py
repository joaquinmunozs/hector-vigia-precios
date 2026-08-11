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
UMBRAL_OFERTA = 0.40       # 40%-70% = oferta real, para cualquier producto

# EL FILTRO NO ES EL PRECIO, ES EL AHORRO (11-ago-2026)
#
# Un piso de precio alto deja fuera gangas de verdad: una creatina de $20.000
# a $10.000 es un hallazgo, y con un piso de $100.000 nunca se avisaba. Pero
# sin ningún filtro entra la basura, porque el 50% de $2.000 también es 50%.
#
# Lo que separa una cosa de la otra no es cuánto vale el producto sino cuánta
# plata se ahorra el suscriptor. $10.000 de ahorro importan igual en una
# creatina que en un notebook; $900 no importan en ninguno de los dos.
AHORRO_MINIMO = 8_000
UMBRAL_CATEGORIA = 0.35    # 35%-50%: SOLO electrónicos u hogar, ver arriba
# Historial mínimo para no depender de la línea base. Subió de 3 a 5 el
# 11-ago-2026: con una barrida completa al día, 3 lecturas son 3 días y la
# mediana todavía se mueve con cualquier promoción de fin de semana. Con 5 ya
# hay dos fines de semana adentro y el número deja de bailar.
MIN_OBSERVACIONES = 5
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

-- Qué hallazgos ya salieron a Instagram/Facebook (ver redes.py).
--
-- Va en una tabla aparte y NO como una columna de `alertas` a propósito: el
-- aviso a Telegram y la publicación en redes son dos cosas con ritmos
-- distintos (Telegram es inmediato, redes corre unas veces al día) y una
-- puede fallar sin la otra. Con una columna, un fallo de Meta obligaría a
-- reescribir la fila de la alerta, que es el registro de que SÍ se avisó.
CREATE TABLE IF NOT EXISTS publicaciones (
    url          TEXT NOT NULL,
    red          TEXT NOT NULL,          -- 'instagram' | 'facebook'
    publicado_en INTEGER NOT NULL,
    id_externo   TEXT,                   -- id del post que devuelve Meta
    PRIMARY KEY (url, red)
);
"""


def _migrar(con):
    """Pone al día una base creada por una versión anterior.

    `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, así que una
    base vieja se queda sin las columnas nuevas y revienta al consultarlas.
    Pasó de verdad con el volumen de Modal, que tenía la tabla `alertas` sin
    la columna `tipo`.
    """
    faltantes = {
        "alertas": [
            ("tipo", "TEXT NOT NULL DEFAULT 'error'"),
            # NULL = todavía no se sabe si la tienda corrigió el precio.
            # Se llena sola cuando se vuelve a leer esa URL y el precio ya
            # recuperó su referencia — ver `marcar_si_restablecido`.
            ("restablecido_en", "INTEGER"),
        ],
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
    # WAL: lectores y UN escritor a la vez. OJO: no permite dos escritores
    # simultáneos — eso no existe en SQLite, con WAL ni sin él. Lo que WAL sí
    # da es que los lectores no se bloqueen con el escritor.
    con.execute("PRAGMA journal_mode=WAL")
    # synchronous=NORMAL es lo que hace barato comitear seguido: en WAL, con
    # FULL cada commit paga un fsync. Y comitear seguido no es un lujo, es la
    # única forma de que el lock de escritura no quede tomado mientras el otro
    # hilo baja una página (ver el commit del 11-ago-2026). En WAL, NORMAL
    # solo arriesga perder las últimas transacciones ante un corte de luz del
    # runner, nunca corromper la base — y acá la base se rehace sola.
    con.execute("PRAGMA synchronous=NORMAL")
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
    # El piso de precio decide si un producto BARATO merece avisarse con solo
    # 35% de caída — no a qué tópico pertenece. Se separaban las dos cosas mal:
    # una cortina de $9.000 con 60% de descuento perdía su categoría por el
    # piso y terminaba solo en Ofertas, nunca en Hogar. Ahora la categoría se
    # calcula SIN precio (es lo que el producto es) y el piso se aplica aparte
    # (es cuánto tiene que caer para molestar a alguien).
    categoria = categorias.clasificar(nombre, tienda)
    categoria_con_piso = categorias.clasificar(nombre, tienda, precio_actual)
    piso = UMBRAL_CATEGORIA if categoria_con_piso else UMBRAL_OFERTA
    if caida < piso:
        return None

    # El ahorro en pesos, no el precio del producto. Ver AHORRO_MINIMO.
    if (referencia - precio_actual) < AHORRO_MINIMO:
        return None

    # UNA OFERTA SIN HISTORIAL NO SE AVISA (11-ago-2026)
    #
    # Sin historial, la referencia es la foto del día que se descubrió el
    # producto. Si ese día estaba inflado —y en el retail chileno se infla
    # justo antes de cada Cyber— el precio normal de la semana siguiente se ve
    # como un -55% que nunca existió. Ese es el falso positivo caro: no se nota
    # revisando el mensaje, solo entrando a comprar.
    #
    # Los ERRORES de precio sí siguen saliendo desde el primer día: para pasar
    # el 70% no basta con una referencia mal fijada, el precio tiene que haberse
    # caído de verdad. Y el error dura minutos — esperar historial sería llegar
    # tarde siempre, que es lo mismo que no avisar.
    if not con_historial and caida < UMBRAL_ERROR:
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


MIN_CATALOGO_PARA_TASA = 500   # bajo esto, un puñado de errores da un % ruidoso


def tasas_error_por_tienda(con, ventana_dias=30, ahora=None):
    """Fracción de productos de cada tienda que tuvo un ERROR de precio real
    en los últimos `ventana_dias` — la probabilidad empírica de que esa
    tienda "se quiebre" de nuevo, para reforzar `caliente.puntaje` con datos
    reales en vez de solo marca+precio.

    Arranca vacío (sin alertas todavía no hay tasa para nadie) y se corrige
    solo con cada error real que se registre — no hace falta sembrar nada a
    mano. Se exige un catálogo de al menos `MIN_CATALOGO_PARA_TASA`
    productos para que la tasa cuente: una tienda de 10 productos con 1
    error da un "10%" que no significa nada.
    """
    desde = int((ahora or time.time()) - ventana_dias * 86400)
    errores = con.execute("""
        SELECT p.tienda AS tienda, COUNT(DISTINCT a.url) AS n
        FROM alertas a JOIN precios p ON p.url = a.url
        WHERE a.tipo = ? AND a.avisado_en >= ?
        GROUP BY p.tienda
    """, (ERROR, desde)).fetchall()

    catalogo = con.execute(
        "SELECT tienda, COUNT(DISTINCT url) AS n FROM precios GROUP BY tienda"
    ).fetchall()
    tam = {r["tienda"]: r["n"] for r in catalogo}

    return {
        r["tienda"]: r["n"] / tam[r["tienda"]]
        for r in errores
        if tam.get(r["tienda"], 0) >= MIN_CATALOGO_PARA_TASA
    }


def marcar_si_restablecido(con, url, precio_actual, ahora=None):
    """Si esta URL tiene una alerta sin resolver y el precio ya volvió a
    estar cerca de su referencia de entonces, anota cuánto tardó la tienda
    en corregirlo. Es la única forma honesta de responder "cuánto dura un
    error de precio en Chile": medirlo con datos propios, no adivinar.

    "Cerca" es 90% de la referencia — no exige volver EXACTO al precio
    viejo (que a veces sube o baja un poco al corregirse) para no dejar
    casos reales sin medir por un detalle de un par de pesos.
    """
    ahora = int(ahora or time.time())
    abierta = con.execute(
        "SELECT id, referencia FROM alertas "
        "WHERE url=? AND restablecido_en IS NULL "
        "ORDER BY avisado_en DESC LIMIT 1", (url,)).fetchone()
    if not abierta or precio_actual < abierta["referencia"] * 0.9:
        return None
    con.execute("UPDATE alertas SET restablecido_en=? WHERE id=?",
                (ahora, abierta["id"]))
    con.commit()
    return ahora


def duracion_errores(con, ventana_dias=30, ahora=None):
    """Cuánto tardaron en corregirse los errores YA RESUELTOS de los
    últimos `ventana_dias` — mediana y percentil 90, en minutos. `None` si
    todavía no hay ninguno resuelto (normal al principio: hace falta que el
    vigilante vuelva a leer la URL después del error para saber que se
    corrigió, no solo que se avisó)."""
    desde = int((ahora or time.time()) - ventana_dias * 86400)
    filas = con.execute(
        "SELECT avisado_en, restablecido_en FROM alertas "
        "WHERE tipo=? AND restablecido_en IS NOT NULL AND avisado_en >= ?",
        (ERROR, desde)).fetchall()
    if not filas:
        return None
    minutos = sorted((f["restablecido_en"] - f["avisado_en"]) / 60 for f in filas)
    return {
        "n": len(minutos),
        "mediana_min": statistics.median(minutos),
        "p90_min": minutos[int(0.9 * (len(minutos) - 1))],
    }


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
