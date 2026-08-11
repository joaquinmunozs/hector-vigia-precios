# Héctor · proxy de tiendas (Cloudflare Worker)

> ⚠️ **Esto es de Héctor, el vigía de precios. NO es de Rat.IA.**
>
> Las dos cosas viven en la misma cuenta de Cloudflare
> (`contacto@teamcondorcl.com`) y en este mismo repositorio, así que es fácil
> confundirlas. No comparten código, ni secretos, ni datos:
>
> | Worker | Qué es | Dónde vive |
> |---|---|---|
> | **`hector-proxy-tiendas`** | **Este.** Baja páginas de tiendas bloqueadas | `proxy-tiendas/` |
> | `ratia-cobro` | Cobros de Rat.IA por Flow.cl | `cobro/` |
> | `ratia-landing` | Landing de Rat.IA | `cobro/landing/` |
>
> Si vas a desplegar, hazlo **desde esta carpeta** (`cd proxy-tiendas &&
> npx wrangler deploy`). Desde la raíz, Wrangler puede tomar el
> `wrangler.toml` de Rat.IA y subir el Worker equivocado — ya pasó una vez.

## Qué problema resuelve

`paris.cl` y `easy.cl` responden **403** al runner de GitHub Actions, que sale
por IPs de Azure. Medido el 11-ago-2026:

| Desde | paris.cl | easy.cl |
|---|---|---|
| Runner de GitHub (`20.169.74.50`, Azure) | **403** | **403** |
| Conexión de casa (Chile) | 200 | 200 |
| Edge de Cloudflare | **200** | **200** |

No es `robots.txt` ni un timeout: es la IP. Por eso el descubrimiento del
10-ago dio **0 fichas** para las dos, mientras las otras 24 tiendas
funcionaban. El mismo repo ya documentaba el fenómeno para `ripley.cl` desde
Modal (ver `SITEMAPS_CONOCIDOS` en `descubrir.py`).

Este Worker es el puente: Héctor le pide la página a Cloudflare, y Cloudflare
se la pide a la tienda.

## Cómo se usa

No hay que llamarlo a mano. `descubrir.bajar()` —que es el único punto por
donde salen TODAS las peticiones de Héctor: descubrimiento, barrida y
vigilante— revisa si el dominio está en `POR_PROXY` y lo enruta solo.

Se activa con dos variables de entorno:

```
HECTOR_PROXY_URL=https://hector-proxy-tiendas.contacto-e95.workers.dev
HECTOR_PROXY_TOKEN=<el secreto>
```

**Sin ellas no hace nada** y todo funciona como antes. Es a propósito: en el
PC de Joaquín esas tiendas responden bien directo, así que no tiene sentido
gastar peticiones del Worker. En GitHub Actions las dos variables están en
`.github/workflows/hector.yml` (la URL) y en los secrets del repo (el token,
como `HECTOR_PROXY_TOKEN`).

## No es un proxy abierto

Un Worker que baja cualquier URL para cualquiera termina usado por terceros, y
la cuenta la paga Joaquín. Dos cerrojos:

1. **Token compartido** en la cabecera `x-hector-token`. Sin él, 401.
2. **Lista blanca de dominios** (`PERMITIDOS` en `index.js`). Cualquier otro
   dominio da 403, aunque el token sea correcto.

Probado al desplegar: sin token → 401 · `example.com` con token → 403 ·
`paris.cl` con token → 200 con el XML real.

## Límites y cuándo empieza a costar

Plan gratis de Cloudflare Workers: **100.000 peticiones al día**, 10 ms de CPU
por petición y 50 subpeticiones. Los 10 ms no son problema —esperar la
respuesta de la tienda no cuenta como CPU, solo el JavaScript que la reenvía—
así que el techo real es el de 100.000 al día.

Eso alcanza para aproximadamente **una pasada completa de Paris por día**. Si
Paris resulta tener más fichas que eso, o se quiere meter en el ciclo de 4 h
como el resto de las tiendas, el plan pagado son **$5/mes** y quita el tope
diario.

## Si algún día deja de funcionar

Las IPs de salida de Cloudflare son compartidas, así que Paris podría
bloquearlas también. Se vería como un **403 en `x-tienda-status`** — el Worker
conserva el código de la tienda a propósito, para que un bloqueo no se
disfrace de página sin precio. Ahí las opciones son proxy residencial (de
pago) o correr esas tiendas desde el PC.

## Cambiar el token

```bash
cd proxy-tiendas
npx wrangler secret put PROXY_TOKEN                   # Cloudflare
gh secret set HECTOR_PROXY_TOKEN --repo joaquinmunozs/hector-vigia-precios
```

Los dos tienen que quedar con el MISMO valor, o Héctor recibe 401 en cada
petición a esas tiendas.

---

## Levantar una segunda instancia (otra IP)

Varias instancias de Héctor pueden correr a la vez desde IPs distintas y eso
**suma capacidad**, pero solo si cada una se hace cargo de tiendas distintas.
Dos instancias con la misma lista no duplican volumen: duplican trabajo, y
avisan dos veces el mismo hallazgo. Pasó el 6-ago-2026 con un deploy viejo de
Modal corriendo en paralelo — el throughput cayó de 40/seg a 0,2/seg.

Repartidas no hay duplicado posible: un hallazgo pertenece a una tienda y esa
tienda tiene un solo dueño. Por eso todas pueden compartir el **mismo bot de
Telegram y el mismo grupo**.

1. El socio hace un fork del repo, o lo clona en su propia cuenta.
2. Copia los secrets: `TELEGRAM_BOT_TOKEN`, `VIGIA_CHAT_ID` y los cuatro
   `VIGIA_TOPICO_*`. Son los mismos: el objetivo es que avise al mismo grupo.
3. Define la **variable** (no secreto) `HECTOR_TIENDAS` en Settings →
   Variables, con su subconjunto separado por comas.
4. Listo. Cada instancia mantiene su propia base como artifact.

Reparto sugerido, por tamaño de catálogo:

| Instancia | Tiendas |
|---|---|
| Joaquín | `falabella.com,hites.com,spdigital.cl,antartica.cl` |
| Segunda | `tricot.cl,abc.cl,bata.cl,tottus.cl,salcobrand.cl,construmart.cl` |

Las tiendas que no se nombren en ninguna instancia **quedan sin vigilar**, y
nadie se entera: no hay error, solo dejan de llegar sus avisos. Si se reparte,
conviene que la suma cubra las 44.
