// Cliente de Flow.cl — suscripciones (cargo automático).
//
// ⚠️ LO QUE NO ESTÁ VERIFICADO CONTRA LA DOCUMENTACIÓN REAL
// ------------------------------------------------------------------------
// El diseño (docs/superpowers/specs/2026-08-08-ratia-cobro-onboarding-design.md)
// ya marca esto como riesgo conocido: la documentación pública de Flow
// promete "suscripciones sin programación" pero el flujo real por API es
// crear cliente → registrar tarjeta → verificar → activar. Los nombres
// exactos de endpoint y de cada parámetro de acá están escritos con la mejor
// info disponible SIN cuenta real para probarlos. Antes de aceptar el primer
// pago de verdad, confirmar cada función de este archivo contra
// https://www.flow.cl/docs/api.html (sección Suscripciones) ya con el
// dashboard de la cuenta aprobada. Todo lo demás del proyecto (webhook,
// firma, KV, Telegram) no depende de esto y ya se puede dar por bueno.

const BASE_URL = "https://www.flow.cl/api";

/** Firma HMAC-SHA256 de Flow: concatenar "clave+valor" de los parámetros
 * ordenados alfabéticamente por clave, y firmar con la secretKey en hex.
 * Esta parte SÍ está documentada igual en todos los endpoints de Flow. */
async function firmar(params, secretKey) {
  const claves = Object.keys(params).sort();
  const cadena = claves.map((k) => `${k}${params[k]}`).join("");
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secretKey), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]
  );
  const firma = await crypto.subtle.sign("HMAC", key, enc.encode(cadena));
  return [...new Uint8Array(firma)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function llamar(env, path, params, metodo = "POST") {
  const firmados = { ...params, apiKey: env.FLOW_API_KEY };
  firmados.s = await firmar(firmados, env.FLOW_SECRET_KEY);

  let resp;
  if (metodo === "GET") {
    const qs = new URLSearchParams(firmados).toString();
    resp = await fetch(`${BASE_URL}${path}?${qs}`);
  } else {
    resp = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(firmados).toString(),
    });
  }
  const cuerpo = await resp.json();
  if (!resp.ok) {
    throw new Error(`Flow ${path} → ${resp.status}: ${JSON.stringify(cuerpo)}`);
  }
  return cuerpo;
}

/** Crea el cliente en Flow a partir del email de la landing. El vínculo
 * con Telegram se hace DESPUÉS, por /start (ver kv.js e index.js) — a esta
 * altura todavía no sabemos quién es en Telegram. */
export async function crearCliente(env, email) {
  return llamar(env, "/customer/create", { name: email, email });
  // Respuesta esperada (VERIFICAR): { customerId, url } — `url` es la
  // página hospedada por Flow donde la persona ingresa la tarjeta.
}

export async function estadoRegistro(env, customerId) {
  return llamar(env, "/customer/getRegisterStatus", { customerId }, "GET");
  // Respuesta esperada (VERIFICAR): { status, creditCardType, last4 }
}

/** Crea la suscripción recurrente una vez que la tarjeta quedó registrada. */
export async function crearSuscripcion(env, customerId, planId) {
  return llamar(env, "/subscription/create", {
    planId,
    customerId,
    subscriptionPeriod: 1, // VERIFICAR: código del período mensual en Flow
  });
  // Respuesta esperada (VERIFICAR): { subscriptionId, status }
}

export async function cancelarSuscripcion(env, subscriptionId) {
  return llamar(env, "/subscription/cancel", { subscriptionId });
}

/** Confirma el estado real de un pago/cargo contra la API de Flow.
 * NUNCA confiar solo en el body del webhook — cualquiera puede pegarle al
 * endpoint público. Esto es lo que de verdad valida que se cobró. */
export async function getEstadoPago(env, token) {
  return llamar(env, "/payment/getStatus", { token }, "GET");
}

/** Valida la firma que Flow adjunta a las notificaciones del plan
 * (urlCallback / urlConfirmation). Mismo algoritmo que `firmar`, aplicado
 * a los parámetros que Flow mandó menos la firma misma. */
export async function firmaValida(env, params) {
  const { s, ...resto } = params;
  if (!s) return false;
  const esperada = await firmar(resto, env.FLOW_SECRET_KEY);
  return esperada === s;
}
