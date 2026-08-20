# Héctor — bitácora del 20 de agosto de 2026

> **Léeme antes de tocar código.** Este archivo existe para que alguien que
> nunca vio a Héctor pueda entender qué hace, por qué está armado así, y
> cuáles son las trampas que ya nos costaron caro. Casi toda decisión rara
> que veas en el código tiene una razón medida, no una preferencia. Antes de
> "arreglar" algo que se ve torcido, busca acá por qué está torcido.

---

## 1. Qué es Héctor, en una frase

Héctor vigila los precios de **44 tiendas chilenas** y **23 aerolíneas**, y
cuando algo cae mucho más de lo normal lo avisa por Telegram, separado por
tópicos. Dos tipos de hallazgo:

- **Error de precio** (caída ≥ 70%): la tienda se equivocó. Dura minutos.
- **Oferta real** (caída ≥ 40%): un descuento de verdad, medido contra el
  historial, no contra el "precio normal" que la tienda dice tener.

Todo el valor está en la segunda mitad de esa frase: **medido contra el
historial**. El retail chileno infla precios antes de cada Cyber para
después "rebajarlos". Héctor no cree en el precio tachado; cree en lo que él
mismo vio durante 30 días.

---

## 2. El recorrido completo de un dato

```
sitemaps de la tienda
      │  descubrir.py — saca URLs de fichas, respetando robots.txt
      ▼
  catálogo de fichas (URL + tienda)
      │  vigia.py — barrida completa del catálogo
      │  vigilante.py — ciclo rápido sobre la "lista caliente"
      ▼
  descubrir.bajar(url)  ← ÚNICO punto por donde sale toda petición
      │  extractor.py — saca el precio del HTML (JSON-LD, microdata, Next.js)
      │  adaptadores.py — los casos especiales por tienda
      ▼
  baseprecios.py — guarda la lectura, calcula la referencia, decide si es hallazgo
      │  categorias.py — a qué tópico pertenece
      ▼
  alertas.py — arma el mensaje y lo manda a Telegram
```

**`descubrir.bajar()` es el cuello de botella a propósito.** Todas las
peticiones de Héctor —descubrimiento, barrida y vigilante— pasan por ahí. Si
necesitas cambiar cómo se pide una página (proxy, cabeceras, límites), se
cambia en un solo lugar. No lo repartas.

---

## 3. Los módulos, uno por uno

| Archivo | Qué hace | Qué NO hace |
|---|---|---|
| `correr.py` | Punto de entrada de GitHub Actions | No tiene lógica de negocio |
| `tiendas.py` | Las 44 tiendas: dominio, nivel de defensa, rubro | No decide categorías de producto |
| `aerolineas.py` | Las 23 aerolíneas con rutas desde Chile | No busca pasajes por fecha |
| `descubrir.py` | Saca URLs de sitemaps + **baja toda página** | — |
| `extractor.py` | Lee el precio del HTML | No sabe de tiendas específicas |
| `adaptadores.py` | Los casos especiales por tienda | — |
| `baseprecios.py` | Historial, línea base, y si algo es hallazgo (SQLite) | No manda mensajes |
| `categorias.py` | A qué tópico va un hallazgo | No decide si es hallazgo |
| `caliente.py` | Arma y mantiene la lista caliente | — |
| `vigilante.py` | Ciclo rápido sobre la lista caliente | — |
| `vigia.py` | Barrida del catálogo completo | — |
| `alertas.py` | Arma y manda los avisos a Telegram | No decide umbrales |
| `medir_limites.py` | Cuántas req/s aguanta cada **tienda** | — |
| `medir_vuelos.py` | Lo mismo para **aerolíneas**, escalera más suave | — |
| `probar_*.py` | 16 pruebas. Se corren a mano, no en CI | — |

Las 44 tiendas por dificultad: **17 limpias, 23 medias, 4 difíciles**.

---

## 4. Las reglas de negocio, y por qué son así

Todas viven en `baseprecios.py`. **No las cambies sin leer esta sección.**

| Constante | Valor | Qué significa |
|---|---|---|
| `UMBRAL_ERROR` | 0.70 | ≥70% de caída = error de precio |
| `UMBRAL_OFERTA` | 0.40 | ≥40% = oferta real (piso general) |
| `UMBRAL_CATEGORIA` | 0.35 | Electrónicos y Hogar bajan el piso a 35% |
| `UMBRAL_VUELOS` | 0.40 | **Vuelos NO usa el 35%** — ver §7 |
| `AHORRO_MINIMO` | $8.000 | Piso en pesos… salvo para errores de precio |
| `PRECIO_MINIMO` | $20.000 | Piso de precio por categoría… salvo vuelos |
| `MIN_OBSERVACIONES` | 5 | Lecturas mínimas para tener historial |
| `DIAS_MINIMOS_HISTORIAL` | 7 | Días mínimos para tener historial |

