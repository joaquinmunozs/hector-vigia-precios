# -*- coding: utf-8 -*-
"""Punto de entrada para GitHub Actions. Lo que en Modal hacía `modal_app.py`.

QUÉ HACE UNA CORRIDA
------------------------------------------------------------------------------
  1. Lanza el VIGILANTE en un hilo: vigila la lista caliente (~1.500 productos
     deseables) cada pocos segundos, durante casi toda la ventana.
  2. En paralelo hace la BARRIDA: recorre hasta 250.000 productos del catálogo
     completo, con rotación, para no dejar nada sin mirar nunca.
  3. Los lunes descubre productos nuevos; los días 1 y 15 recalibra las
     referencias de precio.

POR QUÉ EL VIGILANTE ES UN HILO Y NO OTRO PROCESO
------------------------------------------------------------------------------
En Modal esto corría como dos contenedores separados escribiendo el MISMO
archivo SQLite, y lo corrompió de verdad (7-ago-2026: "database disk image is
malformed", ni `integrity_check` podía leerlo). Dentro de un solo proceso, con
una conexión por hilo y modo WAL, SQLite sí da la garantía que se necesita.

Y OJO: la conexión del vigilante se abre DENTRO de su propio hilo. sqlite3
prohíbe usar una conexión desde un hilo distinto al que la creó — abrirla
afuera y pasarla como argumento falla en silencio y el vigilante corre cero
ciclos sin que nadie se entere. Ya pasó una vez.

DURACIÓN
------------------------------------------------------------------------------
GitHub Actions corta cualquier job a las 6 h, sin excepción. El ciclo se
programa cada 6 h, así que la ventana útil es de ~5.25 h para dejar margen de
cierre: hay que alcanzar a hacer el checkpoint de SQLite y subir el artifact,
o se pierde el trabajo de toda la corrida.
"""
import datetime
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

import alertas
import baseprecios
import depurar_robots
import vigia
import vigilante

# Cuánto dura el vigilante. El job entero tiene 350 min de tope y el cron
# corre cada 6 h; 5.25 h deja margen para cerrar, hacer checkpoint y subir el
# artifact antes de cualquier corte.
SEGUNDOS_VIGILANTE = int(5.25 * 3600)

# Tope duro de la barrida (10-ago-2026, ver `barrida.segundos_max` en
# vigia.py): con esto corriendo a la par del vigilante, el peor caso es
# SEGUNDOS_VIGILANTE + este margen de overhead, nunca "lo que tarde la
# barrida esta vez" — que es justo lo que se rompió cuando una tienda
# empezó a bloquear al runner a mitad de camino. 3 h deja de sobra para
# los ~3 h que toma un catálogo sano y corta antes de que el vigilante
# (5.25 h) termine, así el `hilo.join()` de más abajo espera poco.
SEGUNDOS_BARRIDA = int(3.0 * 3600)


def _es_lunes_temprano(ahora):
    return ahora.weekday() == 0 and ahora.hour < 8


# Descubrimiento a pedido, sin esperar al lunes.
#
# Por qué existe: el descubrimiento solo corre con el catálogo vacío o los
# lunes antes de las 08:00 UTC. Si el catálogo se pierde o queda a medias un
# lunes por la tarde (pasó el 10-ago-2026: quedó en 3 tiendas de 44), no hay
# forma de repoblarlo hasta el lunes siguiente — una semana vigilando una
# fracción del retail sin que nada avise.
#
# Es SOLO descubrimiento y termina: no lanza el vigilante ni la barrida. Eso
# es deliberado. Descubrir 44 tiendas y ADEMÁS aguantar el vigilante de 3,4 h
# se pasaría del tope de 350 min del job y se perdería todo al cortarse. Así
# la corrida es corta, guarda el catálogo en el artifact, y la siguiente
# corrida programada ya arranca con todo.
def _solo_descubrir():
    return os.environ.get("HECTOR_SOLO_DESCUBRIR", "").strip() == "1"



# ── Una barrida completa AL DÍA, no cada 4 horas ─────────────────────────
#
# Corregido el 11-ago-2026 a instancia de Joaquín, y tenía razón. Con los
# hilos subidos, el catálogo entero cabe en media hora, y la tentación era
# barrerlo en cada corrida de 4 h. Eso rompe el producto por dos lados:
#
# 1. LA MEDIANA DEJA DE SER HISTÓRICA. `baseprecios.historial` usa las
#    últimas 60 lecturas. A 6 barridas al día, esas 60 lecturas cubren 10
#    días: la "referencia histórica" pasaría a ser un promedio de la semana
#    pasada. A una barrida al día cubren dos meses, que es lo que el usuario
#    entiende por histórico y lo que hace que una caída signifique algo.
#
# 2. RIESGO DE BLOQUEO. 439.375 fichas × 6 veces al día son 2,6 millones de
#    peticiones diarias contra las mismas 24 tiendas. Ninguna medición de
#    ritmo cubre ese volumen sostenido.
#
# La lista caliente NO cambia: sigue corriendo sin parar en cada corrida, y
# es la que detecta un error de precio en menos de un minuto. La barrida
# completa es para construir historial, y el historial se construye con
# lecturas espaciadas, no con muchas seguidas.
def _toca_barrida_completa(ahora):
    """Solo en la primera corrida del día (la de las 00:00 UTC).

    `HECTOR_BARRIDA_COMPLETA=1` la fuerza sin mirar la hora. Existe porque
    atarla al reloj tiene un modo de falla real: las corridas toman el SHA
    del momento en que se CREAN y pueden quedar encoladas horas, así que la
    corrida "de las 00:00" puede terminar arrancando a las 04:00 y saltarse
    la barrida del día sin que nadie se entere. Y para ponerse al día con el
    catálogo hace falta poder pedirla a mano, no esperar al reloj.
    """
    if os.environ.get("HECTOR_BARRIDA_COMPLETA", "").strip() == "1":
        print("\nBarrida completa FORZADA (pedida a mano).")
        return True
    return ahora.hour < 4


