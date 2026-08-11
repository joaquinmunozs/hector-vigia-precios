# Integraciones pendientes — MercadoPago e Instagram

> **Para Max y su Claude.** Este documento es el encargo técnico de las dos
> integraciones que faltan para que Héctor cobre y publique solo. Está escrito
> para que se pueda implementar sin adivinar: endpoints exactos, credenciales,
> trampas conocidas y cómo probar cada cosa.
>
> **Lo visual NO va acá.** Joaquín hace las plantillas de los carruseles y las
> commitea aparte. Este encargo es solo la fontanería: que la plata entre y que
> las imágenes suban.
>
> Contexto general del proyecto: ver [`CONTEXTO.md`](CONTEXTO.md).
> Escrito el **11-ago-2026**.

---

## Lo que hay que construir, en una frase

**MercadoPago:** que alguien pague $2.990 por WhatsApp y entre solo al grupo
de Telegram en menos de un minuto.

**Instagram:** que Héctor publique sus hallazgos como carruseles, con retraso,
sin que nadie apriete un botón.

Son independientes. Se pueden hacer en cualquier orden y por personas distintas.

---

# 1 · MercadoPago — cobro por suscripción

## Qué reemplaza

El código de `cobro/` está escrito para **Flow**, que se descartó el
11-ago-2026. La arquitectura sirve igual: **lo único que cambia es quién avisa
que alguien pagó.** El Worker, el KV, el link de invitación de un solo uso y el
cron de vencidos quedan intactos.

## Por qué MercadoPago y no una landing con pasarela

La campaña manda a **WhatsApp**, no a un sitio. Ahí la conversación ya está
abierta, y meter una landing en el medio agrega dos pasos donde la gente se
cae. El link de MP se pega en el chat y se paga ahí mismo.

## Suscripción recurrente, no pago único

Hay que usar **`preapproval`** (suscripción con cargo automático), no un link
de pago simple.

La diferencia decide el negocio: con pago único, **cada mes el suscriptor tiene
que decidir pagar de nuevo**. En un ticket de $2.990 eso mata la retención — es
exactamente la razón por la que se descartó Khipu. Con `preapproval` la tarjeta
queda guardada y MercadoPago cobra solo.

## Credenciales

De la cuenta de **MercadoPago Developers de Max**, en
<https://www.mercadopago.cl/developers/panel>:

| Secret | Dónde sale | Para qué |
|---|---|---|
| `MP_ACCESS_TOKEN` | Panel → Tus integraciones → Credenciales de producción | Todas las llamadas a la API |
| `MP_WEBHOOK_SECRET` | Panel → Webhooks → Configurar notificaciones | Validar que el webhook es real |

**Importante:** empezar con las credenciales de **prueba** (`TEST-...`) hasta
que el flujo completo funcione, y recién ahí cambiar a producción. MP permite
simular pagos con tarjetas de prueba sin mover plata real.

## Paso 1 — Crear el plan (una sola vez)

```http
POST https://api.mercadopago.com/preapproval_plan
Authorization: Bearer {MP_ACCESS_TOKEN}
Content-Type: application/json

{
  "reason": "Héctor — alertas de precio",
  "auto_recurring": {
    "frequency": 1,
    "frequency_type": "months",
    "transaction_amount": 2990,
    "currency_id": "CLP"
  },
  "back_url": "https://t.me/…",
  "payer_email": ""
}
```

Devuelve un `id` y un **`init_point`**: esa URL es el link que se pega en
WhatsApp. Es fija — se crea una vez y se usa siempre.

Guardar el `id` del plan en el repo (no es secreto).

## Paso 2 — Recibir el aviso de pago

MercadoPago manda un `POST` al webhook con este cuerpo:

```json
{
  "type": "subscription_preapproval",
  "action": "created",
  "data": { "id": "2c938084…" }
}
```

**El webhook NO trae el estado ni el monto.** Solo el id. Hay que consultarlo:

```http
GET https://api.mercadopago.com/preapproval/{data.id}
Authorization: Bearer {MP_ACCESS_TOKEN}
```

De ahí salen `status` (`authorized` = pagó y está vigente), `payer_email` y
`next_payment_date`.

### Los tres eventos que importan

| `type` | Cuándo llega | Qué hacer |
|---|---|---|
| `subscription_preapproval` | Alguien se suscribe o cambia de estado | Si `status = authorized` → dar acceso |
| `subscription_authorized_payment` | Se cobró la mensualidad | Extender el vencimiento +30 días |
| `payment` | Pago suelto | Ignorar, salvo que se venda algo aparte |

## Paso 3 — Validar la firma (obligatorio)

El endpoint es público: **cualquiera puede pegarle y regalarse acceso gratis.**
Sin esta validación el negocio está abierto.

MercadoPago manda una cabecera `x-signature` con este formato:

```
ts=1704908010,v1=618c85345248dd820d5fd456117c2ab2ef8eda45a0282ff693eac24131a5e839
```

