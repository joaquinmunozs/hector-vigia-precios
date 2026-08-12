# -*- coding: utf-8 -*-
"""Vigilancia continua de la lista caliente. Detección en menos de un minuto.

    python vigilante.py                # corre hasta que lo cortes
    python vigilante.py --ciclos 3     # 3 vueltas y termina (para probar)
    python vigilante.py --sin-avisar   # no manda nada a Telegram

CÓMO SE DIFERENCIA DE LA BARRIDA
------------------------------------------------------------------------------
`vigia.py` recorre 250.000 productos una vez cada 4 horas. Este recorre miles
de productos una y otra vez, sin parar. Es la diferencia entre revisar todo
el catálogo de vez en cuando y tener a alguien mirando los productos
importantes todo el rato.

DOS SUB-NIVELES DENTRO DEL MISMO PRESUPUESTO (9-ago-2026)
------------------------------------------------------------------------------
"Errores de precio" y "ofertas" no tienen el mismo plazo de negocio: un error
(caída ≥70%) es el pilar del negocio y tiene que avisarse en menos de un
minuto; una oferta (35%-70%) alcanza con avisarse dentro de los 10 minutos.
Meterlos en una sola lista con un solo ritmo desperdicia presupuesto: o se
trata a las ofertas con la urgencia de un error (no cabe, son muchas más), o
se trata a los errores con la paciencia de una oferta (se pierde el pilar
del negocio).

La solución no es agregar MÁS peticiones — eso es justo lo que puede
bloquearnos — es **repartir el mismo presupuesto ya medido como seguro**
(`RITMO_SEGURO`) en dos partes:

  🔥 FIJA      ~70% del cupo · la lista caliente de `caliente.py` (imán +
               precio) · se revisa ENTERA cada vuelta (`VUELTA_OBJETIVO`,
               59 s) → detección de errores en menos de un minuto, siempre.
  🔁 ROTATIVA  ~30% del cupo · el resto del catálogo con precio conocido,
               ordenado por precio (los más caros primero, igual que la
               fija) · cada vuelta avanza una ventana, así que la lista
               COMPLETA queda cubierta dentro de `VENTANA_OFERTAS_SEG`
               (10 min) → detección de ofertas en minutos, no en horas.

El truco es el mismo `_con_rotacion` que ya usa `vigia.py` para la barrida
completa, aplicado a una escala de segundos en vez de horas: los caros fijos
siempre se miran, la cola rota, y el tamaño de la cola se calcula para que
la vuelta completa quepa en la ventana de 10 minutos — nunca al revés.

EL PRESUPUESTO DE PETICIONES
------------------------------------------------------------------------------
El límite no es nuestra máquina: es no incomodar a las tiendas. Los ritmos
seguros se midieron en vivo con `medir_limites.py` (7-ago-2026) y están en
RITMO_SEGURO. Se respeta uno por tienda, en paralelo entre tiendas.

Con miles de productos repartidos y esos ritmos, una vuelta completa toma
menos de un minuto. O sea: si un precio se cae, se detecta en la siguiente
vuelta, no en la siguiente barrida.

POR QUÉ NO GUARDA TODAS LAS LECTURAS
------------------------------------------------------------------------------
A esta frecuencia se leen millones de precios al día. Guardarlos todos
inflaría la base sin aportar nada: el historial que sirve para calcular la
referencia se construye con lecturas espaciadas, no con miles del mismo minuto.
Por eso solo se guarda cuando el precio CAMBIA respecto a la última lectura.
"""
import argparse
import math
import os
import queue
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import adaptadores
import alertas
import baseprecios
import caliente
import descubrir
import extractor

