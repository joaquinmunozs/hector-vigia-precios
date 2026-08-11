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
//   POST /webhook/mercadopago
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
      <p>Último paso: toca el botón para que Héctor te mande el acceso al grupo.</p>
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
    await tg.mandarTexto(env, chatId, "Para entrar, paga primero desde la landing de Rat.IA.");
    return new Response("ok");
  }

  const customerId = await kv.leerVinculo(env, token);
  if (!customerId) {
    await tg.mandarTexto(env, chatId, "Ese link ya venció. Vuelve a la página de pago y toca el botón de Telegram de nuevo.");
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


/** Reintenta las invitaciones que quedaron a medias.
 *
 * Si Telegram estaba caído en el momento exacto en que alguien tocó /start, su
 * registro quedó en `pendiente_invitacion` y hasta ahora NADIE lo retomaba: la
 * persona pagó, no entró al grupo, y el sistema no volvía a intentarlo nunca.
 * Es el caso que el encargo marca como inaceptable — un pago no se puede
 * perder por un error de Telegram.
 *
 * Se apoya en que ya existe `telegramId`: solo llega a este estado quien tocó
 * /start, así que se le puede escribir de vuelta.
 */
async function reintentarInvitaciones(env) {
  const suscriptores = await kv.listarSuscriptores(env);
  let recuperados = 0;
  for (const s of suscriptores) {
    if (s.estado !== "pendiente_invitacion" || !s.telegramId) continue;
    try {
      const link = await tg.crearInvitacion(env);
      await tg.mandarTexto(env, s.telegramId,
        `¡Listo! Ya se resolvió el problema técnico. Entra al grupo de Rat.IA acá 👇\n${link}\n\n` +
        `Este link es de un solo uso y se agota al usarlo.`);
      // Recién acá pasa a activo. El vencimiento se respeta si ya lo traía:
      // el mes se le cuenta desde que pagó, no desde que pudimos escribirle.
      await kv.guardarSuscriptor(env, s.customerId, {
        estado: "activo",
        vencimiento: s.vencimiento || en30Dias(),
      });
      recuperados++;
    } catch (e) {
      console.error(`Sigue fallando la invitación de ${s.customerId}:`, e);
    }
  }
  if (recuperados) {
    await tg.avisarAdmin(env,
      `Se recuperaron ${recuperados} acceso(s) que habían quedado pendientes.`);
  }
}


/* MercadoPago — alternativa a Flow para bajar el CAC.
 *
 * La campaña de Meta lleva a WhatsApp y ahí se manda el link de suscripcion
 * de MP. Se saltan la landing y el formulario de tarjeta, que son los dos
 * pasos donde mas gente se cae.
 *
 * MP NO firma el webhook como Flow: manda un aviso de que "paso algo con el
 * pago X" y hay que preguntarle a la API que paso. Confiar en el cuerpo del
 * aviso permitiria regalarse una suscripcion con un POST, asi que el estado
 * se relee SIEMPRE desde MP con el token privado.
 */
/* VALIDACIÓN DE LA FIRMA DE MERCADOPAGO
 *
 * El endpoint es público: cualquiera puede pegarle. Releer el recurso desde la
 * API con el token privado ya impide fabricar un pago de la nada, pero la firma
 * es la que impide además que alguien REPITA un aviso ajeno que sí es válido.
 * Las dos defensas se quedan.
 *
 * MP manda `x-signature: ts=1704908010,v1=<hmac>`. El manifiesto que se firma
 * es esta cadena EXACTA, con los `;` finales incluidos:
 *
 *     id:{data.id};request-id:{x-request-id};ts:{ts};
 *
 * Dos trampas que cuestan una tarde:
 *   · el `data.id` va en minúsculas cuando es alfanumérico;
 *   · si no viene la cabecera `x-request-id`, ese campo se omite del
 *     manifiesto PERO el `;` se mantiene.
 */
function _hex(buffer) {
  return [...new Uint8Array(buffer)]
    .map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** Comparación de tiempo constante. Un `===` sobre el hmac filtra, por lo que
 * tarda en fallar, cuántos caracteres iniciales acertó quien lo intenta. */
function _igualEnTiempoConstante(a, b) {
  if (a.length !== b.length) return false;
  let dif = 0;
  for (let i = 0; i < a.length; i++) dif |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return dif === 0;
}

async function firmaMPValida(req, env, aviso) {
  // Sin secreto configurado NO se procesa. Antes esto era un `return true`
  // "para poder probar", que es la forma de dejar el cobro abierto en
  // producción sin que nadie lo note.
  if (!env.MP_WEBHOOK_SECRET) {
    console.error("MP_WEBHOOK_SECRET sin configurar: se rechaza el aviso");
    return false;
  }

  const firma = req.headers.get("x-signature") || "";
  const partes = Object.fromEntries(
    firma.split(",").map((p) => {
      const i = p.indexOf("=");
      return i < 0 ? ["", ""] : [p.slice(0, i).trim(), p.slice(i + 1).trim()];
    }));
  const ts = partes.ts;
  const recibido = partes.v1;
  if (!ts || !recibido) return false;

  const id = String(aviso.data?.id ?? "").toLowerCase();
  const reqId = req.headers.get("x-request-id");

  let manifiesto = `id:${id};`;
  if (reqId) manifiesto += `request-id:${reqId};`;
  manifiesto += `ts:${ts};`;

  const clave = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(env.MP_WEBHOOK_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const calculado = _hex(await crypto.subtle.sign(
    "HMAC", clave, new TextEncoder().encode(manifiesto)));

  return _igualEnTiempoConstante(calculado, recibido.toLowerCase());
}


/** Relee el recurso desde MP con el token privado. El aviso solo dice "pasó
 * algo con X"; creer en su cuerpo permitiría regalarse una suscripción con un
 * POST. */
async function leerDeMP(env, ruta, id) {
  const r = await fetch(`https://api.mercadopago.com/${ruta}/${id}`, {
    headers: { Authorization: "Bearer " + env.MP_ACCESS_TOKEN },
  });
  return r.ok ? await r.json() : null;
}

function en30Dias() {
  return new Date(Date.now() + DIAS_PERIODO * 86400 * 1000).toISOString();
}

/* SE RESPONDE 200 ANTES DE TRABAJAR, NO DESPUÉS
 *
 * MercadoPago corta la conexión a los ~22 s y, si no recibió respuesta, manda
 * el aviso de nuevo. Con el trabajo hecho antes de responder, una tarde lenta
 * de la API de MP o de Telegram convierte un pago en dos avisos reintentados.
 *
 * `ctx.waitUntil` mantiene vivo el Worker hasta que la tarea termina aunque la
 * respuesta ya se haya ido. La contrapartida es que un fallo ya no se puede
 * contar en la respuesta: por eso el `catch` avisa al admin en vez de quedar
 * en un log que nadie mira — un pago perdido en silencio es lo peor que puede
 * pasar acá.
 */
async function manejarWebhookMP(req, env, ctx) {
  const aviso = await req.json().catch(() => ({}));

  if (!(await firmaMPValida(req, env, aviso))) {
    return new Response("firma inválida", { status: 401 });
  }

  ctx.waitUntil(procesarAvisoMP(env, aviso).catch(async (e) => {
    console.error("aviso de MercadoPago falló:", e);
    try {
      await tg.avisarAdmin(env,
        `⚠️ Un aviso de pago de MercadoPago falló al procesarse ` +
        `(${aviso.type || aviso.topic} ${aviso.data?.id}). ` +
        `Puede haber un pago sin acceso entregado — revisar en el panel de MP.`);
    } catch (e2) {
      console.error("tampoco se pudo avisar al admin:", e2);
    }
  }));

  return new Response(JSON.stringify({ ok: true }), {
    headers: { "content-type": "application/json" },
  });
}

/** El trabajo de verdad. Corre después de haber respondido, así que lo que
 * devuelve es solo para el log: nadie lo lee del otro lado. */
async function procesarAvisoMP(env, aviso) {
  const tipo = aviso.type || aviso.topic;
  const id = aviso.data?.id || (aviso.resource || "").split("/").pop();
  if (!id) return "sin id";

  // SOLO los dos tópicos de suscripción. El tópico `payment` se ignora a
  // propósito: MP avisa CADA cobro de una suscripción por `payment` Y por
  // `subscription_authorized_payment`, así que atender los dos contaba dos
  // veces la misma plata en Supabase y mandaba dos accesos. Rat.IA se vende
  // solo por suscripción, así que un pago suelto no existe.
  //
  //   subscription_preapproval        → la suscripción se autorizó = ALTA
  //   subscription_authorized_payment → un cobro mensual salió = RENOVACIÓN
  const esAlta = ["subscription_preapproval", "preapproval"].includes(tipo);
  const esRenovacion = tipo === "subscription_authorized_payment";
  if (!esAlta && !esRenovacion) {
    return "ignorado";
  }

  // Idempotencia ANTES de cualquier efecto: MP reintenta el mismo aviso
  // varias veces. La clave lleva el tópico porque un `preapproval` y un
  // `authorized_payment` pueden compartir número.
  const clave = `mp:${tipo}:${id}`;
  if (await env.RATIA_KV.get(clave)) {
    return "ya procesado";
  }

  if (esRenovacion) {
    // VERIFICAR contra un cobro real: se asume que /authorized_payments/{id}
    // devuelve `preapproval_id` y `status`. Es el único punto de este flujo
    // que no se puede comprobar sin una suscripción viva.
    const pago = await leerDeMP(env, "authorized_payments", id);
    if (!pago) return "no se pudo verificar";
    if (pago.status !== "approved") return "no pagado";

    const customerId = "mp:" + pago.preapproval_id;
    const sub = await kv.leerSuscriptor(env, customerId);
    if (!sub) {
      // Cobro de una suscripción que nunca se dio de alta acá: no se
      // inventa un suscriptor, se avisa para revisarlo a mano.
      await tg.avisarAdmin(env,
        `Cobro de MercadoPago sin alta registrada (preapproval ${pago.preapproval_id}). Revisar a mano.`);
      return "sin alta";
    }

    await env.RATIA_KV.put(clave, "1", { expirationTtl: 60 * 60 * 24 * 90 });
    await kv.guardarSuscriptor(env, customerId,
      { estado: "activo", vencimiento: en30Dias() });
    await sb.registrarIngreso(env, {
      tipo: "renovacion",
      plan: sub.plan || "mercadopago",
      montoBruto: Math.round(pago.transaction_amount || 0),
    });
    return "renovacion ok";
  }

  // ── Alta ──────────────────────────────────────────────────────────────
  const pre = await leerDeMP(env, "preapproval", id);
  if (!pre) return "no se pudo verificar";
  if (pre.status !== "authorized") {
    return "no autorizada";
  }

  await env.RATIA_KV.put(clave, "1", { expirationTtl: 60 * 60 * 24 * 90 });

  const monto = Math.round(pre.auto_recurring?.transaction_amount || 0);
  const correo = pre.payer_email || "sin correo";
  const customerId = "mp:" + id;

  // El plan sale del mismo contador que Flow: la escalera de precio es del
  // negocio, no del medio de pago. Sin esto, una alta por MP no consumía
  // cupo de fundador y los $2.990 de por vida se repartían de más.
  const plan = await kv.decidirPlan(env);
  const numeroAlta = await kv.registrarAlta(env);

  // ESTO es lo que hacía falta para que el cron de vencidos lo vea. Sin
  // `estado` ni `vencimiento`, `correrCronVencidos` lo saltaba y quien
  // pagaba por MP se quedaba en el grupo gratis para siempre.
  await kv.guardarSuscriptor(env, customerId, {
    estado: "activo",
    plan: plan.nombre,
    medioPago: "mercadopago",
    mpPreapprovalId: id,
    email: correo,
    vencimiento: en30Dias(),
    altaEn: new Date().toISOString(),
  });

  await sb.registrarIngreso(env, {
    tipo: "alta",
    plan: plan.nombre,
    montoBruto: monto,
  });

  if (numeroAlta === kv.LIMITE_FUNDADOR) {
    await tg.avisarAdmin(env,
      `🎉 Rat.IA llegó a ${kv.LIMITE_FUNDADOR} altas. Los que entren desde ahora quedan en plan "regular" ($4.990).`);
  }

  // El pago llega por MP, pero el comprador está en WhatsApp: no hay página
  // de retorno donde mostrarle el botón. Se genera su token de acceso y se
  // avisa al admin para reenviárselo por el mismo chat donde venía.
  const token = await kv.crearVinculo(env, customerId);
  await tg.avisarAdmin(env,
    `Pago por MercadoPago recibido (${monto} CLP, ${correo}) — alta #${numeroAlta}, plan ${plan.nombre}.\n` +
    `Mándale este acceso por WhatsApp:\n` +
    `https://t.me/HectorRat_bot?start=${token}`);

  return "alta ok";
}

export default {
  // `ctx` hace falta para `waitUntil` en el webhook de MercadoPago: ahí se
  // responde 200 al toque y el trabajo sigue después (ver manejarWebhookMP).
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/iniciar") return await manejarIniciar(req, env);
      if (url.pathname === "/registro-callback") return await manejarRegistroCallback(req, env);
      if (url.pathname === "/telegram-webhook" && req.method === "POST") return await manejarTelegramWebhook(req, env);
      if (url.pathname === "/webhook/flow" && req.method === "POST") return await manejarWebhookFlow(req, env);
      if (url.pathname === "/webhook/mercadopago" && req.method === "POST") return await manejarWebhookMP(req, env, ctx);
      return html("Rat.IA — cobro. Ver /iniciar.", 404);
    } catch (e) {
      console.error(e);
      return html("Error interno.", 500);
    }
  },

  async scheduled(_event, env) {
    // Los vencidos primero: sacar a quien ya no paga es lo que protege el
    // ingreso. Y va en try aparte para que un fallo ahí no impida recuperar
    // los accesos pendientes, que es plata ya cobrada sin entregar.
    try {
      await correrCronVencidos(env);
    } catch (e) {
      console.error("cron de vencidos falló:", e);
    }
    try {
      await reintentarInvitaciones(env);
    } catch (e) {
      console.error("reintento de invitaciones falló:", e);
    }
  },
};
