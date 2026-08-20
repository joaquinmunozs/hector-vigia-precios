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

# Vuelos tiene su propio piso y NO usa el 35% de las otras categorías.
# Es un pedido explícito ("ofertas de 40% para arriba al tópico de vuelos") y
# además tiene sentido solo: en pasajes, un 35% es una promoción de martes
# cualquiera, mientras que en un notebook es un hallazgo. Si se dejara en 35%
# el tópico se llenaría de ruido, que es la forma más rápida de que alguien lo
# silencie.
UMBRAL_VUELOS = 0.40
# Historial mínimo para no depender de la línea base. Subió de 3 a 5 el
# 11-ago-2026: con una barrida completa al día, 3 lecturas son 3 días y la
# mediana todavía se mueve con cualquier promoción de fin de semana. Con 5 ya
# hay dos fines de semana adentro y el número deja de bailar.
MIN_OBSERVACIONES = 5

# Días de observación real antes de creerle a la mediana. Reemplaza a
# MIN_OBSERVACIONES como criterio de "tener historial" — ver el comentario
# dentro de `evaluar`. Siete días cubren un ciclo semanal completo del retail,
# fin de semana incluido, que es cuando más se mueven los precios en Chile.
DIAS_MINIMOS_HISTORIAL = 7


def _dias_cubiertos(ts, ahora):
    """Cuántos días de observación real cubren estos tramos."""
    if not ts:
        return 0.0
    return (int(ahora) - min(d for _, d, _ in ts)) / 86400.0
VENTANA_REPETIR = 12 * 3600
# Fallos SEGUIDOS antes de sacar una URL del catálogo. Un éxito lo resetea
# (ver `limpiar_fallo`), así que esto cuenta rachas, no fallos totales.
#
# Subido de 2 a 6 el 12-ago-2026. Con 2 se estaba borrando catálogo bueno: el
# 11-ago el catálogo cayó de 439.375 a 360.863 fichas (-78.512) en un día, y
# quedaron 138.861 URLs con exactamente 1 fallo, o sea a una racha de morir.
# Entre ellas el 66,7% de Falabella — la tienda que MEJOR mide, con 72% de
# cobertura. Una tienda que mide bien no tiene dos tercios de fichas basura:
# lo que fallaba era la lectura, no la URL.
#
# ✅ RESUELTO el 12-ago-2026: la distinción ya existe (`vigia.desenlace`).
#
# Acá se mezclaban dos cosas distintas: una URL de sitemap que nunca fue ficha
# (basura, hay que borrarla) y una lectura que falló por bloqueo o timeout (la
# ficha está bien, el que falla es el acceso). Ahora sólo llega a `fallos` el
# primer caso: los rechazos se cuentan aparte y NO acercan la URL a que la
# borren. Un 404/410 sigue tratándose como "muerta".
#
# El 6 se deja como está aunque ya no sea imprescindible. Bajarlo a 2 volvería
# a hacer que dos lecturas raras seguidas borren una ficha buena, y el margen
# no cuesta nada: son fichas que igual no dan precio.
TOPE_FALLOS = 6

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

