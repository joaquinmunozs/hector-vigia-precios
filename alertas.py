# -*- coding: utf-8 -*-
"""Arma y envía los avisos a Telegram, con la estructura de las capturas.

FORMATO
------------------------------------------------------------------------------
Copiado de los canales que ya funcionan en Chile:

    🔥 SRank  spdigital  Apple iPhone 16 Pro Max 🔍
    $1.489.990 -> $16.989 (98,9%)
    PRODUCTO                       <- enlace directo a la ficha
    Precio histórico 📉
    06/08/2026  $1.489.990
    05/08/2026  $1.349.990

El "rank" no es decoración: ordena de un vistazo qué tan grande es el hallazgo,
y es lo que hace que alguien abra el mensaje en vez de ignorarlo. Sale del
porcentaje de caída, no se asigna a mano.

CUATRO TÓPICOS, Y UN MENSAJE PUEDE IR A DOS
------------------------------------------------------------------------------
  🚨 Errores de precio  -> caída 70% a 99%   (cualquier producto)
  🏷️ Ofertas reales     -> caída 40% a 70%   (cualquier producto)
  📱 Electrónicos       -> caída 35% a 70%   (solo electrónica)
  🏠 Hogar              -> caída 35% a 70%   (solo hogar)

Los dos primeros van separados a propósito: quien paga por errores de precio
no quiere que le llegue una oferta del 55% mezclada, y quien busca ofertas no
necesita que le suene el teléfono a las 4 AM por un error que dura 20 minutos.

EL DUPLICADO ES INTENCIONAL, PERO SOLO DESDE EL 60% (11-ago-2026)
------------------------------------------------------------------------------
Un hallazgo de categoría sobre el 60% sale DOS VECES: en Ofertas reales y en
su tópico de categoría. No es un bug ni un descuido — quien sigue solo
Electrónicos no debería perderse un -60% en un notebook por no estar mirando
el tópico general, y quien sigue solo Ofertas tampoco.

Entre 35% y 60% va SOLO a su tópico de categoría. El corte original era 50%
y se subió a 60% el 11-ago porque Ofertas terminaba siendo la suma de todos
los tópicos, y un tópico saturado se silencia. Ver `destinos` y
`UMBRAL_DUPLICAR`, que es donde vive el número.

Sobre el 70% NO se duplica: ahí ya es error de precio y va únicamente al
tópico de errores. El tope de los tópicos de categoría es 69%.

Nada bajo 35% llega acá, y entre 35% y 40% solo llega si es de categoría:
ese corte lo pone `baseprecios.evaluar`, no este archivo.
"""
import html
import itertools
import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.request

import baseprecios
import categorias

# Rango de caída -> (emoji, etiqueta). El orden importa: se evalúa de mayor a
# menor y se usa el primero que calce.
#   70%-99% caen en los rangos de ERROR     (van al tópico de errores)
#   40%-70% caen en los de OFERTA           (ofertas + su categoría si tiene)
#   35%-40% caen en el de CATEGORIA         (solo su tópico de categoría)
#
# ── LOS CORTES SALEN DE `baseprecios`, NO SE ESCRIBEN DOS VECES ───────────
#
# Estaban copiados a mano, y el 11-ago `UMBRAL_OFERTA` bajó de 0,50 a 0,40
# ("ropa, zapatillas y todo lo que no es electrónica ni hogar entra desde
# ahí") sin que esta tabla se enterara. Efecto: una oferta de -45% se
# clasificaba como OFERTA y se mandaba al tópico de Ofertas, pero llegaba
# rotulada "📉 Rebaja" — la etiqueta que este archivo reserva para lo que
# NO alcanza a ser oferta. En la base del 13-ago eran 23 de 130 avisos.
#
# Nadie lo iba a ver leyendo el código: hay que cruzar dos archivos. Ahora
# el corte vive en un solo lado y esto lo lee.
RANGOS = (
    (0.90, "🔥", "SRank"),      # 90-99%: el error grande, el que vuela
    (0.80, "🅰️", "ARank"),
    (baseprecios.UMBRAL_ERROR, "🅱️", "BRank"),      # 70-80%: error más leve
    (0.60, "🏷️", "Oferta+"),                        # 60-70%: oferta muy fuerte
    (baseprecios.UMBRAL_OFERTA, "🏷️", "Oferta"),    # el piso general
    (baseprecios.UMBRAL_CATEGORIA, "📉", "Rebaja"),  # solo Electrónicos/Hogar
)


