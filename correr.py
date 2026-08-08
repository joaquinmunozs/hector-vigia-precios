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
programa cada 4 h, así que la ventana útil es de ~3.5 h para dejar margen de
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
import vigia
import vigilante

# Cuánto dura el vigilante. El job entero tiene 6 h de tope duro y el cron
# corre cada 4 h; 3.4 h deja margen para que la barrida cierre, se haga el
# checkpoint y se suba el artifact antes de cualquier corte.
SEGUNDOS_VIGILANTE = int(3.4 * 3600)


def _es_lunes_temprano(ahora):
    return ahora.weekday() == 0 and ahora.hour < 8


def _toca_recalibrar(ahora):
    """Los días 1 y 15 se refrescan las referencias con el historial real.

    Va espaciado a propósito: una referencia que se actualiza muy seguido se
    "acostumbra" a un precio bajo y deja de verlo como caída.
    """
    return ahora.day in (1, 15)


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

    def _vigilar():
        # La conexión se abre ACÁ DENTRO, en el hilo que la va a usar.
        try:
            con_v = baseprecios.abrir()
            n = vigilante.correr(con_v, avisar=True,
                                 segundos_max=SEGUNDOS_VIGILANTE)
            con_v.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con_v.close()
            print("vigilante: %s hallazgos en la tanda" % n)
        except Exception as ex:                          # noqa: BLE001
            print("vigilante falló: %s" % str(ex)[:150])

    hilo = threading.Thread(target=_vigilar, daemon=True)
    hilo.start()
    print("\nvigilante lanzado en paralelo (%.1f h)\n" % (SEGUNDOS_VIGILANTE / 3600))

    hallazgos = vigia.barrida(con, avisar=True)
    print("\nestado tras la barrida:", baseprecios.estadisticas(con))

    if _toca_recalibrar(ahora):
        try:
            tocados = baseprecios.recalcular_bases(con)
            print("recalibración: %d referencias actualizadas" % tocados)
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

    ruta = baseprecios.RUTA
    if os.path.exists(ruta):
        print("base final: %.1f MB" % (os.path.getsize(ruta) / 1024 / 1024))
    print("hallazgos de la barrida: %d" % len(hallazgos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
