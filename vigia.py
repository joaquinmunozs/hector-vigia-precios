# -*- coding: utf-8 -*-
"""Barre los productos vigilados, guarda precios y avisa los hallazgos.

    python vigia.py --descubrir      # llena el catálogo desde los sitemaps
    python vigia.py                  # una barrida completa
    python vigia.py --sin-avisar     # barre pero no manda nada a Telegram
    python vigia.py --limite 200     # solo N productos (para probar)
    python vigia.py --estado         # cuánto historial hay acumulado

CONCURRENCIA
------------------------------------------------------------------------------
Se leen HILOS productos a la vez. Medido en vivo: 14 productos/segundo con 16
hilos, contra sitios que responden bien. Sin concurrencia, un catálogo de
157.000 productos tomaría más de 100 horas por barrida — inviable.

El límite no es la CPU (esto es puro esperar red), sino la cortesía: demasiadas
peticiones simultáneas contra la MISMA tienda es lo que gatilla un bloqueo. Por
eso el trabajo se reparte por tienda, no en un solo montón: así ninguna recibe
más de unas pocas conexiones a la vez.

LA BASE ES DE UN SOLO HILO
------------------------------------------------------------------------------
SQLite en modo WAL aguanta lecturas concurrentes, pero las escrituras se hacen
todas desde el hilo principal, con los resultados que van llegando. Escribir
desde 16 hilos a la vez es la receta para 'database is locked'.
"""
import argparse
import queue
import random
import sys
import threading
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import adaptadores
import alertas
import baseprecios
import descubrir
import extractor
import tiendas as cat_tiendas
# Solo por `_hilos_para` y la tabla de ritmos seguros. No hay ciclo:
# `vigilante` no importa `vigia`.
import vigilante

# Tope de conexiones simultáneas contra UNA misma tienda.
#
# MEDIDO EN VIVO con medir_limites.py (modo prudente, 7-ago-2026): NINGUNA
# tienda bloqueó ni a 20 hilos, con 100% de éxito y sin degradar el tiempo de
# respuesta. Los ritmos seguros (con 40% de margen bajo el último escalón
# sano) fueron:
#     Falabella (API)  86 req/s   ← la API ni se inmuta
#     Tottus           44 req/s
#     Adidas           30 req/s
#     Ripley          6,6 req/s   ← HTML de 200 KB, lento; su API volaría
#
# El 8 anterior era muy conservador. Se sube a 15: sigue holgado bajo el 20
# que todas toleraron, y casi duplica el rendimiento por tienda. El límite no
# es la máquina (esto es puro esperar red) sino no llamar la atención de un
# WAF — por eso NO se llega al 20 medido, se deja margen.
# ── El cuello de botella, medido el 11-ago-2026 ──────────────────────────
#
# Con los ritmos ya medidos, las 24 tiendas permiten en conjunto 649 peticiones
# por segundo. El código estaba capado en 60 hilos, que a media de 0,5 s de
# respuesta son ~80 req/s: el límite dejó de ser la tienda y pasó a ser
# nuestro. La última barrida hizo 150.000 fichas en 180 min — 13,9 req/s, un
# 2% de lo que las tiendas aguantan.
#
# Se sube a 200 hilos globales y 40 por tienda. Eso da un techo de ~270 req/s,
# todavía MUY por debajo de los 649 permitidos: el limitador por tienda sigue
# siendo el que manda, y es el que protege de un bloqueo. Los hilos solo dejan
# de estorbar.
#
# Con eso el catálogo completo (439.375 fichas) cabe en una sola barrida de
# menos de una hora, así que TOPE_BARRIDA sube para no dejar nada fuera por
# rotación.
HILOS_POR_TIENDA = 40
HILOS_TOTAL = 200          # tope global, sumando todas las tiendas
PAUSA = 0.25              # entre peticiones del mismo hilo