def _plata(n):
    return "$" + format(int(n), ",d").replace(",", ".")


def _rango(caida):
    for minimo, emoji, etiqueta in RANGOS:
        if caida >= minimo:
            return emoji, etiqueta
    return "📉", "Rebaja"


# Categoría -> variable de entorno con el id de su tópico.

# ── Variantes del mismo producto ─────────────────────────────────────────
#
# Una cortina publicada en ocho colores son ocho URLs distintas con el mismo
# precio y la misma caída: ocho avisos idénticos que saturan el tópico y
# hacen que la gente lo silencie. Para el suscriptor es UN producto.
#
# Se agrupan por (tienda, título sin el color/talla, precio). El color se
# quita del título en vez de compararlo: dos publicaciones que solo difieren
# en "Azul" / "Rojo" colapsan a la misma clave, y se manda una sola con el
# número de variantes.
_COLOR = re.compile(
    r"\b(negro|blanco|gris|plata|dorado|beige|crema|caf[ée]|marr[óo]n|"
    r"azul|celeste|marino|verde|oliva|amarillo|naranj[ao]|rojo|burdeo|"
    r"rosa|rosado|fucsia|morado|lila|violeta|turquesa|coral|vino|"
    r"multicolor|transparente|natural|"
    r"talla\s*\w{1,4}|\b(?:xs|s|m|l|xl|xxl)\b|"
    r"\d+\s*(?:plazas?|cm|mm|ml|lts?|litros?)|"
    r"unitalla|surtido)\b", re.I)


def _clave_variante(det):
    base = _COLOR.sub("", det.get("nombre") or "")
    base = re.sub(r"[\s\-–—,/]+", " ", base).strip().lower()
    return (det.get("tienda"), base, int(det.get("precio") or 0))


def agrupar_variantes(detecciones):
    """Un aviso por producto, no por color. Devuelve (deteccion, cuantas)."""
    grupos = {}
    for d in detecciones:
        grupos.setdefault(_clave_variante(d), []).append(d)
    salida = []
    for lista in grupos.values():
        # Se avisa la de mayor caída: si alguna variante bajó más, esa es la
        # que vale la pena mostrar.
        mejor = max(lista, key=lambda x: x.get("caida") or 0)
        salida.append((mejor, len(lista)))
    return salida


# Desde esta caída, un hallazgo de categoría se duplica también en Ofertas:
# ya no es "una buena oferta de hogar", es de las mejores del día y merece
# estar donde mira todo el mundo.
UMBRAL_DUPLICAR = 0.60

TOPICO_DE_CATEGORIA = {
    categorias.ELECTRONICOS: "VIGIA_TOPICO_ELECTRONICOS",
    categorias.HOGAR: "VIGIA_TOPICO_HOGAR",
    # Vuelos entra igual que las otras dos categorías, y por eso hereda gratis
    # el piso del 40% (`UMBRAL_OFERTA`) y el techo del 70%, que es lo que se
    # pidió: ofertas de 40% para arriba al tópico de vuelos, y sobre 70% al de
    # errores de precio como cualquier otro hallazgo.
    categorias.VUELOS: "VIGIA_TOPICO_VUELOS",
}