El manifiesto a firmar se arma así, **respetando el orden y los `;` finales**:

```
id:{data.id};request-id:{cabecera x-request-id};ts:{ts};
```

Y se compara con HMAC-SHA256 usando `MP_WEBHOOK_SECRET`. Si no coincide →
`401`, sin procesar nada.

> **Trampa:** el `data.id` va en minúsculas si viene alfanumérico. Y si falta la
> cabecera `x-request-id`, ese campo se omite del manifiesto pero el `;` se
> mantiene. Un manifiesto mal armado da firma inválida siempre y se pierde una
> tarde depurando.

## Paso 4 — Dar el acceso

Esto **ya está escrito** en `cobro/src/telegram.js`. Crea un link de invitación
de un solo uso:

```
POST https://api.telegram.org/bot{token}/createChatInviteLink
{ "chat_id": …, "member_limit": 1, "expire_date": … }
```

Y el cron diario saca a los vencidos con `banChatMember` **seguido de**
`unbanChatMember`.

> **No quitar el `unban`.** `banChatMember` a secas deja al usuario **vetado
> para siempre**: si vuelve a pagar el mes siguiente, no puede entrar. El
> `unban` inmediato lo saca del grupo pero le devuelve el derecho a reingresar.

## Errores que hay que manejar

- **Webhook repetido.** MP reintenta si no recibe `200` rápido. Guardar el id
  procesado y responder `200` sin hacer nada la segunda vez. Sin esto se
  generan dos links de invitación por un pago.
- **Responder `200` antes de terminar.** MP corta a los ~22 segundos. Conviene
  responder al toque y hacer el trabajo después (`ctx.waitUntil` en Workers).
- **Telegram caído al crear el link.** Guardar el pago igual, marcarlo
  `pendiente_invitacion`, y que el cron reintente. **Nunca se puede perder un
  pago por un error de Telegram.**

## Cómo probar sin plata real

1. Credenciales `TEST-…` y usuario de prueba (Panel → Cuentas de prueba).
2. Tarjeta de prueba: `5416 7526 0258 2580`, cualquier fecha futura, CVV `123`.
   El nombre del titular define el resultado: `APRO` aprueba, `OTHE` rechaza.
3. Para el webhook en local: `npx wrangler dev --remote` y apuntar la URL de
   notificación del panel a ese túnel.

---

# 2 · Instagram — publicación automática de carruseles

## Requisitos que ya están cumplidos

- Cuenta de Instagram tipo **Empresa** ✅
- Vinculada a una **Página de Facebook** ✅

Sin esas dos cosas la API no existe, así que ese camino ya está despejado.

## Credenciales

| Secret | Para qué |
|---|---|
| `IG_ACCESS_TOKEN` | Publicar |
| `IG_USER_ID` | A qué cuenta se publica |

### Cómo sacar el `IG_USER_ID`

Con un token que tenga los permisos de abajo:

```http
GET https://graph.facebook.com/v21.0/me/accounts
     → devuelve las Páginas; tomar el {page-id} de la de Héctor

GET https://graph.facebook.com/v21.0/{page-id}?fields=instagram_business_account
     → devuelve el IG_USER_ID
```

### Permisos que necesita la App de Meta

```
instagram_basic
instagram_content_publish
pages_show_list
pages_read_engagement
```

> **Ojo con el token.** El de usuario dura 1 hora; el de larga duración, 60
> días. Para algo que corre solo todos los días conviene un **System User
> token** desde Business Manager → Configuración → Usuarios del sistema: **no
> expira**. Si se usa el de 60 días, hay que renovarlo o el bot muere en
> silencio dos meses después.
>
> Vale revisar si sirve el token que ya usa `meta-analyzer` de Cóndor.ai: si la
> Página de Héctor está en el mismo Business Manager, puede alcanzar con
> agregarle los permisos.

## Publicar un carrusel: tres llamadas

**Instagram no acepta subir archivos.** Descarga las imágenes desde una URL
pública, así que primero hay que alojarlas (ver más abajo).

### 1. Un contenedor por imagen (entre 2 y 10)

```http
POST https://graph.facebook.com/v21.0/{IG_USER_ID}/media
  ?image_url=https://…/slide-1.jpg
  &is_carousel_item=true
  &access_token={IG_ACCESS_TOKEN}
```

Cada llamada devuelve un `id`. Guardarlos en orden — **ese es el orden en que
se verán en el carrusel**.

### 2. El contenedor del carrusel

```http
POST https://graph.facebook.com/v21.0/{IG_USER_ID}/media
  ?media_type=CAROUSEL
  &children={id1},{id2},{id3}
  &caption={texto url-encodeado}
  &access_token={IG_ACCESS_TOKEN}
```

### 3. Publicar

```http
POST https://graph.facebook.com/v21.0/{IG_USER_ID}/media_publish
  ?creation_id={id_del_carrusel}
  &access_token={IG_ACCESS_TOKEN}
```

