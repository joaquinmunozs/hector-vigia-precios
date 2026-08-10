// Worker de cobro de Rat.IA — ver el diseño completo en
// docs/superpowers/specs/2026-08-08-ratia-cobro-onboarding-design.md
//
// POR QUÉ HAY UN PASO DE MÁS ANTES DE ENTRAR AL GRUPO
// ------------------------------------------------------------------------
// La API de Telegram no deja mandarle un DM en frío a un "@username": el
// bot solo puede escribirle a alguien que YA le escribió antes. Por eso el
// flujo real es: paga en Flow → vuelve a nuestra página → esa página le
// pide tocar un botón de Telegram (`t.me/HectorRat_bot?start=<token>`) →
// al tocar Start, Telegram nos manda su `chat_id` numérico de verdad, y
// ahí SÍ se le puede mandar el link de invitación. Ese `chat_id` es
// también lo único que sirve para el ban/unban del cron de vencidos.
//
// RUTAS
// ------------------------------------------------------------------------
//   GET  /iniciar?email=<email>
//        La landing manda al usuario acá después de pedirle el email.
//        Crea el cliente en Flow y redirige a la página (de Flow) donde
//        registra la tarjeta.
//
//   GET  /registro-callback?customerId=...
//        A donde Flow devuelve al usuario después de registrar la tarjeta
//        (urlReturn/urlConfirmation del customer). Si quedó registrada,
//        crea la suscripción, cuenta la alta y muestra el botón de
//        Telegram para terminar de vincular la cuenta.
//
//   POST /telegram-webhook
//        Updates del bot. Solo procesa `/start <token>`: liga ese
//        customerId con el chat_id real y manda la invitación.
//
//   POST /webhook/flow
//        Notificación de Flow por cada cargo del plan (alta o renovación).
//        SIEMPRE se confirma contra getStatus antes de creer nada — el
//        endpoint es público, cualquiera puede pegarle al body.
//
// CRON (ver wrangler.toml, una vez al día)
//   scheduled() recorre los suscriptores vencidos y los saca del grupo.

import * as flow from "./flow.js";
import * as tg from "./telegram.js";
import * as kv from "./kv.js";
import * as sb from "./supabase.js";

const DIAS_PERIODO = 30;