def destinos(det):
    """A qué tópicos va este hallazgo. Puede ser más de uno — ver el duplicado.

    Devuelve una lista de ids de tópico (los que estén configurados). Si
    ninguno lo está, devuelve [None], que manda el mensaje al hilo general
    del grupo en vez de perderlo.
    """
    ids = []

    if det["tipo"] == baseprecios.ERROR:
        # Sobre 70% es error de precio y va SOLO ahí: no se duplica a la
        # categoría, porque el tope de esos tópicos es 69%.
        ids.append(os.environ.get("VIGIA_TOPICO_ERRORES"))
    else:
        # UN SOLO DESTINO POR HALLAZGO (11-ago-2026)
        #
        # Antes, un producto de categoría entre 50% y 70% salía DOS veces: en
        # Ofertas y en su tópico. La idea era que nadie se perdiera nada. En la
        # práctica pasó lo contrario: Ofertas terminó siendo la suma de todos
        # los tópicos y quedó saturado, que es la forma más rápida de que
        # alguien lo silencie — y un tópico silenciado no vende nada.
        #
        # Ahora cada hallazgo tiene un destino y solo uno: si es de hogar va a
        # Hogar, si es de electrónica va a Electrónicos, y Ofertas se queda con
        # lo que no calza en ninguna categoría (ropa, zapatillas, deporte).
        # Quien quiera verlo todo se suscribe a los tres tópicos; quien solo
        # quiera hogar ya no tiene que aguantar el resto.
        var = TOPICO_DE_CATEGORIA.get(det.get("categoria"))
        destino = os.environ.get(var) if var else None
        if destino:
            ids.append(destino)

        # Ofertas es el acceso rápido a lo urgente, no el cajón de lo que
        # sobra. Recibe dos cosas:
        #   · lo que no calza en ninguna categoría (ropa, zapatillas, deporte)
        #   · TODO lo que pase el 60%, tenga la categoría que tenga
        # Un iPhone al 59% se queda solo en Electrónicos; al 60% aparece en los
        # dos. Quien quiere lo prioritario mira un tópico; quien quiere su
        # categoría completa, la suya.
        if det["caida"] >= UMBRAL_DUPLICAR or (
                not destino and det["tipo"] == baseprecios.OFERTA):
            ids.append(os.environ.get("VIGIA_TOPICO_OFERTAS"))

    ids = [i for i in ids if i]
    return ids or [None]


def _escapar(t):
    return (str(t or "").replace("&", "&amp;")
            .replace("<", "&lt;").replace(">", "&gt;"))


def _limpiar_nombre(nombre, url):
    """El nombre suele venir con entidades HTML dobles o vacío.

    Se usa `html.unescape` DOS veces en vez de reemplazar entidades a mano.
    El reemplazo manual solo cubría `&#x20;` (el espacio), así que Antártica
    llegaba al suscriptor como "12 Reglas Para Vivir. Un Ant&#xED;d" — la
    `&#xED;` (í), `&#xBA;` (º) y `&#x2F;` (/) pasaban intactas. Se ve en
    `historial/2026-08-11.json`.

    Dos pasadas porque la entidad viene doblemente escapada en algunas
    tiendas (`&amp;#x20;`): la primera deja `&#x20;`, la segunda el espacio.
    """
    n = html.unescape(html.unescape(nombre or ""))
    n = " ".join(n.split())
    if not n:
        # Sin nombre, se arma uno legible desde la URL.
        cola = url.rstrip("/").split("/")[-1].replace("-", " ")
        n = cola[:70].title()
    return n[:90]