# Peticiones por segundo, por tienda. Es el 60% del último escalón donde la
# tienda respondió 100% sin degradar, así que deja margen antes de que un WAF
# se moleste.
#
# MEDIDO con medir_limites.py subiendo hasta 120 hilos (7-ago-2026). NINGUNA
# tienda bloqueó ni una sola vez, con 100% de éxito en todos los escalones:
#     falabella (API)  120 hilos → 148 req/s   (se satura la conexión, no ellos)
#     tottus           120 hilos →  82 req/s
#     adidas            90 hilos →  67 req/s   (a 120 se puso lenta)
#
# El techo de Falabella es de NUESTRA conexión, no suyo: a 90 hilos daba 159
# req/s y a 120 bajó a 148. Desde Modal, con mejor red, el número sube.
RITMO_SEGURO = {
    # Medidos con medir_limites.py contra una FICHA real de cada tienda
    # (11-ago-2026). El valor guardado es el 60% del ultimo escalon donde
    # la tienda respondio 100% sin degradar, asi que ya trae margen.
    #
    # Antes casi todas corrian con el 5.0 por defecto, que nunca se midio.
    # Ninguna se quejo ni a 120 hilos, salvo rosen que degrado a 60.
    # jumbo (3.9) y vans (7.0) quedan POR DEBAJO del viejo 5.0 en un caso:
    # son lentas de verdad, y forzarlas era arriesgar un bloqueo por nada.
    "falabella.com": 105.0,
    "construmart.cl": 49.0,
    "antartica.cl": 47.5,
    "puma.cl": 46.0,
    "rosen.cl": 40.5,
    "doite.cl": 29.0,
    "farmaciasahumada.cl": 27.5,
    "sportline.cl": 27.5,
    "winnerchile.cl": 22.0,
    "santaisabel.cl": 21.5,
    "hushpuppies.cl": 20.0,
    "reuse.cl": 16.0,
    "underarmour.cl": 16.0,
    "vans.cl": 7.0,
    "jumbo.cl": 3.9,
    # Sin medir todavia: casaideas, salcobrand y tricot fallaron la medicion
    # (hay que darles una ficha valida). El resto conserva el defecto.
    "tottus.cl": 49.0,
    "adidas.cl": 40.0,
    "paris.cl": 16.0,
    "spdigital.cl": 15.0,
    "hites.com": 15.0,
    "abc.cl": 15.5,
    "bata.cl": 21.5,
    "_por_defecto": 5.0,
}

# Cuántos segundos puede tardar, como máximo, en dar una vuelta de la lista
# FIJA. Es el número que define el pilar del negocio: si la vuelta demora
# T segundos, un ERROR de precio se detecta a los T segundos como peor caso.
#
# Subido de 7 a 59 el 9-ago-2026, a propósito: no hay evidencia (ni de
# Chile ni de afuera — se buscó) de que 7s detecte algo que 59s no
# detectaría igual, y sin ese margen extra el cupo de la lista fija era
# mucho más chico (~1.500 productos a 7s). A 59s cabe casi 8 veces más
# catálogo en la misma velocidad segura por tienda — sigue siendo "en
# menos de un minuto", muy por debajo de cualquier referencia conocida
# (herramientas como Keepa avisan 15 min a horas después).
VUELTA_OBJETIVO = 59.0

# Qué fracción del cupo de cada tienda va a la lista FIJA (errores) vs. a la
# ROTATIVA (ofertas). 70/30: el pilar del negocio se queda con la mayoría,
# pero el 30% que rota alcanza de sobra para la ventana de 10 min — ver la
# cuenta en `cargar_lista`.
PROPORCION_FIJA = 0.7

# Referencia de cuánto se querría tardar en dar una vuelta completa a las
# ofertas. YA NO RECORTA LA LISTA (12-ago-2026): antes el catálogo se cortaba
# para caber acá, que es justo lo que dejaba productos fuera. Ahora entra todo
# el catálogo y el tiempo de vuelta real se mide y se informa; este número
# queda solo como la meta contra la que compararlo.
VENTANA_OFERTAS_SEG = 600.0   # 10 minutos

PAUSA_ENTRE_VUELTAS = 0.0      # sin respiro: la vuelta ya está limitada por ritmo


