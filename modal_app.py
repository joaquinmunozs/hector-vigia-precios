# -*- coding: utf-8 -*-
"""Despliegue en Modal. Todo el sistema corre solo y en el plan gratuito.

    modal deploy modal_app.py

CALENDARIO
------------------------------------------------------------------------------
  barrer               cada 4 h    lee precios y avisa hallazgos
  refrescar_catalogo   lunes 4 AM  busca productos nuevos en los sitemaps
  recalibrar           diario 5 AM revisa si toca recalcular la línea base

POR QUÉ CADA 4 HORAS Y NO CADA 1
------------------------------------------------------------------------------
El catálogo son ~165.000 productos y una barrida completa toma ~1.9 h con los
hilos repartidos proporcionalmente. Correrlo cada hora significaría barridas
encimadas y, sobre todo, el triple de tráfico contra cada tienda — que es
exactamente lo que gatilla que te bloqueen.

Cada 4 h también mantiene el costo dentro de los $30/mes de crédito gratis de
Modal: ~6 barridas al día × ~3 h de contenedor liviano. Correrlo cada 2 h se
pasaría del crédito.

LA RECALIBRACIÓN A 1 Y 3 SEMANAS
------------------------------------------------------------------------------
Modal.Cron es recurrente, no sirve para "una vez, dentro de una semana". Por
eso `recalibrar` corre TODOS los días y adentro pregunta si ya toca: a los 7 y
a los 21 días desde el arranque. Cada hito se hace una sola vez, y queda
anotado en la propia base para que no se repita aunque el cron corra 30 veces.
"""
import modal

app = modal.App("vigia-precios")

imagen = (
    modal.Image.debian_slim(python_version="3.12")
    # curl_cffi replica el saludo TLS de Chrome. Sin él, adidas.cl responde
    # 403 y Falabella no se puede leer: son ~1,5 millones de fichas que se
    # perderían por no instalar una librería de 3 MB.
    .pip_install("curl_cffi")
    # Los add_local_file van SIEMPRE al final: Modal no admite pasos de build
    # después de agregar archivos locales.
    .add_local_file("adaptadores.py", "/root/adaptadores.py")
    .add_local_file("alertas.py", "/root/alertas.py")
    .add_local_file("caliente.py", "/root/caliente.py")
    .add_local_file("vigilante.py", "/root/vigilante.py")
    .add_local_file("baseprecios.py", "/root/baseprecios.py")
    .add_local_file("descubrir.py", "/root/descubrir.py")
    .add_local_file("extractor.py", "/root/extractor.py")
    .add_local_file("tiendas.py", "/root/tiendas.py")
    .add_local_file("vigia.py", "/root/vigia.py")
)

volumen = modal.Volume.from_name("vigia-precios-db", create_if_missing=True)
RUTA_DB = "/datos/precios.db"

# `vigia-env` trae TODO lo de Telegram: el token de Héctor (@HectorRat_bot),
# el chat del grupo "Rat.IA" y los dos tópicos.
#
# Antes esto tomaba el token prestado de `steve-env`. Ya no: Héctor es su
# propio bot, y compartir el token de Steve significaba que un problema en
# cualquiera de los dos (revocarlo, rotarlo) tumbaba al otro sin aviso.
SECRETOS = [
    modal.Secret.from_name("vigia-env", required_keys=[]),
]


def _preparar():
    import os
    import sys
    os.environ["VIGIA_DB"] = RUTA_DB
    sys.path.insert(0, "/root")


