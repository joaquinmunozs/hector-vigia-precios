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
    # puma BAJADA de 46.0 a 5.0 el 12-ago-2026. En la corrida 31600451844 dio
    # 686 lecturas buenas contra 45.649 rechazos (1,5%), corriendo a 46 × 0,35
    # = 16,1 req/s. La sonda 31623034286 leyó 10 fichas de a una desde el
    # runner: 200 en las 10. O sea NO nos bloquea — se ahoga con el volumen
    # sostenido, igual que spdigital (§6 de la bitácora del 11-ago).
    #
    # Por qué la medición decía 46 y la realidad dice que no: `medir_limites`
    # mide en RÁFAGAS CORTAS contra UNA sola URL. Eso captura el límite
    # instantáneo, no el de 3,4 h seguidas. Es el mismo desfase que tricot pero
    # al revés: tricot mide mal y anda bien; puma mide bien y anda mal.
    #
    # Queda en el conservador 5.0 (→ 1,75 req/s con el factor actual). Subirlo
    # exige medir SOSTENIDO, no en ráfaga.
    "puma.cl": 5.0,
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
    # ── Medidos el 12-ago-2026 DESDE EL RUNNER, no desde casa ─────────────
    # (workflow `medir-tiendas.yml`, corrida 31555412634). Las dos aguantaron
    # 120 hilos con 100% de éxito y sin degradar: 65,2 req/s cada una, que con
    # el 60% de margen del método dan 39,1. Antes corrían a 15 y a 5.0.
    "hites.com": 39.1,
    "salcobrand.cl": 39.1,
    #
    # tricot NO se pudo medir, y esta vez no es por el objetivo: devolvió
    # desafío de WAF con 0% de éxito ya en el primer escalón de 5 hilos.
    #
    # OJO CON LEER ESO COMO "tricot nos bloquea": en producción tricot es la
    # tienda con MEJOR cobertura de todas (25.856 de 25.947 fichas, 99,6%).
    # Si estuviera bloqueando, no mediría casi nada. Lo que la delata es el
    # método: `medir_limites.py` le pega SIEMPRE A LA MISMA URL —su propio
    # comentario dice que es el peor caso a propósito— y el WAF de tricot
    # reacciona a esa repetición, no al volumen. La barrida pide fichas
    # distintas y por eso no la gatilla.
    #
    # Se queda en el `_por_defecto` de 5.0, que es conservador y funciona.
    # Subirlo requiere medirlo con fichas rotativas, no con una sola.
    # ── tottus: NOS BLOQUEA, y el proxy NO lo resuelve (12-ago-2026) ────────
    #
    # Bajada de 49.0 a 1.0, que es contención de daño y no un arreglo.
    #
    # Venía con 84 lecturas buenas contra 96.120 rechazos (0,1%) — el 65% de
    # todos los rechazos del sistema. El log la marcaba "BAJARLE EL RITMO" y
    # eso estaba equivocado: la sonda 31623034286 leyó 10 fichas DE A UNA, con
    # 1,2 s entre medio, y dio 403 en las 10. No es ritmo, es bloqueo.
    #
    # Se probaron las tres salidas conocidas, en orden de costo:
    #
    #   1. Worker de Cloudflare (lo que destrabó paris y easy) → NO SIRVE.
    #      Verificado con la sonda 31623549818 ya con tottus en POR_PROXY y el
    #      Worker desplegado: 403 en las 10 por el camino real. Rechaza también
    #      al edge de Cloudflare. Se revirtió para no gastar cuota al pedo.
    #   2. Cabecera `Referer`, que fue lo que destrabó falabella → no aplica:
    #      desde una IP no bloqueada la ficha ya da 200 sin ella.
    #   3. La API interna del grupo Falabella
    #      (`/s/browse/v1/product/cl?productId=N`), que es de donde se saca el
    #      precio de falabella.com → responde 200 pero con
    #      `{"responseType":"NOT_FOUND"}` para los dos IDs de la URL de tottus.
    #      El catálogo de tottus no está en esa API.
    #
    # Lo que sí se sabe: desde la casa de Joaquín la misma ficha da 200 con
    # 1,06 MB. Es bloqueo por rango de IP, y alcanza a Azure y a Cloudflare.
    # Salir de esto cuesta plata (proxy residencial) o cambiar de dónde corre.
    # Es de los pocos casos donde pagar está justificado — pero es decisión de
    # Joaquín, no un cambio que se mete de contrabando en un commit.
    #
    # Mientras tanto 1.0 × 0,35 = 0,35 req/s: sigue intentando por si el
    # bloqueo se levanta, sin volver a envenenar la tabla de salud.
    "tottus.cl": 1.0,
    "adidas.cl": 40.0,
    "paris.cl": 16.0,
    # spdigital se queda bajo a propósito: no es que no se pueda leer (18 de
    # 20 URLs "fallidas" leyeron bien de a una), es que se ahoga bajo carga.
    "spdigital.cl": 15.0,
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

