# -*- coding: utf-8 -*-
"""Cuántas peticiones por segundo aguanta cada aerolínea.

    python medir_vuelos.py            # todas
    python medir_vuelos.py latam sky  # solo esas

Reusa el motor de `medir_limites.py` (misma escalera de concurrencia, mismos
criterios de bloqueo) y solo cambia dos cosas, a propósito:

ESCALERA MÁS SUAVE QUE LA DE LAS TIENDAS
------------------------------------------------------------------------------
Las tiendas se miden hasta 120 hilos porque ninguna se inmutó a 20. Las
aerolíneas no son eso: casi todas están detrás de Akamai, Incapsula o
Cloudflare con reglas de fraude, porque su negocio es que nadie les raspe
tarifas. Un 429 de Falabella se olvida en un rato; una IP marcada por LATAM o
Copa puede quedar así por días, y es la IP de la casa de Joaquín.

Además no hace falta: son 23 páginas, no 155.000 fichas. A 3 req/s la vuelta
completa toma 8 segundos. Medir si aguantan 120 hilos es preguntar algo que
no vamos a usar nunca.

MENOS PETICIONES POR ESCALÓN
------------------------------------------------------------------------------
20 en vez de 30. Con 23 aerolíneas, cada petición de más se multiplica por 23
y alarga la medición sin agregar precisión.

QUÉ HACER CON EL RESULTADO
------------------------------------------------------------------------------
El "recomendado" de la última columna es el ritmo al que Héctor puede pegarle
sin llamar la atención. Va a `vigilante.py` igual que el de las tiendas.
"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import aerolineas
import medir_limites as motor

# Ver el docstring: escalera suave y tanda corta. Se reemplazan los valores
# del módulo porque el motor los lee al vuelo, así no hay que duplicar código.
motor.ESCALONES = (2, 5, 10, 20, 40)
motor.PETICIONES_POR_ESCALON = 20

OBJETIVOS = aerolineas.objetivos()


def main():
    pedidas = [a for a in sys.argv[1:] if not a.startswith("-")]
    objetivos = ({k: OBJETIVOS[k] for k in pedidas if k in OBJETIVOS}
                 if pedidas else OBJETIVOS)
    if pedidas and not objetivos:
        print("No conozco: %s" % ", ".join(pedidas))
        print("Claves validas: %s" % ", ".join(sorted(OBJETIVOS)))
        return

    print("Midiendo %d aerolineas. Escalera %s, %d peticiones por escalon."
          % (len(objetivos), motor.ESCALONES, motor.PETICIONES_POR_ESCALON))

    filas = []
    for nombre, (url, cab) in objetivos.items():
        try:
            filas.append(motor.medir(nombre, url, cab))
        except Exception as e:                                # noqa: BLE001
            print("  error: %s" % str(e)[:70])
            filas.append((nombre, 0, 0.0, 0.0))
        time.sleep(5)     # respiro entre aerolineas distintas

    print("\n\n" + "=" * 70)
    print("%-18s %10s %14s %16s" % ("AEROLINEA", "TOPE HILOS", "REQ/S SANO",
                                    "RECOMENDADO"))
    print("=" * 70)
    for nombre, tope, sano, seguro in sorted(filas, key=lambda f: -f[3]):
        aviso = "  <- no se pudo medir" if tope == 0 else ""
        print("%-18s %10d %13.1f/s %14.1f/s%s" % (nombre, tope, sano, seguro, aviso))
    print("\nEl 'recomendado' es el 60%% del ultimo escalon comodo: el ritmo al")
    print("que operar sin rozar nunca el bloqueo.")

    vivas = [f for f in filas if f[1] > 0]
    if vivas:
        total = sum(1 for _ in vivas)
        lento = min(f[3] for f in vivas)
        print("\n%d de %d aerolineas medidas. La mas lenta admite %.1f req/s,"
              % (total, len(filas), lento))
        print("asi que una vuelta completa a las %d toma ~%.0f segundos."
              % (total, total / max(lento, 0.1)))


if __name__ == "__main__":
    main()
