# -*- coding: utf-8 -*-
"""Vigilancia continua de la lista caliente. Detección en segundos.

    python vigilante.py                # corre hasta que lo cortes
    python vigilante.py --ciclos 3     # 3 vueltas y termina (para probar)
    python vigilante.py --sin-avisar   # no manda nada a Telegram

CÓMO SE DIFERENCIA DE LA BARRIDA
------------------------------------------------------------------------------
`vigia.py` recorre 250.000 productos una vez cada 4 horas. Este recorre ~1.500
una y otra vez, sin parar. Es la diferencia entre revisar todo el catálogo de
vez en cuando y tener a alguien mirando los productos importantes todo el rato.

DOS SUB-NIVELES DENTRO DEL MISMO PRESUPUESTO (9-ago-2026)
------------------------------------------------------------------------------
"Errores de precio" y "ofertas" no tienen el mismo plazo de negocio: un error
(caída ≥70%) es el pilar del negocio y tiene que avisarse en segundos: una
oferta (35%-70%) alcanza con avisarse dentro de los 10 minutos. Meterlos en
una sola lista con un solo ritmo desperdicia presupuesto: o se trata a las
ofertas con la urgencia de un error (no cabe, son muchas más), o se trata a
los errores con la paciencia de una oferta (se pierde el pilar del negocio).

La solución no es agregar MÁS peticiones — eso es justo lo que puede
bloquearnos — es **repartir el mismo presupuesto ya medido como seguro**
(`RITMO_SEGURO`) en dos partes:

  🔥 FIJA      ~70% del cupo · la lista caliente de `caliente.py` (imán +
               precio) · se revisa ENTERA cada vuelta (~7 s) → detección
               de errores en segundos, siempre.
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

Con ~1.500 productos repartidos y esos ritmos, una vuelta completa toma unos
pocos segundos. O sea: si un precio se cae, se detecta en la siguiente vuelta,
no en la siguiente barrida.

POR QUÉ NO GUARDA TODAS LAS LECTURAS
------------------------------------------------------------------------------
A esta frecuencia se leen millones de precios al día. Guardarlos todos
inflaría la base sin aportar nada: el historial que sirve para calcular la
referencia se construye con lecturas espaciadas, no con miles del mismo minuto.
Por eso solo se guarda cuando el precio CAMBIA respecto a la última lectura.
"""
import argparse
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
    "falabella.com": 88.0,     # su API ni se inmuta: 100% a 120 hilos
    "tottus.cl": 49.0,
    "adidas.cl": 40.0,
    # Paris se estanca en ~27 req/s pase lo que pase con los hilos, y responde
    # en ~1 seg: su HTML pesa 1,9 MB. No bloquea, simplemente es pesado. Por
    # eso aporta pocos productos a la lista caliente pese a ser un retail
    # grande — el peso de la página es el límite, no su WAF.
    "paris.cl": 16.0,
    # 15,4 y no 8: la medición vieja apuntaba a la PORTADA de spdigital, que
    # es enorme y se genera distinto que una ficha. Medido contra una ficha
    # real da casi el doble. Lección: medir siempre contra lo que se va a leer.
    "spdigital.cl": 15.0,
    "_por_defecto": 5.0,
}

# Cuántos segundos puede tardar, como máximo, en dar una vuelta de la lista
# FIJA. Es el número que de verdad define el pilar del negocio: si la vuelta
# demora 7 s, un ERROR de precio se detecta a los 7 s como peor caso — bajo
# el margen de 10 s pedido.
VUELTA_OBJETIVO = 7.0

# Qué fracción del cupo de cada tienda va a la lista FIJA (errores) vs. a la
# ROTATIVA (ofertas). 70/30: el pilar del negocio se queda con la mayoría,
# pero el 30% que rota alcanza de sobra para la ventana de 10 min — ver la
# cuenta en `cargar_lista`.
PROPORCION_FIJA = 0.7

# Tope de tiempo para que la lista ROTATIVA dé una vuelta completa. No es una
# meta que se persiga con más peticiones: el tamaño de la lista se recorta
# para que quepa en esta ventana con el cupo ya asignado (ver `cargar_lista`).
VENTANA_OFERTAS_SEG = 600.0   # 10 minutos

PAUSA_ENTRE_VUELTAS = 0.0      # sin respiro: la vuelta ya está limitada por ritmo


def _ritmo(tienda):
    return RITMO_SEGURO.get(tienda, RITMO_SEGURO["_por_defecto"])


def _leer(tienda, url):
    especial = adaptadores.para(tienda)
    if especial:
        d = especial(url, lambda u, c: descubrir.bajar(u, tiempo=15, cabeceras=c))
        if d:
            return d
    return extractor.extraer(descubrir.bajar(url, tiempo=15))


