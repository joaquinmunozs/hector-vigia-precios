// Crea el plan de suscripción de Rat.IA en MercadoPago. Se corre UNA vez.
//
//     node crear-plan-mp.mjs                 # lista los planes que ya existen
//     node crear-plan-mp.mjs --crear         # crea el plan
//
// El token se lee de la variable de entorno MP_ACCESS_TOKEN, nunca de un
// argumento: lo que va en la línea de comandos queda en el historial del shell
// y es visible en la lista de procesos mientras corre.
//
//     $env:MP_ACCESS_TOKEN = "TEST-..."      # PowerShell
//     node crear-plan-mp.mjs --crear
//
// EMPEZAR CON CREDENCIALES DE PRUEBA (TEST-...). El plan que se cree con un
// token de prueba solo existe en el entorno de prueba, así que al pasar a
// producción hay que volver a correr esto con el token real. Eso es lo que se
// quiere: probar el flujo completo sin mover plata.
//
// LO QUE DEVUELVE, Y QUÉ HACER CON ELLO
// ---------------------------------------------------------------------------
//   `id`          -> el id del plan. NO es secreto: va al repo.
//   `init_point`  -> LA URL que se pega en WhatsApp. Es fija y se usa siempre.
//
// Por qué un plan (`preapproval_plan`) y no un link de pago suelto: con pago
// único el suscriptor tiene que decidir pagar otra vez cada mes, y en un ticket
// de $2.990 eso mata la retención. Es la misma razón por la que se descartó
// Khipu. Con el plan, la tarjeta queda guardada y MercadoPago cobra solo.

const TOKEN = (process.env.MP_ACCESS_TOKEN || "").trim();
const API = "https://api.mercadopago.com";

// El back_url es a donde MercadoPago manda a la persona después de pagar.
// Apunta al bot: el comprador viene de WhatsApp, así que lo útil es dejarlo en
// la conversación con Héctor, no en una página nuestra.
const BACK_URL = process.env.MP_BACK_URL || "https://t.me/HectorRat_bot";

const PLAN = {
  reason: "Héctor — alertas de precio",
  auto_recurring: {
    frequency: 1,
    frequency_type: "months",
    transaction_amount: 2990,
    currency_id: "CLP",
  },
  back_url: BACK_URL,
  payer_email: "",
};

if (!TOKEN) {
  console.error("Falta MP_ACCESS_TOKEN en el entorno.");
  console.error('  PowerShell:  $env:MP_ACCESS_TOKEN = "TEST-..."');
  process.exit(1);
}

const esPrueba = TOKEN.startsWith("TEST-");
console.log(`Token: ${esPrueba ? "PRUEBA (TEST-)" : "PRODUCCIÓN"} · ${TOKEN.length} caracteres\n`);

async function llamar(ruta, opciones = {}) {
  const r = await fetch(API + ruta, {
    ...opciones,
    headers: {
      Authorization: "Bearer " + TOKEN,
      "Content-Type": "application/json",
      ...(opciones.headers || {}),
    },
  });
  const texto = await r.text();
  let datos;
  try {
    datos = JSON.parse(texto);
  } catch {
    datos = { crudo: texto };
  }
  if (!r.ok) {
    // El cuerpo del error de MP es lo único que dice QUÉ campo rechazó.
    // Perderlo obliga a adivinar el payload.
    throw new Error(`HTTP ${r.status} en ${ruta}: ${JSON.stringify(datos)}`);
  }
  return datos;
}

async function listar() {
  const r = await llamar("/preapproval_plan/search?limit=20");
  const planes = r.results || [];
  if (!planes.length) {
    console.log("No hay planes creados todavía. Corre con --crear.");
    return;
  }
  console.log(`${planes.length} plan(es):\n`);
  for (const p of planes) {
    const m = p.auto_recurring || {};
    console.log(`  id          ${p.id}`);
    console.log(`  reason      ${p.reason}`);
    console.log(`  estado      ${p.status}`);
    console.log(`  cobro       ${m.transaction_amount} ${m.currency_id} cada ${m.frequency} ${m.frequency_type}`);
    console.log(`  init_point  ${p.init_point}`);
    console.log("");
  }
}

async function crear() {
  console.log("Creando el plan:");
  console.log(JSON.stringify(PLAN, null, 2));
  console.log("");

  const p = await llamar("/preapproval_plan", {
    method: "POST",
    body: JSON.stringify(PLAN),
  });

  console.log("Plan creado.\n");
  console.log(`  id del plan  ${p.id}`);
  console.log(`  estado       ${p.status}`);
  console.log("");
  console.log("  LINK PARA PEGAR EN WHATSAPP:");
  console.log(`  ${p.init_point}`);
  console.log("");
  console.log("Siguiente paso: en el panel de MercadoPago → Webhooks, apuntar");
  console.log("la URL de notificación al Worker y activar SOLO estos eventos:");
  console.log("  · subscription_preapproval");
  console.log("  · subscription_authorized_payment");
  console.log("");
  console.log("NO activar `payment`: MercadoPago avisa cada cobro de una");
  console.log("suscripción por dos vías, y atender las dos cuenta la misma");
  console.log("plata dos veces y manda dos accesos a la misma persona.");
}

try {
  if (process.argv.includes("--crear")) {
    await crear();
  } else {
    await listar();
    console.log("(esto solo listó. Para crear el plan: --crear)");
  }
} catch (e) {
  console.error("\nFalló:", e.message);
  process.exit(1);
}