# ── FRECUENCIA ADAPTATIVA: no todo merece la misma atención (12-ago-2026) ──
#
# Medido sobre la base de producción: de 175.212 fichas con precio conocido,
# **173.410 (99,0%) nunca han cambiado de precio**. Solo 1.802 se movieron
# alguna vez, y están concentradas: Falabella 1.250, Santa Isabel 310,
# Hites 144.
#
# Con una cola uniforme, el 99% del presupuesto se gasta releyendo productos
# quietos para encontrar el 1% que se mueve. Repartir por volatilidad no pide
# ni una petición más: cambia a QUIÉN se le dan.
#
# LA TRAMPA, Y POR QUÉ HAY TRES NIVELES Y NO DOS
# --------------------------------------------------------------------------
# El historial tiene 1,1 días. Una ficha "que nunca cambió" puede ser una que
# de verdad no se mueve, o una que simplemente no ha tenido tiempo de
# moverse — y con dos niveles las dos caen en el mismo saco. Mandar al
# congelador un producto del que todavía no se sabe nada es exactamente el
# error que haría perder el primer error de precio de una ficha nueva.
#
# Por eso quien tiene poco historial NO es frío, es DESCONOCIDO, y se lee a
# frecuencia media hasta que se sepa qué es. El sistema se afina solo con los
# días: cada ficha se va mudando de nivel según lo que demuestre.
#
#   🔥 MOVIL       ya cambió de precio al menos una vez
#   ❓ DESCONOCIDO menos de MIN_PARA_ENFRIAR lecturas: aún no se sabe
#   🧊 QUIETO      suficientes lecturas y siempre el mismo precio
#
# NADA SALE DE LA LISTA. Los tres niveles se siguen leyendo enteros; lo único
# que cambia es cada cuánto le toca a cada uno.
REPARTO_ROTATIVA = {"movil": 0.40, "desconocido": 0.45, "quieto": 0.15}

# Cuántas lecturas hacen falta para creerle a un "nunca cambió". Por debajo de
# esto la ficha es DESCONOCIDA, no quieta. Mismo criterio que
# `baseprecios.MIN_OBSERVACIONES` usa para creerle a una mediana.
MIN_PARA_ENFRIAR = 5


def _nivel(distintos, lecturas):
    """En qué nivel de frecuencia cae una ficha."""
    if distintos > 1:
        return "movil"
    if lecturas < MIN_PARA_ENFRIAR:
        return "desconocido"
    return "quieto"


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

