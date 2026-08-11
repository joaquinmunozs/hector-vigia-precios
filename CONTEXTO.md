# Rat.IA — contexto completo del proyecto

> **Para quien llega nuevo (humano o Claude).** Este documento existe para que
> alguien que nunca vio el proyecto entienda en una lectura qué es, cómo está
> armado, por qué se tomó cada decisión, qué está hecho y qué falta. No repite
> lo que ya dice el código: repite lo que el código **no puede** decir.
>
> Última actualización: **11-ago-2026**.

---

## 1. Qué es Rat.IA

Un servicio de suscripción que avisa por Telegram cuando el retail chileno
comete **un error de precio** (el televisor que quedó publicado en $9.990) o
publica **una oferta que de verdad lo es**.

**Precio: $2.990/mes.** El grupo de Telegram tiene cuatro Temas y el suscriptor
ve los cuatro.

### El problema que resuelve, y por qué no es obvio

Los canales de ofertas que ya existen en Chile copian el descuento que anuncia
la tienda. Ese número es **ficticio en la mayoría del retail chileno**: el
"precio normal" tachado se infla justo antes del Cyber para que el -70% del
cartel parezca real.

Rat.IA mide el descuento contra **lo que la tienda cobró de verdad las últimas
semanas**, porque guarda el historial de precios él mismo. Esa es la única
diferencia que importa y es toda la propuesta de valor. Si algún día el
producto empieza a copiar descuentos anunciados, deja de tener razón de ser.

### Estado del negocio

| | |
|---|---|
| Suscriptores pagando | **0** — el cobro está construido pero no lanzado |
| Producto técnico | Funcionando, corriendo solo desde el 8-ago-2026 |
| Lo que falta para vender | Link de MercadoPago de Max + encender la campaña |

---

## 2. Las dos mitades del sistema

El repo tiene dos cosas que se entienden por separado:

| | Qué es | Dónde vive |
|---|---|---|
| **Héctor** | El scraper. Vigila tiendas, guarda precios, detecta y avisa | Raíz del repo + GitHub Actions |
| **Cobro** | Landing, pagos y control de acceso al grupo | `cobro/` + Cloudflare |

Héctor es el producto. Cobro es la caja registradora. Se pueden tocar por
separado sin romper el otro.

---

## 3. Héctor — cómo funciona el scraper

Una corrida (`correr.py`) hace **dos cosas a la vez**, en el mismo proceso:

| | Qué mira | Ritmo |
|---|---|---|
| **Vigilante** (`vigilante.py`) | La *lista caliente*: ~1.500 productos deseables | Cada pocos segundos, 3,4 h |
| **Barrida** (`vigia.py`) | El catálogo completo, rotando | Una pasada por corrida |

**Van en el mismo proceso a propósito, no por comodidad.** En Modal corrían
como dos contenedores escribiendo el mismo SQLite y lo corrompieron de verdad
(7-ago-2026, `database disk image is malformed`). Si alguien alguna vez separa
esto en dos jobs, va a repetir ese día.

### Los módulos

```
correr.py         punto de entrada de GitHub Actions
tiendas.py        las 44 tiendas, con dificultad y rubro
descubrir.py      saca URLs de producto de los sitemaps, respetando robots.txt
extractor.py      lee el precio del HTML (JSON-LD, microdata, Next.js streaming)
adaptadores.py    los casos especiales por tienda
baseprecios.py    historial, línea base y clasificación (SQLite)
categorias.py     a qué tópico pertenece cada producto
caliente.py       arma la lista de productos deseables
alertas.py        redacta y manda los mensajes de Telegram
analisis_semanal.py  resumen semanal de movimiento de precios
proxy-tiendas/    Cloudflare Worker para las tiendas que bloquean al runner
```

### Cómo se decide que algo es un hallazgo

Todo pasa por `baseprecios.evaluar()`. No hay dos detectores: lo único que
separa "oferta" de "error de precio" es **cuánto cayó**.

1. **La referencia.** Con 5+ lecturas, la **mediana** del historial. Mediana y
   no promedio: si un error de precio viejo quedó registrado, el promedio se
   iría al suelo y la referencia quedaría falseada para siempre.
2. **La caída** = `1 - (precio_hoy / referencia)`.
3. **El piso**: 35% para electrónica y hogar, 40% para el resto.
4. **Ahorro mínimo $8.000.** Filtrar por precio del producto dejaba fuera
   gangas reales (una creatina de $20.000 a $10.000 lo es). Lo que separa una
   ganga de la basura no es cuánto vale el producto sino **cuánta plata se
   ahorra el suscriptor**.
