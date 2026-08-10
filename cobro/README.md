# Rat.IA — cobro (Cloudflare Worker + Flow.cl)

Implementa los pasos 2-6 del plan en
`../docs/superpowers/specs/2026-08-08-ratia-cobro-onboarding-design.md`
(Worker + webhook, estado en KV, invitación de Telegram, cron de vencidos,
landing). El paso 1 — **crear la cuenta Flow a nombre de Cóndor.ai** — es
manual y le toca a Joaquín; todo lo de acá está escrito para no depender
de que esa cuenta ya exista, salvo para el despliegue final.

## Decisiones ya tomadas (8-ago-2026)

- **Escalera de precio:** los primeros **100** suscriptores quedan de por
  vida en **$2.990**; del 101 en adelante, **$4.990**. Es un tope fijo de
  personas, no una fecha — lo decide `contador:altas` en KV
  (`src/kv.js`).
- **Sin panel de tramos ni cupones** (fuera de alcance, ver el diseño):
  solo existen los dos planes de precio de arriba.

## ⚠️ Lo único no verificado: la API de suscripciones de Flow

El propio diseño ya lo marca como riesgo conocido: la documentación
comercial de Flow promete "sin programación" pero el FAQ describe un
flujo por API (crear cliente → registrar tarjeta → verificar → activar).
Escribí `src/flow.js` con la mejor info disponible, pero **nombres de
endpoint y de parámetros están marcados `VERIFICAR` en el código** y hay
que confirmarlos contra `https://www.flow.cl/docs/api.html` (sección
Suscripciones) recién cuando la cuenta esté aprobada y haya dashboard real
para probar. Todo lo demás (firma de webhook, KV, Telegram, cron) no
depende de Flow y ya se puede dar por bueno.

## Checklist de activación (en orden, después de que la cuenta Flow esté aprobada)

1. **Instalar dependencias y loguear Wrangler**
   ```
   cd cobro
   npm install
   npx wrangler login
   ```

2. **Crear el namespace de KV**
   ```
   npx wrangler kv namespace create ratia_kv
   ```
   Pegar el `id` que devuelve en `wrangler.toml`, en `RATIA_KV`.

3. **En el dashboard de Flow:** crear los dos planes de suscripción
   mensual — uno a $2.990 (fundador) y otro a $4.990 (regular) — y copiar
   sus `planId` en `wrangler.toml` (`FLOW_PLAN_ID_FUNDADOR` /
   `FLOW_PLAN_ID_REGULAR`).

3.5. **Aplicar la migración de Supabase** (en el repo `condor-ai`, no en
   este): pegar y correr
   `condor-ai/supabase/migrations/ingresos_ratia.sql` en el SQL Editor de
   Supabase. Es lo que le permite a **Nicolás** (el bot de reportes de
   Cóndor.ai) sumar los ingresos de Rat.IA al IVA del F29 — ver la sección
   más abajo. Después, en `wrangler.toml`, pegar `SUPABASE_URL` y
   `SUPABASE_ANON_KEY` (Project Settings → API en Supabase — es la misma
   Supabase de condor-ai, no una nueva).

4. **Cargar los secretos** (nunca van al repo — es público):
   ```
   npx wrangler secret put FLOW_API_KEY
   npx wrangler secret put FLOW_SECRET_KEY
   npx wrangler secret put TELEGRAM_BOT_TOKEN      # el mismo token de Héctor
   npx wrangler secret put ADMIN_TELEGRAM_CHAT_ID  # tu chat_id, para los avisos
   ```
   `VIGIA_CHAT_ID` y los `FLOW_PLAN_ID_*` van en `[vars]` de
   `wrangler.toml` (no son secretos).

5. **Desplegar el Worker**
   ```
   npx wrangler deploy
   ```
   Wrangler imprime la URL (`https://ratia-cobro.<tu-cuenta>.workers.dev`).
   Guardarla — se usa en los tres pasos siguientes.

6. **Configurar en Flow, dentro del panel de cada plan:**
   - `urlConfirmation` / callback del plan → `<url-del-worker>/webhook/flow`
   - URL de retorno del registro de tarjeta → `<url-del-worker>/registro-callback`

7. **Apuntar el webhook de Telegram al Worker** (reemplaza cualquier
   webhook anterior de Héctor para el bot — Héctor sigue mandando alertas
   igual, esto es aparte):
   ```
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=<url-del-worker>/telegram-webhook"
   ```

8. **Publicar la landing** (`landing/index.html`) en Cloudflare Pages. Si
   queda en un dominio distinto al del Worker, cambiar el `action` del
   `<form>` a la URL completa del Worker (`<url-del-worker>/iniciar`).

9. **Prueba de punta a punta con un pago real** (paso 7 del plan
   original): pagar de verdad, confirmar que llega el link de Telegram en
   menos de un minuto, y que entra al grupo con los 4 Temas.

## Qué queda pendiente de verificar en el código (buscar `VERIFICAR`)

- `src/flow.js`: campo con la URL de registro de tarjeta en la respuesta
  de `/customer/create`; valor exacto de éxito en `getRegisterStatus`;
  nombre del parámetro de período mensual en `/subscription/create`.
- `src/index.js` (`manejarWebhookFlow`): cómo llega `customerId` en el
  payload real del webhook de cargo/renovación del plan, el código de
  "pagado" real de `getStatus`, y el nombre real del campo `amount` (se
  usa para registrar el monto exacto de cada renovación en Supabase — si
  no llega, `src/supabase.js` cae al precio fijo del plan, que en la
  práctica es lo mismo salvo que Flow cobre un monto distinto por algún
  motivo).

Ninguno de estos bloquea escribir o probar el resto — solo hace falta
tener el dashboard de Flow abierto al lado la primera vez que se prueba un
pago real, y ajustar esos puntos si el payload real difiere.

## Automatización del IVA para el F29 (8-ago-2026)

Cada alta y cada renovación se registra en `ingresos_ratia` (Supabase de
Cóndor.ai) apenas se confirma el pago — ver `src/supabase.js`. **Nicolás**
(`condor-ai/services/nicolas/nicolas.mjs`) ya lee esa tabla en su reporte
mensual junto con los `pagos` de los clientes de la agencia, calcula el
neto y el IVA débito de los dos juntos (el F29 es por RUT, y el Flow de
Rat.IA está a nombre de Cóndor.ai) y lo manda por Telegram + una hoja
nueva en Sheets, lista para copiar al F29.

**Lo que NO queda automatizado, a propósito:** el crédito fiscal (IVA de
las compras/gastos del mes) no está en ninguna base de datos accesible acá
— eso sigue siendo manual, con el contador o directo en el SII. Y nadie
declara ni paga el F29 automáticamente: Nicolás solo deja los números
calculados, la presentación en el SII sigue siendo una acción humana.
