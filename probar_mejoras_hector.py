# -*- coding: utf-8 -*-
"""(codex) Pruebas locales de clasificación, alertas y shards."""
import os
import concurrent.futures
import multiprocessing
import sqlite3
import tempfile
import time

import alertas
import baseprecios
import combinar_bases
import particionar_base
import vigia
import vigilante


def _sembrar(ruta, tienda, url, precio):
    anterior = baseprecios.RUTA
    baseprecios.RUTA = ruta
    try:
        con = baseprecios.abrir()
        baseprecios.guardar(con, tienda, url, "Producto", precio, cuando=100)
        baseprecios.fijar_base(con, url, precio, cuando=100)
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.close()
    finally:
        baseprecios.RUTA = anterior


def probar_clasificacion():
    assert vigilante._nivel(1, 100, 100 + 6 * 86400) == "desconocido"
    assert vigilante._nivel(1, 100, 100 + 8 * 86400) == "quieto"
    assert vigilante._nivel(2, 100, 101) == "movil"


def probar_ritmo_adaptativo():
    control = vigilante._ControlRitmo("prueba.cl", 10.0)
    for _ in range(20):
        control.resultado(False)
    reducido = control.ritmo()
    assert reducido == 5.0
    control.ultimo_ajuste -= 31
    for _ in range(200):
        control.resultado(True)
    assert reducido < control.ritmo() <= 10.0
    assert vigilante.SONDEO_BLOQUEADA_SEG >= 15 * 60


def probar_ritmo_no_se_atasca_en_el_piso():
    """(20-ago-2026) La corrida 32371236694 mostró a falabella.com atrapada
    3+ horas en su piso (10% del objetivo) con 92,5% de éxito: bajar
    necesitaba 20 de 40 (15%) cada 30s, subir necesitaba 200 sanas seguidas
    cada 60s -- una asimetría de 5x en cantidad y 2x en tiempo. Esta prueba
    fija el umbral y el cooldown de recuperación para que no vuelvan a
    desacoplarse de los de bajar."""
    assert vigilante.UMBRAL_RECUPERACION <= 3 * 40   # no 5x la ventana de bajar (20 de 40)

    control = vigilante._ControlRitmo("prueba.cl", 10.0)
    for _ in range(20):
        control.resultado(False)
    bajado = control.ritmo()
    assert bajado == 5.0

    # Con el umbral viejo (200) sanas y este mismo número de peticiones, esto
    # NO alcanzaba para subir ni un escalón.
    control.ultimo_ajuste -= 31
    for _ in range(vigilante.UMBRAL_RECUPERACION):
        control.resultado(True)
    subido = control.ritmo()
    assert subido > bajado

    # El cooldown de subir tiene que ser el mismo que el de bajar (30s), no
    # el doble: si no, subir sigue siendo más lento que bajar aunque el
    # umbral de conteo ya esté arreglado.
    control.ultimo_ajuste -= 31
    for _ in range(vigilante.UMBRAL_RECUPERACION):
        control.resultado(True)
    assert control.ritmo() > subido


def probar_shards(carpeta):
    origen = os.path.join(carpeta, "origen.db")
    _sembrar(origen, "a.cl", "https://a.cl/1", 1000)
    _sembrar(origen, "b.cl", "https://b.cl/1", 2000)
    a = os.path.join(carpeta, "a.db")
    b = os.path.join(carpeta, "b.db")
    import shutil
    shutil.copy2(origen, a)
    shutil.copy2(origen, b)
    particionar_base.particionar(a, ["a.cl"], vacuum=False)
    particionar_base.particionar(b, ["b.cl"], vacuum=False)
    salida = os.path.join(carpeta, "unida.db")
    assert combinar_bases.combinar([a, b], salida) == 2
    con = sqlite3.connect(salida)
    assert con.execute("SELECT COUNT(DISTINCT tienda) FROM precios").fetchone()[0] == 2
    con.close()