def armar_texto(det, tienda):
    """El mensaje listo para Telegram, en HTML."""
    emoji, etiqueta = _rango(det["caida"])
    pct = det["caida"] * 100

    lineas = [
        "%s <b>%s</b>  <i>%s</i>" % (emoji, etiqueta, _escapar(tienda)),
        "<b>%s</b>" % _escapar(_limpiar_nombre(det.get("nombre"), det["url"])),
        "",
        "<s>%s</s> → <b>%s</b>  (<b>%.1f%%</b>)" % (
            _plata(det["referencia"]), _plata(det["precio"]), pct),
        "",
        '<a href="%s">PRODUCTO</a>' % det["url"],
    ]

    # El precio habitual, cuando difiere del mínimo de 30 días contra el que
    # se anuncia la caída. Que difieran NO es un detalle técnico: significa
    # que el producto estuvo más caro de lo normal en el último mes, que es
    # justo lo que la Directiva Omnibus obliga a no esconder.
    #
    # Se muestra el número más chico como titular y el otro como contexto, no
    # al revés: el titular tiene que ser el que aguanta que lo revisen.
    habitual = det.get("habitual")
    if habitual and habitual > det["referencia"]:
        lineas.insert(4, "<i>habitualmente %s</i>" % _plata(habitual))

    if det.get("historico"):
        lineas += ["", "<b>Precio histórico</b> 📉"]
        # El historial viene de más reciente a más antiguo, que es como lo
        # muestran los canales de referencia.
        for p in det["historico"][:4]:
            lineas.append("  %s" % _plata(p))
    elif not det.get("con_historial"):
        # Honestidad: si la referencia es la foto del día uno y no un historial
        # acumulado, el mensaje lo dice. Un "-80%" sin respaldo es justo lo que
        # hace que la gente deje de creerle al canal.
        lineas += ["", "<i>Referencia: precio al registrar el producto "
                   "(historial aún en construcción)</i>"]

    return "\n".join(lineas)


INTERVALO_TELEGRAM = 3.1
REINTENTOS_TELEGRAM = 4


def _retry_after(error):
    """Extrae el `retry_after` que Telegram devuelve en un HTTP 429."""
    try:
        cuerpo = error.read().decode("utf-8", "replace")
        datos = json.loads(cuerpo)
        return float((datos.get("parameters") or {}).get("retry_after") or 0)
    except Exception:                                  # noqa: BLE001
        return 0.0