# ── EL TOPE QUE FALTABA: EL RUNNER, NO LAS TIENDAS (12-ago-2026) ───────────
#
# El cambio de "un hilo por tienda" a "los hilos que aguante cada tienda"
# arregló el diseño pero se pasó de rosca con el hardware. Medido en la
# corrida 31559797454, contra la 31515035162 de la noche anterior:
#
#                     ANTES        DESPUÉS     resultado
#   vigilante hilos      21            216
#   vigilante req/s    24,4          0,126     194x PEOR
#   barrida hilos        60            106
#   barrida req/s      12,7           1,63     7,8x PEOR
#   hilos TOTALES        81            322
#
# La prueba de que es el runner y no las tiendas: **la barrida cayó 7,8x sin
# que su lógica de concurrencia cambiara**, sólo por correr al lado de un
# vigilante con 216 hilos. Un `ubuntu-latest` son 2-4 vCPU, y 322 hilos de
# Python compitiendo por el GIL pasan más tiempo en cambios de contexto que
# leyendo respuestas.
#
# O sea: hay DOS techos, y hasta ahora sólo se respetaba uno.
#   · el de cada tienda  -> RITMO_SEGURO (cuánto aguanta ella)
#   · el de la máquina   -> este (cuántos hilos puede atender el runner)
#
# El vigilante corre EN PARALELO con la barrida, así que este presupuesto es
# sólo su mitad. El sistema andaba sano con 21 + 60 = 81 hilos; 60 acá deja
# margen y sigue siendo casi el triple de lo que había.
#
# Es la pared que la investigación de asyncio ya anticipaba: con hilos no se
# puede pasar de acá. Subirlo sin migrar a asyncio vuelve a romperlo.
def _tope_hilos_total():
    try:
        return max(4, int(os.environ.get("HECTOR_TOPE_HILOS", "60")))
    except ValueError:
        return 60

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
    """Cuántos hilos sostienen el ritmo de esta tienda, mirando SOLO a la
    tienda. Es el ideal; el reparto real lo recorta `_reparto_hilos`.

    Un hilo entrega 1/LATENCIA_TIPICA req/s, así que para `r` req/s hacen
    falta `r × LATENCIA_TIPICA` hilos. Es la línea que faltaba: sin ella el
    ritmo quedaba capado en 1/latencia por tienda, sin importar lo que dijera
    RITMO_SEGURO.
    """
    return max(1, min(TOPE_HILOS_TIENDA,
                      int(math.ceil(_ritmo(tienda) * LATENCIA_TIPICA))))


def _reparto_hilos(tiendas):
    """Reparte el presupuesto de hilos del runner entre las tiendas.

    Si lo que pide cada tienda cabe en el tope, se le da lo que pide. Si no,
    se escala TODO proporcionalmente: la tienda que más aguanta sigue
    llevándose la mayor tajada, pero nadie se pasa del presupuesto de la
    máquina. Sin este reparto, 21 tiendas pidiendo su ideal sumaban 216 hilos
    y hundían el runner (ver `_tope_hilos_total`).
    """
    ideal = {t: _hilos_para(t) for t in tiendas}
    total = sum(ideal.values()) or 1
    tope = _tope_hilos_total()
    if total <= tope:
        return ideal
    escala = tope / float(total)
    return {t: max(1, int(n * escala)) for t, n in ideal.items()}


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
        SELECT tienda, url, nombre, MAX(precio) AS precio,
               COUNT(DISTINCT precio) AS distintos,
               COUNT(*)               AS lecturas
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
    # Cada tienda tiene TRES colas, una por nivel de frecuencia (ver
    # REPARTO_ROTATIVA arriba). Dentro de cada una sigue mandando el precio.
    resto_por_tienda = {}
    for f in filas:
        if f["url"] in fija_urls:
            continue
        niv = _nivel(f["distintos"], f["lecturas"])
        resto_por_tienda.setdefault(f["tienda"], {}).setdefault(niv, []).append(
            (f["url"], f["nombre"], f["precio"] or 0))

    # Dónde quedó la rotación de ofertas de cada tienda en la corrida anterior.
    # Se lee acá, en el hilo que tiene la conexión: los hilos de tienda no
    # pueden tocar sqlite (ver el docstring de correr.py).
    marcas = _marcas_rotacion(con)
    hilos = _reparto_hilos(set(fija_por_tienda) | set(resto_por_tienda))

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
        colas = resto_por_tienda.get(t, {})
        rotativas, cupos, desdes = {}, {}, {}
        # El cupo se reparte entre los niveles que EXISTEN en esta tienda. Si
        # una no tiene móviles todavía (la mayoría al principio), su 40% se
        # reparte entre los otros dos en vez de perderse.
        presentes = {k: v for k, v in REPARTO_ROTATIVA.items() if colas.get(k)}
        suma = sum(presentes.values()) or 1.0
        for niv, peso in presentes.items():
            rotativas[niv] = sorted(colas[niv], key=lambda x: -x[2])
            cupos[niv] = max(1, int(cupo_rot * peso / suma))
            desdes[niv] = marcas.get(niv + "|" + t, 0) % max(1, len(rotativas[niv]))

        plan[t] = {
            "fija": fija_por_tienda.get(t, []),
            "rotativas": rotativas,
            "cupos": cupos,
            "desdes": desdes,
            "cupo_rot": cupo_rot,
            "hilos": hilos.get(t, 1),
        }
    return plan