5. **Tiene que ser el más barato jamás visto.** Si ya estuvo a ese precio, es
   una promoción que se repite todos los meses, no un hallazgo.
6. **Nada del mismo producto en 12 horas.**
7. **Sin historial no se avisan ofertas, solo errores.** Sin historial la
   referencia es la foto del día uno; si ese día estaba inflado, el precio
   normal de la semana siguiente se ve como un -55% que nunca existió. Un
   error del 70% no sale de una referencia mal fijada, así que ese sí se avisa
   desde el primer día — y además dura minutos, esperar sería no avisar.

### A qué tópico va cada hallazgo

| Caída | Tópico |
|---|---|
| 70%–99% | 🚨 Errores de precio, **solo ahí** |
| 60%–69% | Su categoría **+ Ofertas** |
| 40%–59% | Su categoría, o Ofertas si no tiene categoría |
| 35%–39% | Solo 📱 Electrónicos o 🏠 Hogar |

Ofertas es el acceso rápido a lo urgente. Un iPhone al 59% se queda solo en
Electrónicos; al 60% aparece en los dos.

Antes de enviar, **las variantes del mismo producto colapsan en un mensaje**
(una cortina en ocho colores es un producto, no ocho avisos).

---

## 3 bis. Estado real de los datos — 11-ago-2026

> Los números salen de la base de producción (el artifact de la última corrida,
> 319 MB, última lectura **11-ago 14:21**). **No** de la copia local, que está
> desactualizada y da cifras mucho menores — si alguien mide sobre su
> `precios.db` local va a creer que el sistema está peor de lo que está.

| | |
|---|---|
| Fichas descubiertas | **360.863** |
| Fichas **con precio medido** | **175.212** (48,6%) |
| Fichas con 5+ lecturas (listas para detectar ofertas) | **41.462** |
| Tiendas configuradas | 44 |
| Tiendas **con datos** | **21** |
| Tiendas sin una sola ficha | **23** |
| Alertas emitidas en total | 46 (1 error de precio, 21 ofertas, 24 de categoría) |

Las 41.462 con historial suficiente son **el activo real del negocio**: son las
únicas sobre las que hoy se puede afirmar un descuento con respaldo. El resto
del catálogo todavía está juntando historia.

### Las 21 tiendas que sí están dando datos

| Tienda | Fichas | Medidas | % | Nota |
|---|---:|---:|---:|---|
| falabella.com | 86.745 | 62.806 | 72% | El catálogo más grande |
| hites.com | 84.130 | 28.683 | 34% | Mucho por medir todavía |
| spdigital.cl | 64.903 | 799 | **1,2%** | Descubierto casi entero, medido casi nada |
| tricot.cl | 25.947 | 25.856 | **99,6%** | |
| bata.cl | 19.168 | 8.323 | 43% | |
| tottus.cl | 16.322 | 55 | **0,3%** | Recién empezando |
| salcobrand.cl | 14.209 | 12.618 | 89% | |
| santaisabel.cl | 11.191 | 8.012 | 72% | |
| construmart.cl | 9.886 | 9.886 | **100%** | |
| antartica.cl | 8.043 | 8.043 | **100%** | |
| puma.cl | 6.850 | 1.193 | 17% | |
| farmaciasahumada.cl | 5.408 | 5.405 | **100%** | |
| underarmour.cl | 2.372 | 1.455 | 61% | |
| hushpuppies.cl | 2.189 | 0 | **0%** | Descubre pero no mide |
| vans.cl | 1.001 | 0 | **0%** | Descubre pero no mide |
| doite.cl | 928 | 928 | 100% | |
| rosen.cl | 768 | 532 | 69% | |
| reuse.cl | 433 | 433 | 100% | |
| sportline.cl | 226 | 108 | 48% | |
| winnerchile.cl | 140 | 73 | 52% | |
| adidas.cl | 4 | 4 | 100% | Prácticamente sin descubrir |

### Las 23 tiendas configuradas que NO están dando nada

```
paris.cl        easy.cl          jumbo.cl         casaideas.cl
dinsa.cl        wei.cl           dafiti.cl        crandon.cl
converse.cl     kmarket.cl       buscalibre.cl    mall.cl
dijon.com       tecnored.cl      abc.cl           preunic.cl
pcfactory.cl    reifschneider.cl homy.cl          ripley.cl
cruzverde.cl    decathlon.cl     apple.com/cl
```

Están en `tiendas.py` pero su catálogo está vacío en producción. Las causas
conocidas, por grupo:

