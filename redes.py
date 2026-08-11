# -*- coding: utf-8 -*-
"""Publica los hallazgos en Instagram y Facebook, con el creativo generado.

    python redes.py --probar          # arma todo y NO publica (imprime)
    python redes.py                   # publica de verdad
    python redes.py --horas 24        # ventana de hallazgos a considerar
    python redes.py --tope 2          # cuántos publicar como máximo

PARA QUÉ EXISTE
------------------------------------------------------------------------------
El número que decide si Rat.IA es un negocio es el CAC: el modelo aguanta
hasta $7.807 y la campaña medida daba $12.000. Esto es una vía para bajarlo
sin pagar más pauta — el hallazgo mismo es el anuncio.

Por decisión de Joaquín (11-ago-2026) se publica TODO, incluidos los errores
de precio del 70%+. Es a sabiendas de que compite con lo que se cobra: la
apuesta es que el alcance vale más que la exclusividad mientras haya 0
suscriptores.

POR QUÉ CORRE APARTE DE HÉCTOR
------------------------------------------------------------------------------
No va dentro de `correr.py`. Dos razones:

  1. El vigilante es el pilar del producto y tiene un presupuesto de 3,4 h
     medido. Meterle generación de imágenes (que son minutos de espera por
     pieza, asíncronos) le roba tiempo a lo que detecta los errores.
  2. Las corridas de `hector.yml` se están encolando — 12 de las últimas 23
     terminaron canceladas esperando cupo. Agregarle trabajo empeora eso.

Así que esto lee el MISMO artifact de `precios.db` y corre en su propio
workflow, igual que `analisis_semanal.py`.

EL CREATIVO NO REUSA LA FOTO DE LA TIENDA
------------------------------------------------------------------------------
Se genera con Higgsfield. Además de quedar con identidad propia, evita
republicar la foto de producto de Falabella o Paris, que no es nuestra.

LA IMAGEN TIENE QUE ESTAR EN UNA URL PÚBLICA
------------------------------------------------------------------------------
Meta NO acepta que le subas el archivo: baja la imagen de una URL que le
pasas. Por eso el flujo es generar → obtener URL → pasársela a Meta, y no
"generar y subir".

SIN CREDENCIALES NO PUBLICA NADA
------------------------------------------------------------------------------
Igual que `alertas.py` sin `TELEGRAM_BOT_TOKEN`: si faltan las claves,
imprime en pantalla lo que habría publicado. Es la forma segura de probar
cambios sin llenarle el feed a nadie.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

import alertas
import baseprecios

# ── Credenciales ─────────────────────────────────────────────────────────
HF_ID = (os.environ.get("HF_API_KEY_ID") or "").strip()
HF_SECRET = (os.environ.get("HF_API_KEY_SECRET") or "").strip()
META_TOKEN = (os.environ.get("META_ACCESS_TOKEN") or "").strip()
META_IG_USER_ID = (os.environ.get("META_IG_USER_ID") or "").strip()
META_PAGE_ID = (os.environ.get("META_PAGE_ID") or "").strip()

# Versión de la Graph API. Se fija a propósito en vez de usar la última:
# Meta descontinúa versiones con aviso, y una publicación que falla en
# silencio es peor que un error visible al actualizar.
GRAPH = "https://graph.facebook.com/v21.0"

HF_BASE = "https://platform.higgsfield.ai"
# VERIFICAR contra la cuenta real: el endpoint del modelo de imagen y el
# nombre del campo de resultado. La documentación pública muestra
# `/higgsfield-ai/soul/standard` y un ciclo submit → poll, pero no detalla
# la forma exacta de la respuesta.
HF_MODELO = "/higgsfield-ai/soul/standard"

CUENTA = os.environ.get("RATIA_CUENTA_IG", "@ratia.cl")


def _pedir(url, datos=None, cabeceras=None, metodo=None, tiempo=60):
    cab = {"Content-Type": "application/json"}
    cab.update(cabeceras or {})
    cuerpo = json.dumps(datos).encode("utf-8") if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, headers=cab, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        # El cuerpo del error es lo único que dice QUÉ permiso falta o qué
        # campo rechazó Meta. Perderlo obliga a adivinar.
        detalle = ""
        try:
            detalle = e.read().decode("utf-8")[:400]
        except Exception:                        # noqa: BLE001
            pass
        raise RuntimeError("HTTP %s en %s: %s" % (e.code, url.split("?")[0], detalle))


# ── Qué se publica ───────────────────────────────────────────────────────
def candidatos(con, horas=12, tope=3):
    """Los mejores hallazgos recientes que todavía no salieron en redes.

    Se ordena por caída, no por fecha: si hay más hallazgos que cupo, salen
    los más grandes. Y se cruza con `publicaciones` para no repetir — sin
    eso, cada corrida volvería a publicar los mismos de la ventana.
    """
    desde = int(time.time() - horas * 3600)
    filas = con.execute("""
        SELECT a.url, a.tipo, a.precio, a.referencia, a.caida, a.avisado_en,
               (SELECT nombre FROM precios p WHERE p.url = a.url
                  AND p.nombre <> '' ORDER BY p.visto_en DESC LIMIT 1) AS nombre,
               (SELECT tienda FROM precios p WHERE p.url = a.url LIMIT 1) AS tienda
        FROM alertas a
        WHERE a.avisado_en >= ?
          AND NOT EXISTS (SELECT 1 FROM publicaciones pub
                           WHERE pub.url = a.url AND pub.red = 'instagram')
        GROUP BY a.url
        ORDER BY a.caida DESC
        LIMIT ?
    """, (desde, tope)).fetchall()

    return [{
        "url": f["url"], "tipo": f["tipo"], "precio": f["precio"],
        "referencia": f["referencia"], "caida": f["caida"],
        "nombre": alertas._limpiar_nombre(f["nombre"], f["url"]),
        "tienda": f["tienda"] or "",
    } for f in filas]


def marcar_publicado(con, url, red, id_externo=None):
    con.execute(
        "INSERT INTO publicaciones (url, red, publicado_en, id_externo) "
        "VALUES (?,?,?,?) ON CONFLICT(url, red) DO UPDATE SET "
        "publicado_en=excluded.publicado_en, id_externo=excluded.id_externo",
        (url, red, int(time.time()), id_externo))
    con.commit()


# ── El texto ─────────────────────────────────────────────────────────────
def pie_de_foto(det):
    """El caption. Instagram NO hace clickeables los links del caption, así
    que el CTA manda a la bio en vez de pegar una URL que nadie puede tocar.
    """
    emoji, etiqueta = alertas._rango(det["caida"])
    pct = det["caida"] * 100
    es_error = det["tipo"] == baseprecios.ERROR

    lineas = [
        "%s %s en %s" % (emoji, "ERROR DE PRECIO" if es_error else etiqueta.upper(),
                         det["tienda"]),
        "",
        det["nombre"],
        "",
        "Antes: %s" % alertas._plata(det["referencia"]),
        "Ahora: %s" % alertas._plata(det["precio"]),
        "Baja: %.0f%%" % pct,
        "",
        # La honestidad ES el producto: el porcentaje se mide contra lo que la
        # tienda cobró de verdad, no contra el "precio normal" tachado. Si el
        # caption no lo dice, el post se lee igual que el de cualquier canal
        # de ofertas y se pierde lo único que nos diferencia.
        "Medido contra lo que esta tienda cobró de verdad las últimas "
        "semanas, no contra el precio tachado del cartel.",
        "",
    ]
    if es_error:
        lineas.append("Los errores de precio duran minutos. Apúrate.")
    else:
        lineas.append("Link del producto y avisos al instante en el grupo: "
                      "link en la bio %s" % CUENTA)

    lineas += [
        "",
        "#ofertaschile #erroresdeprecio #chile #ahorro #%s"
        % det["tienda"].split(".")[0].replace("-", ""),
    ]
    return "\n".join(lineas)


def prompt_creativo(det):
    """El prompt de la imagen.

    Es una PLANTILLA FIJA con solo el producto variable, no un prompt libre
    por hallazgo. La razón es de marca: si cada post se ve distinto, el feed
    no se reconoce como de nadie. Lo que cambia es el sujeto; el estilo,
    el encuadre y la paleta se quedan quietos.

    OJO: no se le pide a la imagen que ponga el precio ni texto. Los modelos
    de imagen escriben números mal, y un precio equivocado en un post de un
    producto que vende precios correctos es el peor error posible. Los
    números van en el caption, que es texto de verdad.
    """
    return (
        "Fotografía de producto de estudio, limpia y moderna, de: %s. "
        "Fondo de color plano azul profundo, iluminación suave de estudio, "
        "producto centrado y nítido, sombra sutil, estilo editorial de "
        "e-commerce premium, formato cuadrado. "
        "Sin texto, sin números, sin letras, sin marcas de agua."
        % det["nombre"]
    )


# ── Higgsfield ───────────────────────────────────────────────────────────
def generar_imagen(det, esperar_max=300):
    """Genera el creativo y devuelve una URL pública, o None.

    El ciclo de Higgsfield es asíncrono: se manda el pedido, devuelve un id
    y una URL de estado, y hay que consultar hasta que termine. No es
    instantáneo, y por eso esto no puede vivir dentro de la corrida de
    Héctor.
    """
    if not (HF_ID and HF_SECRET):
        return None

    cab = {"Authorization": "Key %s:%s" % (HF_ID, HF_SECRET)}
    envio = _pedir(HF_BASE + HF_MODELO,
                   datos={"params": {"prompt": prompt_creativo(det),
                                     "aspect_ratio": "1:1"}},
                   cabeceras=cab)

    # VERIFICAR los nombres reales de estos campos contra la cuenta. Se
    # aceptan varias formas a propósito: es más barato tolerar dos nombres
    # posibles que perder la corrida entera por uno distinto.
    url_estado = (envio.get("status_url") or envio.get("statusUrl") or "")
    if not url_estado:
        ident = envio.get("id") or envio.get("request_id")
        if not ident:
            raise RuntimeError("Higgsfield no devolvió id ni status_url: %s"
                               % str(envio)[:200])
        url_estado = "%s/requests/%s" % (HF_BASE, ident)

    esperado = 0
    while esperado < esperar_max:
        time.sleep(6)
        esperado += 6
        estado = _pedir(url_estado, cabeceras=cab)
        situacion = str(estado.get("status") or "").lower()
        if situacion in ("completed", "succeeded", "success", "done"):
            return _url_de_resultado(estado)
        if situacion in ("failed", "error", "canceled", "cancelled"):
            raise RuntimeError("Higgsfield falló: %s" % str(estado)[:200])
    raise RuntimeError("Higgsfield no terminó en %d s" % esperar_max)


def _url_de_resultado(estado):
    """Busca la primera URL de imagen en la respuesta, sea como sea que venga.

    Se hurga en vez de leer una ruta fija porque la forma exacta de la
    respuesta no está documentada públicamente. Es el mismo criterio que
    usa `extractor._de_nextdata` con los árboles de Next.js: buscar el dato
    en vez de asumir dónde está.
    """
    vistos = []

    def hurgar(nodo, prof=0):
        if prof > 8 or vistos:
            return
        if isinstance(nodo, str):
            if nodo.startswith("http") and any(
                    e in nodo.lower() for e in (".jpg", ".jpeg", ".png", ".webp")):
                vistos.append(nodo)
            return
        if isinstance(nodo, dict):
            # `url` primero: es el nombre más probable y evita agarrar una
            # miniatura antes que la imagen final.
            for clave in ("url", "image_url", "output_url", "result_url"):
                if isinstance(nodo.get(clave), str):
                    hurgar(nodo[clave], prof + 1)
                    if vistos:
                        return
            for v in nodo.values():
                hurgar(v, prof + 1)
        elif isinstance(nodo, list):
            for v in nodo:
                hurgar(v, prof + 1)

    hurgar(estado)
    return vistos[0] if vistos else None


# ── Meta ─────────────────────────────────────────────────────────────────
def publicar_instagram(url_imagen, pie):
    """Dos pasos: contenedor y publicación. Devuelve el id del post.

    El contenedor no es un detalle de la API: Instagram baja la imagen en
    ese momento, así que si la URL no es pública el fallo ocurre acá y no
    al publicar.
    """
    cont = _pedir("%s/%s/media" % (GRAPH, META_IG_USER_ID),
                  datos={"image_url": url_imagen, "caption": pie,
                         "access_token": META_TOKEN})
    ident = cont.get("id")
    if not ident:
        raise RuntimeError("Instagram no devolvió id de contenedor: %s" % str(cont)[:200])

    # Un contenedor recién creado puede seguir en FINISHED pendiente: Meta
    # está bajando la imagen. Publicar de inmediato devuelve un error
    # confuso ("media not ready"), así que se le da margen.
    time.sleep(5)

    pub = _pedir("%s/%s/media_publish" % (GRAPH, META_IG_USER_ID),
                 datos={"creation_id": ident, "access_token": META_TOKEN})
    return pub.get("id")


def publicar_facebook(url_imagen, pie):
    """Foto al feed de la Página. Un solo paso, distinto de Instagram."""
    r = _pedir("%s/%s/photos" % (GRAPH, META_PAGE_ID),
               datos={"url": url_imagen, "caption": pie,
                      "access_token": META_TOKEN})
    return r.get("post_id") or r.get("id")


# ── Orquestación ─────────────────────────────────────────────────────────
def publicar(con, horas=12, tope=3, probar=False):
    lista = candidatos(con, horas=horas, tope=tope)
    if not lista:
        print("Sin hallazgos nuevos en las últimas %d h." % horas)
        return 0

    print("%d hallazgo(s) para publicar:\n" % len(lista))
    hechos = 0
    for det in lista:
        pie = pie_de_foto(det)
        print("─" * 68)
        print("%s · %s · -%.0f%%" % (det["tienda"], det["nombre"], det["caida"] * 100))

        try:
            url_imagen = generar_imagen(det)
        except Exception as e:                       # noqa: BLE001 — que falle
            print("  creativo falló: %s" % str(e)[:200])   # uno no puede
            continue                                       # frenar los demás

        if not url_imagen:
            print("  [sin Higgsfield configurado — no se generó imagen]")

        if probar or not (META_TOKEN and META_IG_USER_ID):
            print("  [modo prueba: no se publica]")
            print("  imagen: %s" % (url_imagen or "(ninguna)"))
            print("\n%s\n" % pie)
            continue

        if not url_imagen:
            # Meta necesita una imagen. Sin creativo no hay post, y publicar
            # la foto de la tienda no es una alternativa: no es nuestra.
            print("  se salta: no hay imagen que publicar")
            continue

        try:
            ig = publicar_instagram(url_imagen, pie)
            marcar_publicado(con, det["url"], "instagram", ig)
            print("  instagram: %s" % ig)
            hechos += 1
        except Exception as e:                       # noqa: BLE001
            print("  instagram falló: %s" % str(e)[:250])

        if META_PAGE_ID:
            try:
                fb = publicar_facebook(url_imagen, pie)
                marcar_publicado(con, det["url"], "facebook", fb)
                print("  facebook: %s" % fb)
            except Exception as e:                   # noqa: BLE001
                print("  facebook falló: %s" % str(e)[:250])

    return hechos


def main():
    p = argparse.ArgumentParser(description="Publica hallazgos en IG y Facebook")
    p.add_argument("--probar", action="store_true",
                   help="arma el post y lo imprime, sin publicar")
    p.add_argument("--horas", type=int, default=12)
    p.add_argument("--tope", type=int, default=3)
    args = p.parse_args()

    con = baseprecios.abrir()
    n = publicar(con, horas=args.horas, tope=args.tope, probar=args.probar)
    print("\npublicados: %d" % n)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