def _marcas_rotacion(con):
    """Por dónde iba la rotación de ofertas de cada tienda, de la corrida
    anterior. Vive en la misma tabla `marcadores` que usa `vigia.py`."""
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    # La clave es `rot_<nivel>|<tienda>`: una rotación por cola, porque cada
    # nivel avanza a su propio ritmo y compartir un marcador los descuadraría.
    return {f["clave"][4:]: f["valor"] for f in con.execute(
        "SELECT clave, valor FROM marcadores WHERE clave LIKE 'rot_%'")}


def _guardar_marcas(con, progreso):
    """Anota dónde quedó cada cola, para que la próxima corrida siga ahí."""
    for tienda, p in progreso.items():
        for niv, desde in (p.get("desdes") or {}).items():
            con.execute(
                "INSERT INTO marcadores (clave, valor) VALUES (?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                ("rot_" + niv + "|" + tienda, int(desde)))
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


# ── Salud por tienda: lo que hace falta para decidir si se sube el ritmo ───
#
# `RECHAZO` son los códigos con los que una tienda dice "me estás incomodando":
# 403 (prohibido), 429 (demasiadas peticiones) y 503 (no disponible). Si estos
# suben al subir `HECTOR_FACTOR_RITMO`, hay que bajarle el ritmo A ESA TIENDA
# —no a todas—, que es justo lo que se hizo con spdigital.
#
# Un timeout o un error de parseo NO son lo mismo: significan ficha lenta o
# rota, y se arreglan de otra forma.
RECHAZO = (403, 429, 503, 502, 520, 521, 522, 429)

_SALUD = {}
_SALUD_LOCK = threading.Lock()


def _clase_de_fallo(ex):
    cod = getattr(ex, "code", None) or getattr(ex, "status_code", None)
    if cod in RECHAZO:
        return "rechazo"
    if "timeout" in type(ex).__name__.lower() or "timeout" in str(ex).lower():
        return "timeout"
    return "otro"


def _marca(tienda, clase):
    with _SALUD_LOCK:
        d = _SALUD.setdefault(tienda, {})
        d[clase] = d.get(clase, 0) + 1


# ── LA COLA TIENE TOPE, Y POR QUÉ (16-ago-2026) ────────────────────────────
#
# Antes esto era `queue.Queue()` sin tope. Los hilos de tienda bajaban fichas
# mucho más rápido de lo que el ÚNICO hilo consumidor podía evaluarlas, la
# cola crecía en silencio, y al cerrar la tanda todo lo que quedaba adentro se
# tiraba sin que `evaluar()` lo mirara jamás. Medido en producción:
#
#     corrida        ok (bajadas)   leídos (evaluadas)   drenado
#     15-ago 12:41       642.262            642.242        100%
#     16-ago 18:32       472.707             82.375         17%
#
# Unas 390.000 fichas por corrida bajadas, parseadas y tiradas. Eso es lo que
# tenía a Héctor casi mudo: no es que no hubiera caídas, es que el 83% de lo
# que se bajaba nunca se evaluaba.
#
# EL CUELLO NO ES LA BASE: medido contra la base real (370 MB), el trabajo por
# item (evaluar + guardar + commit) rinde 1.413 items/s. El consumidor real
# hacía 6,7/s.
#
# ES EL GIL. `extractor.extraer` corre TODAS sus estrategias sobre el HTML
# completo: ~33 ms de CPU pura por ficha, con el GIL tomado. Con ~60 hilos de
# tienda haciendo eso, el consumidor no alcanza a correr. Medido: el bucle
# consumidor solo rinde 500 items/s; con 8 hilos parseando en paralelo, 0,2/s.
# Y la cuenta cierra con producción: 472.707 fichas × 33 ms son ~15.600
# segundos de CPU de parseo dentro de una tanda de 12.242 segundos — el
# proceso está sobresuscrito, no queda GIL para nadie más.
#
# El tope arregla las dos cosas de una: cuando el consumidor se atrasa, el
# hilo de tienda se FRENA en el `put`, y al frenarse deja de parsear — que es
# exactamente el CPU que le faltaba al consumidor. Se baja menos, pero se
# evalúa todo lo que se baja, que es lo único que produce alertas.
#
# El tope no es un número fino: alcanza con que sea lo bastante grande para
# absorber ráfagas (una vuelta de falabella son ~735 fichas fijas) y lo
# bastante chico para que el atraso se note enseguida en vez de acumularse.
COLA_MAXIMA = 5_000

# Cuánto espera el hilo de tienda a que se libere cupo antes de dar la lectura
# por perdida. Generoso a propósito: frenar es lo que se busca, descartar es
# el último recurso. Si aparecen descartes en la tabla de salud, el consumidor
# está tan atrasado que ya no es contrapresión, es un problema aparte.
ESPERA_COLA = 30.0


def _entregar(salida, tienda, url, d, parar=None):
    """Pone una lectura en la cola del consumidor, con contrapresión.

    Devuelve True si quedó entregada. Si la cola sigue llena después de
    `ESPERA_COLA`, la anota como `descartado` y devuelve False: una ficha que
    se bajó y no se evaluó tiene que verse en la tabla de salud, nunca
    desaparecer en silencio como pasaba antes.

    `parar` se mira mientras se espera. Al cerrar la tanda el consumidor deja
    de sacar, así que TODOS los hilos de tienda quedarían trabados hasta
    cumplir los 30 s y anotarían un descarte cada uno: unos 60 descartes
    falsos por corrida, justo en la columna que se acaba de agregar para
    detectar los de verdad. Ese caso sale sin anotar nada — la lectura se
    pierde igual, pero es el cierre normal de la tanda, no una señal.
    """
    fin = time.time() + ESPERA_COLA
    while True:
        if parar is not None and parar.is_set():
            return False
        try:
            salida.put((tienda, url, d), timeout=0.25)
            return True
        except queue.Full:
            if time.time() >= fin:
                _marca(tienda, "descartado")
                return False


def _resumen_salud():
    """Tabla de salud por tienda, ordenada por la peor. Es la que se mira
    antes de decidir si el ritmo puede subir otro escalón."""
    filas = []
    with _SALUD_LOCK:
        for t, d in _SALUD.items():
            ok = d.get("ok", 0)
            rech = d.get("rechazo", 0)
            tout = d.get("timeout", 0)
            otro = d.get("otro", 0)
            desc = d.get("descartado", 0)
            tot = ok + rech + tout + otro
            if tot or desc:
                filas.append((t, ok, rech, tout, otro,
                              100.0 * ok / tot if tot else 0.0, desc))
    return sorted(filas, key=lambda f: f[5])


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
    rotativas = plan["rotativas"]
    cupos = plan["cupos"]
    # Los hilos ya vienen repartidos contra el presupuesto del runner, no es
    # lo que esta tienda pediría por su cuenta (ver `_reparto_hilos`).
    n = plan.get("hilos") or _hilos_para(tienda)
    intervalo = n / max(0.01, _ritmo(tienda))
    # Retoma donde quedó la corrida ANTERIOR, no en cero. Con vueltas de
    # ofertas que duran más que una corrida de 3,4 h, arrancar siempre en 0
    # significaba releer eternamente la cabeza de la lista y no llegar nunca
    # a la cola — el mismo modo de falla silencioso que la rotación de
    # `vigia.py` ya tenía resuelto con su marcador.
    desdes = dict(plan.get("desdes") or {})

    def _tanda(urls):
        for url in urls:
            if parar.is_set():
                return
            t0 = time.time()
            try:
                d = _leer(tienda, url)
                _marca(tienda, "ok")
                # Puede FRENAR acá si el consumidor está atrasado, y está bien:
                # es la contrapresión que evita bajar 390.000 fichas por corrida
                # para tirarlas después (ver COLA_MAXIMA). El `t0` de abajo ya
                # descuenta esta espera del intervalo, así que frenar no hace
                # que después se dispare una ráfaga para "recuperar".
                _entregar(salida, tienda, url, d, parar)
            except Exception as ex:                    # noqa: BLE001 — una
                # NO se traga el fallo en silencio (12-ago-2026). Antes acá
                # había un `pass` pelado, y eso dejaba ciego justo al que
                # decide si se puede subir el ritmo: sin saber qué tienda
                # empieza a rebotar, "subir el factor y ver cómo responde" no
                # se puede hacer con datos, sólo a ojo.
                #
                # Se separa el rechazo (403/429/503 = la tienda se está
                # incomodando, hay que bajarle el ritmo) de los demás errores
                # (timeout, ficha rota), porque significan cosas distintas y
                # se corrigen distinto.
                _marca(tienda, _clase_de_fallo(ex))
            # Ritmo constante: se descuenta lo que ya tomó la petición, así el
            # ritmo real es el pedido y no "el pedido más lo que demoró".
            resto = intervalo - (time.time() - t0)
            if resto > 0:
                time.sleep(resto)

    while not parar.is_set():
        t_vuelta = time.time()
        # Cada vuelta lleva la lista fija entera + una tajada de CADA nivel de
        # frecuencia. Los tres avanzan en paralelo con su propio marcador, así
        # que un nivel chico (los móviles) da muchas vueltas completas por cada
        # una que da un nivel grande (los quietos) — que es exactamente el
        # efecto buscado, y sale solo del tamaño de cada cola.
        ventana = []
        for niv, lista in rotativas.items():
            tajada, desdes[niv] = _ventana_rotativa(
                lista, desdes.get(niv, 0), cupos.get(niv, 0))
            ventana += tajada
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
        p["desdes"] = dict(desdes)
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

    def _tam(p, niv):
        return len(p["rotativas"].get(niv, ()))

    total_fija = sum(len(p["fija"]) for p in plan.values())
    total_rot = sum(sum(len(l) for l in p["rotativas"].values())
                    for p in plan.values())
    print("🔥 Errores: %d productos fijos · 🔁 Ofertas: %d en rotación · %d tiendas"
          % (total_fija, total_rot, len(plan)))
    for niv in ("movil", "desconocido", "quieto"):
        tot = sum(_tam(p, niv) for p in plan.values())
        print("     %-12s %8d fichas (%.0f%% del cupo rotativo)"
              % (niv, tot, REPARTO_ROTATIVA[niv] * 100))
    print()

    # Cuánto tarda CADA nivel en dar una vuelta completa. Es la cifra que
    # importa ahora: el promedio del catálogo ya no dice nada útil cuando los
    # niveles van a ritmos distintos a propósito.
    ciclo = {niv: {} for niv in REPARTO_ROTATIVA}
    for t, p in sorted(plan.items(), key=lambda x: -len(x[1]["fija"])):
        vuelta = (len(p["fija"]) + p["cupo_rot"]) / _ritmo(t)
        detalle = []
        for niv, lista in p["rotativas"].items():
            cu = max(1, p["cupos"].get(niv, 1))
            ciclo[niv][t] = (len(lista) / cu) * vuelta
            detalle.append("%s %d/%d" % (niv[:4], cu, len(lista)))
        print("   %-18s %4d fijos · %.1f req/s (%d hilos) · vuelta ~%.0f seg · %s"
              % (t, len(p["fija"]), _ritmo(t), p.get("hilos", 1), vuelta,
                 " · ".join(detalle)))

    peor_error = max((len(p["fija"]) + p["cupo_rot"]) / _ritmo(t) for t, p in plan.items())
    req_s = sum(_ritmo(t) for t in plan)
    usados = sum(p.get("hilos", 1) for p in plan.values())
    pedidos = sum(_hilos_para(t) for t in plan)
    print("\n   → %d hilos de %d pedidos (tope del runner: %d) · %.0f req/s "
          "presupuestados (factor %.2f)"
          % (usados, pedidos, _tope_hilos_total(), req_s, _factor_ritmo()))
    print("   → errores: detección hasta %.0f segundos" % peor_error)
    for niv in ("movil", "desconocido", "quieto"):
        if ciclo[niv]:
            peor = max(ciclo[niv].values())
            lento = max(ciclo[niv], key=ciclo[niv].get)
            print("   → %-12s vuelta completa cada %6.1f min (la más lenta: %s)"
                  % (niv, peor / 60, lento))
    print()

    salida = queue.Queue(maxsize=COLA_MAXIMA)
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
    total = sum(len(p["fija"]) + sum(min(p["cupos"].get(niv, 0), len(l))
                                     for niv, l in p["rotativas"].items())
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
    #
    # OJO CON LEER ESTO MAL (16-ago-2026): `leidos` NO es lo que se bajó de
    # las tiendas, es lo que el consumidor alcanzó a EVALUAR. Durante días la
    # línea dijo "real: 6.7 req/s (3%)" y se leyó como un problema de red o de
    # ritmo, cuando la red iba a 38 req/s y el atasco estaba en el consumidor.
    # Por eso ahora se imprimen las dos, y la diferencia entre ellas tiene
    # nombre propio. Si BAJADAS es alto y EVALUADAS es bajo, subir
    # HECTOR_FACTOR_RITMO empeora las cosas: baja más fichas para tirar.
    with _SALUD_LOCK:
        bajadas = sum(d.get("ok", 0) for d in _SALUD.values())
        descartadas = sum(d.get("descartado", 0) for d in _SALUD.values())
    print("   presupuestado: %.0f req/s · bajadas: %.1f/seg · evaluadas: %.1f/seg (%.0f%%)"
          % (req_s, bajadas / dur, leidos / dur,
             100.0 * (leidos / dur) / max(1, req_s)))
    # Al cerrar la tanda siempre quedan unas pocas en vuelo (una por hilo de
    # tienda, como mucho): avisar por ESO sería ruido en todas las corridas y
    # el aviso dejaría de mirarse. Se avisa cuando el atraso es estructural.
    # Para referencia, la corrida del 16-ago tenía el 83% sin evaluar.
    sin_mirar = max(0, bajadas - leidos)
    if sin_mirar > max(200, 0.02 * bajadas):
        print("   ⚠️  %d fichas bajadas que NO se evaluaron (%.0f%% de lo bajado)"
              % (sin_mirar, 100.0 * sin_mirar / max(1, bajadas)))
        print("      el consumidor va más lento que las tiendas — mirar el GIL "
              "del parseo antes que el ritmo (ver COLA_MAXIMA)")
    if descartadas:
        print("   ⚠️  %d descartadas por cola llena tras %.0f s de espera"
              % (descartadas, ESPERA_COLA))

    # ── LA TABLA QUE DECIDE SI EL RITMO PUEDE SUBIR ───────────────────────
    #
    # Se mira `rechazo`: son los 403/429/503 con los que la tienda dice que
    # se está incomodando. Si una tienda los tiene y las demás no, el
    # problema es de ESA tienda y se le baja su ritmo en RITMO_SEGURO, no el
    # factor global. Si aparecen en varias a la vez, entonces sí es el factor.
    filas = _resumen_salud()
    if filas:
        print("\n   SALUD POR TIENDA (factor %.2f) — mirar la columna rechazo:"
              % _factor_ritmo())
        print("   %-20s %9s %9s %9s %10s %8s"
              % ("tienda", "ok", "rechazo", "timeout", "descartado", "% ok"))
        for t, ok, rech, tout, otro, pct, desc in filas:
            aviso = "  <-- BAJARLE EL RITMO" if rech and pct < 90 else ""
            if desc:
                aviso = "  <-- SE TIRARON LECTURAS" + aviso
            print("   %-20s %9d %9d %9d %10d %7.1f%%%s"
                  % (t, ok, rech, tout, desc, pct, aviso))
        total_rech = sum(f[2] for f in filas)
        if total_rech == 0:
            print("   → cero rechazos: el ritmo se puede subir otro escalón.")
        else:
            malas = [f[0] for f in filas if f[2] and f[5] < 90]
            print("   → %d rechazos en total%s" % (
                total_rech, (" · revisar: " + ", ".join(malas[:5])) if malas else ""))
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