# ── EL CUELLO DE BOTELLA QUE ESTUVO TAPADO HASTA EL 12-AGO-2026 ────────────
#
# `RITMO_SEGURO` dice cuántas peticiones por segundo aguanta cada tienda, y
# `cupo()` dimensiona las listas con ese número. Pero `_vigilar_tienda` corría
# UN SOLO HILO por tienda, y un hilo secuencial no puede hacer 105 req/s: hace
# 1/latencia, o sea ~1,2 req/s. La fórmula presupuestaba 25 veces más trabajo
# del que físicamente se hacía.
#
# Medido en producción (corrida 31515035162): 298.535 lecturas en 3,4 h =
# 24,4 req/s con 21 tiendas — exactamente 21 hilos ÷ 0,86 s de latencia. La
# capacidad segura de esas mismas tiendas, sumada, es de ~625 req/s. Se estaba
# usando el 4%.
#
# El efecto real: la vuelta de Falabella (6.195 productos a 105 req/s
# presupuestados) tardaba ~85 min en vez de los 59 s prometidos. El pilar del
# negocio — "un error de precio se detecta en menos de un minuto" — no se
# cumplía, y no se veía porque el log imprime la vuelta TEÓRICA (cupo/ritmo),
# nunca la medida.
#
# Arreglo: cada tienda recibe tantos hilos como necesite para sostener SU
# ritmo ya medido como seguro. No se pide ni una petición por encima de lo
# medido; se deja de desperdiciar el 96% de lo que ya estaba autorizado.

# Cuánto tarda una petición, punta a punta. Sale de dividir las lecturas
# reales por el tiempo real de la corrida citada arriba (24,4 req/s ÷ 21
# hilos). Solo se usa para dimensionar cuántos hilos hacen falta: si en la
# práctica resulta más baja, sobran hilos y el `intervalo` los frena igual,
# así que errar por alto acá es inofensivo.
LATENCIA_TIPICA = 0.9

# Tope de hilos contra UNA tienda. `medir_limites.py` llegó a 120 sin que
# ninguna se quejara; 90 deja margen bajo eso.
TOPE_HILOS_TIENDA = 90

# ── La rampa, y por qué no se salta directo al ritmo completo ──────────────
#
# Los ritmos de `RITMO_SEGURO` se midieron en RÁFAGAS CORTAS con
# `medir_limites.py`, no sosteniéndolos durante 3,4 h seguidas. Son cosas
# distintas: un WAF mira volumen acumulado, no solo picos. El propio
# `vigia.py` ya advierte lo mismo sobre la barrida ("ninguna medición de ritmo
# cubre ese volumen sostenido").
#
# Por eso el arreglo entra con freno de mano: 0,35 del ritmo medido son ~220
# req/s, que ya son 9 veces lo de hoy y siguen bajo el 21% del último escalón
# donde las tiendas respondieron 100%. Se sube mirando el resultado de una
# corrida real, no de una vez.
#
#   HECTOR_FACTOR_RITMO=0.6   → ~375 req/s
#   HECTOR_FACTOR_RITMO=1.0   → ~625 req/s (el techo ya medido como seguro)
#
# Qué mirar antes de subirlo, en el log de la corrida: que `fallos:` no crezca
# de golpe en una tienda concreta (eso es un WAF cortando, no una ficha mala)
# y que `req/s real` se acerque al presupuestado. Si una tienda empieza a
# fallar en bloque, baja el factor o dale a ESA tienda un ritmo menor en
# RITMO_SEGURO — no bajes el global por una.
def _factor_ritmo():
    try:
        f = float(os.environ.get("HECTOR_FACTOR_RITMO", "0.35"))
    except ValueError:
        return 0.35
    return max(0.05, min(1.0, f))


def _ritmo(tienda):
    """Peticiones/s que se le van a pedir a esta tienda, ya con la rampa."""
    return RITMO_SEGURO.get(tienda, RITMO_SEGURO["_por_defecto"]) * _factor_ritmo()


def _hilos_para(tienda):
    """Cuántos hilos sostienen el ritmo de esta tienda.

    Un hilo entrega 1/LATENCIA_TIPICA req/s, así que para `r` req/s hacen
    falta `r × LATENCIA_TIPICA` hilos. Es la línea que faltaba: sin ella el
    ritmo quedaba capado en 1/latencia por tienda, sin importar lo que dijera
    RITMO_SEGURO.
    """
    return max(1, min(TOPE_HILOS_TIENDA,
                      int(math.ceil(_ritmo(tienda) * LATENCIA_TIPICA))))


