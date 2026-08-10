# Héctor — vigía de precios

Vigila el retail chileno y avisa por Telegram cuando aparece **un error de
precio** (el televisor que quedó en $9.990) o **una oferta real** (una caída
grande contra el precio que ese producto venía teniendo de verdad, no contra
el "precio normal" tachado que inventa la tienda).

Hoy vigila **44 tiendas** chilenas y un catálogo del orden de las cientos de
miles de fichas. Corre solo en GitHub Actions cada 4 horas.

---

## Cómo está armado

Una corrida (`correr.py`) hace dos cosas **a la vez**:

| | Qué mira | Cada cuánto |
|---|---|---|
| **Vigilante** (`vigilante.py`) | La *lista caliente*: ~1.500 productos deseables (`caliente.py`) | Cada pocos segundos, durante 3,4 h |
| **Barrida** (`vigia.py`) | Hasta 250.000 fichas del catálogo completo, rotando | Una pasada por corrida, con tope de 3 h |

Además, los **lunes** busca productos nuevos (`descubrir.py`) y los días **1 y
15** recalibra las líneas base de precio. La recalibración va espaciada a
propósito: una referencia que se actualiza muy seguido se "acostumbra" al
precio bajo y deja de verlo como caída.

Los dos corren **en el mismo proceso**, el vigilante en un hilo. No es un
detalle de estilo: en Modal corrían como dos contenedores escribiendo el mismo
SQLite y lo corrompieron de verdad (7-ago-2026, *"database disk image is
malformed"*).

### Los módulos

```
correr.py        punto de entrada de GitHub Actions (lo que antes hacía modal_app.py)
tiendas.py       las 44 tiendas, con su nivel de dificultad y rubro
descubrir.py     saca URLs de producto de los sitemaps, respetando robots.txt
extractor.py     lee el precio del HTML (JSON-LD, microdata, Next.js streaming…)
adaptadores.py   los casos especiales por tienda
baseprecios.py   historial, línea base y clasificación de hallazgos (SQLite)
caliente.py      arma y mantiene la lista caliente
vigilante.py     el ciclo rápido sobre la lista caliente
vigia.py         la barrida del catálogo completo
alertas.py       arma y manda los avisos a Telegram
depurar_robots.py saca del catálogo lo que una tienda pasó a prohibir
probar_*.py      pruebas manuales por tienda; se corren a mano, no en CI
cobro/           aparte: el Worker de pagos de Rat.IA (ver cobro/README.md)
```

### Dónde vive la base de precios

En `precios.db` (SQLite, ~36 MB), **fuera del repo a propósito**: git no
deduplica binarios que cambian 6 veces al día, así que commitearla haría
crecer el historial sin freno. Persiste entre corridas como **artifact** de
GitHub Actions, que en repos públicos es gratis e ilimitado.

Esa decisión tiene una consecuencia que costó cara: si el artifact no se
restaura, el catálogo se redescubre desde cero, cada URL se trata como
"nueva", su precio de referencia se fija en el mismo instante que se lee, y
**nunca hay una base anterior contra la que comparar una caída** — o sea, cero
avisos, sin ningún error visible. Si alguna vez vuelven a faltar avisos,
mirar primero el paso *"¿Qué base tenemos?"* del log: tiene que decir **"base
recuperada"**, no *"sin base previa"*.

---

## Correrlo en tu máquina

Hace falta Python 3.12.

```bash
pip install -r requirements.txt
cp .env.ejemplo .env      # y completar los valores (ver más abajo)
python correr.py
```

`correr.py` corre la ventana completa (3,4 h). Para probar algo puntual
conviene ir directo al módulo:

```bash
python probar_paris.py          # leer precios de una tienda
python diagnosticar.py          # revisar el estado de la base
python configurar_telegram.py   # ver/ajustar el grupo y los tópicos
```

`curl_cffi` no es opcional: imita el saludo TLS de Chrome. Sin eso, adidas.cl
responde 403 y Falabella no se puede leer — son cientos de miles de fichas
que se perderían por no instalar una librería.

### Variables de entorno

Las mismas seis en local (`.env`) y en la nube (Secrets del repo):

| Variable | Para qué |
|---|---|
| `TELEGRAM_BOT_TOKEN` | El bot que manda los avisos |
| `VIGIA_CHAT_ID` | El supergrupo donde escribe |
| `VIGIA_TOPICO_ERRORES` | Tópico "errores de precio" |
| `VIGIA_TOPICO_OFERTAS` | Tópico "ofertas reales" |
| `VIGIA_TOPICO_ELECTRONICOS` | Tópico por categoría |
| `VIGIA_TOPICO_HOGAR` | Tópico por categoría |

Si faltan los `VIGIA_TOPICO_*`, esos avisos caen al hilo general del grupo y
todo *parece* funcionar: no hay error, solo tópicos vacíos para siempre.

**Nada de esto va al repo, que es público.** Ver `.gitignore`.

---

## Cómo corre en la nube

`.github/workflows/hector.yml`, cron cada 4 h (`0 */4 * * *`) y también a mano
desde la pestaña Actions (*Run workflow*).

Tres cosas del workflow que conviene no tocar sin entender:

- **`cancel-in-progress: false`.** Si el cron se atrasa y dispara mientras la
  corrida anterior sigue escribiendo, la nueva espera en la cola. Con `true`,
  GitHub mataría a la anterior a mitad de escritura — el corte exacto que
  corrompió la base en Modal.
- **El paso "Encontrar la corrida anterior (no esta misma)".**
  `action-download-artifact` sin `run_id` busca "la corrida más reciente de
  este workflow", que es **esta misma**, recién creada. Se encontraba a sí
  misma, no hallaba artifact, y el catálogo se reiniciaba. Por eso el `run_id`
  se resuelve a mano con `gh api` y se le pasa explícito.
- **`if: always()` al guardar la base.** Si la barrida se cae a la mitad, igual
  hay que guardar lo que sí alcanzó a leer.

El cron de GitHub no tiene SLA: atrasos de 5 a 30 min son normales y en horas
de carga alta puede saltarse una corrida. Para un ciclo de 4 h es tolerable.

---

## Si vas a aportar

- **El código y los comentarios están en español**, y los comentarios explican
  *por qué*, no *qué*. Varios documentan una trampa que ya costó horas: si vas
  a cambiar algo que tiene un comentario largo encima, leelo primero.
- **Agregar una tienda:** sumarla a `TIENDAS` en `tiendas.py` con su nivel, y
  probar con un `probar_<tienda>.py` que el extractor le saque el precio a 8 de
  8 fichas. Si el HTML esconde el precio de una forma nueva, la estrategia va
  en `extractor.py`; si es una rareza de esa sola tienda, en `adaptadores.py`.
- **Respetar `robots.txt` no es negociable.** `descubrir.py` filtra por
  `Disallow` y `depurar_robots.py` saca del catálogo lo que una tienda pasó a
  prohibir después.
- **Nunca subir credenciales.** El repo es público y el token de Telegram
  abre el grupo entero.
- **No commitear `precios.db`** (ya está en `.gitignore`, pero se puede forzar
  sin querer con `git add -f`).

---

## Rat.IA (`cobro/`)

Subcarpeta aparte, con su propio README: el Worker de Cloudflare que cobra la
suscripción de Rat.IA por Flow.cl, entrega la invitación al grupo de Telegram
y registra cada pago en Supabase para el F29. No comparte código con Héctor —
solo el mismo bot de Telegram y el mismo repo.