@app.function(
    image=imagen,
    volumes={"/datos": volumen},
    schedule=modal.Cron("0 */4 * * *", timezone="America/Santiago"),
    timeout=60 * 60 * 4,   # 4 h: la barrida toma ~1.9 h, el resto es margen
    secrets=SECRETOS,
)
def barrer():
    _preparar()
    import threading

    import baseprecios
    import vigia
    import vigilante

    # El vigilante corre en un HILO de este mismo contenedor, NO en un
    # contenedor Modal aparte.
    #
    # ANTES `vigilar_caliente.spawn()` lo lanzaba como una función Modal
    # separada — es decir, OTRO contenedor, con su propia conexión y su
    # propio `volumen.commit()`, escribiendo el MISMO archivo SQLite al mismo
    # tiempo que este. Un Volumen de Modal no es un sistema de archivos con
    # bloqueo real entre contenedores: dos `commit()` concurrentes al mismo
    # archivo lo corrompieron de verdad (visto el 7-ago-2026:
    # "database disk image is malformed", integrity_check ni siquiera podía
    # leer el archivo).
    #
    # Corriendo como hilo, las dos conexiones (la de la barrida y la del
    # vigilante) apuntan al MISMO archivo local dentro de este único
    # contenedor — ahí sí el modo WAL de SQLite da la garantía que se
    # necesita, porque es exactamente para lo que WAL existe. Solo hay UN
    # `volumen.commit()` al final, cuando las dos ya terminaron.
    def _vigilar():
        # La conexión se abre AQUÍ DENTRO, en el propio hilo que la va a
        # usar. sqlite3 lo exige explícito: una conexión creada en un hilo no
        # se puede usar desde otro ("SQLite objects created in a thread can
        # only be used in that same thread") — abrirla afuera y pasarla como
        # argumento (lo que hacía la primera versión de este fix) rompía
        # exactamente por eso, silenciosamente atrapado por el except de
        # abajo, así que el vigilante corría cero ciclos sin que se notara.
        try:
            con_caliente = baseprecios.abrir()
            n = vigilante.correr(con_caliente, avisar=True,
                                 segundos_max=int(3.7 * 3600))
            con_caliente.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con_caliente.close()
            print("vigilante: %s hallazgos en la tanda" % n)
        except Exception as e:                          # noqa: BLE001
            print("vigilante falló: %s" % str(e)[:120])

    hilo_caliente = threading.Thread(target=_vigilar, daemon=True)
    hilo_caliente.start()

    con = baseprecios.abrir()
    hallazgos = vigia.barrida(con, avisar=True)
    print("estado:", baseprecios.estadisticas(con))
    # El checkpoint fuerza que todo lo escrito quede en el .db principal antes
    # de subirlo al volumen — sin esto, puede quedar viviendo solo en el
    # archivo -wal, y otro contenedor que monte el volumen después vería un
    # archivo "válido" pero sin las tablas o sin los datos recientes.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    # Bloqueante a propósito: si la barrida termina antes que el vigilante
    # (lo normal, la barrida es más rápida que las 3.7h del vigilante), acá
    # se espera. Cuesta lo mismo en cómputo de Modal — el contenedor ya está
    # pagado por las 4h igual — y evita comprometer el archivo con un commit
    # mientras el otro hilo todavía escribe.
    hilo_caliente.join()

    volumen.commit()      # sin esto el historial NO persiste entre corridas

    # Los días 1 y 15 se aprovecha esta misma corrida para refrescar las
    # referencias. Va DESPUÉS del commit para no arriesgar la barrida si
    # la recalibración falla.
    import datetime
    hoy = datetime.datetime.now()

    # Lunes: buscar productos nuevos en los sitemaps.
    if hoy.weekday() == 0 and hoy.hour < 4:
        try:
            refrescar_catalogo.local()
        except Exception as e:                          # noqa: BLE001
            print("descubrimiento falló (la barrida sí quedó): %s" % str(e)[:90])

    # Días 1 y 15: refrescar las referencias con el historial acumulado.
    if _toca_recalibrar():
        try:
            recalibrar.local()
        except Exception as e:                          # noqa: BLE001
            print("recalibración falló (la barrida sí quedó): %s" % str(e)[:90])
    return len(hallazgos)