### Las cuatro reglas que parecen arbitrarias y no lo son

1. **La referencia es el MÍNIMO histórico, no la mediana.** Caso real: un
   producto de $100.000 inflado a $150.000 cinco días antes del Cyber y
   "rebajado" a $38.000. Contra la mediana daba −75% y salía como 🚨 ERROR
   DE PRECIO. Contra el mínimo da −62%, que es lo que de verdad bajó.

2. **Una oferta sin historial no se avisa; un error sí.** Sin historial, la
   referencia es la foto del día que se descubrió el producto. Si ese día
   estaba inflado, el precio normal de la semana siguiente se ve como un
   −55% que nunca existió. Los errores sí salen desde el día uno: para pasar
   el 70% el precio tiene que haberse caído de verdad, y un error dura
   minutos — esperar historial sería llegar tarde siempre.

3. **El piso de $8.000 NO aplica a errores de precio.** (15-ago) Hubo
   **cero alertas en 24 h** con cientos de miles de cambios detectados. La
   causa: una broca de $7.290 a $1.000 (−86%) nunca alcanza $8.000 de
   ahorro absoluto. El piso de plata tiene sentido para ofertas, no para
   errores: ahí el porcentaje ya es la señal.

4. **Un hallazgo tiene UN destino, no varios.** (11-ago) Antes, un producto
   de categoría entre 50% y 70% salía en Ofertas *y* en su tópico. Ofertas
   terminó siendo la suma de todo y quedó saturado — la forma más rápida de
   que alguien silencie el canal. Ahora: cada hallazgo va a un tópico, y
   Ofertas solo recibe lo que no calza en ninguna categoría, más todo lo que
   pase el 60%.

---

## 5. Dónde corre

`.github/workflows/hector.yml`, **cron cada 6 h** (`0 */6 * * *`), en
**4 shards paralelos** de tiendas, con `timeout-minutes: 350` cada uno.

⚠️ **Esto obliga a que el repo sea PÚBLICO.** GitHub da minutos de Actions
ilimitados a repos públicos, y solo 2.000/mes a los privados. Héctor puede
consumir del orden de 4 × 350 × 4 = **5.600 minutos por día**. En un repo
privado se apaga el primer día, o cuesta cientos de dólares al mes. Si
alguien propone "pasemos el repo a privado", la respuesta es no, salvo que
antes se resuelva esto.

### El proxy de Cloudflare

`easy.cl` y `paris.cl` devuelven **403 a las IPs de GitHub Actions** (salen
por Azure). Desde una conexión chilena responden 200. Por eso existe
`proxy-tiendas/`, un Worker de Cloudflare que hace de puente.

Se activa solo si están `HECTOR_PROXY_URL` y `HECTOR_PROXY_TOKEN`. **Sin
ellas no hace nada** y todo funciona como antes — a propósito, porque desde
el PC de Joaquín esas tiendas responden bien directo.

---

## 6. Los tópicos de Telegram

| Tópico | Variable | ID | Qué recibe |
|---|---|---|---|
| 🚨 Errores de precio | `VIGIA_TOPICO_ERRORES` | 2 | Caída ≥70%, cualquier producto |
| 🏷️ Ofertas reales | `VIGIA_TOPICO_OFERTAS` | 4 | Lo sin categoría, y todo lo ≥60% |
| 📱 Electrónicos | `VIGIA_TOPICO_ELECTRONICOS` | 36 | 35%–69%, solo electrónica |
| 🏠 Hogar | `VIGIA_TOPICO_HOGAR` | 38 | 35%–69%, solo hogar |
| 🛒 Supermercado | `VIGIA_TOPICO_SUPERMERCADO` | 350 | **creado pero SIN USAR** |
| ✈️ Ofertas Vuelos | `VIGIA_TOPICO_VUELOS` | 351 | ≥40%, solo vuelos |

⚠️ **Si falta un `VIGIA_TOPICO_*`, no hay error.** El aviso cae al hilo
general del grupo y todo *parece* funcionar: simplemente ese tópico queda
vacío para siempre. Es el modo de falla más silencioso que tiene Héctor.

⚠️ **Supermercado (350) está creado pero nadie lo usa.** No hay patrones de
clasificación para supermercado en `categorias.py` ni la variable está en el
workflow. Es trabajo pendiente, no un bug.

