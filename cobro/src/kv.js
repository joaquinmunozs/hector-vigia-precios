// Estado del cobro en Cloudflare KV.
//
// ESQUEMA DE CLAVES
// ------------------------------------------------------------------------
//   sub:{customerId}       → { estado, vencimiento, telegramId, plan,
//                               flowSubscriptionId, email, altaEn }
//   pago:{tokenOFlowOrder} → "1"   (dedupe: Flow puede reintentar el
//                                   webhook, sin esto un reintento
//                                   generaría dos links de invitación)
//   vinculo:{token}        → customerId   (TTL 24h: puente entre "volvió
//                                   del registro de tarjeta en Flow" y
//                                   "abrió Telegram y tocó Start")
//   contador:altas         → entero en texto. Solo sube con ALTAS nuevas
//                                   (no con renovaciones), define la
//                                   escalera de precio.
//
// POR QUÉ LA CLAVE PRINCIPAL ES customerId Y NO EL USERNAME DE TELEGRAM
// ------------------------------------------------------------------------
// La API de Telegram solo deja mandarle un mensaje PRIVADO a alguien si
// ese alguien ya le escribió al bot antes — no existe forma de mandarle un
// DM a un "@username" en frío. Por eso el vínculo con Telegram se hace
// recién cuando la persona toca el deep-link `t.me/HectorRat_bot?start=…`
// (ver `telegram-webhook` en index.js): ahí Telegram nos entrega su
// `chat_id` numérico de verdad, que es lo único que sirve tanto para
// mandarle el link de invitación como para el ban/unban del cron.

const KEY_CONTADOR = "contador:altas";
// Se exporta porque el aviso de "llegamos al cupo" se manda desde index.js,
// en los dos caminos de pago (Flow y MercadoPago). Tenerlo duplicado como un
// 100 suelto allá era la forma de que un día quedaran en desacuerdo.
export const LIMITE_FUNDADOR = 100; // decisión de Joaquín, 8-ago-2026

/** Plan que le toca a la PRÓXIMA persona que se da de alta, según cuántas
 * altas nuevas hubo hasta ahora. Los primeros 100 quedan en $2.990 para
 * siempre (su registro ya quedó marcado `plan: "fundador"` en el momento
 * de la alta); del 101 en adelante es $4.990. No es un tramo de precio que
 * se mueva solo con el tiempo — es un tope fijo de personas, así que no
 * hace falta más que crear el segundo plan en Flow una sola vez.
 * Ver "Qué NO se construye" en el diseño: esto NO es un sistema de tramos
 * arbitrario, es un único corte ya decidido. */
export async function decidirPlan(env) {
  const actual = parseInt((await env.RATIA_KV.get(KEY_CONTADOR)) || "0", 10);
  return actual < LIMITE_FUNDADOR
    ? { nombre: "fundador", planId: env.FLOW_PLAN_ID_FUNDADOR }
    : { nombre: "regular", planId: env.FLOW_PLAN_ID_REGULAR };
}

/** Sube el contador SOLO cuando una alta se confirma de verdad (tarjeta
 * registrada y suscripción creada), nunca al iniciar el checkout — si no,
 * alguien que abandona el pago le roba el cupo de $2.990 a otra persona
 * que sí pagó. */
export async function registrarAlta(env) {
  const actual = parseInt((await env.RATIA_KV.get(KEY_CONTADOR)) || "0", 10);
  const nuevo = actual + 1;
  await env.RATIA_KV.put(KEY_CONTADOR, String(nuevo));
  return nuevo; // devuelve el número de alta (ej: la #100) para el aviso al admin
}

export async function crearVinculo(env, customerId) {
  const token = crypto.randomUUID().replace(/-/g, "").slice(0, 24);
  await env.RATIA_KV.put(`vinculo:${token}`, customerId, { expirationTtl: 86400 });
  return token;
}

export async function leerVinculo(env, token) {
  return env.RATIA_KV.get(`vinculo:${token}`);
}

export async function yaProcesado(env, idPago) {
  return (await env.RATIA_KV.get(`pago:${idPago}`)) !== null;
}

export async function marcarProcesado(env, idPago) {
  await env.RATIA_KV.put(`pago:${idPago}`, "1", { expirationTtl: 60 * 60 * 24 * 90 });
}

export async function guardarSuscriptor(env, customerId, datos) {
  const previo = (await leerSuscriptor(env, customerId)) || {};
  await env.RATIA_KV.put(`sub:${customerId}`, JSON.stringify({ ...previo, ...datos }));
}

export async function leerSuscriptor(env, customerId) {
  const crudo = await env.RATIA_KV.get(`sub:${customerId}`);
  return crudo ? JSON.parse(crudo) : null;
}

/** Todos los suscriptores — para el cron de vencidos. `list()` de KV trae
 * hasta 1000 claves por página; con la meta de 60-100 suscriptores esto no
 * necesita paginar todavía. Si el negocio crece mucho más, ahí sí hay que
 * recorrer `cursor`. */
export async function listarSuscriptores(env) {
  const { keys } = await env.RATIA_KV.list({ prefix: "sub:" });
  const resultado = [];
  for (const k of keys) {
    const v = await env.RATIA_KV.get(k.name);
    if (v) resultado.push({ customerId: k.name.slice(4), ...JSON.parse(v) });
  }
  return resultado;
}