@app.function(
    image=imagen,
    volumes={"/datos": volumen},
    # Sin `schedule`: la dispara `barrer` los lunes. Mismo motivo que
    # `recalibrar` — el plan gratis de Modal solo permite 5 crons en total.
    timeout=60 * 45,
)
def refrescar_catalogo():
    _preparar()
    import baseprecios
    import vigia

    con = baseprecios.abrir()
    n = vigia.descubrir_productos(con)
    print("fichas nuevas: %d" % n)
    print("estado:", baseprecios.estadisticas(con))
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    volumen.commit()


def _toca_recalibrar():
    """¿Hoy es día de refrescar las referencias? (1 y 15 de cada mes)

    Vive dentro de `barrer` en vez de tener su propio cron porque el plan
    gratis de Modal permite 5 funciones programadas en total, y Steve ya usa
    dos. Recalibrar es rápido y corre dos veces al mes: no justifica gastar
    uno de los cupos.
    """
    import datetime
    return datetime.datetime.now().day in (1, 15)


@app.function(
    image=imagen,
    volumes={"/datos": volumen},
    # Sin `schedule`: la dispara `barrer` los días 1 y 15. Ver _toca_recalibrar.
    #
    # Recalibrar seguido no conviene: la referencia debe moverse despacio,
    # porque si sigue al precio del día se "acostumbra" a una baja y deja de
    # verla como caída.
    timeout=60 * 45,
    secrets=SECRETOS,
)
def recalibrar():
    """Refresca la línea base con la mediana del historial ya acumulado.

    OJO — esto NO es lo que hace que el sistema detecte. La detección funciona
    desde la SEGUNDA barrida: la primera fija la referencia y la segunda ya
    compara contra ella. Si algo vale $20.000 y cae a $5.000, se avisa a las
    4 horas, sin esperar ningún historial.

    Esto solo MEJORA la referencia. Su punto débil es que la foto del día uno
    pudo tomarse con el producto en oferta; con la mediana de semanas de
    historial esa distorsión se corrige sola.
    """
    _preparar()
    import time

    import alertas
    import baseprecios

    con = baseprecios.abrir()

    f = con.execute("SELECT MIN(visto_en) t FROM precios WHERE visto_en > 0").fetchone()
    if not f or not f["t"]:
        print("Todavía no hay observaciones: nada que recalibrar.")
        con.close()
        return

    dias = (time.time() - f["t"]) / 86400
    tocados = baseprecios.recalcular_bases(con)
    print("días de historial: %.1f · líneas base recalculadas: %d" % (dias, tocados))

    if not tocados:
        print("Ningún producto tiene aún historial suficiente.")
        con.close()
        return

    e = baseprecios.estadisticas(con)
    alertas._enviar(
        "🔄 <b>Héctor — recalibración</b>\n\n"
        "Historial acumulado: <b>%.0f días</b>\n"
        "Referencias actualizadas: <b>%s</b>\n\n"
        "Productos vigilados: %s\n"
        "Observaciones acumuladas: %s\n\n"
        "<i>Las referencias ahora salen de la mediana del historial real.</i>"
        % (dias,
           format(tocados, ",d").replace(",", "."),
           format(e["vigilados"], ",d").replace(",", "."),
           format(e["observaciones"], ",d").replace(",", ".")))

    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    volumen.commit()


##############################################################################
# `vigilar_caliente` como función Modal aparte SE ELIMINÓ a propósito el
# 7-ago-2026. Existió antes, lanzada con `.spawn()` desde `barrer` — es decir,
# en OTRO contenedor, escribiendo el MISMO archivo SQLite que la barrida al
# mismo tiempo. Un Volumen de Modal no da bloqueo real entre contenedores:
# dos `commit()` concurrentes al mismo archivo lo CORROMPIERON de verdad.
#
# El vigilante ahora corre como un hilo DENTRO de `barrer` (ver más arriba).
# Se elimina la función completa, y no solo se deja de llamar, porque una
# función Modal desplegada se puede invocar a mano (`modal run` o desde otro
# script) sin querer — y si alguien la corre mientras `barrer` está activo,
# recrea el bug exacto que se acaba de arreglar.
##############################################################################