- **paris.cl y easy.cl** — devuelven **403 a la IP de Azure** del runner de
  GitHub. Ya existe la solución: salen por el Worker `hector-proxy-tiendas` de
  Cloudflare, probado A/B en el mismo job (60 fichas vs 403). Aparecen en cero
  porque el descubrimiento con proxy todavía no corrió completo.
- **dafiti.cl y homy.cl** — **no tienen sitemap**. Necesitan descubrimiento por
  crawl de categorías, que no está implementado.
- **buscalibre.cl** — su ficha de producto llega en 9 KB y el precio lo pinta
  JavaScript después. El HTML crudo no alcanza: necesita navegador.
- **tricot.cl, salcobrand.cl, casaideas.cl** — fallaron al medir sus límites de
  velocidad, así que no tienen ritmo seguro asignado. (Tricot y Salcobrand
  después se recuperaron y hoy están entre las mejores; casaideas sigue en
  cero.)
- **El resto** — no se ha llegado a ellas. El catálogo se repuebla solo los
  lunes y no alcanza a cubrir las 44 en una pasada.

### Los tres casos raros que conviene mirar primero

**spdigital.cl: 64.903 descubiertas, 799 medidas (1,2%).** Es la tercera tienda
más grande del catálogo y está prácticamente sin medir. Desbloquear esto sube
la cobertura más que agregar cualquier tienda nueva.

**hushpuppies.cl y vans.cl: descubren bien, miden 0%.** 3.190 fichas con URL y
ni un precio. Eso no es falta de tiempo — es que el extractor no encuentra el
precio en esas páginas. Necesitan un adaptador propio en `adaptadores.py`.

**tottus.cl: 16.322 fichas, 55 medidas.** Recién descubierta, todavía no le
tocó su turno de medición.

### La fragilidad operativa más grande

**El catálogo se repuebla solo los lunes, antes de las 08:00 UTC.** Si ese
lunes falla, pasa una semana entera con el catálogo que haya quedado. Ya pasó:
llegó a quedar en 3 de 44 tiendas.

---

## 4. Cobro — cómo entra la plata

> ### ⚠️ CAMBIO DE RUMBO — 11-ago-2026
>
> **Ya NO se cobra con Flow.** La decisión es cobrar por **WhatsApp, mandando
> un link de suscripción de MercadoPago** desde la cuenta de MP Developers de
> **Max**.
>
> **Por qué cambió:** la campaña no va a mandar a una landing sino a WhatsApp
> (ver §5 — es lo que baja el CAC de ~$12.000 a ~$5.000-6.200, y el CAC es el
> problema central del negocio). Si la conversación ya está en WhatsApp, meter
> una landing y una pasarela en el medio agrega dos pasos donde la gente se
> cae. El link de MP se pega en el chat y se paga ahí mismo.
>
> **Qué implica para el código de `cobro/`:** el Worker, el KV y el cron de
> vencidos **siguen sirviendo** — lo que cambia es quién avisa que alguien
> pagó. Hay que reemplazar el webhook de Flow por el de MercadoPago y validar
> su firma. El ciclo de vida del suscriptor, el link de invitación de un solo
> uso y el `ban`+`unban` no cambian en nada.
>
> **Lo que sigue abajo describe el diseño con Flow.** Se deja porque el
> razonamiento de por qué NO Stars, NO Khipu y NO InviteMember sigue valiendo,
> y porque la arquitectura es la misma salvo el proveedor.

```
Anuncio → landing → página de pago de Flow (alojada por Flow)
                          ↓ paga en CLP
                    webhook de Flow
                          ↓
                Cloudflare Worker
                 ├── valida la firma
                 ├── guarda el vencimiento en KV
                 └── createChatInviteLink (un solo uso)
                          ↓
              entra al GRUPO CON LOS 4 TEMAS

Cron diario → vencidos → banChatMember + unbanChatMember
```

### Por qué Flow y no las alternativas

| | Pérdida efectiva | Neto de $2.990 | Cuándo cobras |
|---|---|---|---|
| Telegram Stars | **~38%** | ~$1.850 | 21 días, en cripto |
| **Flow** | **~3,4%** | **~$2.887** | 3 días, a tu banco |

Además de lo económico hubo un bloqueante técnico: **las suscripciones nativas
de Stars solo existen en canales, y los canales no tienen Temas.** Mantener los
cuatro Temas era requisito duro.

