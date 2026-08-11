// Héctor · proxy de tiendas — Cloudflare Worker
//
// PROYECTO: Héctor (vigía de precios). NO es de Rat.IA.
// Ver README.md al lado: en esta misma cuenta de Cloudflare vive el Worker de
// cobros de Rat.IA, y son cosas distintas que no comparten nada.
//
// QUÉ RESUELVE
// ---------------------------------------------------------------------------
// paris.cl y easy.cl responden 403 al runner de GitHub Actions (IP de Azure,
// medido el 11-ago-2026: 20.169.74.50). Desde el edge de Cloudflare responden
// 200 con el XML de verdad. Este Worker es el puente: Héctor le pide la página
// a Cloudflare y Cloudflare se la pide a la tienda.
//
// NO ES UN PROXY ABIERTO, y eso importa: un Worker que baja cualquier URL para
// cualquiera termina usado por terceros y el que paga la cuenta es Joaquín.
// Dos cerrojos:
//   1. token compartido en la cabecera `x-hector-token` (secreto de Cloudflare)
//   2. lista blanca de dominios — solo las tiendas que de verdad están bloqueadas
const PERMITIDOS = new Set(["paris.cl", "www.paris.cl", "easy.cl", "www.easy.cl"]);

export default {
  async fetch(request, env) {
    if (request.headers.get("x-hector-token") !== env.PROXY_TOKEN) {
      return new Response("no", { status: 401 });
    }

    const destino = new URL(request.url).searchParams.get("u");
    if (!destino) return new Response("falta ?u=", { status: 400 });

    let objetivo;
    try {
      objetivo = new URL(destino);
    } catch {
      return new Response("url invalida", { status: 400 });
    }
    if (objetivo.protocol !== "https:" || !PERMITIDOS.has(objetivo.hostname)) {
      return new Response("dominio no permitido: " + objetivo.hostname, { status: 403 });
    }

    // Se imita un navegador chileno. Sin esto algunas tiendas devuelven una
    // versión distinta de la página, o directamente el desafío del WAF.
    const r = await fetch(objetivo.toString(), {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-CL,es;q=0.9",
      },
      redirect: "follow",
    });

    // Se devuelve el cuerpo tal cual y se conserva el código de estado: si la
    // tienda bloquea al propio Cloudflare algún día, Héctor tiene que ver ese
    // 403 y no un 200 vacío que parece una página sin precio.
    return new Response(r.body, {
      status: r.status,
      headers: {
        "content-type": r.headers.get("content-type") || "text/plain; charset=utf-8",
        "x-tienda-status": String(r.status),
      },
    });
  },
};