# Una ficha que no contesta en 8 s no va a contestar: la latencia normal es
# de ~0,9 s. Con 15 s (y el reintento de `bajar` con curl_cffi encima) cada
# URL muerta se comía el equivalente a 30 lecturas buenas del presupuesto de
# su hilo. Bajarlo no pierde fichas sanas y devuelve ese tiempo a la vuelta.
TIEMPO_FICHA = 8


def _leer(tienda, url):
    especial = adaptadores.para(tienda)
    if especial:
        d = especial(url, lambda u, c: descubrir.bajar(u, tiempo=TIEMPO_FICHA,
                                                       cabeceras=c))
        if d:
            return d
    return extractor.extraer(descubrir.bajar(url, tiempo=TIEMPO_FICHA))


def cupo(tienda, vuelta=VUELTA_OBJETIVO):
    """Cuántos productos de esta tienda caben respetando la vuelta objetivo.

    Es una división simple pero es LA fórmula del sistema:

        productos = ritmo_seguro × segundos_de_vuelta

    Falabella a 88 req/s con vuelta de 59 s son 5.192 productos. Ripley a
    5 req/s, solo 295. Por eso no se reparte el cupo en partes iguales: cada
    tienda aporta según lo rápido que responda, y una tienda lenta no puede
    arrastrar a las demás.
    """
    return max(1, int(_ritmo(tienda) * vuelta))


def capacidad_total(vuelta=VUELTA_OBJETIVO, tiendas=None):
    """Cuántos productos se pueden vigilar en total a esa velocidad."""
    doms = tiendas or [d for d in RITMO_SEGURO if not d.startswith("_")]
    return {d: cupo(d, vuelta) for d in doms}


def cargar_lista(con, vuelta=VUELTA_OBJETIVO):
    """El plan de vigilancia de cada tienda: cuota FIJA (errores) + ROTATIVA
    (ofertas), repartiendo el mismo cupo ya medido como seguro — nunca de más.

    Devuelve {tienda: {"fija": [...], "rotativa": [...], "cupo_rot": N}}.
    """
    filas = con.execute("""
        SELECT tienda, url, nombre, MAX(precio) AS precio
        FROM precios
        WHERE precio > 0
        GROUP BY url
    """).fetchall()

    # Refuerzo por historial real: tiendas que ya se equivocaron antes suben
    # en el ranking, no solo las que tienen marca+precio altos. Arranca vacío
    # (sin alertas todavía, nadie tiene refuerzo) y se corrige solo con cada
    # error real — ver `baseprecios.tasas_error_por_tienda`.
    tasas_error = baseprecios.tasas_error_por_tienda(con)

    # Se pide un tope alto y después se corta POR TIENDA: así una tienda
    # rápida con muchos productos buenos no le come el cupo a las demás.
    elegidos = caliente.elegir(
        [(f["tienda"], f["url"], f["nombre"], f["precio"]) for f in filas],
        tope=100_000, tasas_error=tasas_error)

    fija_por_tienda, fija_urls = {}, set()
    for t, u, n, p in elegidos:
        cupo_fijo = max(1, int(cupo(t, vuelta) * PROPORCION_FIJA))
        lista = fija_por_tienda.setdefault(t, [])
        if len(lista) < cupo_fijo:
            lista.append((u, n, p))
            fija_urls.add(u)

    # Candidatas a "ofertas": el resto del catálogo con precio conocido,
    # ordenado por precio (los más caros primero — mismo criterio que la
    # fija), sin los que ya están fijos. No hace falta un piso de precio: el
    # orden y el tope ya priorizan solos lo que vale la pena vigilar.
    resto_por_tienda = {}
    for f in filas:
        if f["url"] in fija_urls:
            continue
        resto_por_tienda.setdefault(f["tienda"], []).append(
            (f["url"], f["nombre"], f["precio"] or 0))

    # Dónde quedó la rotación de ofertas de cada tienda en la corrida anterior.
    # Se lee acá, en el hilo que tiene la conexión: los hilos de tienda no
    # pueden tocar sqlite (ver el docstring de correr.py).
    marcas = _marcas_rotacion(con)

    plan = {}
    for t in set(fija_por_tienda) | set(resto_por_tienda):
        cupo_total = cupo(t, vuelta)
        cupo_fijo = max(1, int(cupo_total * PROPORCION_FIJA))
        cupo_rot = max(0, cupo_total - cupo_fijo)

        # NADA FUERA DEL CATÁLOGO (12-ago-2026)
        # ------------------------------------------------------------------
        # Antes la lista rotativa se recortaba a `cupo_rot × vueltas_en_
        # ventana` para que una vuelta completa cupiera en 10 min. Esa fue la
        # decisión que dejaba productos fuera: con el cupo real de entonces
        # daba 57.080 candidatas de 173.000 con precio conocido — dos de cada
        # tres ofertas del catálogo eran invisibles para el sistema, y no
        # había nada en el log que lo dijera.
        #
        # Ahora entra TODO el catálogo con precio y la ventana pasa a ser una
        # CONSECUENCIA MEDIDA, no un recorte: la vuelta tarda lo que tarde y
        # el log lo informa. Es el cambio que hace que "todas las ofertas, de
        # todas las tiendas" sea cierto en vez de aspiracional.
        candidatas = sorted(resto_por_tienda.get(t, []), key=lambda x: -x[2])
        plan[t] = {
            "fija": fija_por_tienda.get(t, []),
            "rotativa": candidatas,
            "cupo_rot": cupo_rot,
            "desde": marcas.get(t, 0) % max(1, len(candidatas)),
        }
    return plan