---

## 7. Lo nuevo del 20 de agosto: vuelos

Héctor ahora vigila pasajes. **23 aerolíneas** con rutas desde Chile, todas
verificadas contra el sitio real (se probaron 49 URLs, no se adivinó ninguna).

### Qué cubre y qué NO — importante

Se vigila la **página de ofertas** de cada aerolínea, que es una URL fija con
precios adentro: el mismo problema que Héctor ya sabe resolver.

**NO cubre** una tarifa barata en una fecha suelta que la aerolínea no
publicó como oferta. Un vuelo no tiene ficha: el precio depende de
ruta + fecha + disponibilidad y cambia sin que cambie ninguna URL. Para eso
haría falta una API de búsqueda (Amadeus, Kiwi, Skyscanner), que es otra
arquitectura y se paga. **Si alguien pide "que vigile todos los vuelos con
escala", eso es lo que está pidiendo, y no es una extensión de esto.**

### Las dos decisiones que casi salen mal

1. **Piso del 40%, no del 35%.** Al entrar Vuelos como "categoría" heredaba
   `UMBRAL_CATEGORIA` (35%). Se le puso `UMBRAL_VUELOS = 0.40`. En pasajes,
   un 35% es una promoción de martes cualquiera.
2. **Los vuelos se saltan `PRECIO_MINIMO`.** Es lo más importante de esta
   parte: el mejor hallazgo posible del rubro es el Santiago–Madrid a $4.000
   mal cargado, y ese pasaje cuesta *menos* que el piso de $20.000. Con el
   piso puesto no se avisaría nunca.

Un vuelo se reconoce **por dominio** (`categorias._es_aerolinea`), al revés
que todo lo demás. Ahí sí corresponde: la razón para clasificar por nombre
era que Falabella vende de todo; latam.com vende una sola cosa.

### El estudio de peticiones por segundo

Está completo en `docs/medicion-aerolineas-2026-08-20.md`. Resumen:
**22 de 23 aguantaron 40 hilos sin degradar** — el cuello no son las
aerolíneas.

| | recomendado |
|---|---:|
| lufthansa | 86,3 req/s |
| klm | 65,4 req/s |
| **skyairline** | 23,2 req/s |
| **jetsmart** | 10,4 req/s |
| **latam** | **1,4 req/s** |
| paranair | 0,6 req/s |
| copa | bloqueada |

**LATAM es la más lenta por lejos** y es la aerolínea más importante del
país. No es que bloquee: su página pesa 724 KB y el límite es el ancho de
banda.

⚠️ **Medir a Copa dejó marcada la IP de la casa en su WAF.** Bloquea incluso
una petición cada 5 segundos, en todas sus URLs. **No volver a medir Copa
desde ese PC**: si hay que reintentar, desde el runner de GitHub o por el
proxy. Copa quedó marcada `WAF` en `aerolineas.py`.

---

## 8. El incidente de la cuota de Cloudflare (20-ago)

La cuenta `contacto@teamcondorcl.com` tiene un límite gratuito de **100.000
peticiones al día compartido entre TODOS los Workers**. Medido con la API de
analytics de Cloudflare:

| Worker | Peticiones / 24 h |
|---|---:|
| **`hector-proxy-tiendas`** | **138.775** |
| `planeta-webhook-ml` | 185 |
| `steve-disparador` | 20 |
| `veci-leads-api` | 12 |
| `ratia-cobro` | 3 |

**Héctor es el 99,8%.** Con la cuota agotada Cloudflare devuelve error 1027
(HTTP 429), y ese día tumbó el webhook de MercadoLibre (las ventas de
Planeta Shop dejaron de registrarse) y dejó al ERP sirviendo HTML donde
debía ir JSON.

**El número no viene de la cantidad de fichas, sino de la frecuencia.** Hay
985 fichas de easy.cl y **cero** de paris.cl; el vigilante las repasa ~141
veces al día. Una estimación anterior decía "25.000 fichas por corrida" y
estaba equivocada. Importa porque recortar catálogo no habría servido de
nada.

**Parche aplicado:** `HECTOR_PROXY_MAX_CORRIDA` (por defecto 15.000
peticiones por corrida) en `descubrir.py`. Al agotarse, las peticiones salen
directas — o sea Héctor se comporta como antes de que el proxy existiera, y
esas fichas se saltan. **Recorta cobertura de easy.cl: es un puente, no la
solución.** La solución es el plan Workers de US$5/mes (10 millones
incluidas).

---

## 9. Las lecciones que costaron caro