DIAS_ENTRE_RECALIBRACIONES = 7
_CLAVE_RECALIBRACION = "ultima_recalibracion"


def _ultima_recalibracion(con):
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    f = con.execute("SELECT valor FROM marcadores WHERE clave=?",
                    (_CLAVE_RECALIBRACION,)).fetchone()
    return f["valor"] if f else 0


def _anotar_recalibracion(con, ahora=None):
    ahora = int(ahora or time.time())
    con.execute("CREATE TABLE IF NOT EXISTS marcadores ("
                "clave TEXT PRIMARY KEY, valor INTEGER NOT NULL)")
    con.execute("INSERT INTO marcadores (clave, valor) VALUES (?,?) "
                "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
                (_CLAVE_RECALIBRACION, ahora))
    con.commit()


def _toca_recalibrar(con, ahora=None):
    """Se refrescan las referencias cuando pasó una semana desde la última.

    Va espaciado a propósito: una referencia que se actualiza muy seguido se
    "acostumbra" a un precio bajo y deja de verlo como caída. Ese espaciado NO
    cambió — lo que cambió es contra qué se mide.

    ANTES SE MIRABA EL DÍA DEL MES (16-ago-2026)
    --------------------------------------------------------------------------
    Decía `ahora.day in (1, 15)`. Parece equivalente y no lo es: la condición
    real de `recalcular_bases` es tener DIAS_MINIMOS_HISTORIAL (7) días de
    historial, y el historial no arranca el día 1 del mes — arranca cuando
    arranca, y vuelve a cero cada vez que se pierde la base.

    Lo que pasó en producción: el historial de Héctor empieza el 10-ago, así
    que el 15-ago tenía 5 días cuando hacían falta 7. Las 4 corridas de ese día
    imprimieron "recalibración: 0 referencias actualizadas" y la siguiente
    oportunidad caía recién el 1-sep. Resultado: las 190.317 líneas base
    seguían diciendo `inicial` — o sea, la referencia de cada producto era el
    PRIMER precio que se le vio, no su precio normal.

    Y eso apagaba dos de los tres canales: `evaluar()` exige historial para
    anunciar ofertas y rebajas de categoría, así que solo salían errores de
    precio. Los avisos cayeron de 160 el 11-ago a entre 0 y 6 por día.

    Ahora se pregunta lo que de verdad importa: ¿pasaron 7 días desde la
    última vez? Si nunca se hizo, alcanza con que el historial dé para algo.
    """
    ahora = int(ahora or time.time())
    ultima = _ultima_recalibracion(con)
    if ultima:
        return (ahora - ultima) >= DIAS_ENTRE_RECALIBRACIONES * 86400
    # Nunca se recalibró: se hace en cuanto haya un producto que califique,
    # sin esperar a ninguna fecha. Se pregunta a la base en vez de suponer,
    # porque `recalcular_bases` usa exactamente el mismo corte.
    corte = ahora - baseprecios.DIAS_MINIMOS_HISTORIAL * 86400
    f = con.execute(
        "SELECT 1 FROM precios WHERE precio>0 AND visto_en>0 AND visto_en<=? "
        "LIMIT 1", (corte,)).fetchone()
    return bool(f)


