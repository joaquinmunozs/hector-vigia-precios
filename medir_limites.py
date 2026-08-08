# -*- coding: utf-8 -*-
"""Mide, con prudencia, cuántas peticiones por segundo aguanta cada tienda.

FILOSOFÍA — MODO A (PRUDENTE)
------------------------------------------------------------------------------
No busca el techo duro a la fuerza: sube el ritmo por escalones y FRENA al
primer signo de bloqueo. La meta no es "cuánto resiste antes de caer" sino
"cuál es el ritmo cómodo al que puedo pegarle sin llamar la atención".

Por qué así: encontrar el límite reventándolo deja la IP marcada en esa
tienda por un rato, y esa IP es la de la casa de Joaquín. Un número un poco
conservador que NO quema la IP vale más que el máximo exacto que sí la quema.

CÓMO MIDE
------------------------------------------------------------------------------
Para cada tienda prueba una escalera de concurrencia: 1, 2, 5, 10, 20 hilos.
En cada escalón manda una tanda corta y mira dos cosas:

  · tasa de éxito  -> si cae bajo 90%, esa tienda ya no está cómoda
  · tiempo de respuesta -> si se dispara, la tienda está "pensándolo" (a
    punto de bloquear)

Apenas un escalón falla, se detiene esa tienda y se reporta el ÚLTIMO escalón
sano como su tope recomendado. Entre tanda y tanda hay una pausa, para no
encadenar presión.

QUÉ ES UN "BLOQUEO" ACÁ
------------------------------------------------------------------------------
  · HTTP 403 / 429 / 503
  · una página de desafío ("just a moment", captcha)
  · un salto brusco del tiempo de respuesta (> 3x el del primer escalón)
"""
import concurrent.futures as futuros
import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from curl_cffi import requests as cffi

# La escalera de concurrencia.
#
# Empezó topada en 20 (modo prudente) y NINGUNA tienda se inmutó ahí: 100% de
# éxito y sin degradar. O sea el techo real estaba más arriba y no lo habíamos
# encontrado. Se extiende para hallarlo, manteniendo la regla de oro: frenar
# al PRIMER signo de bloqueo o lentitud, nunca insistir.
ESCALONES = (5, 20, 40, 60, 90, 120)
PETICIONES_POR_ESCALON = 30      # tanda corta: suficiente para medir, poco ruido
PAUSA_ENTRE_ESCALONES = 4.0      # respiro entre tandas
UMBRAL_EXITO = 0.90              # bajo esto, la tienda ya no está cómoda
FACTOR_LENTITUD = 3.0            # si el tiempo se triplica, está por bloquear

DESAFIO = ("just a moment", "checking your browser", "captcha", "access denied",
           "pardon our interruption", "request unsuccessful", "cf-challenge")

# Una URL real por tienda (ficha o API). Se le pega SIEMPRE a la misma, que es
# el peor caso: golpear muchas fichas distintas es más benigno que una sola.
OBJETIVOS = {
    "falabella-api": ("https://www.falabella.com/s/browse/v1/product/cl?productId=144811750",
                      {"Referer": "https://www.falabella.com/falabella-cl/"}),
    "ripley":        ("https://simple.ripley.cl/control-ps5-dualsense-sony-blanco-2000380632868p", {}),
    # Apuntar a la PORTADA daba 0,3 req/s y hacía creer que la tienda era
    # lentísima. La portada de spdigital es enorme y se genera distinto que una
    # ficha. Siempre medir contra una ficha de producto real.
    "spdigital":     ("https://www.spdigital.cl/001r00623-corvo-xfer-belt/", {}),
    "adidas":        ("https://www.adidas.cl/zapatos-de-futbol-copa-mundial/015110.html", {}),
    "tottus":        ("https://www.tottus.cl/", {}),
    "paris":         ("https://www.paris.cl/plumon-all-season-2-plazas-916167999.html", {}),
}


