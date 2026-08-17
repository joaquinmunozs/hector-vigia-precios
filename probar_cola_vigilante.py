# -*- coding: utf-8 -*-
"""Lo que se baja de una tienda no se puede tirar en silencio.

    python probar_cola_vigilante.py

QUÉ SE ROMPIÓ (16-ago-2026)
------------------------------------------------------------------------------
Héctor llevaba días con casi cero alertas pese a bajar medio millón de fichas
por corrida. Verificado contra los logs reales de producción (runs 31901450697,
31932081912, 31947818446, 31964875708):

    corrida        ok (bajadas)   leídos (evaluadas)   drenado
    15-ago 12:41       642.262            642.242        100%
    15-ago 18:33       472.121             74.375         16%
    16-ago 18:32       472.707             82.375         17%

`ok` lo cuenta el hilo de cada tienda cuando la descarga sale bien; `leídos` lo
cuenta el ÚNICO hilo consumidor cuando saca de la cola y evalúa. La diferencia
—unas 390.000 fichas por corrida— se bajó, se parseó, se puso en la cola… y se
tiró a la basura al cerrar la tanda, sin que `evaluar()` la mirara nunca. En el
log no aparecía ningún error: la cola era `queue.Queue()` SIN TOPE, así que
crecía en silencio y nadie se enteraba.

POR QUÉ EL CONSUMIDOR NO DA ABASTO
------------------------------------------------------------------------------
No es la base: medido contra la base real de producción (370 MB), el trabajo por
item (evaluar + guardar + commit) rinde 1.413 items/s. El consumidor real hace
6,7/s.

Es el GIL. `extractor.extraer` cuesta ~33 ms de CPU pura por ficha (corre TODAS
las estrategias sobre el HTML completo) y los ~60 hilos de tienda lo ejecutan
sin soltar el GIL. Medido en esta misma máquina: el bucle consumidor solo rinde
500 items/s; con apenas 8 hilos parseando en paralelo cae a 0,2 items/s.

La cuenta cierra con producción: 472.707 fichas × 33 ms = ~15.600 segundos de
CPU de parseo metidos en una tanda que dura 12.242 segundos. El proceso está
sobresuscrito: no queda GIL para el consumidor.

QUÉ SE ARREGLA
------------------------------------------------------------------------------
La cola pasa a tener tope. Cuando el consumidor se atrasa, el hilo de tienda se
FRENA en vez de seguir bajando fichas que nadie va a mirar — y de paso deja de
quemar GIL parseando, que es justo lo que ahoga al consumidor. Si aun así no
puede entregar, la lectura se anota como `descartado` y sale en la tabla de
salud: la pérdida deja de ser invisible.
"""
import os
import sys
import queue
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import vigilante                          # noqa: E402


def _limpiar_salud():
    with vigilante._SALUD_LOCK:
        vigilante._SALUD.clear()


def main():
    fallos = 0

    # ── 1. La cola tiene tope ─────────────────────────────────────────────
    print("1. la cola del vigilante tiene tope")
    tope = getattr(vigilante, "COLA_MAXIMA", 0)
    if not tope or tope <= 0:
        print("  ❌ COLA_MAXIMA no existe o no es un tope real (%r) — "
              "la cola vuelve a crecer sin límite" % tope)
        fallos += 1
    else:
        print("  ✅ COLA_MAXIMA = %d" % tope)

    # ── 2. Con la cola llena, la lectura NO se pierde en silencio ─────────
    print("\n2. con la cola llena, la lectura se anota como descartada")
    _limpiar_salud()
    llena = queue.Queue(maxsize=1)
    llena.put(("relleno", "u", {}))       # queda sin cupo
    antes = vigilante.ESPERA_COLA
    vigilante.ESPERA_COLA = 0.05          # que la prueba no tarde 30 s
    try:
        entregado = vigilante._entregar(llena, "hites.com", "http://x", {"precio": 1})
    finally:
        vigilante.ESPERA_COLA = antes

    if entregado:
        print("  ❌ dice que entregó en una cola sin cupo")
        fallos += 1
    else:
        desc = vigilante._SALUD.get("hites.com", {}).get("descartado", 0)
        if desc != 1:
            print("  ❌ no quedó anotado como descartado (descartado=%r) — "
                  "la pérdida sigue siendo invisible" % desc)
            fallos += 1
        else:
            print("  ✅ devuelve False y lo anota: descartado=1")

    # ── 3. Lo descartado sale en la tabla de salud ────────────────────────
    print("\n3. lo descartado aparece en la tabla de salud")
    filas = vigilante._resumen_salud()
    fila = [f for f in filas if f[0] == "hites.com"]
    if not fila:
        print("  ❌ la tienda no aparece en la tabla")
        fallos += 1
    elif len(fila[0]) < 7:
        print("  ❌ la tabla no trae columna de descartados: %r" % (fila[0],))
        fallos += 1
    else:
        print("  ✅ sale en la tabla con su columna de descartados")

    # ── 4. Contrapresión: el productor se frena, no acumula ───────────────
    print("\n4. el productor se frena cuando el consumidor se atrasa")
    _limpiar_salud()
    cola = queue.Queue(maxsize=10)
    parar = threading.Event()
    puestos = []

    def productor():
        for i in range(500):
            if parar.is_set():
                return
            vigilante._entregar(cola, "hites.com", "http://x/%d" % i, {"precio": i})
            puestos.append(i)

    h = threading.Thread(target=productor, daemon=True)
    h.start()
    time.sleep(0.4)                        # el consumidor NO saca nada
    parar.set()
    h.join(timeout=5)

    if cola.qsize() > 10:
        print("  ❌ la cola creció por encima del tope: %d" % cola.qsize())
        fallos += 1
    elif len(puestos) >= 500:
        print("  ❌ el productor no se frenó: metió las %d sin esperar a nadie"
              % len(puestos))
        fallos += 1
    else:
        print("  ✅ la cola quedó en %d (tope 10) y el productor se frenó "
              "en %d de 500" % (cola.qsize(), len(puestos)))

    # ── 5. Al cerrar la tanda no se inventan descartes ────────────────────
    print("\n5. cerrar la tanda no ensucia la columna de descartados")
    _limpiar_salud()
    llena = queue.Queue(maxsize=1)
    llena.put(("relleno", "u", {}))
    cerrando = threading.Event()
    cerrando.set()                         # la tanda ya terminó
    t0 = time.time()
    entregado = vigilante._entregar(llena, "hites.com", "http://x",
                                    {"precio": 1}, cerrando)
    tardo = time.time() - t0

    desc = vigilante._SALUD.get("hites.com", {}).get("descartado", 0)
    if entregado:
        print("  ❌ dice que entregó en una cola sin cupo")
        fallos += 1
    elif desc:
        print("  ❌ anotó %d descarte(s) en el cierre normal — con ~60 hilos "
              "eso son 60 falsos por corrida y la columna deja de servir" % desc)
        fallos += 1
    elif tardo > 1.0:
        print("  ❌ se quedó esperando %.1f s pese a que la tanda ya cerró" % tardo)
        fallos += 1
    else:
        print("  ✅ sale al toque (%.2f s) y sin anotar descartes falsos" % tardo)

    print()
    if fallos:
        print("❌ %d fallos" % fallos)
    else:
        print("✅ lo que se baja se evalúa o se anota — nada se tira en silencio")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
