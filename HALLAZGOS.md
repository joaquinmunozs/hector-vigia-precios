# Hallazgos y trabajo de Max — 11-ago-2026

> **Para Joaquín.** Esto es lo que encontramos al entrar al repo y lo que quedó
> hecho. Está ordenado por lo que te conviene saber primero: los bugs que ya
> estaban y no se veían, después lo construido, y al final lo que sigue
> bloqueado y por quién.
>
> Todo lo que dice "verificado" se probó de verdad, y abajo está cómo
> reproducirlo. Lo que no se pudo comprobar está marcado como tal.

---

## En una frase

`cobro/` no compilaba (así que **nada** de cobro se podía desplegar), los
suscriptores de MercadoPago no vencían nunca, y faltaba guardar la foto del
producto sin la cual los carruseles son imposibles. Eso está arreglado, más el
verificador de §5 del encargo.

---

## 1 · Bugs que ya estaban

### El Worker de cobro no compilaba

El commit `9bade85` (webhook de MercadoPago) dejó **saltos de línea reales
dentro de literales de string** en `cobro/src/index.js`. Un `SyntaxError` tumba
el módulo completo, así que no era solo el webhook de MP: se caían con él Flow,
el vínculo de Telegram y el cron de vencidos. Es la sexta vez que pasa lo que
advierte `CONTEXTO.md` §11.

**La trampa que hace que no se vea.** `node --check` da **falso OK** sobre un
`.js`, porque lo parsea como CommonJS. Hay que comprobarlo como ESM, que es lo
que usan los Workers:

```bash
cp cobro/src/index.js /tmp/x.mjs && node --check /tmp/x.mjs
```

En `master` de antes eso falla en la línea 262. Vale la pena dejarlo como
costumbre: es el chequeo que habría cazado esto los seis casos.

### Quien pagaba por MercadoPago no vencía nunca

`manejarWebhookMP` creaba el vínculo de acceso pero **jamás escribía un registro
con `estado` ni `vencimiento`**. `correrCronVencidos` filtra por esos dos
campos, así que los saltaba: la persona pagaba un mes y se quedaba en el grupo
**gratis de por vida**. Es exactamente la garantía de la que presume el diseño
("nunca se queda alguien adentro gratis para siempre").

Y además las altas por MP **no consumían cupo de fundador**, así que los $2.990
de por vida se repartían de más.

### Las invitaciones que fallaban no se reintentaban nunca

Si Telegram estaba caído justo cuando alguien tocaba `/start`, su registro
quedaba en `pendiente_invitacion` — y nadie lo retomaba. Pagó y no entró. El
encargo lo marca como inaceptable y tenía razón: no existía el reintento.
Ahora lo hace el cron diario, y el mes se le cuenta desde que pagó, no desde
que pudimos escribirle.

### hushpuppies.cl y vans.cl: 3.190 fichas midiendo cero

El `CONTEXTO` dice "el extractor no encuentra el precio en esas páginas". En
realidad **lo encontraba y lo descartaba**. Las dos son Shopify y publican un
`ProductGroup` cuyo `offers` viene en `null`, con el precio real dentro de
`hasVariant` — una entrada por talla o color, cada una un `Product` completo con
su `offers.price`. `_de_jsonld` solo aceptaba `product` y `book`.

El arreglo extiende el descenso que ya hacía por `@graph`. **Verificado en vivo:
hushpuppies $29.990, vans $44.990.**

Urgía más de lo que parecía: con el `TOPE_FALLOS = 2` que había entonces, cada
barrida purgaba esas 3.190 URLs y el descubrimiento del lunes las volvía a
agregar — un ciclo que gastaba peticiones y no dejaba nada.

Llegaste a la misma conclusión por otro camino en `329fd2d` (subir `TOPE_FALLOS`
a 6 tras ver caer el catálogo de 439.375 a 360.863 fichas en un día). Vale
notar que las dos cosas se refuerzan: subir el tope evita perder catálogo bueno,
y arreglar el extractor quita de raíz el motivo por el que esas fichas fallaban.
Con las dos, hushpuppies y vans dejan de fallar en vez de solo tardar más en
morir.

### Entidades HTML crudas en los nombres

El reemplazo a mano solo cubría el espacio, así que Antártica llegaba al grupo
como `12 Reglas Para Vivir. Un Ant&#xED;d` — la `í`, la `º` y la `/` se
mandaban sin desescapar. Se ve en `historial/2026-08-11.json`. Ahora
`html.unescape` doble, y el corte a 90 caracteres va **después** de desescapar
(antes podía partir una entidad al medio).

Queda un resto: `extractor.py` guarda el nombre ya escapado y truncado a 120
caracteres, así que un título largo puede llegar con media entidad. Esto lo
arregla en el mensaje; la raíz está en el extractor.

---

## 2 · Dos correcciones a `INTEGRACIONES.md`

Nada de esto es reproche — son dos cosas que si quedan como están hacen perder
tiempo a quien las tome.