function html(cuerpo, status = 200) {
  return new Response(cuerpo, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

async function manejarIniciar(req, env) {
  const url = new URL(req.url);
  const email = (url.searchParams.get("email") || "").trim();
  if (!email) return html("Falta el email.", 400);

  const cliente = await flow.crearCliente(env, email);
  // VERIFICAR: el nombre real del campo con la URL de registro de tarjeta
  // en la respuesta de /customer/create (acá se asume `url`).
  await kv.guardarSuscriptor(env, cliente.customerId, {
    estado: "pendiente_registro",
    email,
  });

  return Response.redirect(cliente.url, 302);
}

async function manejarRegistroCallback(req, env) {
  const url = new URL(req.url);
  const customerId = url.searchParams.get("customerId");
  if (!customerId) return html("Falta customerId.", 400);

  const registro = await flow.estadoRegistro(env, customerId);
  // VERIFICAR: el valor exacto que indica éxito en `registro.status`.
  if (registro.status !== "registered" && registro.status !== 1) {
    await kv.guardarSuscriptor(env, customerId, { estado: "registro_fallido" });
    return html("No se pudo registrar la tarjeta. Intenta de nuevo desde la landing.");
  }

  const plan = await kv.decidirPlan(env);
  const suscripcion = await flow.crearSuscripcion(env, customerId, plan.planId);
  const numeroAlta = await kv.registrarAlta(env);

  const vencimiento = new Date(Date.now() + DIAS_PERIODO * 86400 * 1000).toISOString();
  await kv.guardarSuscriptor(env, customerId, {
    estado: "activo",
    plan: plan.nombre,
    flowSubscriptionId: suscripcion.subscriptionId,
    vencimiento,
    altaEn: new Date().toISOString(),
  });

  if (numeroAlta === 100) {
    await tg.avisarAdmin(env,
      "🎉 Rat.IA llegó a 100 altas. Los que entren desde ahora quedan en plan \"regular\" ($4.990) — los primeros 100 quedan de por vida en $2.990.");
  }

  // Para que Nicolás (condor-ai) sume esto al IVA débito del mes — ver
  // supabase/migrations/ingresos_ratia.sql en ese repo. Nunca bloquea el
  // alta: si Supabase falla, el pago y el acceso ya quedaron bien.
  await sb.registrarIngreso(env, {
    tipo: "alta",
    plan: plan.nombre,
    flowSubscriptionId: suscripcion.subscriptionId,
  });

  const token = await kv.crearVinculo(env, customerId);
  return html(`
    <!doctype html><html><head><meta charset="utf-8"><title>¡Pago recibido!</title></head>
    <body style="font-family:sans-serif;max-width:480px;margin:80px auto;text-align:center">
      <h1>¡Pago recibido!</h1>
      <p>Último paso: tocá el botón para que Héctor te mande el acceso al grupo.</p>
      <a href="https://t.me/HectorRat_bot?start=${token}"
         style="display:inline-block;padding:14px 28px;background:#26A5E4;color:#fff;
                border-radius:8px;text-decoration:none;font-weight:bold">
        Abrir Telegram y entrar
      </a>
    </body></html>`);
}

/** Update de Telegram. Solo nos interesa `/start <token>`: es el único
 * comando que puede mandar alguien que todavía no está en el grupo. */
async function manejarTelegramWebhook(req, env) {
  const update = await req.json();
  const msg = update.message;
  if (!msg || !msg.text || !msg.text.startsWith("/start")) {
    return new Response("ok"); // ignorar cualquier otro update
  }

  const token = msg.text.split(" ")[1];
  const chatId = msg.from.id;
  if (!token) {
    await tg.mandarTexto(env, chatId, "Para entrar, pagá primero desde la landing de Rat.IA.");
    return new Response("ok");
  }

  const customerId = await kv.leerVinculo(env, token);
  if (!customerId) {
    await tg.mandarTexto(env, chatId, "Ese link ya venció. Volvé a la página de pago y tocá el botón de Telegram de nuevo.");
    return new Response("ok");
  }

  await kv.guardarSuscriptor(env, customerId, { telegramId: chatId, telegramUsername: msg.from.username });

  try {
    const link = await tg.crearInvitacion(env);
    await tg.mandarTexto(env, chatId,
      `¡Listo! Entra al grupo de Rat.IA acá 👇\n${link}\n\nEste link es de un solo uso y se agota al usarlo.`);
  } catch (e) {
    await kv.guardarSuscriptor(env, customerId, { estado: "pendiente_invitacion" });
    console.error("No se pudo mandar la invitación:", e);
    await tg.mandarTexto(env, chatId, "Tu pago está confirmado pero hubo un problema técnico armando el link. Ya nos llega el aviso, te escribimos apenas se resuelva.");
  }

  return new Response("ok");
}

async function manejarWebhookFlow(req, env) {
  const form = await req.formData();
  const params = Object.fromEntries(form.entries());

  if (!(await flow.firmaValida(env, params))) {
    return new Response("firma inválida", { status: 401 });
  }

  const idPago = params.token || params.commerceOrder;
  if (await kv.yaProcesado(env, idPago)) {
    return new Response("ok", { status: 200 }); // reintento de Flow, ya se procesó
  }

  const estado = await flow.getEstadoPago(env, params.token);
  // VERIFICAR: el código de "pagado" real de getStatus (acá se asume 2), y
  // cómo llega el customerId/subscriptionId en este webhook de renovación
  // (el payload real de Flow para cargos de plan hay que confirmarlo).
  if (estado.status === 2) {
    await kv.marcarProcesado(env, idPago);
    const customerId = params.customerId;
    const sub = customerId ? await kv.leerSuscriptor(env, customerId) : null;
    if (sub) {
      // Alta inicial ya se maneja en /registro-callback. Acá solo se
      // estira el plazo en cada renovación mensual.
      const vencimiento = new Date(Date.now() + DIAS_PERIODO * 86400 * 1000).toISOString();
      await kv.guardarSuscriptor(env, customerId, { estado: "activo", vencimiento });

      await sb.registrarIngreso(env, {
        tipo: "renovacion",
        plan: sub.plan,
        montoBruto: estado.amount, // VERIFICAR: nombre real del campo en getStatus
        flowSubscriptionId: sub.flowSubscriptionId,
      });
    }
  }

  return new Response("ok", { status: 200 });
}

async function correrCronVencidos(env) {
  const suscriptores = await kv.listarSuscriptores(env);
  const ahora = Date.now();
  let sacados = 0;
  for (const s of suscriptores) {
    if (s.estado !== "activo" || !s.vencimiento || !s.telegramId) continue;
    if (new Date(s.vencimiento).getTime() < ahora) {
      try {
        await tg.sacarDelGrupo(env, s.telegramId);
        await kv.guardarSuscriptor(env, s.customerId, { estado: "vencido" });
        sacados++;
      } catch (e) {
        console.error(`No se pudo sacar a ${s.customerId}:`, e);
      }
    }
  }
  if (sacados) await tg.avisarAdmin(env, `Cron de vencidos: se sacó a ${sacados} suscriptor(es).`);
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/iniciar") return await manejarIniciar(req, env);
      if (url.pathname === "/registro-callback") return await manejarRegistroCallback(req, env);
      if (url.pathname === "/telegram-webhook" && req.method === "POST") return await manejarTelegramWebhook(req, env);
      if (url.pathname === "/webhook/flow" && req.method === "POST") return await manejarWebhookFlow(req, env);
      return html("Rat.IA — cobro. Ver /iniciar.", 404);
    } catch (e) {
      console.error(e);
      return html("Error interno.", 500);
    }
  },

  async scheduled(_event, env) {
    await correrCronVencidos(env);
  },
};