@app.function(image=imagen, volumes={"/datos": volumen}, timeout=60 * 2)
def reiniciar_db():
    """Reconstruye precios.db desde cero. SOLO para corrupción irrecuperable.

    Se usa una vez, el 7-ago-2026, después de que dos contenedores Modal
    escribieran el mismo archivo a la vez y lo corrompieran (ver el fix en
    `barrer`: el vigilante ahora corre como hilo del mismo contenedor, no
    como función separada). `integrity_check` ni siquiera podía leer el
    archivo, así que no había nada que rescatar — y la tabla `alertas` seguía
    vacía, o sea nunca se perdió ninguna alerta real, solo el historial de
    precios de el día y medio que llevaba corriendo.
    """
    import os

    borrados = []
    for sufijo in ("", "-wal", "-shm", "-journal"):
        ruta = "/datos/precios.db" + sufijo
        if os.path.exists(ruta):
            os.remove(ruta)
            borrados.append(ruta)

    import baseprecios
    con = baseprecios.abrir()      # recrea el esquema limpio
    stats = baseprecios.estadisticas(con)
    # Con journal_mode=WAL, el esquema recién creado puede quedar viviendo en
    # el archivo -wal sin haberse volcado todavía al .db principal. Si
    # `volumen.commit()` sube los archivos en ese momento y OTRO contenedor
    # monta el volumen después, hay ambigüedad de orden — se vio en vivo:
    # integrity_check daba "ok" (el archivo es válido) pero "no such table"
    # (las tablas no estaban ahí). El checkpoint fuerza que todo quede en el
    # .db principal ANTES de subirlo, sin depender de ese orden.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    volumen.commit()
    return {"borrados": borrados, "estadisticas_tras_reiniciar": stats}


@app.function(image=imagen, volumes={"/datos": volumen}, timeout=60 * 5)
def integridad_db():
    """PRAGMA integrity_check crudo, sin pasar por baseprecios.abrir().

    Existe para diagnosticar el 'database disk image is malformed' que salió
    en estado_produccion: hay que saber si es corrupción total o parcial
    ANTES de decidir si se recupera o se reconstruye desde cero.
    """
    import sqlite3
    con = sqlite3.connect("/datos/precios.db", timeout=30)
    try:
        filas = con.execute("PRAGMA integrity_check").fetchall()
    except Exception as e:                       # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    resultado = [f[0] for f in filas]
    ok = resultado == ["ok"]

    conteos = {}
    for tabla in ("precios", "linea_base", "alertas", "fallos", "marcadores"):
        try:
            conteos[tabla] = con.execute(
                "SELECT COUNT(*) FROM %s" % tabla).fetchone()[0]
        except Exception as e:                    # noqa: BLE001
            conteos[tabla] = "error: %s" % str(e)[:80]
    con.close()
    return {"ok": ok, "detalle": resultado[:10], "conteos": conteos}