def _bajar(url, cabeceras=None):
    # Se delega en descubrir.bajar para heredar su reintento con el TLS de
    # Chrome imitado: tiendas como adidas.cl responden 403 a urllib y 200 al
    # disfraz, con la misma IP. Duplicar la descarga acá dejaba esas tiendas
    # fuera de la barrida aunque el descubrimiento sí las viera.
    #
    # tiempo=8, no 20 (bajado 10-ago-2026): con 60 hilos, una tienda que
    # empieza a bloquear/tirar el runner a medio camino deja cada hilo
    # colgado 20 s por request fallida — eso fue lo que hizo caer el
    # throughput de 40+/seg a 0,2/seg a mitad de una barrida real (ver
    # `segundos_max` en `barrida`, más abajo, para el resto de la historia).
    # 8 s sigue siendo holgado contra un sitio sano (los tiempos de
    # respuesta medidos rondan 1-2 s) y corta 2,5x más rápido el costo de
    # uno que no responde.
    return descubrir.bajar(url, tiempo=8, cabeceras=cabeceras)


def leer(tienda, url):
    """Precio de una ficha: primero el lector a medida, si esa tienda tiene.

    Falabella y compañía no publican el precio en el HTML, así que el
    extractor genérico nunca lo encontraría por mucho que se le insista.
    """
    especial = adaptadores.para(tienda)
    if especial:
        d = especial(url, _bajar)
        if d:
            return d
    return extractor.extraer(_bajar(url))


# ── TRES DESENLACES, NO DOS (12-ago-2026) ──────────────────────────────────
#
# Antes todo lo que no fuera 404/410 caía en "falla", y cada falla acercaba la
# URL a que la borraran. Eso mezcla dos cosas que se corrigen al revés:
#
#   sin_precio -> la página se bajó bien y no traía precio. Casi siempre es una
#                 categoría o una landing que el sitemap incluyó: basura de
#                 verdad, y sacarla del catálogo está bien.
#   rechazo    -> la tienda no nos dejó leer (401/403/429/5xx) o se cortó la
#                 red. La ficha está perfecta; el que falla es el acceso.
#                 Borrarla es perder catálogo bueno por un problema nuestro.
#
# No es teórico. El 11-ago el catálogo cayó de 439.375 a 360.863 fichas en un
# día (−78.512) y el 66,7% de Falabella —la tienda que MEJOR mide, 72% de
# cobertura— quedó a una racha de morir. Y desde el 12-ago tottus responde 403
# a todo: con el criterio viejo, sus fichas se irían borrando solas por estar
# bloqueadas, y al desbloquearse no habría catálogo al que volver.
#
# Está afuera de `barrida` para poder probarla: metida en el closure no había
# manera de verificar el mapeo sin levantar hilos y una tienda falsa.
RECHAZO_HTTP = (401, 403, 429, 500, 502, 503, 504)