def _marcas_rotacion(con):
    """Por dónde iba la rotación de ofertas de cada tienda, de la corrida
    anterior. Vive en la misma tabla `marcadores` que usa `vigia.py`."""
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    return {f["clave"][4:]: f["valor"] for f in con.execute(
        "SELECT clave, valor FROM marcadores WHERE clave LIKE 'rot_%'")}


def _guardar_marcas(con, progreso):
    """Anota dónde quedó cada tienda, para que la próxima corrida siga ahí."""
    for tienda, p in progreso.items():
        if "desde" not in p:
            continue
        con.execute(
            "INSERT INTO marcadores (clave, valor) VALUES (?,?) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
            ("rot_" + tienda, int(p["desde"])))
    con.commit()


def _ventana_rotativa(rotativa, desde, cupo_rot):
    """La tajada de la lista rotativa que le toca a ESTA vuelta, dando la
    vuelta al final para no dejar nunca la cola sin cubrir. Mismo truco que
    `_con_rotacion` en vigia.py, a otra escala de tiempo."""
    if not rotativa or cupo_rot <= 0:
        return [], desde
    if desde + cupo_rot <= len(rotativa):
        ventana = rotativa[desde:desde + cupo_rot]
    else:
        ventana = rotativa[desde:] + rotativa[:(desde + cupo_rot) - len(rotativa)]
    return ventana, (desde + cupo_rot) % len(rotativa)