@app.function(image=imagen, volumes={"/datos": volumen}, timeout=60 * 5)
def estado_produccion():
    """Estado real de la base de PRODUCCIÓN: no una estimación, lo que hay.

    Existe para responder "¿está funcionando de verdad?" con datos de la base
    que usa el sistema en vivo, no con la capacidad teórica ni con logs de
    Modal (que solo guardan una ventana reciente y se pueden leer mal si se
    mezclan dos corridas).
    """
    _preparar()
    import time

    import baseprecios
    import vigilante

    con = baseprecios.abrir()
    stats = baseprecios.estadisticas(con)

    # La lista caliente REAL ahora mismo, con los precios que ya se conocen
    # — no la capacidad máxima teórica.
    por_tienda = vigilante.cargar_lista(con)
    caliente = {t: len(u) for t, u in por_tienda.items()}

    # Últimas alertas reales enviadas (si las hay), y el marcador de
    # rotación de la barrida grande, que prueba que avanza sola.
    alertas_recientes = con.execute(
        "SELECT url, tipo, precio, referencia, caida, avisado_en "
        "FROM alertas ORDER BY avisado_en DESC LIMIT 10").fetchall()
    marcador = con.execute(
        "SELECT valor FROM marcadores WHERE clave='rotacion'").fetchone()

    # Lecturas de precio en la última hora: si este número es 0, el sistema
    # está detenido aunque todo lo demás se vea bien.
    hace_1h = int(time.time()) - 3600
    lecturas_1h = con.execute(
        "SELECT COUNT(*) n FROM precios WHERE visto_en > ?", (hace_1h,)
    ).fetchone()["n"]

    # Una muestra real (no inventada) para poder probar la cadena de
    # detección con un precio que de verdad está guardado ahora mismo.
    muestra = con.execute(
        "SELECT tienda, url, nombre, precio FROM precios "
        "WHERE precio > 50000 AND tienda='falabella.com' "
        "ORDER BY visto_en DESC LIMIT 3").fetchall()

    con.close()
    return {
        "estadisticas": stats,
        "lista_caliente_real": caliente,
        "lista_caliente_total": sum(caliente.values()),
        "lecturas_ultima_hora": lecturas_1h,
        "alertas_recientes": [dict(a) for a in alertas_recientes],
        "marcador_rotacion": marcador["valor"] if marcador else 0,
        "muestra_real": [dict(m) for m in muestra],
    }


@app.function(image=imagen, timeout=60 * 10)
def diagnosticar_tienda(dominios: list):
    """Por qué una tienda da 0 fichas DESDE MODAL aunque funcione en el PC.

    Existe porque pasó de verdad: Ripley y Tottus devolvían más de un millón de
    fichas desde la casa y cero desde Modal. La causa candidata es la IP —
    Modal sale por un centro de datos, y varios WAF tratan esas IPs distinto
    que a una conexión residencial. Sin este diagnóstico uno lo atribuye al
    código y va a buscar el bug donde no está.
    """
    _preparar()
    import descubrir

    # Devuelve en vez de imprimir: los logs de Modal se entremezclan entre
    # corridas y es facilísimo leer el resultado de la corrida anterior y
    # concluir mal.
    salida = {}
    # Una URL de ficha conocida por tienda: sirve para separar "no puedo
    # descubrir su catálogo" de "no puedo leer sus precios". Son problemas
    # distintos: lo primero se arregla descubriendo desde otro lado, lo
    # segundo no tiene vuelta sin cambiar de IP.
    FICHA_CONOCIDA = {
        "ripley.cl": "https://simple.ripley.cl/control-ps5-dualsense-sony-blanco-2000380632868p",
    }
    for dom in dominios:
        if dom in FICHA_CONOCIDA:
            try:
                html = descubrir.bajar(FICHA_CONOCIDA[dom], tiempo=25)
                salida[dom + " (ficha)"] = {"bytes": len(html),
                                            "tiene_precio": '"price"' in html}
            except Exception as e:                    # noqa: BLE001
                salida[dom + " (ficha)"] = {"error": str(e)[:70]}
        try:
            raices = descubrir.ubicar_sitemap(dom)
            fichas = descubrir.fichas_de(dom, tope=50) if raices else []
            salida[dom] = {"sitemaps": raices[:2], "n_sitemaps": len(raices),
                           "fichas": len(fichas), "ejemplo": fichas[0][:80] if fichas else ""}
        except Exception as e:                        # noqa: BLE001
            salida[dom] = {"error": "%s: %s" % (type(e).__name__, str(e)[:80])}
    return salida


@app.local_entrypoint()
def probar():
    """modal run modal_app.py — una barrida ahora, sin esperar al cron."""
    n = barrer.remote()
    print("hallazgos: %s" % n)
