# Rat.IA — cobro y onboarding

**Fecha:** 2026-08-08
**Estado:** diseño aprobado, listo para implementar

## Qué se construye

Lo que falta para que Rat.IA cobre: que alguien vea un anuncio, pague en pesos
chilenos y quede dentro del grupo de Telegram en menos de un minuto, sin que
nadie mueva un dedo del otro lado.

El scraper ya funciona. El grupo con sus cuatro Temas ya existe. **Lo único
que no existe es la forma de cobrar** — hoy no hay una sola línea de código de
pagos en el proyecto.

## Lo que NO cambia

El grupo de Telegram con sus cuatro Temas queda **exactamente como está**:

| Tópico | `message_thread_id` | Caída |
|---|---|---|
| 🚨 Errores de precio | 2 | 70%-99% |
| 🏷️ Ofertas reales | 4 | 50%-69% |
| 📱 Electrónicos | 36 | 35%-69% |
| 🏠 Hogar | 38 | 35%-69% |

Mantener los Temas fue requisito duro. Todo lo demás se diseñó alrededor de eso.

## Decisiones tomadas, con su razón

### Flow.cl, no Telegram Stars

Stars era tentador porque no necesita infraestructura, pero se descartó al
mirar los números reales:

| | Stars | Flow |
|---|---|---|
| Pérdida efectiva | **~38%** (30% Apple/Google + 5% Fragment) | ~3,4% |
| Neto sobre $2.990 | ~$1.850 | **~$2.887** |
| Cuándo cobras | 21 días | 3 días hábiles |
| En qué moneda | TON (cripto) | **Pesos, a tu banco** |
| Mínimo para retirar | 1.000 Stars (~7 suscriptores) | sin mínimo |

Y hay un bloqueante técnico además del económico: **las suscripciones nativas
de Stars solo existen en canales, y los canales no tienen Temas.**

### Tampoco Khipu, ni InviteMember, ni Hotmart

- **Khipu** tiene la comisión más baja (~0,8%) pero funciona por transferencia:
  no queda tarjeta guardada, así que **cada mes el suscriptor tiene que
  decidir pagar de nuevo**. En un ticket de $2.990 eso mata la retención.
- **InviteMember** cobra **US$32,50/mes fijo** y solo acepta Stripe/PayPal —
  **no cubre RedCompra**, que es el 44% del mercado chileno.
- **Hotmart** se lleva ~10% y necesita Zapier para hablar con Telegram.

Flow cubre Webpay, débito RedCompra y transferencia: cómo paga el chileno.

### Webhook, no polling

Se evaluó consultar la API de Flow por cron desde el GitHub Actions que ya
corre Héctor. Funcionaría y sería gratis, pero introduce **hasta 15 minutos de
espera entre pagar y entrar**. Para una compra por impulso eso es fatal: la
persona paga, no pasa nada, y cree que la estafaron.

Con webhook el acceso es inmediato, y Cloudflare Workers lo hace gratis.

## Arquitectura

```
Anuncio  →  landing  →  página de pago de Flow (alojada por Flow)
                              ↓ paga en CLP
                        webhook de Flow
                              ↓
                    Cloudflare Worker
                     ├── valida la firma
                     ├── guarda el vencimiento en KV
                     └── createChatInviteLink (un solo uso, 1 miembro)
                              ↓
                  Héctor le manda el link por Telegram
                              ↓
                    entra al GRUPO CON LOS 4 TEMAS

Cron diario (Worker) → vencidos → banChatMember + unbanChatMember
```

### Por qué `banChatMember` seguido de `unbanChatMember`

`banChatMember` a secas deja al usuario **vetado para siempre**: si vuelve a
pagar el mes siguiente, no puede entrar. Llamar `unbanChatMember` justo
después lo saca del grupo pero le devuelve el derecho a reingresar. Sin eso, un
suscriptor que se atrasa un día queda perdido para siempre.

## Componentes

| Componente | Dónde vive | Qué hace |
|---|---|---|
| **Landing** | Cloudflare Pages | Explica el producto y manda a pagar. Una sola pantalla |
| **Worker de pagos** | Cloudflare Workers | Recibe el webhook de Flow, valida y da acceso |
| **Estado** | Cloudflare KV | `telegram_id → {vencimiento, flow_customer_id, estado}` |
| **Cron de vencidos** | Cron Trigger de Workers | Una vez al día saca a los que no renovaron |
| **Héctor** | ya existe | Publica alertas. Se le agrega mandar el link de invitación |