También se descartaron:
- **Khipu** (~0,8%, la comisión más baja) funciona por transferencia: no queda
  tarjeta guardada, así que **cada mes el suscriptor decide pagar de nuevo**.
  En un ticket de $2.990 eso mata la retención.
- **InviteMember**: US$32,50/mes fijo y no cubre RedCompra, que es el 44% del
  mercado chileno.
- **Hotmart**: ~10% y necesita Zapier para hablar con Telegram.

### Por qué webhook y no polling

Consultar Flow por cron sería gratis, pero mete **hasta 15 minutos entre pagar
y entrar**. Para una compra por impulso eso es fatal: la persona paga, no pasa
nada, y cree que la estafaron.

### El detalle que parece un bug y no lo es

`banChatMember` seguido de `unbanChatMember`. `banChatMember` a secas deja al
usuario **vetado para siempre**: si vuelve a pagar el mes siguiente, no puede
entrar. El `unban` inmediato lo saca del grupo pero le devuelve el derecho a
reingresar.

### Es a prueba de fallos por diseño

El acceso depende de que **llegue un pago**, no de que llegue un aviso de
cancelación. Si Flow deja de mandar webhooks, en el peor caso la gente vence
sola. **Nunca se queda alguien adentro gratis para siempre.**

---

## 5. Números del negocio

Modelo corrido el 10-ago-2026, escenario realista (CAC $12.000, churn 12%):

| | |
|---|---|
| Deja de necesitar capital | **Mes 10** |
| Capital total necesario | **$2,7 millones** |
| CAC máximo tolerable | **$7.807** con churn 12% |

**El CAC de $12.000 es el problema central del negocio, no un detalle.** Está
por encima del techo que aguanta el modelo.

La vía para bajarlo, ya definida y no ejecutada: campaña de Meta con **CTA a
WhatsApp** en vez de a la landing, y ahí mandar el link de pago. Estimado
**$5.000–6.200 de CAC**, que sí cabe dentro del modelo.

Referencia real de otra campaña de Cóndor.ai con ese formato: 61 conversaciones
iniciadas → 32 llegaron al segundo mensaje → 22 al tercero. **La fuga no está
en el anuncio, está en la conversación**, y un lead de WhatsApp espera respuesta
en menos de 30 segundos.

---

## 6. La idea de multiplicar capacidad con socios

Héctor corre en GitHub Actions, que es gratis pero tiene un techo de horas y
una sola IP de salida. La idea: **cada socio levanta su propia instancia** en
su propia cuenta de GitHub, con su propio subconjunto de tiendas.

Ya está implementado: la variable `HECTOR_TIENDAS` reparte las 44 tiendas entre
instancias. Cada repo define su subconjunto y no se pisan. Todas escriben al
**mismo bot de Telegram**, así que el suscriptor no nota nada.

Ventajas reales: más horas de cómputo, **IP distinta** (varias tiendas bloquean
por IP), y el catálogo se recorre más rápido.

Lo que falta: que Max levante su instancia y se decida el reparto de tiendas.

---

## 7. Decisiones que parecen raras y tienen razón

Están acá para que nadie las "arregle" sin saber.

**El catálogo se repuebla solo los lunes, y la barrida completa es una vez al
día.** Se probó cada 4 horas y se revirtió: el dato que se le vende al
suscriptor es un **histórico**, una mediana sostenida por meses. Una referencia
que se actualiza muy seguido se "acostumbra" al precio bajo y deja de verlo
como caída.

**Un precio que se contradice no se usa.** Si dos estrategias de lectura
difieren más de 3×, el producto no se mide. Salió de un caso real: una manteca
de cacao de $34.030 se alertó como $681, que era el **precio por gramo** que la
ficha muestra al lado. Una ficha sin dato es invisible; una alerta falsa la ve
el suscriptor entero, entra al link, y deja de creerle al canal.

**Commit por escritura, no cada 50.** Batchear commits mantenía el lock de
escritura tomado durante descargas HTTP y producía `database is locked`.

**`cancel-in-progress: false` en el workflow.** Con `true`, GitHub mata el
proceso anterior a mitad de escritura — el corte abrupto que corrompió el
SQLite en Modal.

**El artifact de la base se busca por `run_id` explícito.** Sin eso, la acción
encuentra *la corrida actual* (que aún no tiene artifact), no encuentra nada, y
**el catálogo se reinicia desde cero**. Pasó durante días sin que nadie lo
notara: cada URL era "nueva", su referencia se fijaba en el mismo instante en
que se leía, y nunca había con qué comparar.