Están acá para que nadie las repita:

1. **Medir contra la portada miente.** La portada de spdigital hacía creer
   que la tienda era lentísima. La de skyairline da desafío WAF mientras
   `/chile/destinos` responde 200 con 986 KB. **Medí siempre contra una
   ficha real.**
2. **Cuando algo "no se actualiza" o "no llega", sospecha primero de la
   fuente de datos**, no de la lógica de negocio. ¿Es la correcta? ¿Está
   viva? Dos bugs distintos del mismo día tuvieron esa forma.
3. **Un HTTP 200 puede ser un fallo.** El ERP devolvía `index.html` con 200
   donde esperábamos JSON. No hay error en ningún log; la plataforma
   simplemente se ve vacía.
4. **El bug del `pum`:** 11 avisos de error de precio eran falsos — se leía
   el precio por kilo como si fuera el precio del producto. Arreglado, pero
   la lección es que un extractor "que funciona" puede estar leyendo el
   número equivocado.
5. **Un regex de sanidad puede descartar en silencio lo más importante.** En
   el reenvío de Rat.IA, un regex que aceptaba solo un decimal ("98,9%")
   descartaba el canal más valioso porque a veces manda dos ("95,38%").
6. **No revientes una tienda para encontrar su techo.** La IP que se quema
   es la de la casa de Joaquín. Por eso `medir_limites.py` frena al primer
   signo de bloqueo. Aun así, Copa nos marcó.

---

## 10. Correrlo en tu máquina

```bash
pip install -r requirements.txt

python aerolineas.py                # ver el registro de aerolíneas
python medir_vuelos.py latam sky    # medir algunas aerolíneas
python medir_limites.py falabella   # medir una tienda
python probar_vuelos.py             # la prueba de vuelos
for f in probar_*.py; do python $f; done   # las 16
```

**La base `precios.db` NO está en el repo.** Pesa ~36 MB y git no es para
eso. La local que puedas tener es un fragmento y **engaña**: hoy tiene
154.868 filas y 16 tiendas, contra las 44 de producción. Si vas a sacar
conclusiones de datos, baja el artifact `precios-db-*` de Actions.

Correr `medir_vuelos.py` **por tandas de 5**, no las 23 de una: tarda ~1,2
min por aerolínea y la salida se pierde entera si el proceso se corta (ya
nos pasó).

---

## 11. Estado y pendientes

### Hecho el 20-ago
- ✅ Vuelos: `aerolineas.py`, `medir_vuelos.py`, `probar_vuelos.py`
- ✅ `UMBRAL_VUELOS`, salto de `PRECIO_MINIMO`, ruteo al tópico 351
- ✅ `VIGIA_TOPICO_VUELOS` enganchado en `hector.yml`
- ✅ Estudio de req/s de las 23 aerolíneas
- ✅ Presupuesto del proxy (`HECTOR_PROXY_MAX_CORRIDA`)
- ✅ Suite completa: **16/16 pasan**

### Pendiente
- ⬜ **Plan Workers de US$5/mes.** Sin eso, Héctor tumba el webhook de ventas
  de Planeta Shop todos los días.
- ⬜ **Tópico Supermercado (350)**: creado, sin patrones ni variable.
- ⬜ **Copa**: necesita pasar por el proxy para vigilarse.
- ⬜ **qatar, aircanada, emirates, boa, plusultra, estelar**: no entraron.
  El motivo de cada una está al final de `aerolineas.py`.
- ⬜ Reintentar la medición de Copa en unos días, desde otra IP.

---

## 12. Si vas a aportar

1. **Lee el comentario antes de cambiar la línea.** Casi todo lo raro tiene
   una fecha y un caso real al lado. Si el comentario no explica por qué,
   ese es el bug a arreglar.
2. **Los umbrales son negocio, no estilo.** Cambiar `UMBRAL_OFERTA` cambia
   cuánta gente silencia el canal. Discútelo antes.
3. **Toda medición contra el sitio real, nunca de memoria.** Las 23
   aerolíneas están verificadas; las 6 que no entraron dicen por qué.
4. **Si agregas una petición, que salga por `descubrir.bajar()`.**
5. **Corre las 16 pruebas antes de subir.** Tardan poco.
6. **Nunca subas `precios.db`, `.env` ni ningún `.session`.** Un archivo
   `.session` de Telethon equivale a la contraseña de la cuenta de Telegram.

---

*Escrito el 20-ago-2026. Si algo de acá ya no calza con el código, el código
manda — pero avisa, porque significa que esta bitácora quedó vieja.*
