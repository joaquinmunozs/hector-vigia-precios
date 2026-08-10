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
HILOS_POR_TIENDA = 15
HILOS_TOTAL = 60          # tope global, sumando todas las tiendas
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


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


# ─────────────────────────────── descubrir ──────────────────────────────────
def descubrir_productos(con, niveles=("limpia", "media"), tope_tienda=100000):
    """Registra todas las fichas que publiquen los sitemaps."""
    total = 0
    for t in cat_tiendas.por_nivel(*niveles):
        dom = t["dominio"]
        if dom in cat_tiendas.SOLO_CON_NAVEGADOR:
            print("  %-20s se salta: el precio lo pinta JavaScript" % dom)
            continue
        try:
            fichas = descubrir.fichas_de(dom, tope=tope_tienda)
        except Exception as e:                       # noqa: BLE001
            print("  %-20s error al descubrir: %s" % (dom, str(e)[:45]))
            continue

        nuevas = 0
        for u in fichas:
            cur = con.execute(
                "INSERT INTO precios (tienda, url, nombre, precio, visto_en) "
                "SELECT ?,?,'',0,0 WHERE NOT EXISTS "
                "(SELECT 1 FROM precios WHERE url=?)", (dom, u, u))
            nuevas += cur.rowcount
        con.commit()
        print("  %-20s %6d fichas (%d nuevas)" % (dom, len(fichas), nuevas))
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
TOPE_BARRIDA = 150_000

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
    filas = con.execute("""
        SELECT p.tienda, p.url,
               COALESCE(MAX(CASE WHEN p.precio > 0 THEN p.precio END), -1) AS ref
        FROM precios p
        GROUP BY p.url
        ORDER BY p.tienda, ref DESC
    """).fetchall()
    objetivos = [(f["tienda"], f["url"]) for f in filas]

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
            except urllib.error.HTTPError as e:
                resultados.put(("muerta" if e.code in (404, 410) else "falla",
                                tienda, u, None))
            except Exception:                        # noqa: BLE001
                resultados.put(("falla", tienda, u, None))
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
        for i in range(n):
            h = threading.Thread(target=trabajar, args=(tienda, urls[i::n]), daemon=True)
            h.start()
            hilos.append(h)
    print("  (%d hilos repartidos entre %d tiendas)\n" % (len(hilos), len(por_tienda)))

    hallazgos, leidos, fallas, muertas, procesados = [], 0, 0, 0, 0
    descartadas = 0
    total = len(objetivos)

    while procesados < total:
        if segundos_max and (time.time() - inicio) > segundos_max:
            print("  (tope de %.1f h alcanzado, se corta con %d/%d — la próxima "
                  "barrida retoma el resto por la rotación)"
                  % (segundos_max / 3600, procesados, total))
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
        elif estado == "falla":
            # Una URL que no da precio dos veces seguidas casi nunca es una
            # ficha: es una categoría o una landing que el sitemap incluyó.
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
            baseprecios.guardar(con, tienda, url, d["nombre"], precio)
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

        if procesados % 250 == 0:
            con.commit()
            vel = procesados / max(1, time.time() - inicio)
            print("  ... %d/%d  (%.1f/seg, quedan ~%.0f min)" % (
                procesados, total, vel, (total - procesados) / max(vel, 0.1) / 60))

    con.commit()
    dur = time.time() - inicio
    print("\nleídos: %d · sin precio: %d · muertas: %d · hallazgos: %d  [%.1f min]"
          % (leidos, fallas, muertas, len(hallazgos), dur / 60))

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