### §2 marca Instagram como listo, y no lo está

El encargo da por cumplido "Cuenta de Instagram tipo Empresa ✅" y "Vinculada a
una Página de Facebook ✅". **Max confirmó que la cuenta sigue siendo
personal.** Mientras no se convierta, la API de publicación no existe y todo el
punto 2 está bloqueado. La conversión es de un minuto (app de Instagram →
Configuración → Tipo de cuenta y herramientas), pero conviene arreglar el
documento para que nadie construya sobre esa premisa.

### Faltaba una pieza para poder armar los carruseles

Se decidió que los carruseles lleven la **foto real del producto**, y §5 exige
"foto disponible" para publicar. Pero **el scraper no guardaba la URL de la
imagen**: `extractor.py` devolvía solo `nombre`, `precio`, `hay_stock` y
`fuente`, y `precios` no tenía columna para eso. El requisito era literalmente
imposible de cumplir.

Ya está hecho (ver más abajo), pero es un prerrequisito que el encargo no
menciona y sin el cual el bloque 2 no arranca.

---

## 3 · Lo que quedó construido

| Qué | Dónde |
|---|---|
| Firma `x-signature` de MP, con comparación de tiempo constante | `cobro/src/index.js` |
| Respuesta 200 antes del trabajo (`ctx.waitUntil`) | `cobro/src/index.js` |
| Reintento de invitaciones pendientes en el cron | `cobro/src/index.js` |
| Ciclo de suscripción de MP (alta, vencimiento, renovación) | `cobro/src/index.js` |
| Crear el plan por API y obtener el `init_point` | `cobro/crear-plan-mp.mjs` |
| `ProductGroup` de Shopify + foto del producto | `extractor.py` |
| Columna `precios.imagen`, por migración | `baseprecios.py` |
| Verificador previo a publicar (§5) | `verificador.py` |
| Cargar secretos sin exponerlos | `cargar-secretos.ps1` |

### Sobre la firma de MercadoPago

Las dos trampas del manifiesto quedaron cubiertas y probadas: el `data.id` va en
minúsculas, y si no viene `x-request-id` ese campo se omite **pero el `;` se
mantiene**. La comparación del hmac es de tiempo constante, porque un `===`
filtra —por lo que tarda en fallar— cuántos caracteres acertó quien lo intenta.

**Sin `MP_WEBHOOK_SECRET` configurado, el aviso se rechaza.** La alternativa
("dejarlo pasar para poder probar") es la forma de dejar el cobro abierto en
producción sin que nadie lo note.

### Sobre el verificador

Dos decisiones que conviene no cambiar sin leer el comentario:

**Si el precio bajó MÁS entre el aviso y la publicación, se publica el nuevo.**
No es una tolerancia simétrica: lo que se rechaza es que haya **subido**, porque
ahí el post estaría mintiendo. Si bajó todavía más, el número correcto es el de
ahora, y publicar el de la alerta sería publicar un dato falso — justo lo que
este módulo existe para evitar.

**El lector se reusa de `vigia.leer`, no se duplica.** Así hereda los
adaptadores por tienda y el TLS imitado. Escribir otro lector ahí sería
garantizar que un día queden en desacuerdo y que el verificador rechace fichas
que la barrida sí sabe leer.

La lista negra (medicamentos, alcohol, armas, adulto, tabaco) no es moralismo:
son las categorías por las que Meta restringe una cuenta, y perder la cuenta es
perder el canal. Al probarla salieron dos falsos positivos, corregidos:
`pistola de aire comprimido` caía en medicamentos (por `comprimido`) y
`pistola de silicona` caía en armas — Construmart tiene el catálogo lleno de
esas herramientas.

---

## 4 · Cosas que encontramos y NO tocamos

Quedan acá porque son decisiones tuyas, no bugs a arreglar de paso.

### Se está perdiendo más de la mitad de las corridas

De las últimas 23 corridas de `hector.yml`: **12 canceladas**, 8 exitosas, 1
fallida. Las canceladas son todas `schedule` y vivieron entre 2 y 9 horas antes
de morir — encoladas detrás de una corrida en curso y desplazadas al llegar la
siguiente. Con `concurrency` + `cancel-in-progress: false`, GitHub mantiene
**una sola** corrida pendiente por grupo.

La causa de fondo es que una corrida dura ~3,5 h contra un cron de 4 h: no hay
margen para la cola. Esto le pega directo al vigilante, que es el pilar del
producto.

### `spdigital.cl` no es un problema de extracción

El `CONTEXTO` §3bis lo pone como el caso #1 a mirar, con la hipótesis de que no
se puede leer. **Le pegamos a 3 fichas en vivo y leyó 3/3** ($87.980, $17.350,
$109.660, todas por `og-meta`). Su 1,2% medido es de **agendamiento**, no de
lectura — la barrida no le llega. Buscar el problema en el extractor es buscar
donde no está.

### La rotación de la barrida dejó de actuar