### Verificar antes de publicar

Los contenedores tardan en procesarse. Conviene consultar:

```http
GET https://graph.facebook.com/v21.0/{container_id}?fields=status_code
```

Hasta que dé `FINISHED`. Si se publica antes, falla. Con reintento cada 5
segundos y un tope de 60 alcanza de sobra.

## Requisitos de las imágenes

| | |
|---|---|
| Formato | **JPEG** (el PNG se rechaza) |
| Peso | máximo 8 MB |
| Proporción | entre 4:5 y 1.91:1 — para carrusel usar **1080×1350** |
| Todas iguales | el carrusel usa la proporción de la primera |

## Límites

- **25 publicaciones por día** por cuenta. El plan usa 1-4, así que sobra.
- Consultable en `GET /{IG_USER_ID}/content_publishing_limit`.

## Dónde viven las imágenes

Instagram necesita una URL pública. Recomendado: **bucket de Cloudflare R2**
(10 GB gratis, ya se usa Cloudflare en el proyecto).

Flujo: subir → publicar → borrar a los 7 días. Instagram solo necesita la URL
durante la publicación; después la imagen queda en sus servidores.

> **No commitear las imágenes al repo.** Aunque después se borren, quedan en el
> historial de git para siempre. Es el mismo motivo por el que la base de
> precios vive como artifact y no en el repo.

## Errores que hay que manejar

- **Token vencido** → avisar por Telegram. Un bot que dejó de publicar y nadie
  se entera es peor que no tenerlo.
- **Contenedor que nunca llega a `FINISHED`** → descartar el post y avisar. No
  reintentar en bucle: cada intento consume cuota diaria.
- **Límite diario alcanzado** → posponer, no perder.

---

# 3 · Los secrets, todos juntos

Van en **Settings → Secrets and variables → Actions** del repo, y en
`wrangler secret put` para el Worker de cobro.

| Secret | Dónde se usa | Estado |
|---|---|---|
| `MP_ACCESS_TOKEN` | Worker de cobro | ⬜ falta |
| `MP_WEBHOOK_SECRET` | Worker de cobro | ⬜ falta |
| `IG_ACCESS_TOKEN` | Bot de Instagram | ⬜ falta |
| `IG_USER_ID` | Bot de Instagram | ⬜ falta |
| `R2_ACCOUNT_ID` · `R2_ACCESS_KEY` · `R2_SECRET_KEY` | Subir imágenes | ⬜ falta |
| `TELEGRAM_BOT_TOKEN` | Todo | ✅ cargado |
| `VIGIA_CHAT_ID` | Todo | ✅ cargado |
| `VIGIA_TOPICO_*` (4) | Alertas | ✅ cargado |
| `HECTOR_PROXY_TOKEN` | Proxy de tiendas | ✅ cargado |

**Ninguna credencial va en el repo.** El `.gitignore` bloquea `.env`,
`config.json` y `*.db`. Este repo es **público**.

---

# 4 · Lo que NO entra en este encargo

Para que no haya dudas de alcance:

- **El diseño de los carruseles.** Las plantillas las hace Joaquín. Lo que sí
  entra es dejar una función que reciba los datos de un hallazgo y devuelva las
  rutas de las imágenes ya generadas — con una plantilla provisional fea
  alcanza, se reemplaza después.
- **La generación con IA.** Se descartó el 11-ago: todo va con plantilla. Sale
  más barato, más rápido, y sobre todo **los precios salen exactos** — un
  modelo de imagen inventa dígitos, y la credibilidad del dato es justamente lo
  que se vende.
- **El logo y la mascota.** Los hace Joaquín.

---

# 5 · Reglas del contenido que sí hay que respetar

Aunque lo visual sea de otro, la lógica de **qué** se publica es parte de esto:

| Tipo | Caída | Retraso | Cuántos |
|---|---|---|---|
| Carrusel del día | los mejores | 20:00 fijo | 1 diario |
| Error de precio | 70-100% | 6-24 h | los que haya |
| Oferta destacada | 60-69% | 2-4 h | máx. 1 al día |

**El retraso es a propósito y no se toca:** el que paga el grupo de Telegram
tiene que enterarse primero. Si Instagram publica al mismo tiempo, no hay razón
para pagar.

## El verificador — la pieza más importante

**Antes de publicar, hay que volver a leer la ficha del producto y confirmar el
precio.** Si ya no coincide o no se puede leer, no se publica.

No es paranoia: el 11-ago Héctor alertó una manteca de cacao a $681 cuando
valía $34.030 — había leído el precio **por gramo**. En Telegram eso se corrige
con un mensaje. En Instagram queda público y con capturas.

Como no hay revisión humana, además hay que exigir:

- Historial suficiente (5+ lecturas). Nada medido contra la foto del día uno.
- Nombre legible y foto disponible.
- Lista negra de categorías: medicamentos, alcohol, armas, contenido adulto.