def desenlace(exc):
    """Qué significa esta excepción: 'muerta', 'rechazo' o 'sin_precio'."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (404, 410):
            return "muerta"
        return "rechazo" if exc.code in RECHAZO_HTTP else "sin_precio"
    # Timeout, DNS, conexión cortada: nada de esto dice nada sobre la URL, así
    # que no puede contar para borrarla. URLError va PRIMERO que OSError porque
    # hereda de él, y el orden de un isinstance encadenado sí importa.
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return "rechazo"
    # Lo que queda es el lector: no encontró precio en algo que sí se bajó.
    return "sin_precio"


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


# ─────────────────────────────── descubrir ──────────────────────────────────
def _marcador_descubrir(con, dominio, valor=None):
    """Por cuál sitemap hijo le toca empezar a esta tienda la próxima vez.

    Vive en la misma tabla `marcadores` que la rotación de la barrida y la del
    vigilante, con prefijo `desc_` para no pisarlas.
    """
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    clave = "desc_" + dominio
    if valor is None:
        f = con.execute("SELECT valor FROM marcadores WHERE clave=?",
                        (clave,)).fetchone()
        return f["valor"] if f else 0
    con.execute("INSERT INTO marcadores (clave, valor) VALUES (?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (clave, int(valor)))
    con.commit()
    return valor


def descubrir_productos(con, niveles=("limpia", "media"), tope_tienda=100000):
    """Registra todas las fichas que publiquen los sitemaps."""
    total = 0
    for t in cat_tiendas.por_nivel(*niveles):
        dom = t["dominio"]
        if dom in cat_tiendas.SOLO_CON_NAVEGADOR:
            print("  %-20s se salta: el precio lo pinta JavaScript" % dom)
            continue
        # Por dónde le tocó empezar a esta tienda. Sin esto, una tienda más
        # grande que `tope_tienda` se redescubre idéntica cada semana y el
        # resto de su catálogo no entra nunca — Falabella lleva 100.000 de
        # ~1,5M por exactamente esto. Ver `desde_sitemap`.
        desp = _marcador_descubrir(con, dom)
        try:
            fichas = descubrir.fichas_de(dom, tope=tope_tienda,
                                         desplazamiento=desp)
        except Exception as e:                       # noqa: BLE001
            print("  %-20s error al descubrir: %s" % (dom, str(e)[:45]))
            continue
        # Avanza sólo si la tienda LLENÓ el tope: si cabe entera, rotar la
        # haría empezar por el medio sin ganar nada.
        if len(fichas) >= tope_tienda:
            _marcador_descubrir(con, dom, desp + 1)

        # Las que ya se comprobó que no son fichas NO vuelven a entrar, salvo
        # que hayan pasado DIAS_REINTENTAR_DESCARTADA. Sin esto, cada lunes se
        # remetían las mismas URLs muertas y se volvían a pagar 6 lecturas
        # fallidas por cada una — ver la tabla `descartadas`.
        corte_desc = int(time.time()) - baseprecios.DIAS_REINTENTAR_DESCARTADA * 86400
        nuevas, saltadas = 0, 0
        for u in fichas:
            cur = con.execute(
                "INSERT INTO precios (tienda, url, nombre, precio, visto_en) "
                "SELECT ?,?,'',0,0 WHERE NOT EXISTS "
                "(SELECT 1 FROM precios WHERE url=?) "
                "AND NOT EXISTS (SELECT 1 FROM descartadas "
                "                WHERE url=? AND cuando > ?)",
                (dom, u, u, u, corte_desc))
            if cur.rowcount:
                nuevas += 1
            else:
                saltadas += 1
        con.commit()
        print("  %-20s %6d fichas (%d nuevas%s)"
              % (dom, len(fichas), nuevas,
                 ", %d ya descartadas antes" % saltadas if saltadas else ""))
        total += nuevas
    return total


# ──────────────────────────────── barrida ───────────────────────────────────
# Cuántos productos entran en UNA barrida. Con Falabella (1,5M) y Ripley (1,2M)
# el catálogo son ~3 millones. A los 14 productos/segundo medidos en vivo (ver
# arriba), el tope tiene que caber en el cron de 4 h con margen: 250.000 ya se
# pasaba (250.000/14 ≈ 4,96 h, MÁS que el propio cron). 150.000/14 ≈ 2,98 h,
# deja ~1 h de margen si alguna tienda responde más lento ese día.
#
# No hay dato de "popularidad" o "más vendido": el scraper solo trae nombre y
# precio (ver `extractor.py`), así que no hay cómo priorizar por eso sin
# scrapear campos nuevos. El precio ya es el mejor proxy que existe — un
# error en algo caro es lo que de verdad vale la suscripción.
#
# El tope solo NO basta, porque deja fuera para siempre a lo que quede bajo el
# corte. Por eso va con rotación (ver `_objetivos`): los caros se revisan en
# cada pasada y el resto va rotando, así nada queda sin mirar indefinidamente.
TOPE_BARRIDA = 450_000

# De ese tope, qué parte se reserva a los más caros (el resto rota). Un error
# en un iPhone vale muchísimo más que uno en un paño de cocina, así que los
# caros no ceden su lugar nunca; lo que rota es la cola.
FRACCION_CAROS = 0.6


def _marcador(con, valor=None):
    """Dónde quedó la ventana rotativa la vez anterior.

    Se guarda en la misma base para que sobreviva al reinicio del contenedor:
    en Modal cada barrida corre en una máquina nueva y limpia, así que una
    variable en memoria se perdería entre pasadas y la rotación nunca
    avanzaría — volvería siempre a empezar por el mismo punto.
    """
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    if valor is None:
        f = con.execute(
            "SELECT valor FROM marcadores WHERE clave='rotacion'").fetchone()
        return f["valor"] if f else 0
    con.execute("INSERT INTO marcadores (clave, valor) VALUES ('rotacion', ?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (int(valor),))
    con.commit()
    return valor


def _con_rotacion(con, ordenados, tope):
    """Los más caros + una ventana que avanza sobre el resto en cada barrida."""
    n_caros = int(tope * FRACCION_CAROS)
    caros = ordenados[:n_caros]
    cola = ordenados[n_caros:]
    if not cola:
        return caros

    cupo_cola = tope - n_caros
    desde = _marcador(con) % len(cola)

    # La ventana da la vuelta al final de la lista, por eso se concatena: sin
    # esto, la última tajada quedaría corta y los primeros de la cola se
    # revisarían más seguido que los últimos.
    if desde + cupo_cola <= len(cola):
        ventana = cola[desde:desde + cupo_cola]
    else:
        ventana = cola[desde:] + cola[:(desde + cupo_cola) - len(cola)]

    _marcador(con, (desde + cupo_cola) % len(cola))

    vueltas = len(cola) / max(1, cupo_cola)
    print("  rotación: %d caros fijos + %d de cola (desde %d/%d, "
          "vuelta completa cada %.1f barridas ≈ %.1f h)"
          % (len(caros), len(ventana), desde, len(cola), vueltas, vueltas * 4))
    return caros + ventana


def _objetivos(con, limite=None):
    """Qué revisar en esta barrida: los más caros SIEMPRE + una tajada rotativa.

    Se ordena por el ÚLTIMO PRECIO CONOCIDO, de mayor a menor: un error en algo
    de $1.500.000 vale muchísimo más que en algo de $2.000, y es lo que hace
    que alguien pague por el aviso.

    LA ROTACIÓN, Y POR QUÉ IMPORTA
    --------------------------------------------------------------------------
    Un tope fijo tiene un modo de falla feo: como el orden es siempre el mismo,
    lo que cae bajo el corte no se revisa jamás, y encima en silencio. Acá el
    60% del cupo va a los más caros (que se revisan siempre) y el 40% restante
    avanza por el resto del catálogo como una ventana corrediza, retomando
    donde quedó la vez anterior.

    Así, si algún día el catálogo crece o las tiendas responden más lento, se
    pierde VELOCIDAD DE ROTACIÓN — que se nota y se corrige — en vez de perder
    productos completos sin que nadie se entere.
    """
    # ── EL REPARTO DE ROLES (12-ago-2026) ──────────────────────────────────
    #
    # Desde que el vigilante cubre TODO el catálogo con precio conocido (ver
    # `cargar_lista` en vigilante.py), refrescar fichas ya medidas dejó de ser
    # trabajo de la barrida: el vigilante las relee cada 59 s las caras y cada
    # ~96 min el resto, mucho más seguido de lo que la barrida podría.
    #
    # Lo que el vigilante NO puede hacer es incorporar una ficha nueva: su
    # lista sale de `WHERE precio > 0`, así que una ficha descubierta pero
    # nunca medida es invisible para él. Solo la barrida puede darle esa
    # primera lectura, y son 185.651 fichas — la mitad del catálogo.
    #
    # Por eso el orden ahora pone PRIMERO lo que nunca se midió. Antes iba
    # `ORDER BY p.tienda, ref DESC`, que además tenía un efecto no buscado:
    # como ordenaba por tienda ANTES que por precio, los "caros" que
    # `_con_rotacion` reserva no eran los caros del catálogo sino las
    # primeras tiendas del alfabeto — el docstring de acá abajo prometía un
    # orden por precio global que el SQL nunca hizo.
    filas = con.execute("""
        SELECT p.tienda, p.url,
               COALESCE(MAX(CASE WHEN p.precio > 0 THEN p.precio END), -1) AS ref
        FROM precios p
        GROUP BY p.url
        ORDER BY (ref > 0) ASC, ref DESC
    """).fetchall()
    objetivos = [(f["tienda"], f["url"]) for f in filas]
    sin_medir = sum(1 for f in filas if f["ref"] < 0)
    if sin_medir:
        print("  %d fichas sin medir van primero (son las que el vigilante "
              "todavía no puede ver)" % sin_medir)

    tope = limite or TOPE_BARRIDA
    if tope and len(objetivos) > tope:
        objetivos = _con_rotacion(con, objetivos, tope)
        return objetivos

    if limite:
        # Muestra repartida entre tiendas, no las primeras N de una sola.
        por_tienda = {}
        for t, u in objetivos:
            por_tienda.setdefault(t, []).append(u)
        cupo = max(1, limite // max(1, len(por_tienda)))
        objetivos = [(t, u) for t, us in por_tienda.items() for u in us[:cupo]]
    return objetivos


def barrida(con, avisar=True, limite=None, segundos_max=None):
    """`segundos_max`: corta la barrida ahí aunque queden productos sin ver.

    Agregado 10-ago-2026 tras encontrar corridas reales donde el throughput
    medido (14/seg) se desplomó a 0,2/seg a mitad de camino — una tienda
    empezó a bloquear/tirar el runner de GitHub Actions y cada hilo quedó
    colgado en el timeout de red en vez de fallar rápido. Sin este tope, la
    barrida entera se quedaba corriendo hasta que GitHub la mataba a los 350
    min, y `correr.py` nunca alcanzaba a hacer el checkpoint ni a esperar al
    vigilante — la corrida completa se perdía y encima se acumulaba una cola
    de corridas siguientes esperando el mismo cupo de concurrencia.
    Cortar aquí es exactamente la misma filosofía que ya tiene la rotación
    de `_objetivos`: si un día las cosas van lentas, se pierde VELOCIDAD (se
    revisan menos productos esta vez), no la corrida completa."""
    objetivos = _objetivos(con, limite)
    if not objetivos:
        print("Sin productos. Corre primero:  python vigia.py --descubrir")
        return []

    por_tienda = {}
    for t, u in objetivos:
        por_tienda.setdefault(t, []).append(u)

    print("Barriendo %d productos de %d tiendas...\n" % (len(objetivos), len(por_tienda)))
    resultados = queue.Queue()
    inicio = time.time()

    def trabajar(tienda, urls):
        for u in urls:
            try:
                d = leer(tienda, u)
                resultados.put(("ok", tienda, u, d))
            except Exception as e:                   # noqa: BLE001
                resultados.put((desenlace(e), tienda, u, None))
            time.sleep(PAUSA * random.uniform(0.7, 1.3))

    # Los hilos se reparten PROPORCIONALMENTE al tamaño de cada tienda, no en
    # partes iguales. Con reparto parejo, SPDigital (65.000 productos) recibía
    # el mismo hilo que Winner (140) y tardaba 15 horas mientras la otra
    # terminaba en un minuto: la barrida completa duraba lo que la tienda más
    # lenta, y se pasaba del timeout.
    hilos = []
    total_urls = sum(len(u) for u in por_tienda.values())
    for tienda, urls in por_tienda.items():
        cuota = len(urls) / max(1, total_urls)
        n = int(HILOS_TOTAL * cuota)
        n = max(1, min(HILOS_POR_TIENDA, n))
        # ── El reparto también respeta el RITMO SEGURO de cada tienda ──────
        #
        # Repartir solo por TAMAÑO es lo que rompía spdigital: con 64.903
        # fichas se llevaba ~35 hilos por ser grande, sin mirar que su ritmo
        # medido es de los más bajos. Resultado: 51.876 URLs en `fallos` y
        # 799 medidas (1,2%).
        #
        # Verificado el 12-ago-2026: 20 de esas URLs "fallidas", leídas de a
        # una y con 1,2 s entre medio, dieron 18 buenas. Las 2 restantes eran
        # `/categories/`, que no son fichas. O sea las páginas se leen bien y
        # lo que fallaba era la CARGA, no el extractor (que era la hipótesis
        # del CONTEXTO) ni la falta de turno.
        #
        # `vigilante._hilos_para` ya sabe cuántos hilos sostienen el ritmo
        # seguro de cada tienda; se reusa en vez de duplicar la tabla.
        n = max(1, min(n, vigilante._hilos_para(tienda)))
        for i in range(n):
            h = threading.Thread(target=trabajar, args=(tienda, urls[i::n]), daemon=True)
            h.start()
            hilos.append(h)
    print("  (%d hilos repartidos entre %d tiendas)\n" % (len(hilos), len(por_tienda)))

    hallazgos, leidos, fallas, muertas, procesados = [], 0, 0, 0, 0
    descartadas = 0
    # Los rechazos van aparte de `fallas` a propósito: no acercan la URL a que
    # la borren, pero tienen que VERSE. Un rechazo que no se cuenta es un
    # bloqueo que nadie descubre hasta que la tienda lleva semanas en cero.
    rechazos = 0
    por_tienda_rechazo = {}
    total = len(objetivos)

    while procesados < total:
        if segundos_max and (time.time() - inicio) > segundos_max:
            # Ojo con lo que dice acá: la rotación solo existe si el catálogo
            # pasa de TOPE_BARRIDA (si no, `_objetivos` la salta y no hay
            # marcador que guarde dónde quedó). Cuando NO hay rotación, la
            # próxima barrida empieza otra vez por el principio — que con el
            # orden actual son las fichas sin medir, así que se retoma lo que
            # importa igual, pero por el orden, no por la rotación.
            print("  (tope de %.1f h alcanzado, se corta con %d/%d — %s)"
                  % (segundos_max / 3600, procesados, total,
                     "la próxima barrida retoma por la rotación"
                     if len(objetivos) >= TOPE_BARRIDA
                     else "la próxima vuelve a empezar por las fichas sin medir"))
            break
        try:
            estado, tienda, url, d = resultados.get(timeout=120)
        except queue.Empty:
            print("  (sin respuestas en 2 min, se corta)")
            break
        procesados += 1

        if estado == "muerta":
            baseprecios.olvidar_url(con, url)
            muertas += 1
        elif estado == "rechazo":
            # La tienda no nos dejó leer. NO se anota como fallo de la URL: la
            # ficha está bien y borrarla sería castigarla por un bloqueo o una
            # mala tarde de la tienda. Se cuenta aparte para que se vea.
            rechazos += 1
            por_tienda_rechazo[tienda] = por_tienda_rechazo.get(tienda, 0) + 1
        elif estado == "sin_precio":
            # La página se bajó y no tenía precio. Eso sí hace sospechar de la
            # URL: casi siempre es una categoría o una landing del sitemap.
            if baseprecios.anotar_fallo(con, url):
                baseprecios.olvidar_url(con, url)
                descartadas += 1
            fallas += 1
        else:
            precio = d["precio"]
            # SIN STOCK NO SE AVISA, aunque el precio haya caído un 90%.
            # Un producto agotado suele conservar su último precio publicado, o
            # mostrar uno raro, y avisar de algo que nadie puede comprar es la
            # forma más rápida de que un suscriptor silencie el canal. El
            # precio SÍ se guarda: sirve para el historial, y cuando el
            # producto vuelva a tener stock se evalúa con las reglas normales.
            # El nombre y la tienda van SIEMPRE: sin ellos no se puede
            # clasificar el producto y los tópicos de Electrónicos y Hogar
            # se quedan sin nada entre 35% y 50%.
            det = (baseprecios.evaluar(con, url, precio,
                                       nombre=d["nombre"], tienda=tienda)
                   if d.get("hay_stock", True) else None)
            baseprecios.guardar(con, tienda, url, d["nombre"], precio,
                                imagen=d.get("imagen"))
            # Primera lectura del producto: se fija como su referencia inicial.
            if not baseprecios._base_de(con, url):
                baseprecios.fijar_base(con, url, precio, "inicial")
            baseprecios.limpiar_fallo(con, url)
            leidos += 1
            if det:
                det.update({"tienda": tienda, "nombre": d["nombre"]})
                hallazgos.append(det)
                print("  %s %-16s %s → %s (-%.0f%%)" % (
                    "🚨" if det["tipo"] == baseprecios.ERROR else "🏷️",
                    tienda, _plata(det["referencia"]), _plata(precio),
                    det["caida"] * 100))

        # Commit por producto, no cada 250. Con 250 el lock de escritura
        # quedaba tomado ~35 s (250 fichas a ~7/seg) mientras el vigilante,
        # que escribe en paralelo, agotaba sus 30 s de `timeout` y moría con
        # "database is locked". Lo que importa no es cuántos commits hay: es
        # cuánto rato queda tomado el lock. Ver baseprecios.abrir
        # (synchronous=NORMAL) — con eso el commit en WAL no paga fsync.
        con.commit()

        if procesados % 250 == 0:
            vel = procesados / max(1, time.time() - inicio)
            print("  ... %d/%d  (%.1f/seg, quedan ~%.0f min)" % (
                procesados, total, vel, (total - procesados) / max(vel, 0.1) / 60))

    con.commit()
    dur = time.time() - inicio
    print("\nleídos: %d · sin precio: %d · rechazos: %d · muertas: %d · "
          "hallazgos: %d  [%.1f min]"
          % (leidos, fallas, rechazos, muertas, len(hallazgos), dur / 60))
    if descartadas:
        print("   descartadas del catálogo: %d (por no traer precio, no por "
              "rechazo)" % descartadas)

    # Las tiendas que más rechazan, arriba. Es la lista de a quién hay que
    # bajarle el ritmo o pasar por el proxy — y la que avisa temprano cuando
    # una tienda empieza a bloquearnos, en vez de enterarse por el catálogo.
    if por_tienda_rechazo:
        peores = sorted(por_tienda_rechazo.items(), key=lambda x: -x[1])[:5]
        print("   rechazos por tienda: %s"
              % " · ".join("%s %d" % (t, n) for t, n in peores))

    errores = [h for h in hallazgos if h["tipo"] == baseprecios.ERROR]
    ofertas = [h for h in hallazgos if h["tipo"] == baseprecios.OFERTA]
    rebajas = [h for h in hallazgos if h["tipo"] == baseprecios.CATEGORIA]
    print("   errores de precio: %d · ofertas reales: %d · rebajas 35-50%%: %d"
          % (len(errores), len(ofertas), len(rebajas)))

    if hallazgos and avisar:
        n = alertas.enviar_hallazgos(con, hallazgos)
        print("   avisos enviados: %d" % n)
    return hallazgos


def main():
    p = argparse.ArgumentParser(description="Vigía de precios")
    p.add_argument("--descubrir", action="store_true")
    p.add_argument("--sin-avisar", action="store_true")
    p.add_argument("--limite", type=int)
    p.add_argument("--estado", action="store_true")
    args = p.parse_args()

    con = baseprecios.abrir()

    if args.estado:
        e = baseprecios.estadisticas(con)
        print("vigilados      : %d" % e["vigilados"])
        print("con precio leído: %d" % e["con_precio"])
        print("observaciones  : %d" % e["observaciones"])
        print("con línea base : %d" % e["con_base"])
        print("alertas        : %s" % (e["alertas"] or "ninguna"))
        return 0

    if args.descubrir:
        print("Descubriendo productos...\n")
        n = descubrir_productos(con)
        print("\n%d fichas nuevas." % n)
        print("estado:", baseprecios.estadisticas(con))
        return 0

    barrida(con, avisar=not args.sin_avisar, limite=args.limite)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
