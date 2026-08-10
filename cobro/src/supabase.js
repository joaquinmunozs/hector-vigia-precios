// Registra cada pago confirmado en la misma Supabase de Cóndor.ai, para que
// Nicolás (services/nicolas en el monorepo condor-ai) pueda sumarlo al IVA
// débito del mes — el F29 es por RUT, y el Flow de Rat.IA está a nombre de
// Cóndor.ai, así que su plata cuenta para el mismo IVA que los clientes de
// la agencia.
//
// Usa la ANON KEY, no la service role: este Worker es un endpoint público
// (lo pega el webhook de Flow), así que si la key se filtra algún día el
// daño queda acotado a "puede insertar filas en ingresos_ratia" — ver la
// política RLS en supabase/migrations/ingresos_ratia.sql del monorepo
// condor-ai. Nunca falla el alta/renovación por esto: es contabilidad, no
// el flujo de pago.

const PRECIOS = { fundador: 2990, regular: 4990 };

export async function registrarIngreso(env, { tipo, plan, montoBruto, flowSubscriptionId }) {
  if (!env.SUPABASE_URL || !env.SUPABASE_ANON_KEY) return; // no configurado aún, no bloquea nada

  const monto = montoBruto ?? PRECIOS[plan] ?? null;
  if (!monto) {
    console.error(`No se pudo determinar el monto para registrar en Supabase (plan=${plan})`);
    return;
  }

  try {
    const resp = await fetch(`${env.SUPABASE_URL}/rest/v1/ingresos_ratia`, {
      method: "POST",
      headers: {
        apikey: env.SUPABASE_ANON_KEY,
        Authorization: `Bearer ${env.SUPABASE_ANON_KEY}`,
        "Content-Type": "application/json",
        Prefer: "return=minimal",
      },
      body: JSON.stringify({
        monto_bruto: monto,
        tipo,
        plan,
        flow_subscription_id: flowSubscriptionId || null,
      }),
    });
    if (!resp.ok) console.error("Supabase ingresos_ratia:", resp.status, await resp.text());
  } catch (e) {
    console.error("No se pudo registrar el ingreso en Supabase:", e);
  }
}
