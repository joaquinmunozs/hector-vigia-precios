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
| Lo que falta para vender | Un secret de Flow y encender la campaña |

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

### Estado real de los datos

| | |
|---|---|
| Fichas en catálogo | ~151.000 |
| Tiendas configuradas | 44 |
| Tiendas con datos | 16 |
| Corre | GitHub Actions, cada 4 h |

**El catálogo se repuebla solo los lunes.** Si un lunes falla, pasa una semana
con el catálogo que haya. Es la fragilidad operativa más grande que tiene el
sistema hoy.

---

## 4. Cobro — cómo entra la plata

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

1. **`FLOW_SECRET` en el Worker de cobro** y registrar la URL del webhook en
   Flow. Sin esto no entra un peso.
2. **Encender la campaña** con CTA a WhatsApp, no a la landing (ver §5).

### Importante pero no bloquea

3. **Bajar el CAC.** Es el número que decide si el negocio existe.
4. **El catálogo quedó en 3 de 44 tiendas** y solo se repuebla los lunes antes
   de las 08:00 UTC. Si no se fuerza, pasa una semana así.
5. **Re-medir tricot, salcobrand y casaideas** — fallaron al medir sus límites.
6. **dafiti y homy no tienen sitemap**: necesitan descubrimiento por crawl de
   categorías.

### Ideas que no empezaron

7. Repartir tiendas con Max (el código ya está, falta acordar el reparto).
8. Análisis quincenal leyendo `historial/` para ajustar umbrales con datos.

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