-- URLs que YA se comprobó que no son fichas de producto, para que el
-- descubrimiento no las vuelva a meter cada lunes.
--
-- POR QUÉ (16-ago-2026)
-- ---------------------------------------------------------------------------
-- `olvidar_url` borraba la URL de `precios`, `linea_base` y `fallos`, o sea
-- no dejaba ningún rastro. Y `descubrir_productos` mete una URL si no está en
-- `precios`. Resultado: cada lunes se redescubría exactamente lo mismo que la
-- semana anterior había costado 6 lecturas fallidas descartar.
--
-- Medido: el sitemap de casaideas.cl publica 3.082 URLs y NINGUNA es una
-- ficha (son categorías y landings); el de easy.cl, 929 y ninguna; el de
-- jumbo.cl, 803 de las que solo 3 son fichas. Esas tres tiendas solas metían
-- ~4.800 URLs muertas por semana, cada una con 6 intentos de lectura por
-- delante. Y como al morir no dejaban rastro, la tienda terminaba con 0
-- productos y el ciclo volvía a empezar.
--
-- `cuando` existe para poder readmitirlas: una tienda puede cambiar de
-- plataforma y publicar fichas donde antes había categorías. Ver
-- DIAS_REINTENTAR_DESCARTADA.
CREATE TABLE IF NOT EXISTS descartadas (
    url    TEXT PRIMARY KEY,
    cuando INTEGER NOT NULL DEFAULT 0
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
        # La foto del producto. Va acá y no en el ESQUEMA porque la base de
        # producción ya existe (300 MB de historial) y `CREATE TABLE IF NOT
        # EXISTS` no le agrega columnas a una tabla que ya está.
        #
        # Se guarda por lectura, igual que el nombre, en vez de en una tabla
        # aparte: es un campo del producto tal como se vio ese día, y las
        # tiendas cambian la foto sin avisar. La última lectura manda.
        "precios": [
            ("imagen", "TEXT"),
            # Hasta cuándo estuvo vigente ESE precio. Con `visto_en` forma un
            # RANGO: "costó $X desde el día 1 hasta el día 12". Ver `guardar`.
            # NULL en las filas viejas, que se leen como rango de duración 0.
            ("visto_hasta", "INTEGER"),
        ],
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


def guardar(con, tienda, url, nombre, precio, cuando=None, imagen=None):
    """Guarda el precio como un RANGO, no como una lectura suelta.

    POR QUÉ (12-ago-2026)
    --------------------------------------------------------------------------
    Antes esto insertaba una fila por CADA lectura, aunque el precio fuera
    idéntico al de hace una hora. Medido sobre producción: de 509.834 lecturas
    guardadas, sólo 177.146 eran combinaciones distintas de producto+precio —
    o sea **el 65,3% de las filas era la misma lectura repetida**.

    Y la base entera se baja y se sube en cada corrida. Proyectado a ese
    ritmo: 5,7 GB a los 30 días y 69 GB al año. Justo cuando el producto
    alcanza los 30 días de historial que necesita, el mecanismo que lo guarda
    deja de dar. Guardando sólo los cambios, un año entero cabe en ~268 MB.

    POR QUÉ RANGOS Y NO SIMPLEMENTE DEDUPLICAR
    --------------------------------------------------------------------------
    Borrar las filas repetidas a secas EMPEORA la mediana. Un producto que
    estuvo a $100.000 durante 29 días y a $150.000 durante uno:

        una fila por lectura  -> mediana $100.000  (correcto)
        una fila por precio   -> mediana $125.000  (mal: pesan igual)

    Con rango, cada fila sabe CUÁNTO DURÓ ese precio, y la mediana se pondera
    por tiempo (ver `_mediana_ponderada`). Sale más liviano *y* más correcto
    que las dos alternativas. Es lo que hace Keepa.
    """
    ahora = int(cuando or time.time())
    precio = int(precio)
    ultima = con.execute(
        "SELECT id, precio FROM precios WHERE url=? AND precio>0 "
        "ORDER BY visto_en DESC, id DESC LIMIT 1", (url,)).fetchone()

    # Mismo precio que la última vez: se estira el rango en vez de insertar.
    if ultima and ultima["precio"] == precio:
        con.execute(
            "UPDATE precios SET visto_hasta=?, "
            "nombre=COALESCE(NULLIF(?,''), nombre), "
            "imagen=COALESCE(?, imagen) WHERE id=?",
            (ahora, nombre or "", imagen or None, ultima["id"]))
        return

    con.execute(
        "INSERT INTO precios "
        "(tienda, url, nombre, precio, visto_en, visto_hasta, imagen) "
        "VALUES (?,?,?,?,?,?,?)",
        (tienda, url, nombre or "", precio, ahora, ahora, imagen or None))


def fijar_base(con, url, precio, origen="inicial", cuando=None):
    con.execute(
        "INSERT INTO linea_base (url, precio, fijado_en, origen) VALUES (?,?,?,?) "
        "ON CONFLICT(url) DO UPDATE SET precio=excluded.precio, "
        "fijado_en=excluded.fijado_en, origen=excluded.origen",
        (url, int(precio), int(cuando or time.time()), origen))


# ── LA VENTANA ES DE TIEMPO, NO DE LECTURAS (12-ago-2026) ──────────────────
#
# Antes esto tomaba "las últimas 60 lecturas", sin mirar de cuándo eran. Ese
# criterio tiene un modo de falla que EMPEORA justo cuando el sistema mejora:
# cuanto más rápido vigila, menos tiempo cubren esas 60 lecturas, y más corta
# se vuelve la "historia" contra la que se compara.
#
# Medido sobre la base de producción: las fichas con 5 o más lecturas cubrían
# **0,62 días en promedio**, y las de 16 lecturas apenas 0,44 — o sea que la
# "mediana histórica" que sostiene todo el producto se estaba calculando
# sobre MEDIO DÍA. Con la frecuencia adaptativa recién puesta, los productos
# móviles se leen mucho más seguido, así que la ventana se habría encogido
# todavía más.
#
# Es exactamente el error que hundió la calidad de `ratonean2`, el bot chileno
# que hizo esto mismo antes: su algoritmo confundía la subida estacional
# previa a una fecha especial con un descuento porque "el historial del precio
# era otro".
#
# 30 DÍAS NO ES UN NÚMERO INVENTADO: es el estándar de la Directiva Omnibus de
# la UE, que obliga a anunciar todo descuento contra el PRECIO MÁS BAJO DE LOS
# ÚLTIMOS 30 DÍAS, precisamente para que no se pueda inflar el precio antes de
# la rebaja. Francia le puso 40 millones de euros de multa a Shein en 2025 por
# esa práctica. Alinear la referencia con ese criterio hace que la caída que
# se anuncia signifique lo mismo que significa legalmente en Europa.
VENTANA_HISTORIAL_DIAS = 30

# Tope de filas por si una ficha muy volátil acumula miles. Es alto a
# propósito: sólo se guarda cuando el precio CAMBIA, así que en la práctica
# son unas pocas por ficha (el 99% del catálogo tiene un solo precio distinto).
TOPE_HISTORIAL = 2000


def historial(con, url, dias=None, limite=TOPE_HISTORIAL, ahora=None):
    """Los precios de esta ficha dentro de la ventana de tiempo, no las N
    últimas lecturas. Ver el comentario de `VENTANA_HISTORIAL_DIAS`."""
    return [(p, desde) for p, desde, _hasta in
            tramos(con, url, dias=dias, limite=limite, ahora=ahora)]


def tramos(con, url, dias=None, limite=TOPE_HISTORIAL, ahora=None):
    """Igual que `historial` pero con el rango completo: (precio, desde, hasta).

    Un tramo entra si SE SOLAPA con la ventana, no si empezó dentro: un precio
    que lleva vigente dos meses tiene `visto_en` viejo pero es el precio de
    hoy, y dejarlo fuera sería perder justo la referencia que más pesa.
    """
    dias = VENTANA_HISTORIAL_DIAS if dias is None else dias
    corte = int(ahora or time.time()) - int(dias * 86400)
    filas = con.execute(
        "SELECT precio, visto_en, COALESCE(visto_hasta, visto_en) AS hasta "
        "FROM precios WHERE url=? AND precio>0 "
        "AND COALESCE(visto_hasta, visto_en) >= ? "
        "ORDER BY visto_en DESC LIMIT ?",
        (url, corte, limite)).fetchall()
    return [(f["precio"], f["visto_en"], f["hasta"]) for f in filas]


def _mediana_ponderada(con, url, dias=None, ahora=None):
    """La mediana del precio ponderada por CUÁNTO DURÓ cada uno.

    Es la diferencia entre "qué precio vi más veces" y "qué precio tuvo más
    tiempo", y sólo la segunda pregunta describe lo que el producto vale de
    verdad. Un precio inflado tres días no puede pesar lo mismo que el precio
    normal de tres semanas sólo porque nos tocó mirarlo seguido.

    Si ningún tramo tiene duración (base recién migrada, todas las filas
    viejas), cae a la mediana simple — mismo comportamiento que antes.
    """
    ts = tramos(con, url, dias=dias, ahora=ahora)
    if not ts:
        return None, 0
    pesos = [(p, max(0, h - d)) for p, d, h in ts]
    total = sum(w for _, w in pesos)
    if total <= 0:
        return statistics.median([p for p, _, _ in ts]), len(ts)
    acum = 0.0
    for precio, w in sorted(pesos):
        acum += w
        if acum >= total / 2.0:
            return precio, len(ts)
    return pesos[-1][0], len(ts)


def _base_de(con, url):
    f = con.execute("SELECT precio FROM linea_base WHERE url=?", (url,)).fetchone()
    return f["precio"] if f else None


def _aviso_reciente(con, url, ahora):
    f = con.execute(
        "SELECT avisado_en FROM alertas WHERE url=? ORDER BY avisado_en DESC LIMIT 1",
        (url,)).fetchone()
    return bool(f and (ahora - f["avisado_en"]) < VENTANA_REPETIR)


def _ya_se_dijo(con, url, precio_actual):
    """¿Ya avisamos este producto a este precio, y sigue sin corregirse?

    LA VENTANA DE 12 h NO ALCANZA (13-ago-2026)
    ------------------------------------------------------------------------
    Un precio que se queda abajo vuelve a cruzar el umbral cada vez que se
    lee, y `_aviso_reciente` sólo tapa 12 h. Medido en producción: la manteca
    de cacao salió TRES veces (11, 12 y 13 de agosto), idéntica; las toallas,
    la vajilla y el matcha, dos veces cada una. 4 de los 11 avisos de error
    del período eran repeticiones.

    El filtro que debía impedirlo —"tiene que ser el más barato jamás visto"—
    vive detrás de `if con_historial`, y hoy no protege a nadie: las 170
    alertas de la base de producción tienen la referencia en `origen inicial`,
    o sea ninguna ficha llega a los 7 días de observación.

    La regla no necesita historial: si ya lo dijimos a este precio o más
    barato, y la tienda todavía no lo corrigió, no es noticia otra vez.

    SÓLO cuentan las alertas ABIERTAS (`restablecido_en IS NULL`). Si el
    precio se recuperó y la tienda se vuelve a equivocar la semana siguiente,
    eso SÍ es un hallazgo nuevo — y es además el caso que mejor paga.
    """
    f = con.execute(
        "SELECT MIN(precio) AS piso FROM alertas "
        "WHERE url=? AND restablecido_en IS NULL", (url,)).fetchone()
    return bool(f and f["piso"] is not None and precio_actual >= f["piso"])


def evaluar(con, url, precio_actual, ahora=None, nombre=None, tienda=None):
    """Clasifica el precio de hoy. Devuelve el detalle o None.

    Llamar SIEMPRE antes de guardar el precio nuevo: si no, el precio de hoy
    entra en su propia referencia y diluye la caída.

    `nombre` y `tienda` son opcionales pero conviene pasarlos: sin ellos no
    se puede clasificar el producto y el piso se queda en el 50% de siempre,
    o sea los tópicos de Electrónicos y Hogar no reciben nada entre 35% y 50%.
    """
    ahora = int(ahora or time.time())
    ts = tramos(con, url, ahora=ahora)
    previos = [p for p, _, _ in ts]

    # ── "TENER HISTORIAL" SE MIDE EN DÍAS, NO EN LECTURAS (12-ago-2026) ────
    #
    # Antes el criterio era `len(previos) >= MIN_OBSERVACIONES` (5 lecturas).
    # Eso dejó de significar nada por dos motivos:
    #
    #   1. Desde que los precios se guardan como RANGOS, un producto estable
    #      tiene UNA sola fila aunque lleve un mes vigilado. Contar filas lo
    #      dejaría para siempre "sin historial".
    #   2. Aun antes, contar lecturas engañaba: medido en producción, las
    #      fichas con 5 o más lecturas cubrían 0,62 días. Cinco lecturas de la
    #      misma tarde no son historia de nada.
    #
    # Lo que hace confiable a una referencia es el TIEMPO OBSERVADO. Siete
    # días es lo que el propio README ya prometía, y cubre un ciclo semanal
    # completo del retail — incluido el fin de semana, que es cuando más se
    # mueven los precios en Chile.
    cobertura = _dias_cubiertos(ts, ahora)
    habitual = None
    if cobertura >= DIAS_MINIMOS_HISTORIAL:
        # ── LA CAÍDA SE MIDE CONTRA EL MÍNIMO DE 30 DÍAS (12-ago-2026) ────
        #
        # Antes se medía contra la mediana ponderada. La mediana es la
        # herramienta correcta para saber CUÁNTO VALE el producto, pero la
        # equivocada para anunciar CUÁNTO BAJÓ: si el precio estuvo inflado
        # unos días, la mediana queda alta y el descuento sale exagerado.
        #
        # El caso real con el que se probó: producto de $100.000, inflado a
        # $150.000 cinco días antes del Cyber, "rebajado" a $38.000.
        #     contra la mediana ($150.000) -> -75%  ← lo que se anunciaba
        #     contra el mínimo ($100.000)  -> -62%  ← lo que de verdad bajó
        #
        # -75% cruza UMBRAL_ERROR y el aviso salía como 🚨 ERROR DE PRECIO.
        # -62% es una oferta real. Exagerar contamina justo el tópico donde
        # el producto se juega su credibilidad: si "error de precio" empieza
        # a incluir ofertas normales, deja de significar nada.
        #
        # Es además el criterio de la Directiva Omnibus, que ya se adoptó
        # para la VENTANA de 30 días: el descuento se anuncia contra el
        # precio más bajo de esos 30 días. Se usa el mismo número para la
        # ventana y para la referencia, no dos criterios distintos.
        #
        # No abre un agujero nuevo: el filtro de más abajo YA exigía
        # `precio_actual < min(previos)`, así que el mínimo siempre fue la
        # vara para decidir si se avisa. Ahora también lo es para el número
        # que se muestra.
        habitual, _ = _mediana_ponderada(con, url, ahora=ahora)
        referencia = min(previos) if previos else habitual
        con_historial = True
    else:
        referencia = _base_de(con, url)
        con_historial = False

    if not referencia or referencia <= 0:
        return None

    caida = 1 - (precio_actual / referencia)

    # El piso depende de a qué tópico alimenta: 35% para electrónica y
    # hogar, 40% para vuelos (ver UMBRAL_VUELOS) y 40% para todo lo demás.
    # El piso de precio decide si un producto BARATO merece avisarse con solo
    # 35% de caída — no a qué tópico pertenece. Se separaban las dos cosas mal:
    # una cortina de $9.000 con 60% de descuento perdía su categoría por el
    # piso y terminaba solo en Ofertas, nunca en Hogar. Ahora la categoría se
    # calcula SIN precio (es lo que el producto es) y el piso se aplica aparte
    # (es cuánto tiene que caer para molestar a alguien).
    categoria = categorias.clasificar(nombre, tienda)
    categoria_con_piso = categorias.clasificar(nombre, tienda, precio_actual)
    if categoria_con_piso == categorias.VUELOS:
        piso = UMBRAL_VUELOS
    elif categoria_con_piso:
        piso = UMBRAL_CATEGORIA
    else:
        piso = UMBRAL_OFERTA
    if caida < piso:
        return None

    # El ahorro en pesos, no el precio del producto. Ver AHORRO_MINIMO.
    #
    # NO APLICA A ERRORES DE PRECIO (15-ago-2026)
    # --------------------------------------------------------------------
    # Un error de precio (caída >= UMBRAL_ERROR) es la tienda vendiendo por
    # accidente, no un descuento — el docstring del módulo ya lo decía: "un
    # 75% de caída se entiende solo, no hace falta historial". Pero el piso
    # de $8.000 SÍ lo estaba bloqueando: contra la base de producción, una
    # broca de $7.290 que cae a $1.000 (-86%) o una cortina de $36.990 a
    # $10.990 (-70%) nunca alcanzan $8.000 de ahorro aunque el error sea
    # evidente. Verificado contra 4 caídas reales de 70%-86% en productos de
    # precio bajo: las 4 quedaban silenciadas solo por este piso, nada más
    # las bloqueaba. El piso de plata sigue aplicando a ofertas y categoría,
    # donde sí importa cuánto se ahorra el suscriptor — un error no es una
    # oferta, es la tienda equivocándose.
    if caida < UMBRAL_ERROR and (referencia - precio_actual) < AHORRO_MINIMO:
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
    #
    # Desde que `referencia` ES ese mínimo, este filtro quedó redundante —
    # un precio igual o mayor da caída <= 0 y no pasa el piso. Se deja
    # escrito igual: es la regla de negocio, y si mañana alguien vuelve a
    # cambiar la referencia, esto tiene que seguir siendo cierto.
    if con_historial and previos and precio_actual >= min(previos):
        return None

    if _aviso_reciente(con, url, ahora):
        return None

    # Y aunque hayan pasado las 12 h: si ya lo dijimos a este precio y sigue
    # sin corregirse, repetirlo sólo enseña al suscriptor a silenciar el
    # tópico. Ver `_ya_se_dijo`.
    if _ya_se_dijo(con, url, precio_actual):
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
        # El precio HABITUAL (mediana ponderada por tiempo). No se usa para
        # calcular la caída —ver el comentario en `evaluar`— pero sirve para
        # mostrarlo: "bajó 62% contra su mínimo, y 75% contra lo que suele
        # costar" es más información, no menos. `None` si no hay historial.
        "habitual": int(habitual) if habitual else None,
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
    # SE CIERRAN TODAS LAS ABIERTAS DE ESA URL, NO SÓLO LA ÚLTIMA (13-ago-2026)
    #
    # Antes esto cerraba una sola fila. Con varias alertas abiertas del mismo
    # producto —que es lo normal cuando el precio baja por escalones— las
    # viejas quedaban abiertas para siempre aunque la tienda hubiera corregido
    # hace rato. Eso no molestaba mientras nadie leyera `restablecido_en`,
    # pero `_ya_se_dijo` ahora lo usa para decidir si un error que REAPARECE
    # vuelve a avisarse: con una fila zombi abierta, ese hallazgo —de los que
    # mejor pagan— quedaba tapado en silencio.
    #
    # `duracion_errores` mide fila por fila (`restablecido_en - avisado_en`),
    # así que cerrarlas juntas no le miente: cada aviso conserva su propia
    # duración, contada desde que se emitió hasta que la tienda corrigió.
    con.execute("UPDATE alertas SET restablecido_en=? "
                "WHERE url=? AND restablecido_en IS NULL", (ahora, url))
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

    Se llama los días 1 y 15. Solo toca productos con suficiente TIEMPO
    observado; los demás conservan su base inicial, que ya sirve para
    detectar errores de precio.

    EL FILTRO ES POR DÍAS, NO POR FILAS (12-ago-2026)
    --------------------------------------------------------------------------
    Antes exigía `COUNT(*) >= MIN_OBSERVACIONES`. Desde que los precios se
    guardan como rangos, un producto estable tiene UNA sola fila aunque lleve
    un mes vigilado: con el filtro viejo, la recalibración del día 15 no
    habría tocado casi nada y las referencias se habrían quedado congeladas en
    la foto del primer día — exactamente lo que esta función existe para
    arreglar.
    """
    ahora = int(ahora or time.time())
    corte = ahora - int(DIAS_MINIMOS_HISTORIAL * 86400)
    filas = con.execute(
        "SELECT url, MIN(visto_en) AS primero FROM precios WHERE precio>0 "
        "AND visto_en > 0 GROUP BY url HAVING primero <= ?",
        (corte,)).fetchall()
    tocados = 0
    for f in filas:
        # Misma mediana ponderada por tiempo que usa `evaluar`: si acá se
        # calculara de otra forma, la referencia recalibrada no coincidiría
        # con la que se compara en cada lectura.
        ref, n_tramos = _mediana_ponderada(con, f["url"], ahora=ahora)
        if ref:
            fijar_base(con, f["url"], ref, "recalculada", ahora)
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


# Cada cuánto se le vuelve a dar una oportunidad a una URL descartada. Una
# tienda puede cambiar de plataforma y empezar a publicar fichas donde antes
# había categorías, así que el descarte no puede ser para siempre — pero
# tampoco puede ser semanal, que es lo que pasaba antes de que existiera la
# tabla `descartadas`.
DIAS_REINTENTAR_DESCARTADA = 60


def olvidar_url(con, url, ahora=None):
    """Saca una URL del catálogo y DEJA CONSTANCIA de que se descartó.

    La constancia es la parte que faltaba: sin ella el descubrimiento del
    lunes siguiente la volvía a meter, y se pagaban otras 6 lecturas fallidas
    para llegar a la misma conclusión. Ver la tabla `descartadas`.
    """
    for tabla in ("precios", "linea_base", "fallos"):
        con.execute("DELETE FROM %s WHERE url=?" % tabla, (url,))
    con.execute("INSERT INTO descartadas (url, cuando) VALUES (?,?) "
                "ON CONFLICT(url) DO UPDATE SET cuando=excluded.cuando",
                (url, int(ahora or time.time())))


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