def probar_notificador(carpeta):
    ruta = os.path.join(carpeta, "avisos.db")
    anterior_ruta = baseprecios.RUTA
    anterior_intervalo = alertas.INTERVALO_TELEGRAM
    anterior_enviar = alertas._enviar
    enviados = []
    baseprecios.RUTA = ruta
    con = baseprecios.abrir()
    con.close()
    alertas.INTERVALO_TELEGRAM = 0
    alertas._enviar = lambda texto, topico_id=None: enviados.append(texto) or True
    try:
        n = alertas.NotificadorTelegram(coalescer_seg=0.1)
        base = {"url": "https://x.cl/1", "nombre": "Producto", "tienda": "x.cl",
                "precio": 1000, "referencia": 10000, "caida": 0.9,
                "con_historial": True, "historico": []}
        oferta = dict(base, url="https://x.cl/oferta", tipo=baseprecios.OFERTA,
                      caida=0.5, precio=5000)
        error = dict(base, tipo=baseprecios.ERROR)
        n.enviar(oferta, time.time())
        n.enviar(error, time.time())
        n.cerrar()
        assert len(enviados) == 2
        assert "SRank" in enviados[0]
    finally:
        alertas._enviar = anterior_enviar
        alertas.INTERVALO_TELEGRAM = anterior_intervalo
        baseprecios.RUTA = anterior_ruta


def probar_parseo_multiproceso():
    html = ('<html><head><meta property="product:price:amount" content="10000">'
            '<meta property="og:title" content="Producto"></head><body>' +
            ("x" * 4000) + "</body></html>")
    anterior = vigilante.descubrir.bajar
    vigilante.descubrir.bajar = lambda *a, **k: html
    try:
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=2,
                mp_context=multiprocessing.get_context("spawn")) as pool:
            d = vigilante._leer("prueba.cl", "https://prueba.cl/uno",
                                esperado=10000, pool=pool)
        assert d["precio"] == 10000
    finally:
        vigilante.descubrir.bajar = anterior


def probar_barrida_con_pool(carpeta):
    ruta = os.path.join(carpeta, "barrida.db")
    _sembrar(ruta, "prueba.cl", "https://prueba.cl/uno", 10000)
    html = ('<html><head><meta property="product:price:amount" content="10000">'
            '<meta property="og:title" content="Producto"></head><body>' +
            ("x" * 4000) + "</body></html>")
    anterior_ruta = baseprecios.RUTA
    anterior_bajar = vigilante.descubrir.bajar
    baseprecios.RUTA = ruta
    vigilante.descubrir.bajar = lambda *a, **k: html
    try:
        con = baseprecios.abrir()
        hallazgos = vigia.barrida(con, avisar=False, limite=1, segundos_max=10)
        con.close()
        assert hallazgos == []
    finally:
        vigilante.descubrir.bajar = anterior_bajar
        baseprecios.RUTA = anterior_ruta


def probar_vigilante_con_pool(carpeta):
    ruta = os.path.join(carpeta, "vigilante.db")
    _sembrar(ruta, "prueba.cl", "https://prueba.cl/uno", 10000)
    html = ('<html><head><meta property="product:price:amount" content="10000">'
            '<meta property="og:title" content="Producto"></head><body>' +
            ("x" * 4000) + "</body></html>")
    anterior_ruta = baseprecios.RUTA
    anterior_bajar = vigilante.descubrir.bajar
    baseprecios.RUTA = ruta
    vigilante.descubrir.bajar = lambda *a, **k: html
    try:
        con = baseprecios.abrir()
        assert vigilante.correr(con, avisar=False, ciclos=1) == 0
        con.close()
    finally:
        vigilante.descubrir.bajar = anterior_bajar
        baseprecios.RUTA = anterior_ruta


def main():
    probar_clasificacion()
    probar_ritmo_adaptativo()
    probar_ritmo_no_se_atasca_en_el_piso()
    probar_parseo_multiproceso()
    with tempfile.TemporaryDirectory(prefix="hector-mejoras-") as carpeta:
        probar_shards(carpeta)
        probar_notificador(carpeta)
        probar_barrida_con_pool(carpeta)
        probar_vigilante_con_pool(carpeta)
    print("✅ clasificación temporal, shards y cola priorizada")


if __name__ == "__main__":
    main()
