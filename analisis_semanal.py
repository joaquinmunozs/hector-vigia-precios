# -*- coding: utf-8 -*-
"""Análisis semanal del movimiento de precios, escrito por Claude.

    python analisis_semanal.py            # calcula, analiza y manda a Telegram
    python analisis_semanal.py --probar   # imprime, no manda nada

QUÉ HACE Y QUÉ NO
------------------------------------------------------------------------------
Los números los calcula ESTE archivo, en SQL, sobre la base real. Claude no
ve la base ni inventa cifras: recibe un resumen ya calculado y su trabajo es
interpretarlo — por qué el retail se comporta así, qué se repite, qué conviene
esperar la semana que viene y qué habría que cambiarle al Vigía.

Esa separación es el punto. Un modelo al que se le pide "analiza los precios"
sin datos produce un texto que suena bien y no dice nada. Con las cifras
adelante, produce lecturas que se pueden verificar contra la base.

LAS CINCO PREGUNTAS QUE INTENTA RESPONDER
------------------------------------------------------------------------------
  1. ¿Qué tiendas mueven precios y cuáles están congeladas?
  2. ¿Quién infla antes de rebajar? (subir 20% para después "bajar 40%")
  3. ¿Qué día de la semana se rebaja de verdad?
  4. ¿Cuánto dura un error de precio antes de que lo corrijan?
  5. ¿Qué productos se mueven tanto que conviene vigilarlos más seguido?

La 2 y la 5 son las que valen plata: la 2 es la que separa un descuento real
de uno de cartel, y la 5 alimenta la lista caliente.

CORRE LOS DOMINGOS
------------------------------------------------------------------------------
Con una barrida completa al día, el domingo hay siete lecturas nuevas de cada
producto — suficiente para ver una semana entera, incluido el fin de semana,
que es cuando el retail chileno mueve más.
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import baseprecios
import categorias

SEMANA = 7 * 24 * 3600

# Los resúmenes semanales viven en el repo, no como artifact: son livianos y
# tienen que sobrevivir para poder mirarlos de a varios meses.
HISTORIAL = "historial"
MODELO = "claude-sonnet-5"

# Una subida de al menos esto, seguida de una bajada, es el patrón de la
# "oferta" preparada. Bajo el 8% es ruido de reposición o de tipo de cambio.
ALZA_SOSPECHOSA = 0.08

DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _pct(x):
    return round(x * 100, 1)


def _serie(con, desde):
    """{url: [(visto_en, precio), ...]} de la última semana, ordenado."""
    filas = con.execute(
        "SELECT url, tienda, nombre, precio, visto_en FROM precios "
        "WHERE visto_en >= ? AND precio > 0 ORDER BY url, visto_en", (desde,)).fetchall()
    series, meta = {}, {}
    for url, tienda, nombre, precio, visto in filas:
        series.setdefault(url, []).append((visto, precio))
        meta[url] = (tienda, nombre)
    return series, meta


def por_tienda(series, meta):
    """Cuánto se mueve cada tienda. Una tienda congelada no genera hallazgos:
    si además es cara de barrer, sale del reparto y se gana capacidad."""
    acc = {}
    for url, puntos in series.items():
        if len(puntos) < 2:
            continue
        tienda = meta[url][0]
        d = acc.setdefault(tienda, {"productos": 0, "movidos": 0, "bajadas": [], "alzas": []})
        d["productos"] += 1
        precios = [p for _, p in puntos]
        cambio = (precios[-1] - precios[0]) / precios[0]
        if abs(cambio) < 0.01:
            continue
        d["movidos"] += 1
        (d["bajadas"] if cambio < 0 else d["alzas"]).append(abs(cambio))

    salida = []
    for tienda, d in acc.items():
        if d["productos"] < 20:      # muestra muy chica para concluir nada
            continue
        salida.append({
            "tienda": tienda,
            "productos": d["productos"],
            "pct_con_movimiento": _pct(d["movidos"] / d["productos"]),
            "bajadas": len(d["bajadas"]),
            "alzas": len(d["alzas"]),
            "baja_mediana_pct": _pct(statistics.median(d["bajadas"])) if d["bajadas"] else 0,
            "alza_mediana_pct": _pct(statistics.median(d["alzas"])) if d["alzas"] else 0,
        })
    return sorted(salida, key=lambda x: -x["pct_con_movimiento"])[:15]


def inflaron_antes_de_rebajar(series, meta):
    """El patrón caro: sube, y desde ese techo nuevo "baja".

    Es la razón de existir del Vigía. Si el descuento se mide contra el precio
    inflado de ayer, un -40% de cartel puede ser un precio más alto que el de
    la semana pasada. Acá se cuenta cuántas veces pasó y en qué tiendas.
    """
    casos = []
    for url, puntos in series.items():
        precios = [p for _, p in puntos]
        if len(precios) < 3:
            continue
        techo = max(precios)
        i = precios.index(techo)
        if i == 0 or i == len(precios) - 1:
            continue                       # el techo tiene que estar en medio
        antes, despues = precios[0], min(precios[i:])
        if techo <= antes * (1 + ALZA_SOSPECHOSA) or despues >= techo:
            continue
        casos.append({
            "tienda": meta[url][0],
            "producto": (meta[url][1] or "")[:60],
            "partio": antes,
            "subio_a": techo,
            "termino_en": despues,
            "descuento_de_cartel_pct": _pct(1 - despues / techo),
            # Lo único que importa: contra el precio de hace una semana,
            # ¿es más barato o no?
            "descuento_real_pct": _pct(1 - despues / antes),
        })
    casos.sort(key=lambda c: c["descuento_de_cartel_pct"] - c["descuento_real_pct"], reverse=True)
    return casos


def dia_de_las_rebajas(series):
    """En qué día de la semana bajan los precios. El retail no improvisa:
    si las bajadas se concentran en un día, la barrida puede priorizarlo."""
    cuenta = dict.fromkeys(DIAS, 0)
    for puntos in series.values():
        for (t0, p0), (t1, p1) in zip(puntos, puntos[1:]):
            if p1 < p0 * 0.97:
                cuenta[DIAS[time.localtime(t1).tm_wday]] += 1
    return cuenta


def volatiles(series, meta, tope=15):
    """Los que más se mueven. Son los candidatos naturales a la lista caliente:
    vigilar seguido algo que no cambia nunca es gastar barrida en nada."""
    salida = []
    for url, puntos in series.items():
        precios = [p for _, p in puntos]
        if len(precios) < 4:
            continue
        med = statistics.median(precios)
        if not med:
            continue
        recorrido = (max(precios) - min(precios)) / med
        if recorrido < 0.15:
            continue
        salida.append({"tienda": meta[url][0], "producto": (meta[url][1] or "")[:60],
                       "recorrido_pct": _pct(recorrido), "min": min(precios),
                       "max": max(precios), "lecturas": len(precios)})
    return sorted(salida, key=lambda x: -x["recorrido_pct"])[:tope]


def alertas_de_la_semana(con, desde):
    filas = con.execute(
        "SELECT a.tipo, a.caida, a.precio, p.tienda, p.nombre FROM alertas a "
        "LEFT JOIN (SELECT url, tienda, nombre, MAX(visto_en) FROM precios GROUP BY url) p "
        "ON p.url = a.url WHERE a.avisado_en >= ?", (desde,)).fetchall()
    por_tipo, por_tda, caidas = {}, {}, []
    for tipo, caida, _precio, tienda, nombre in filas:
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1
        por_tda[tienda or "?"] = por_tda.get(tienda or "?", 0) + 1
        caidas.append(caida)
    return {
        "total": len(filas),
        "por_tipo": por_tipo,
        "por_tienda": dict(sorted(por_tda.items(), key=lambda x: -x[1])[:10]),
        "caida_mediana_pct": _pct(statistics.median(caidas)) if caidas else None,
    }


def reunir(ruta="precios.db"):
    # Se abre en solo lectura y aparte de baseprecios.abrir(): este análisis
    # puede correr mientras una barrida escribe, y no tiene por qué tocar el
    # esquema ni competir por el lock de escritura.
    con = sqlite3.connect("file:%s?mode=ro" % ruta, uri=True, timeout=30)
    desde = int(time.time()) - SEMANA
    series, meta = _serie(con, desde)

    medidos = con.execute("SELECT COUNT(DISTINCT url) FROM precios WHERE precio > 0").fetchone()[0]
    con_hist = con.execute(
        "SELECT COUNT(*) FROM (SELECT url FROM precios WHERE precio > 0 "
        "GROUP BY url HAVING COUNT(*) >= ?)", (baseprecios.MIN_OBSERVACIONES,)).fetchone()[0]

    inflados = inflaron_antes_de_rebajar(series, meta)
    datos = {
        "semana_hasta": time.strftime("%Y-%m-%d"),
        "cobertura": {
            "fichas_en_catalogo": con.execute("SELECT COUNT(DISTINCT url) FROM precios").fetchone()[0],
            "con_precio_medido": medidos,
            "con_historial_suficiente": con_hist,
            "leidos_esta_semana": len(series),
        },
        "movimiento_por_tienda": por_tienda(series, meta),
        "inflaron_antes_de_rebajar": {
            "casos": len(inflados),
            "sobre_productos_con_3_lecturas": sum(1 for p in series.values() if len(p) >= 3),
            "ejemplos": inflados[:12],
        },
        "bajadas_por_dia": dia_de_las_rebajas(series),
        "mas_volatiles": volatiles(series, meta),
        "alertas": alertas_de_la_semana(con, desde),
    }
    try:
        datos["duracion_errores_min"] = baseprecios.duracion_errores(con)
    except Exception:                        # noqa: BLE001 — es un extra
        pass
    con.close()
    return datos


INSTRUCCIONES = """Eres el analista del Vigía de Precios, un servicio chileno \
que avisa por Telegram cuando el retail baja precios de verdad o se equivoca al \
publicarlos. Los suscriptores pagan $6.990 al mes.