def cupo(tienda, vuelta=VUELTA_OBJETIVO):
    """Cuántos productos de esta tienda caben respetando la vuelta objetivo.

    Es una división simple pero es LA fórmula del sistema:

        productos = ritmo_seguro × segundos_de_vuelta

    Falabella a 88 req/s con vuelta de 7 s son 616 productos. Ripley a 5 req/s,
    solo 35. Por eso no se reparte el cupo en partes iguales: cada tienda
    aporta según lo rápido que responda, y una tienda lenta no puede arrastrar
    a las demás.
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

    plan = {}
    for t in set(fija_por_tienda) | set(resto_por_tienda):
        cupo_total = cupo(t, vuelta)
        cupo_fijo = max(1, int(cupo_total * PROPORCION_FIJA))
        cupo_rot = max(0, cupo_total - cupo_fijo)

        # Cuántas vueltas entran en la ventana de 10 min, y por lo tanto
        # cuántas candidatas puede cubrir la rotación sin pasarse del cupo
        # ya asignado — la cuenta que hace que "10 minutos" sea una
        # consecuencia del presupuesto, no una promesa sin respaldo.
        vueltas_en_ventana = max(1, int(VENTANA_OFERTAS_SEG / vuelta))
        tope_rotativa = cupo_rot * vueltas_en_ventana

        candidatas = sorted(resto_por_tienda.get(t, []), key=lambda x: -x[2])
        plan[t] = {
            "fija": fija_por_tienda.get(t, []),
            "rotativa": candidatas[:tope_rotativa],
            "cupo_rot": cupo_rot,
        }
    return plan


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


def _vigilar_tienda(tienda, plan, salida, parar):
    """Recorre en bucle los productos de UNA tienda, a su ritmo seguro.

    Cada vuelta: TODA la lista fija (errores, siempre) + una ventana de la
    rotativa (ofertas, una tajada distinta cada vez). El ritmo por producto
    es el mismo para ambas — lo que cambia es cuánto de cada una entra en el
    cupo, no la velocidad a la que se lee.
    """
    intervalo = 1.0 / _ritmo(tienda)
    fija = plan["fija"]
    rotativa = plan["rotativa"]
    cupo_rot = plan["cupo_rot"]
    desde = 0
    while not parar.is_set():
        ventana, desde = _ventana_rotativa(rotativa, desde, cupo_rot)
        for url, _nombre, _precio in fija + ventana:
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
    for t, p in sorted(plan.items(), key=lambda x: -len(x[1]["fija"])):
        vuelta = (len(p["fija"]) + p["cupo_rot"]) / _ritmo(t)
        vueltas_rot = len(p["rotativa"]) / max(1, p["cupo_rot"])
        print("   %-18s %4d fijos + %4d rotativos (de %5d) · %.1f req/s · "
              "vuelta ~%.0f seg · ofertas completas cada ~%.0f min"
              % (t, len(p["fija"]), p["cupo_rot"], len(p["rotativa"]), _ritmo(t),
                 vuelta, vueltas_rot * vuelta / 60))

    peor_error = max((len(p["fija"]) + p["cupo_rot"]) / _ritmo(t) for t, p in plan.items())
    print("\n   → errores: detección hasta %.0f segundos" % peor_error)
    print("   → ofertas fuera de la lista de errores: hasta %.0f min\n"
          % (VENTANA_OFERTAS_SEG / 60))

    salida = queue.Queue()
    parar = threading.Event()
    hilos = [threading.Thread(target=_vigilar_tienda,
                              args=(t, p, salida, parar), daemon=True)
             for t, p in plan.items()]
    for h in hilos:
        h.start()

    # Peticiones por vuelta (fija + ventana rotativa), sumadas entre tiendas.
    # Solo se usa para `--ciclos` en las pruebas — el corte real en
    # producción es `segundos_max`, no un conteo de peticiones.
    total = sum(len(p["fija"]) + p["cupo_rot"] for p in plan.values())
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
            baseprecios.guardar(con, tienda, url, d["nombre"], precio)
            if not baseprecios._base_de(con, url):
                baseprecios.fijar_base(con, url, precio, "inicial")
            # Si esta URL tenía un error sin resolver y el precio ya volvió a
            # su referencia, queda medido cuánto tardó la tienda en
            # corregirlo — es la única forma real de responder "cuánto dura
            # un error de precio", y solo el vigilante relee seguido como
            # para poder medirlo.
            baseprecios.marcar_si_restablecido(con, url, precio)
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
                    threading.Thread(target=alertas.enviar_hallazgos,
                                     args=(con, [det]), daemon=True).start()

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

    dur = max(1, time.time() - inicio)
    print("\nleídos: %d (%.1f/seg) · cambios: %d · hallazgos: %d · %.0f seg"
          % (leidos, leidos / dur, len(ultimo_precio), hallazgos, dur))
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
