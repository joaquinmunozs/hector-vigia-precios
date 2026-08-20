# Cuántas peticiones por segundo aguanta cada aerolínea

**Medido el 20-ago-2026** con `python medir_vuelos.py`, desde la conexión de
casa (Chile). Escalera de concurrencia 2 → 5 → 10 → 20 → 40 hilos, 20
peticiones por escalón, frenando al primer signo de bloqueo o lentitud.

El **recomendado** es el 60% del último escalón cómodo: el ritmo al que
operar sin rozar nunca el bloqueo.

## Resultado

| Aerolínea | Tope (hilos) | req/s sano | **Recomendado** |
|---|---:|---:|---:|
| lufthansa | 40 | 143,8 | **86,3** |
| klm | 40 | 109,0 | **65,4** |
| aireuropa | 40 | 100,0 | **60,0** |
| airfrance | 40 | 62,0 | **37,2** |
| britishairways | 40 | 59,2 | **35,5** |
| united | 40 | 47,7 | **28,6** |
| arajet | 40 | 46,9 | **28,2** |
| **skyairline** | 40 | 38,7 | **23,2** |
| level | 40 | 32,7 | **19,6** |
| qantas | 40 | 30,3 | **18,2** |
| delta | 40 | 25,6 | **15,4** |
| american | 5 | 25,6 | **15,3** |
| wingo | 40 | 22,8 | **13,7** |
| turkish | 40 | 21,2 | **12,7** |
| gol | 40 | 20,1 | **12,0** |
| **jetsmart** | 40 | 17,3 | **10,4** |
| aerolineasarg | 40 | 16,7 | **10,0** |
| iberia | 40 | 14,0 | **8,4** |
| iberojet | 40 | 7,6 | **4,6** |
| avianca | 40 | 6,6 | **3,9** |
| **latam** | 40 | 2,3 | **1,4** |
| paranair | 40 | 0,9 | **0,6** |
| copa | — | — | **bloqueada, ver abajo** |

## Lo que hay que leer de esta tabla

**22 de 23 aguantaron los 40 hilos sin degradar.** O sea el cuello no son
las aerolíneas: para 23 páginas, cualquiera de estos ritmos sobra. Una
vuelta completa a las 22 medidas, al ritmo de la más lenta (paranair, 0,6
req/s), toma unos 37 segundos. Vigilarlas cada 10 minutos es holgado.

**LATAM es la más lenta de las tres chilenas por lejos** (1,4 req/s
recomendado, contra 23,2 de Sky). No es que bloquee: su página de ofertas
pesa 724 KB y el límite es el ancho de banda, no una defensa. Como es la
aerolínea más importante del país, conviene tenerlo presente al fijar cada
cuánto se la repasa.

**american degradó a los 5 hilos** — es la única que mostró el techo abajo.
Su recomendado (15,3 req/s) sigue siendo de sobra.

## Copa: la lección cara del día

Copa dio **desafío WAF en el primer escalón (2 hilos)** y la medición se
detuvo ahí, como corresponde. El problema es lo que pasó después: al probar
5 URLs alternativas suyas, y luego **una sola petición cada 5 segundos**,
todas siguieron dando WAF.

**O sea la medición dejó marcada la IP de la casa en el WAF de Copa.** Es
justo el riesgo que el docstring de `medir_limites.py` advierte ("una IP
marcada por LATAM o Copa puede quedar así por días, y es la IP de la casa de
Joaquín"), y aun con la escalera suave ocurrió igual.

Suele ser temporal (Incapsula libera en minutos u horas). Qué hacer:

- **No volver a medir Copa desde acá.** Si hay que reintentar, que sea desde
  el runner de GitHub Actions o vía el proxy de Cloudflare.
- Copa quedó marcada como `WAF` en `aerolineas.py`. Si se la quiere vigilar
  de verdad, necesita el mismo tratamiento que `easy.cl`: pasar por el proxy.
- Reintentar la medición en unos días para saber si el bloqueo caducó.

## Las que no se midieron porque ni siquiera responden

Documentadas con su motivo al final de `aerolineas.py`: `aircanada` (403 +
desafío), `emirates` (403), `boa` (WAF en todo), `qatar` (todas las rutas
probadas dan 404), `plusultra` (404), `estelar` (el dominio no resuelve).

## Cómo repetir esto

```bash
python medir_vuelos.py                    # las 23
python medir_vuelos.py latam jetsmart     # solo algunas
```

Correrlo por tandas de 5, no las 23 de una: tarda ~1,2 min por aerolínea y
la salida se pierde entera si el proceso se corta.