def _vigilar_tienda(tienda, plan, salida, parar, progreso):
    """Recorre en bucle los productos de UNA tienda, a su ritmo seguro.

    Cada vuelta: TODA la lista fija (errores, siempre) + una ventana de la
    rotativa (ofertas, una tajada distinta cada vez). El ritmo por producto
    es el mismo para ambas — lo que cambia es cuánto de cada una entra en el
    cupo, no la velocidad a la que se lee.

    LA VUELTA SE REPARTE ENTRE VARIOS HILOS (12-ago-2026)
    --------------------------------------------------------------------------
    Antes esto era un solo hilo recorriendo la lista en serie, y ahí moría el
    ritmo: por rápida que fuera la tienda, un hilo entrega 1/latencia ≈ 1,2
    req/s. Ahora la vuelta se parte en `n` tajadas intercaladas y cada una va
    en su hilo.

    El ritmo AGREGADO sigue siendo exactamente el de `RITMO_SEGURO` (ya con la
    rampa): por eso el intervalo de cada hilo se multiplica por `n`. Con 90
    hilos a una petición cada 0,9 s, la tienda ve 100 req/s — no 90 ráfagas
    simultáneas cada 0,9 s. Se reparte el mismo presupuesto entre más manos;
    no se pide más.
    """
    fija = plan["fija"]
    rotativa = plan["rotativa"]
    cupo_rot = plan["cupo_rot"]
    n = _hilos_para(tienda)
    intervalo = n / max(0.01, _ritmo(tienda))
    # Retoma donde quedó la corrida ANTERIOR, no en cero. Con vueltas de
    # ofertas que duran más que una corrida de 3,4 h, arrancar siempre en 0
    # significaba releer eternamente la cabeza de la lista y no llegar nunca
    # a la cola — el mismo modo de falla silencioso que la rotación de
    # `vigia.py` ya tenía resuelto con su marcador.
    desde = plan.get("desde", 0)

    def _tanda(urls):
        for url in urls:
            if parar.is_set():
                return
            t0 = time.time()
            try:
                d = _leer(tienda, url)
                salida.put((tienda, url, d))
            except Exception:                          # noqa: BLE001 — una
                pass                                   # ficha caída no puede
            # Ritmo constante: se descuenta lo que ya tomó la petición, así el
            # ritmo real es el pedido y no "el pedido más lo que demoró".
            resto = intervalo - (time.time() - t0)
            if resto > 0:
                time.sleep(resto)

    while not parar.is_set():
        t_vuelta = time.time()
        ventana, desde = _ventana_rotativa(rotativa, desde, cupo_rot)
        objetivos = [u for u, _n, _p in fija + ventana]
        if not objetivos:
            return
        obreros = [threading.Thread(target=_tanda, args=(objetivos[i::n],),
                                    daemon=True)
                   for i in range(min(n, len(objetivos)))]
        for h in obreros:
            h.start()
        for h in obreros:
            h.join()
        # Se anota DESPUÉS de completar la vuelta: si la corrida se corta a
        # media vuelta, la próxima la repite entera en vez de saltarse el
        # trozo que quedó sin leer.
        p = progreso.setdefault(tienda, {})
        p["desde"] = desde
        p["vueltas"] = p.get("vueltas", 0) + 1
        p["seg_vuelta"] = time.time() - t_vuelta
        p["por_vuelta"] = len(objetivos)
        time.sleep(PAUSA_ENTRE_VUELTAS)


