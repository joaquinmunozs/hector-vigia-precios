// Llamadas a la API de Telegram para el alta, la baja y el aviso al admin.
// Mismo bot que Héctor (@HectorRat_bot) — un solo token, dos usos.

const API = (env) => `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}`;

async function llamar(env, metodo, body) {
  const resp = await fetch(`${API(env)}/${metodo}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const datos = await resp.json();
  if (!datos.ok) throw new Error(`Telegram ${metodo}: ${JSON.stringify(datos)}`);
  return datos.result;
}

export async function mandarTexto(env, chatId, texto) {
  await llamar(env, "sendMessage", { chat_id: chatId, text: texto });
}

/** Link de invitación de un solo uso, para que nadie más lo reuse ni quede
 * dando vueltas en un chat viejo. `member_limit: 1` es lo que lo hace
 * de un solo uso — sin eso, cualquiera con el link entra gratis. */
export async function crearInvitacion(env) {
  const r = await llamar(env, "createChatInviteLink", {
    chat_id: env.VIGIA_CHAT_ID,
    member_limit: 1,
    name: `alta-${Date.now()}`,
  });
  return r.invite_link;
}

/** ban + unban seguido: saca del grupo pero NO lo deja vetado para
 * siempre. Sin el unban, alguien que se atrasa un día y paga de nuevo
 * nunca más podría volver a entrar. Requiere el `chat_id` NUMÉRICO real
 * (no el username) — se obtiene al vincular la cuenta por /start. */
export async function sacarDelGrupo(env, telegramId) {
  await llamar(env, "banChatMember", { chat_id: env.VIGIA_CHAT_ID, user_id: telegramId });
  await llamar(env, "unbanChatMember", {
    chat_id: env.VIGIA_CHAT_ID, user_id: telegramId, only_if_banned: true,
  });
}

export async function avisarAdmin(env, texto) {
  if (!env.ADMIN_TELEGRAM_CHAT_ID) return;
  await mandarTexto(env, env.ADMIN_TELEGRAM_CHAT_ID, texto);
}