def main():
    ahora = datetime.datetime.now()
    print("=" * 70)
    print("HÉCTOR — corrida del %s" % ahora.strftime("%d-%m-%Y %H:%M"))
    print("=" * 70)

    con = baseprecios.abrir()
    e = baseprecios.estadisticas(con)
    print("estado inicial: %s vigilados · %s con precio · %s observaciones"
          % (format(e["vigilados"], ",d").replace(",", "."),
             format(e["con_precio"], ",d").replace(",", "."),
             format(e["observaciones"], ",d").replace(",", ".")))

    if _solo_descubrir():
        print("\nModo SOLO DESCUBRIR (pedido a mano): se repuebla el catálogo\n"
              "y se cierra. No corre ni el vigilante ni la barrida.\n")
        n = vigia.descubrir_productos(con)
        print("fichas nuevas: %d" % n)
        try:
            revisadas, excluidas = depurar_robots.depurar(con)
            if excluidas:
                print("robots.txt: %d ficha(s) sacadas del catálogo (de %d revisadas)"
                      % (excluidas, revisadas))
        except Exception as ex:                          # noqa: BLE001
            print("depuración de robots.txt falló (el catálogo sí quedó): %s" % str(ex)[:120])
        print("\nestado final:", baseprecios.estadisticas(con))
        # Mismo checkpoint que abajo: sin esto el artifact sube una base
        # "válida" pero sin lo que se acaba de descubrir.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
        if os.path.exists(baseprecios.RUTA):
            print("base final: %.1f MB" % (os.path.getsize(baseprecios.RUTA) / 1024 / 1024))
        return 0

    # Catálogo vacío (primera corrida, o artifact perdido): hay que descubrir
    # antes de poder barrer nada.
    if e["vigilados"] == 0:
        print("\nCatálogo vacío — descubriendo productos desde los sitemaps...")
        n = vigia.descubrir_productos(con)
        print("fichas nuevas: %d" % n)
    elif _es_lunes_temprano(ahora):
        print("\nLunes: buscando productos nuevos...")
        try:
            n = vigia.descubrir_productos(con)
            print("fichas nuevas: %d" % n)
        except Exception as ex:                          # noqa: BLE001
            print("descubrimiento falló (la corrida sigue): %s" % str(ex)[:120])

        # Mismo día que el descubrimiento: por si alguna tienda cambió su
        # robots.txt, o quedó algo de antes de que `descubrir.fichas_de`
        # empezara a filtrar por Disallow (9-ago-2026).
        try:
            revisadas, excluidas = depurar_robots.depurar(con)
            if excluidas:
                print("robots.txt: %d ficha(s) sacadas del catálogo (de %d revisadas)"
                      % (excluidas, revisadas))
        except Exception as ex:                          # noqa: BLE001
            print("depuración de robots.txt falló (la corrida sigue): %s" % str(ex)[:120])

    notificador = alertas.NotificadorTelegram()
    # (codex) Un único pool sirve a barrida y vigilante durante toda la tanda.
    pool_parseo = vigilante.crear_pool_parseo()

    def _vigilar():
        # La conexión se abre ACÁ DENTRO, en el hilo que la va a usar.
        try:
            con_v = baseprecios.abrir()
            n = vigilante.correr(con_v, avisar=True,
                                 segundos_max=SEGUNDOS_VIGILANTE,
                                 notificador=notificador,
                                 pool_parseo=pool_parseo)
            con_v.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con_v.close()
            print("vigilante: %s hallazgos en la tanda" % n)
        except Exception as ex:                          # noqa: BLE001
            print("vigilante falló: %s" % str(ex)[:150])

    hilo = threading.Thread(target=_vigilar, daemon=True)
    hilo.start()
    print("\nvigilante lanzado en paralelo (%.1f h)\n" % (SEGUNDOS_VIGILANTE / 3600))

    if _toca_barrida_completa(ahora):
        hallazgos = vigia.barrida(con, avisar=True,
                                  segundos_max=SEGUNDOS_BARRIDA,
                                  notificador=notificador,
                                  pool_parseo=pool_parseo)
    else:
        print("\nSin barrida completa: hoy ya se hizo (solo corre a las 00:00 UTC).")
        print("El vigilante sigue vigilando la lista caliente.\n")
        hallazgos = []
    print("\nestado tras la barrida:", baseprecios.estadisticas(con))

    if _toca_recalibrar(con):
        try:
            tocados = baseprecios.recalcular_bases(con)
            print("recalibración: %d referencias actualizadas" % tocados)
            # Solo se anota si de verdad actualizó algo. Si dio 0 (historial
            # todavía corto), NO se marca: así vuelve a intentarlo en la
            # corrida siguiente en vez de dormir otros 7 días — que es
            # exactamente el modo de falla que tuvo el 15-ago.
            if tocados:
                _anotar_recalibracion(con)
        except Exception as ex:                          # noqa: BLE001
            print("recalibración falló (la barrida sí quedó): %s" % str(ex)[:120])

    # El checkpoint fuerza que todo lo escrito quede en el .db principal, no
    # en el archivo -wal: si el artifact se sube sin esto, la corrida
    # siguiente restaura una base "válida" pero sin los datos recientes.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    # Se espera al vigilante ANTES de terminar: el workflow sube el artifact
    # apenas el proceso muere, y cortarlo a mitad de escritura es exactamente
    # lo que corrompió la base en Modal.
    print("\nesperando al vigilante para cerrar limpio...")
    hilo.join()
    if pool_parseo:
        pool_parseo.shutdown(wait=True, cancel_futures=True)
    print("esperando alertas pendientes de Telegram...")
    notificador.cerrar()
    # El notificador tiene su propia conexión y puede haber escrito después
    # del checkpoint anterior. Este es el checkpoint definitivo del artifact.
    con_final = baseprecios.abrir()
    con_final.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con_final.close()

    ruta = baseprecios.RUTA
    if os.path.exists(ruta):
        print("base final: %.1f MB" % (os.path.getsize(ruta) / 1024 / 1024))
    print("hallazgos de la barrida: %d" % len(hallazgos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