> Nota: esto se escribió mirando `master` antes de `329fd2d`. Ese commit tocó el
> vigilante, no `_objetivos` de `vigia.py`, así que lo de abajo sigue en pie —
> pero conviene releerlo con tu cambio de hilos ya aplicado, porque el ritmo
> real de la barrida cambia y con él la cuenta de las 7,7 h.

`TOPE_BARRIDA` está en `450_000`, por encima del catálogo (360.863). Eso hace
que `len(objetivos) > tope` sea falso y **`_con_rotacion` no se llame nunca**,
así que el marcador de rotación no avanza. Como la barrida se corta a las 3 h y
el catálogo completo necesita ~7,7 h al ritmo medido, cada día se corta sin
terminar.

Matiz honesto: los hilos se reparten por tienda, así que todas avanzan en
paralelo y no hay tiendas que queden sin tocar por orden alfabético. Pero se
perdió la garantía explícita que el comentario del propio código describe
("lo que cae bajo el corte no se revisa jamás, y encima en silencio"). Vale
revisarlo con la base de producción a mano.

### Falta el secret `ANTHROPIC_API_KEY`

`analisis-semanal.yml` lo necesita y no está entre los secrets del repo. Ese
workflow va a fallar el lunes.

### `spdigital.cl` publica una URL de imagen inválida

Su HTML trae, de verdad, `<meta property="og:image" content="https:undefined">`
— un bug de su JavaScript. No es nuestro, pero obligó a validar el host de las
imágenes: "empieza con http" no alcanza, porque eso se habría guardado como si
fuera una foto y el carrusel habría fallado recién al publicar.

### hushpuppies devuelve `.webp`, e Instagram solo acepta JPEG

Es trabajo del renderizador: hay que convertir antes de publicar. Lo dejo dicho
porque el encargo pide JPEG y no menciona que varias tiendas sirven webp.

---

## 5 · Lo que falta, y de quién depende

### Bloqueado en Joaquín

- Corregir los dos puntos de `INTEGRACIONES.md` (§2 de este documento).
- Subir a `sudo-osito` a **admin** del repo: con `write` no se pueden escribir
  secrets de Actions, así que ni el publicador ni el análisis semanal se pueden
  configurar.

### Bloqueado en Max

- Convertir Instagram a cuenta de Empresa y vincularla a la Página.
- `npx wrangler login` para poder desplegar el Worker.
- Credenciales: `MP_ACCESS_TOKEN` (empezando con `TEST-`), `MP_WEBHOOK_SECRET`,
  `ADMIN_TELEGRAM_CHAT_ID`.

### Por construir

`redes.py` **está desalineado con el encargo y hay que rehacerlo.** Se escribió
antes de leer `INTEGRACIONES.md`:

| Lo que hay | Lo que pide el encargo |
|---|---|
| Creativo con Higgsfield | Plantillas por código, sin IA (§4) |
| Imagen única | Carrusel de 2-10, JPEG, 1080×1350 |
| Publica al instante | Retraso obligatorio: 6-24 h errores, 2-4 h ofertas (§5) |
| 3 tandas al día, hasta 2 piezas | 1 carrusel diario a las 20:00 |
| Publica en IG y Facebook | Solo Instagram |
| Sin verificación previa | Ya existe `verificador.py`, falta enchufarlo |
| `META_*` | `IG_ACCESS_TOKEN` / `IG_USER_ID` + los 3 de R2 |

**El cron de `redes.yml` viene desactivado a propósito** (queda solo el disparo
a mano) justamente porque el módulo está desalineado, y porque agregarle 3
corridas diarias a un repo que ya está perdiendo la mitad por cola no ayuda.
Cuando el módulo esté rehecho, se descomenta.

Lo que queda por escribir: el renderizador con Pillow (con conversión desde
webp), la lógica de retraso y cupos, y la subida a R2.

---

## 6 · Cómo probar lo que quedó

```bash
# El Worker compila (antes fallaba)
cd cobro && npm install && npx wrangler deploy --dry-run

# El extractor lee las Shopify y trae la foto
python -c "import descubrir, extractor; \
  print(extractor.extraer(descubrir.bajar('https://www.vans.cl/products/zapatillas-authentic-black-black-vn-3uvn000ee3bka0042')))"

# El verificador, contra una ficha real
python verificador.py --url <url-de-una-ficha> --tienda vans.cl

# El verificador, contra las alertas de las últimas 24 h (necesita precios.db)
python verificador.py --recientes 24

# Crear el plan de MercadoPago (necesita el token en el entorno)
cd cobro && node crear-plan-mp.mjs            # lista lo que ya existe
cd cobro && node crear-plan-mp.mjs --crear    # lo crea
```

Los secretos nunca se pegan en un chat ni en la línea de comandos: van a
`secretos.local` (ignorado por git) y se cargan con `.\cargar-secretos.ps1`,
que reparte cada valor a Actions o al Worker sin imprimirlo.