Abajo van las cifras de la última semana, calculadas sobre la base real del \
sistema. No tienes acceso a nada más: no inventes cifras, no cites números que \
no estén en los datos, y si algo no se puede concluir con lo que hay, dilo.

Escribe un análisis en español de Chile, directo, sin relleno ni saludos, con \
estas cuatro secciones y nada más:

1. QUÉ PASÓ ESTA SEMANA — lo que muestran los números, no un resumen de ellos. \
Si una tienda no movió nada, eso también es información.

2. QUÉ ESTÁ HACIENDO EL RETAIL — la estrategia detrás del patrón. Presta \
atención especial a los casos de "inflaron antes de rebajar": explica para qué \
sirve eso comercialmente y a quién engaña. Si los datos no alcanzan para \
afirmarlo, dilo en vez de suponer.

3. QUÉ ESPERAR LA PRÓXIMA SEMANA — predicciones concretas y verificables \
("tal tienda debería rebajar tal categoría alrededor de tal día"), para poder \
contrastarlas el domingo siguiente. Di también qué tan seguro estás y por qué.

4. QUÉ CAMBIARLE AL VIGÍA — recomendaciones accionables sobre el sistema \
mismo: qué tiendas barrer más o menos seguido, qué productos meter a la lista \
caliente, si algún umbral está mal puesto. Ordénalas por impacto.