def _enviar(texto, topico_id=None):
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("VIGIA_CHAT_ID") or
            os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        print("[sin telegram configurado]\n%s\n" % texto)
        return False

    cuerpo = {"chat_id": chat, "text": texto, "parse_mode": "HTML",
              "disable_web_page_preview": False}
    if topico_id:
        cuerpo["message_thread_id"] = int(topico_id)

    req = urllib.request.Request(
        "https://api.telegram.org/bot%s/sendMessage" % token,
        data=json.dumps(cuerpo).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    for intento in range(REINTENTOS_TELEGRAM):
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
            if r.get("ok"):
                return True
            espera = float((r.get("parameters") or {}).get("retry_after") or 0)
            print("telegram rechazó: %s" % str(r)[:200])
            if not espera or intento + 1 >= REINTENTOS_TELEGRAM:
                return False
            time.sleep(espera + 0.25)
        except urllib.error.HTTPError as e:
            espera = _retry_after(e) if e.code == 429 else 0
            if intento + 1 >= REINTENTOS_TELEGRAM:
                print("telegram falló HTTP %s: %s" % (e.code, str(e)[:120]))
                return False
            time.sleep((espera + 0.25) if espera else min(8, 2 ** intento))
        except Exception as e:                         # noqa: BLE001
            if intento + 1 >= REINTENTOS_TELEGRAM:
                print("telegram falló: %s" % str(e)[:150])
                return False
            time.sleep(min(8, 2 ** intento))
    return False


def enviar_hallazgos(con, hallazgos):
    """Manda cada hallazgo a sus tópicos y lo registra para no repetirlo.

    Un hallazgo puede ir a DOS tópicos (ver `destinos`), pero se anota UNA
    sola vez: la anotación existe para no volver a avisar el mismo producto
    dentro de VENTANA_REPETIR, y eso es por producto, no por tópico.
    """
    enviados = 0
    # Las variantes del mismo producto (una cortina en ocho colores) colapsan
    # a UN aviso. Para el suscriptor es un producto; ocho mensajes idénticos
    # son la forma más rápida de que silencie el tópico.
    agrupados = agrupar_variantes(hallazgos)
    # Los más grandes primero: si hay muchos, los que importan salen antes.
    for det, variantes in sorted(agrupados, key=lambda x: -x[0]["caida"]):
        texto = armar_texto(det, det.get("tienda", ""))
        if variantes > 1:
            texto += ("\n\n<i>Disponible en %d variantes "
                      "(color o medida).</i>" % variantes)
        llego = False
        for topico in destinos(det):
            if _enviar(texto, topico):
                llego = True
                enviados += 1
                # Telegram tumba al bot si se le mandan más de ~20 mensajes
                # por minuto al mismo chat.
                time.sleep(INTERVALO_TELEGRAM)
        if llego:
            for hermana in hallazgos:
                if _clave_variante(hermana) == _clave_variante(det):
                    baseprecios.anotar_alerta(con, hermana)
    con.commit()
    return enviados


def _prioridad(det):
    if det.get("tipo") == baseprecios.ERROR:
        return 0
    if det.get("tipo") == baseprecios.OFERTA:
        return 1
    return 2


class NotificadorTelegram:
    """(codex) Cola durable para avisar sin frenar las lecturas.

    `cerrar()` drena la cola y confirma en SQLite solo los mensajes que
    Telegram aceptó. Los errores de precio adelantan a ofertas y rebajas. El
    hilo es daemon únicamente para no colgar un cierre fatal antes del finally;
    todos los caminos normales llaman explícitamente a `cerrar()`.
    """

    def __init__(self, coalescer_seg=0.35):
        self._cola = queue.PriorityQueue()
        self._secuencia = itertools.count()
        self._cerrando = threading.Event()
        self._coalescer_seg = coalescer_seg
        self._hilo = threading.Thread(target=self._trabajar,
                                      name="telegram-notificador", daemon=True)
        self._hilo.start()

    def enviar(self, det, detectado_en=None):
        if self._cerrando.is_set():
            raise RuntimeError("el notificador ya está cerrando")
        copia = dict(det)
        copia["_detectado_en"] = float(detectado_en or time.time())
        self._cola.put((_prioridad(copia), next(self._secuencia), copia))

    def _trabajar(self):
        con = baseprecios.abrir()
        try:
            while True:
                item = self._cola.get()
                if item is None:
                    self._cola.task_done()
                    break
                lote = [item]
                fin = time.time() + self._coalescer_seg
                while time.time() < fin and len(lote) < 100:
                    try:
                        siguiente = self._cola.get(timeout=max(0.01, fin - time.time()))
                    except queue.Empty:
                        break
                    if siguiente is None:
                        self._cola.task_done()
                        self._cerrando.set()
                        break
                    lote.append(siguiente)

                lote.sort(key=lambda x: (x[0], x[1]))
                hallazgos = [x[2] for x in lote]
                try:
                    enviados = enviar_hallazgos(con, hallazgos)
                    if enviados:
                        ahora = time.time()
                        latencias = [ahora - d.get("_detectado_en", ahora)
                                     for d in hallazgos]
                        print("   telegram: %d envío(s) · latencia cola %.1f–%.1f s"
                              % (enviados, min(latencias), max(latencias)))
                except Exception as ex:                 # noqa: BLE001
                    # La cola nunca puede quedar bloqueada para siempre por un
                    # error de SQLite o formato. Se hace visible y el proceso
                    # puede cerrar conservando el resto de la base.
                    print("telegram: lote no procesado: %s" % str(ex)[:180])
                finally:
                    for _ in lote:
                        self._cola.task_done()
        finally:
            con.close()

    def cerrar(self, timeout=None):
        """Espera todas las alertas y falla de forma visible si el hilo murió."""
        if self._cerrando.is_set():
            return
        self._cerrando.set()
        inicio = time.time()
        while self._cola.unfinished_tasks:
            if not self._hilo.is_alive():
                raise RuntimeError("el notificador murió con alertas pendientes")
            if timeout is not None and time.time() - inicio >= timeout:
                raise RuntimeError("timeout drenando alertas de Telegram")
            time.sleep(0.05)
        self._cola.put(None)
        self._hilo.join(timeout=timeout)
        if self._hilo.is_alive():
            raise RuntimeError("el notificador de Telegram no alcanzó a cerrar")