Todo dentro del plan gratis: **100.000 peticiones/día en Workers, sin tarjeta
de crédito**.

## Ciclo de vida del suscriptor

| Evento | Qué pasa |
|---|---|
| **Alta** | Paga en Flow → webhook → se guarda vencimiento a +30 días → recibe link de un solo uso → entra |
| **Renovación** | Flow cobra solo (Cargo Automático) → webhook → el vencimiento se extiende +30 días |
| **Pago fallido** | No llega webhook de éxito. El vencimiento no se mueve. Vence solo |
| **Baja voluntaria** | Cancela en Flow. Mismo camino: no se renueva y vence |
| **Vencido** | El cron lo saca del grupo (ban + unban) y marca `estado: vencido` |
| **Vuelve** | Paga de nuevo → link nuevo → entra sin fricción, porque nunca quedó vetado |

**El diseño es a prueba de fallos:** el acceso depende de que *llegue* un pago,
no de que llegue un aviso de cancelación. Si Flow deja de mandar webhooks, en
el peor caso la gente vence sola — nunca se queda alguien adentro gratis para
siempre.

## Manejo de errores

- **Webhook duplicado.** Flow puede reintentar. Se guarda el id de la
  transacción y si ya se procesó, se responde 200 sin hacer nada. Sin esto un
  reintento generaría dos links de invitación.
- **Firma inválida.** Se rechaza con 401. El endpoint es público, así que
  cualquiera puede pegarle: sin validar firma, cualquiera se regala acceso.
- **Telegram caído al crear el link.** Se guarda el pago igual y se marca
  `pendiente_invitacion`. El cron reintenta. **Nunca se pierde un pago por un
  fallo de Telegram** — es plata que ya entró.
- **Paga alguien que no dio su Telegram.** La landing pide el usuario de
  Telegram antes de mandar a pagar. Si aun así llega un pago sin asociar,
  queda en una cola para revisar a mano.

## Qué NO se construye

Deliberadamente fuera de alcance:

- Panel de administración — Flow ya tiene el suyo
- Múltiples planes o tramos de precio — se lanza con **uno solo, $2.990**
- Cupones y descuentos
- Facturación electrónica — Flow la emite
- App móvil — el producto vive en Telegram
- Recuperación de carritos abandonados

Con 60 suscriptores como meta inicial, cualquiera de estas cosas es trabajo que
no se paga.

## Plan de implementación

| # | Paso | Tiempo | Bloqueado por |
|---|---|---|---|
| 1 | **Crear cuenta Flow a nombre de Cóndor.ai** | 15 min + aprobación | **Joaquín** |
| 2 | Worker + webhook + validación de firma | ~1 día | — |
| 3 | Guardado de estado en KV + alta/renovación | ~0,5 día | — |
| 4 | Generar y enviar link de invitación | ~0,5 día | — |
| 5 | Cron de vencidos (ban + unban) | ~0,5 día | — |
| 6 | Landing | ~0,5 día | — |
| 7 | Prueba de punta a punta con un pago real | ~0,5 día | paso 1 |

**Los pasos 2 a 6 no dependen de Flow**, así que se construyen mientras la
cuenta espera aprobación. El calendario real lo marca el paso 1.

## Riesgos conocidos

**Telegram en Chile.** El supuesto más frágil de todo el modelo: Chile es país
de WhatsApp. Mandar tráfico frío de Instagram a Telegram significa que parte de
la gente tiene que instalar la app antes de poder pagar. **Hay que medirlo con
presupuesto chico antes de escalar los ads** — es la variable que puede hundir
el CAC.

**El flujo de suscripciones de Flow está sin verificar.** La documentación
comercial promete "sin programación", pero el FAQ oficial dice que no hay
plugins para suscripciones y describe un flujo por API (crear cliente →
registrar tarjeta → verificar → activar). **Hay que confirmarlo contra la
documentación real apenas la cuenta esté aprobada**, antes de dar por buenos
los pasos 2 a 5.

**Precio de lanzamiento.** Se lanza a $2.990. El punto de equilibrio para
pagar la Fase 2 del scraper (proxy residencial para Ripley, ~$165.000/mes) son
**58 suscriptores a ese precio**. Al llegar a ~60, los nuevos pasan a $4.990 y
los primeros quedan como socios fundadores.