Máximo 450 palabras en total. Sin markdown de encabezados (nada de #), sin \
listas anidadas: se lee en Telegram."""


def pensar(datos, api_key):
    cuerpo = {
        "model": MODELO,
        "max_tokens": 2000,
        "system": INSTRUCCIONES,
        "messages": [{"role": "user",
                      "content": json.dumps(datos, ensure_ascii=False, indent=1)}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"content-type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"})
    r = json.loads(urllib.request.urlopen(req, timeout=180).read().decode())
    return "".join(b.get("text", "") for b in r.get("content", []))


def _escapar(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def enviar(texto):
    """Telegram corta en 4096 caracteres; se parte por párrafos, no a lo bruto."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("VIGIA_CHAT_ID") or "").strip()
    topico = (os.environ.get("VIGIA_TOPICO_ANALISIS") or "").strip()
    if not token or not chat:
        print("[sin telegram configurado]\n" + texto)
        return

    trozos, actual = [], ""
    for parrafo in texto.split("\n\n"):
        if len(actual) + len(parrafo) > 3500:
            trozos.append(actual)
            actual = ""
        actual += parrafo + "\n\n"
    trozos.append(actual)

    for i, trozo in enumerate(trozos):
        cabeza = "📊 <b>Análisis semanal de precios</b>\n\n" if i == 0 else ""
        cuerpo = {"chat_id": chat, "text": cabeza + _escapar(trozo.strip()),
                  "parse_mode": "HTML", "disable_web_page_preview": True}
        if topico:
            cuerpo["message_thread_id"] = int(topico)
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % token,
            data=json.dumps(cuerpo).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
        except Exception as e:                       # noqa: BLE001
            print("telegram falló: %s" % str(e)[:150])
        time.sleep(2)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--probar", action="store_true")
    p.add_argument("--base", default="precios.db")
    a = p.parse_args()

    datos = reunir(a.base)
    print(json.dumps(datos["cobertura"], ensure_ascii=False))
    print("casos de alza previa: %d" % datos["inflaron_antes_de_rebajar"]["casos"])

    # EL HISTORIAL ES EL PRODUCTO, NO EL ANÁLISIS
    #
    # Esto se guarda SIEMPRE, aunque no haya modelo que lo lea. La base de
    # precios vive como artifact y se borra a los 3 días; estos resúmenes
    # semanales son livianos, van al repo y quedan para siempre. Cada 15 días
    # se leen todos juntos y ahí recién aparece lo que una sola semana no
    # muestra: si una tienda infla siempre antes del mismo feriado, si el día
    # de las rebajas se corrió, si un umbral quedó mal puesto hace un mes.
    os.makedirs(HISTORIAL, exist_ok=True)
    destino = os.path.join(HISTORIAL, "%s.json" % datos["semana_hasta"])
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)
    print("historial guardado en %s" % destino)

    clave = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not clave:
        # No es un error. El historial —que es lo que importa— ya quedó
        # escrito; el análisis en prosa es un extra que se puede hacer después
        # leyendo estos archivos.
        print("sin ANTHROPIC_API_KEY: se guardó el historial y no se redacta análisis")
        return 0

    texto = pensar(datos, clave)
    if a.probar:
        print("\n" + texto)
        return 0
    enviar(texto)
    print("análisis enviado (%d caracteres)" % len(texto))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