**paris.cl y easy.cl salen por un Worker de Cloudflare.** Devuelven 403 a la IP
de Azure del runner y 200 al edge de Cloudflare. Probado A/B en el mismo job.

---

## 8. Infraestructura y accesos

| Qué | Dónde | Quién tiene acceso |
|---|---|---|
| Scraper | GitHub Actions (este repo) | Joaquín |
| Proxy de tiendas | Cloudflare Worker `hector-proxy-tiendas` | Joaquín |
| Cobro | Cloudflare Worker + KV | Joaquín |
| Landing | Cloudflare Pages | Joaquín |
| Grupo | Telegram, 4 Temas | Joaquín |
| Pagos | Flow.cl | Joaquín |

**Ningún secreto vive en el repo.** Están en GitHub Secrets y en secrets de
Cloudflare. El `.gitignore` bloquea `.env`, `config.json` y `*.db`, y se
verificó que **nunca** se commiteó un `.env`.

Secrets que usa el scraper: `TELEGRAM_BOT_TOKEN`, `VIGIA_CHAT_ID`,
`VIGIA_TOPICO_*` (uno por tópico), `HECTOR_PROXY_TOKEN`.

---

## 9. Qué falta, en orden de urgencia

### Bloquea el lanzamiento

1. **Link de suscripción de MercadoPago**, desde la cuenta de MP Developers de
   Max. Es lo que se le manda al cliente por WhatsApp. Sin esto no entra un
   peso. (Reemplaza al plan anterior con Flow — ver el aviso en §4.)
2. **Adaptar el Worker de `cobro/` al webhook de MercadoPago.** El resto del
   Worker sirve igual: solo cambia quién avisa que alguien pagó.
3. **Encender la campaña** con CTA a WhatsApp (ver §5).

### Importante pero no bloquea

4. **Bajar el CAC.** Es el número que decide si el negocio existe.
5. **Medir spdigital.cl.** 64.903 fichas descubiertas y solo 799 medidas
   (1,2%). Es la tercera tienda más grande: desbloquearla sube la cobertura
   más que agregar cualquier tienda nueva.
5. **Adaptador para hushpuppies.cl y vans.cl.** Descubren 3.190 fichas entre
   las dos y miden **cero**. No es falta de tiempo: el extractor no encuentra
   el precio en esas páginas.
6. **Terminar el descubrimiento de paris.cl y easy.cl por el proxy.** La
   solución al 403 ya existe y está probada; falta que corra completa.
7. **dafiti.cl y homy.cl no tienen sitemap**: necesitan descubrimiento por
   crawl de categorías, que no está implementado.
8. **casaideas.cl** sigue sin medir tras fallar la medición de límites.
9. **Forzar el repoblado del catálogo** si un lunes falla — si no, pasa una
   semana con lo que haya.

### Ideas que no empezaron

10. Repartir tiendas con Max (el código ya está, falta acordar el reparto).
11. Análisis quincenal leyendo `historial/` para ajustar umbrales con datos.

---

## 10. Cómo correrlo en local

```bash
pip install -r requirements.txt
cp .env.ejemplo .env        # y completar el token de Telegram
python correr.py            # corrida completa
```

Modos útiles:

```bash
HECTOR_SOLO_DESCUBRIR=1 python correr.py   # solo repoblar catálogo
HECTOR_TIENDAS=jumbo.cl,paris.cl python correr.py   # solo esas tiendas
python analisis_semanal.py --probar        # análisis sin mandar nada
```

**Sin `TELEGRAM_BOT_TOKEN` no manda nada: imprime los mensajes en pantalla.**
Es la forma segura de probar cambios en las alertas sin llenarle el grupo a
nadie.

---

## 11. Notas para Claude

- **Español neutro, nunca voseo.** "Puedes", no "podés". Aplica a mensajes de
  bots, documentación y respuestas.
- **Actuar, no consultar.** Joaquín prefiere que se haga la tarea completa y se
  avise después en una línea. Solo se pregunta si hay riesgo grave e
  irreversible.
- **No inventar cifras.** Si un número no sale de los datos, no va. Ya pasó una
  vez con estadísticas de una web y hubo que borrarlas.
- **Usar la herramienta Edit para archivos con escapes.** Los heredocs de bash
  convierten `\n` en saltos de línea reales y rompen literales de Python y JS.
  Pasó cinco veces.
- **Antes de borrar mensajes de Telegram**, verificar que son del bot. El bot es
  administrador y puede borrar mensajes de personas. La sonda no destructiva es
  `editMessageReplyMarkup` con teclado vacío: si responde "message is not
  modified", el mensaje es del bot.