def _una(url, cabeceras):
    """Una petición. Devuelve (ok, segundos, motivo_si_falla)."""
    t0 = time.time()
    try:
        r = cffi.get(url, impersonate="chrome", timeout=25, headers=cabeceras)
    except Exception as e:                                    # noqa: BLE001
        return False, time.time() - t0, str(e)[:30]
    dt = time.time() - t0
    if r.status_code in (403, 429, 503):
        return False, dt, "HTTP %s" % r.status_code
    if any(d in r.text[:4000].lower() for d in DESAFIO):
        return False, dt, "desafío WAF"
    if r.status_code >= 400:
        return False, dt, "HTTP %s" % r.status_code
    return True, dt, ""


def _tanda(url, cabeceras, hilos):
    """Manda PETICIONES_POR_ESCALON con `hilos` en paralelo."""
    with futuros.ThreadPoolExecutor(max_workers=hilos) as pool:
        res = list(pool.map(lambda _: _una(url, cabeceras),
                            range(PETICIONES_POR_ESCALON)))
    oks = [r for r in res if r[0]]
    tasa = len(oks) / len(res)
    tiempos = [r[1] for r in res]
    motivos = {r[2] for r in res if not r[0]}
    return tasa, statistics.median(tiempos), motivos


def medir(nombre, url, cabeceras):
    print("\n" + "=" * 66)
    print("%s" % nombre)
    print("=" * 66)
    base_t = None
    tope_sano = 0
    req_seg_sano = 0.0

    for hilos in ESCALONES:
        t0 = time.time()
        tasa, mediana, motivos = _tanda(url, cabeceras, hilos)
        dur = time.time() - t0
        req_seg = PETICIONES_POR_ESCALON / max(dur, 0.01)
        if base_t is None:
            base_t = mediana

        lento = mediana > base_t * FACTOR_LENTITUD
        estado = "✓"
        if tasa < UMBRAL_EXITO:
            estado = "✗ bloqueo (%s)" % ", ".join(motivos)
        elif lento:
            estado = "⚠ lento (%.0fms vs %.0fms base)" % (mediana * 1000, base_t * 1000)

        print("  %2d hilos → %4.1f req/s · éxito %3.0f%% · %4.0fms  %s"
              % (hilos, req_seg, tasa * 100, mediana * 1000, estado))

        if tasa < UMBRAL_EXITO or lento:
            break
        tope_sano = hilos
        req_seg_sano = req_seg
        time.sleep(PAUSA_ENTRE_ESCALONES)

    # Se recomienda el 60% del último escalón sano: margen para no rozar nunca
    # el punto donde la tienda empieza a incomodarse.
    seguro = req_seg_sano * 0.6
    print("  → último escalón cómodo: %d hilos (%.1f req/s)" % (tope_sano, req_seg_sano))
    print("  → RITMO RECOMENDADO (60%% de margen): %.1f req/s" % seguro)
    return nombre, tope_sano, req_seg_sano, seguro


if __name__ == "__main__":
    pedidas = sys.argv[1:]
    objetivos = ({k: OBJETIVOS[k] for k in pedidas if k in OBJETIVOS}
                 if pedidas else OBJETIVOS)

    filas = []
    for nombre, (url, cab) in objetivos.items():
        try:
            filas.append(medir(nombre, url, cab))
        except Exception as e:                                # noqa: BLE001
            print("  error: %s" % str(e)[:60])
        time.sleep(6)     # respiro largo entre tiendas distintas

    print("\n\n" + "=" * 66)
    print("%-16s %10s %14s %16s" % ("TIENDA", "TOPE HILOS", "REQ/S SANO", "RECOMENDADO"))
    print("=" * 66)
    for nombre, tope, sano, seguro in filas:
        print("%-16s %10d %13.1f/s %14.1f/s" % (nombre, tope, sano, seguro))
    print("\nEl 'recomendado' es el ritmo al que operar sin rozar el bloqueo.")