def correr(con, avisar=True, ciclos=None, segundos_max=None):
    plan = cargar_lista(con)
    if not plan:
        print("Lista caliente vacía. Corre primero una barrida normal para\n"
              "que haya precios conocidos:  python vigia.py --limite 2000")
        return 0

    total_fija = sum(len(p["fija"]) for p in plan.values())
    total_rot = sum(len(p["rotativa"]) for p in plan.values())
    print("🔥 Errores: %d productos fijos · 🔁 Ofertas: %d en rotación · %d tiendas\n"
          % (total_fija, total_rot, len(plan)))
    ciclo_ofertas = {}
    for t, p in sorted(plan.items(), key=lambda x: -len(x[1]["fija"])):
        vuelta = (len(p["fija"]) + p["cupo_rot"]) / _ritmo(t)
        vueltas_rot = len(p["rotativa"]) / max(1, p["cupo_rot"])
        ciclo_ofertas[t] = vueltas_rot * vuelta
        print("   %-18s %4d fijos + %4d rotativos (de %6d) · %.1f req/s "
              "(%d hilos) · vuelta ~%.0f seg · catálogo completo cada ~%.0f min"
              % (t, len(p["fija"]), p["cupo_rot"], len(p["rotativa"]), _ritmo(t),
                 _hilos_para(t), vuelta, ciclo_ofertas[t] / 60))

    peor_error = max((len(p["fija"]) + p["cupo_rot"]) / _ritmo(t) for t, p in plan.items())
    peor_oferta = max(ciclo_ofertas.values()) if ciclo_ofertas else 0
    lento = max(ciclo_ofertas, key=ciclo_ofertas.get) if ciclo_ofertas else "-"
    req_s = sum(_ritmo(t) for t in plan)
    print("\n   → %d hilos · %.0f req/s en total (factor %.2f del ritmo medido)"
          % (sum(_hilos_para(t) for t in plan), req_s, _factor_ritmo()))
    print("   → errores: detección hasta %.0f segundos" % peor_error)
    # Ya no es la ventana fija de antes: es lo que de verdad tarda la tienda
    # más lenta en dar una vuelta a TODO su catálogo, sin recortar nada.
    print("   → ofertas: catálogo completo cada %.0f min (la más lenta: %s)\n"
          % (peor_oferta / 60, lento))

    salida = queue.Queue()
    parar = threading.Event()
    progreso = {}
    hilos = [threading.Thread(target=_vigilar_tienda,
                              args=(t, p, salida, parar, progreso), daemon=True)
             for t, p in plan.items()]
    for h in hilos:
        h.start()

    # Peticiones REALES por vuelta (fija + ventana rotativa), sumadas entre
    # tiendas. `cupo_rot` es lo presupuestado, no lo que hay: si la lista
    # rotativa tiene menos candidatas que el cupo (normal con poco catálogo,
    # como en una prueba local), la ventana real es más chica — hay que usar
    # el mínimo, o `--ciclos` nunca corta porque cuenta peticiones que jamás
    # van a pasar. Solo se usa para `--ciclos` en las pruebas — el corte real
    # en producción es `segundos_max`, no un conteo de peticiones.
    total = sum(len(p["fija"]) + min(p["cupo_rot"], len(p["rotativa"]))
                for p in plan.values())
    leidos, hallazgos, inicio = 0, 0, time.time()
    ultimo_precio = {}
    try:
        while True:
            try:
                tienda, url, d = salida.get(timeout=30)
            except queue.Empty:
                print("  (sin respuestas en 30 s)")
                continue

            leidos += 1
            precio = d["precio"]

            # Solo se evalúa y se guarda si el precio CAMBIÓ. A esta frecuencia,
            # la enorme mayoría de las lecturas devuelven lo mismo que hace 5
            # segundos: evaluarlas todas sería quemar CPU y base para nada.
            if ultimo_precio.get(url) == precio:
                continue
            anterior = ultimo_precio.get(url)
            ultimo_precio[url] = precio

            # Sin stock no se avisa (mismo criterio que en vigia.py): un
            # producto agotado no se puede comprar, así que su caída de precio
            # no es una oportunidad. El precio se guarda igual para el
            # historial, y vuelve a evaluarse cuando reponga stock.
            # nombre y tienda: hacen falta para clasificar en Electrónicos u
            # Hogar, que es lo que habilita el piso del 35%.
            det = (baseprecios.evaluar(con, url, precio,
                                       nombre=d["nombre"], tienda=tienda)
                   if d.get("hay_stock", True) else None)
            baseprecios.guardar(con, tienda, url, d["nombre"], precio,
                                imagen=d.get("imagen"))
            if not baseprecios._base_de(con, url):
                baseprecios.fijar_base(con, url, precio, "inicial")
            # Si esta URL tenía un error sin resolver y el precio ya volvió a
            # su referencia, queda medido cuánto tardó la tienda en
            # corregirlo — es la única forma real de responder "cuánto dura
            # un error de precio", y solo el vigilante relee seguido como
            # para poder medirlo.
            baseprecios.marcar_si_restablecido(con, url, precio)

            # Commit en CADA cambio, a propósito.
            #
            # Antes se agrupaba de a 50 para "reducir escrituras". Sonaba
            # razonable y era exactamente al revés: en SQLite la primera
            # escritura abre la transacción y toma el lock de escritura, y el
            # lote lo mantiene tomado a través de las 50 DESCARGAS que van
            # entre medio. La barrida, que escribe en paralelo, esperaba sus
            # 30 s de `timeout` y moría con "database is locked" — se llevaba
            # la corrida entera y todo lo leído (11-ago-2026, run 31449818455).
            #
            # No se había visto nunca porque la lista caliente venía vacía y
            # el vigilante no escribía: el bug estaba tapado, no ausente.
            #
            # Comitear seguido NO es caro: con synchronous=NORMAL (ver
            # baseprecios.abrir) el commit en WAL no paga fsync. Lo que
            # importa no es cuántos commits hay, es cuánto rato queda tomado
            # el lock.
            con.commit()

            if anterior is not None:
                print("  %s %s: %s → %s" % (
                    time.strftime("%H:%M:%S"), tienda,
                    _plata(anterior), _plata(precio)))

            if det:
                hallazgos += 1
                det.update({"tienda": tienda, "nombre": d["nombre"]})
                seg = time.time() - inicio
                print("  🚨 %s  %s → %s (-%.0f%%)  [%s]" % (
                    tienda, _plata(det["referencia"]), _plata(precio),
                    det["caida"] * 100, det["tipo"]))
                if avisar:
                    # En un hilo aparte: `enviar_hallazgos` duerme 3,5 s entre
                    # mensajes para no pasarse del límite de Telegram, y hacerlo
                    # dentro del bucle congelaría la vigilancia justo cuando más
                    # importa — con un hallazgo recién detectado en la mano.
                    #
                    # OJO: `con` es del hilo de ESTE bucle — sqlite3 prohíbe
                    # usar una conexión desde un hilo distinto al que la creó
                    # (falla en silencio dentro de un hilo daemon, sin que
                    # nadie se entere: exactamente el bug que ya pasó una vez
                    # con el vigilante completo, ver el docstring de
                    # correr.py). Por eso el hilo nuevo abre SU PROPIA
                    # conexión — barata, se cierra sola al terminar.
                    def _avisar(det=det):
                        con_hilo = baseprecios.abrir()
                        try:
                            alertas.enviar_hallazgos(con_hilo, [det])
                        finally:
                            con_hilo.close()
                    threading.Thread(target=_avisar, daemon=True).start()

            if ciclos and leidos >= ciclos * total:
                break
            # En Modal cada tanda tiene un timeout duro. Se corta ANTES para
            # alcanzar a cerrar la base y confirmar el volumen: si el timeout
            # mata el contenedor a mitad de escritura, se pierde la tanda.
            if segundos_max and (time.time() - inicio) >= segundos_max:
                print("  (fin de la tanda: %.0f min)" % ((time.time() - inicio) / 60))
                break
    except KeyboardInterrupt:
        print("\n  (cortado a mano)")
    finally:
        parar.set()
        con.commit()   # por si quedó algo a medias al cortar
        # Dónde quedó la rotación de cada tienda, para que la corrida
        # siguiente RETOME ahí en vez de volver a la cabeza de la lista.
        try:
            _guardar_marcas(con, progreso)
        except Exception as ex:                        # noqa: BLE001
            print("  (no se pudo guardar la rotación: %s)" % str(ex)[:80])

    dur = max(1, time.time() - inicio)
    print("\nleídos: %d (%.1f/seg) · cambios: %d · hallazgos: %d · %.0f seg"
          % (leidos, leidos / dur, len(ultimo_precio), hallazgos, dur))

    # El contraste que faltaba: lo PRESUPUESTADO contra lo REALMENTE hecho.
    # Mientras el log solo imprimía la vuelta teórica (cupo ÷ ritmo), un
    # vigilante corriendo al 4% de su capacidad se veía igual que uno sano.
    # Si estas dos cifras se separan mucho, el cuello está en la red o en la
    # latencia, no en el presupuesto — y subir HECTOR_FACTOR_RITMO no ayuda.
    print("   presupuestado: %.0f req/s · real: %.1f req/s (%.0f%%)"
          % (req_s, leidos / dur, 100.0 * (leidos / dur) / max(1, req_s)))
    if progreso:
        completas = sum(1 for p in progreso.values() if p.get("vueltas"))
        print("   vueltas completas: %d tienda(s) · rotación guardada para la "
              "próxima corrida" % completas)
    return hallazgos


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def main():
    p = argparse.ArgumentParser(description="Vigilante de la lista caliente")
    p.add_argument("--ciclos", type=int, help="vueltas completas y termina")
    p.add_argument("--sin-avisar", action="store_true")
    p.add_argument("--tope", type=int, default=caliente.TOPE_CALIENTE)
    args = p.parse_args()

    caliente.TOPE_CALIENTE = args.tope
    con = baseprecios.abrir()
    correr(con, avisar=not args.sin_avisar, ciclos=args.ciclos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
