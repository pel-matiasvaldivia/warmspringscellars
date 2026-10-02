# Manual de operaciones — Cellar desk

Cómo entrar al sistema y atender las solicitudes, los pagos, las facturas y los
envíos del Warm Springs Cellars Wine Club.

La pantalla está en inglés porque es la que ven Robert y Cecilia; en este manual
los nombres de los botones van entre comillas tal como aparecen ahí.

Las reglas de negocio están en [`api/FLOW.md`](api/FLOW.md). Este documento es
el de uso diario: qué abrir, qué apretar y qué revisar.

---

## 1. Entrar

| | |
| --- | --- |
| Dirección | `https://<dominio>/admin` |
| Usuario | lo que esté en `ADMIN_USER` del `.env` (por defecto `cellar`) |
| Contraseña | `ADMIN_PASSWORD` del `.env` |

El navegador pide usuario y contraseña con el cuadro propio de HTTP Basic, no
con un formulario de la página. Para salir de la sesión hay que cerrar el
navegador: Basic no tiene "logout".

Si aparece **503 "ADMIN_PASSWORD is not set, so the desk is closed"**, el
escritorio está cerrado a propósito: falta esa variable. Es deliberado — un
panel de administración sin contraseña es peor que uno caído.

**Segunda cerradura (recomendada).** En `nginx.conf`, dentro de
`location /admin`, hay dos líneas comentadas:

```nginx
# allow 203.0.113.0/24;   # the winery's office
# deny all;
```

Descomentalas con el rango de IP de la bodega y el escritorio deja de existir
para el resto de internet. La contraseña sigue estando: dos cerraduras, no una.

### La barra de avisos

Arriba de cada página, en recuadros **"Not configured:"**, el sistema dice qué
adaptador todavía es un simulador. Los cuatro que importan:

- `SECRET_KEY` sin definir → los links de invitación se firman con una clave que
  cambia en cada reinicio, así que **toda invitación ya enviada deja de abrir**.
- `STRIPE_SECRET_KEY` sin definir → el checkout es **simulado y no se mueve
  dinero**. El flujo completo funciona, pero nadie paga.
- `STRIPE_WEBHOOK_SECRET` sin definir (con Stripe activo) → los webhooks no se
  pueden verificar y **se rechazan**: un pago real no se registraría.
- `SMTP_HOST` sin definir → ningún mail sale; cada uno se guarda como archivo
  `.eml` en `/srv/var/outbox` dentro del contenedor.

Mientras haya recuadros, lo que se ve en pantalla es un ensayo, no la operación.
Para leer los mails que no salieron:

```bash
docker compose exec api ls -t /srv/var/outbox | head
docker compose exec api cat /srv/var/outbox/<archivo>.eml
```

---

## 2. Las cinco pestañas

| Pestaña | Para qué |
| --- | --- |
| **Applications** | Las solicitudes que llegan del formulario del sitio |
| **Members** | Quién está activo, en pausa o dado de baja |
| **Orders** | Las órdenes y sus facturas en PDF |
| **Shipments** | Los envíos, de "listo para empacar" a "entregado" |
| **Releases** | Los despachos de temporada (abril y octubre) |

Las solicitudes pendientes y los envíos sin entregar suben solos al principio de
sus listas. Lo que requiere atención queda arriba.

---

## 3. El recorrido de un socio nuevo

```
formulario → Applications → aprobar → el socio elige nivel → paga
          → orden → factura → envío → entregado
```

### 3.1 Llega la solicitud

Cae en **Applications** en estado `pending`. "Open" abre la ficha: nombre,
email, teléfono, ciudad y estado de envío, el nivel que pidió, cómo nos conoció
y la nota que escribió.

Si el estado de destino no está en la lista de permisos de la bodega, el
formulario del sitio lo avisa antes de enviarse **y el servidor también lo
rechaza** — las dos puntas, porque el navegador se puede saltear. La lista está
en `SHIPPABLE_STATES`, en `api/app/config.py`.

Si la misma persona completa el formulario dos veces mientras su solicitud
sigue `pending`, **no se duplica la fila**: se trata como la misma solicitud y
queda anotado el reintento. En la lista se ve una sola, que es lo correcto —
alguien ansioso no es dos socios.

### 3.2 Aprobar o declinar

En **"The invitation"** se tildan los niveles que esa persona va a poder elegir.
Vienen tildados los tres seleccionables; **The Inner Circle viene destildado
porque es por invitación** — tildalo solo si de verdad querés ofrecerlo.

**"Approve & send the invitation"** hace cuatro cosas en un paso: aprueba la
solicitud, crea la oferta con los niveles tildados, le manda el mail al socio y
deja el link firmado visible en la ficha, abajo, en la tabla **"Invitations"**.

> **Copiá siempre ese link.** Si el mail no sale, el aviso lo dice sin rodeos
> ("Approved, but the email did not go out…") y el link es la única forma de que
> la persona entre. Se puede pegar en un mail a mano o en un WhatsApp.

**"Decline"** con un motivo opcional manda el mail de rechazo. El motivo queda
en el registro interno; al socio no se le muestra.

Una solicitud se aprueba o se declina **una sola vez**. Después de eso los
botones no vuelven: el flujo rechaza la transición en lugar de dejar dos ofertas
vivas para la misma persona.

### 3.3 El socio elige y paga

El link lo lleva a una página con su nombre y los niveles ofrecidos. Elige uno,
va al checkout y paga.

El link **vence a los 14 días** (`OFFER_DAYS`) y **se gasta al usarse**: si
intenta abrirlo de nuevo lee "already been accepted". Eso es correcto, no un
error. Si de verdad hace falta otro, lo que corresponde es una solicitud nueva.

Con Stripe activo, el alta solo se confirma cuando llega el webhook. Si el socio
dice que pagó y en **Orders** no aparece `paid`, el problema está en el webhook,
no en el socio (ver §6).

### 3.4 Lo que se genera solo

Con el pago acreditado, el sistema crea sin intervención: la **membresía**
(`active`), la **orden** (`paid`), la **factura** (con número correlativo sin
huecos) y el **envío** (`ready`). No hay nada que apretar en este paso.

---

## 4. Órdenes y facturas

**Orders** muestra referencia, fecha, socio, nivel, despacho, total, estado y el
número de factura. El número es un link: lo abre como PDF
(`WSC-INV-2026-0001.pdf`).

Estados de una orden: `pending_payment` → `paid` → `fulfilled`, y `refunded` o
`failed` cuando algo se sale del carril. `fulfilled` lo pone el sistema cuando
el envío se marca entregado — no se toca a mano.

El impuesto a las ventas se calcula en cero y **se imprime en cero en la
factura**. Es a propósito: si todavía no lo liquidamos, es mejor que la factura
lo diga que esconderlo. Está anotado en `api/FLOW.md`.

---

## 5. Envíos

**Shipments** es la pantalla del día de empaque. Cada fila trae solo los botones
que el estado actual permite:

| Estado | Botones | Qué pide |
| --- | --- | --- |
| `ready` | "picked", "held" | "held" acepta un motivo |
| `picked` | "in transit", "held" | **"in transit" exige el tracking** — lo pide el formulario y lo vuelve a exigir el servidor; el transportista es opcional y queda como `unknown` |
| `in_transit` | "delivered", "returned" | — |
| `held` | "ready" | se retoma |
| `returned` | "ready" | se vuelve a empacar |
| `delivered` | — | cerrado, y cierra la orden |

**"in transit" es el único botón que le escribe al socio**: le manda el mail con
transportista y número de seguimiento. Por eso el tracking es obligatorio — un
aviso de envío sin número no sirve de nada. Si ese mail falla, el aviso de
pantalla lo dice y conviene reenviarlo a mano.

No se puede saltear pasos. "delivered" sobre un envío que nunca se marcó
`picked` se rechaza. Es a propósito: un envío que nadie empacó y figura
entregado es una conversación imposible de reconstruir seis meses después.

---

## 6. Los dos despachos del año

Abril y octubre, dos por año. En **Releases**:

1. **"Plan it"** con nombre (`Spring 2026`) y fecha de despacho.
2. **"Raise orders"** levanta **una orden por cada membresía activa**, con el
   nivel y el precio de cada una.

**"Raise orders" se puede apretar dos veces sin miedo.** El par
(socio, despacho) es único en la base, así que la segunda corrida saltea a quien
ya tiene orden en lugar de cobrarle de nuevo. Si entraron socios nuevos después
de la primera corrida, apretarlo otra vez es exactamente lo que hay que hacer.

Si una membresía quedó con un nivel que ya no existe en el catálogo, se la
saltea y queda anotado en el registro. No se la factura "a lo que salga".

### Pausas y bajas

En **Members**, cada fila ofrece los cambios de estado válidos: `active` ⇄
`paused`, y `cancelled` desde cualquiera de los dos. **Pausar antes de apretar
"Raise orders"** — una vez levantada la orden, el cobro ya está hecho y hay que
reembolsar. `cancelled` no tiene vuelta.

---

## 7. Cuando algo no cierra

| Síntoma | Dónde mirar |
| --- | --- |
| El socio no recibió la invitación | ¿Hay aviso de `SMTP_HOST` arriba? Si sí, el mail está en `/srv/var/outbox`. Copiale el link desde la ficha. |
| "this invitation link is not valid" | `SECRET_KEY` cambió (reinicio sin la variable fijada en `.env`). Toda invitación anterior murió: hay que aprobar de nuevo. |
| "this invitation has expired" | Pasaron más de 14 días. Solicitud nueva. |
| Pagó y la orden sigue `pending_payment` | El webhook no llegó. Stripe → Developers → Webhooks → Recent deliveries. Debe apuntar a `https://<dominio>/api/payments/webhook` y estar suscripto a `checkout.session.completed`. Reintentar la entrega desde ahí es seguro: un evento repetido no cobra ni factura dos veces. |
| El formulario del sitio devuelve error | Hay un límite de **8 solicitudes por hora por dirección IP**, y **los intentos rechazados también cuentan**. Desde la oficina, probando, se agota rápido. |
| El escritorio da 503 | Falta `ADMIN_PASSWORD`. |
| Un botón de estado no aparece | No es un bug: ese cambio de estado no está permitido desde donde está la fila. Ver la tabla de §5. |

### Lo que hay que respaldar

Todo vive en un único volumen, `club-data` (`/srv/var` dentro del
contenedor): la base `club.sqlite3`, las facturas en PDF y el outbox.

```bash
docker compose exec api tar cz -C /srv var > respaldo-club-$(date +%F).tar.gz
```

Sin ese respaldo se pierden socios, órdenes y facturas. Con él, no.

### Ver qué pasó

La ficha de cada solicitud trae su propio registro al pie: quién aprobó, cuándo
salió el mail, cuándo se abrió la oferta, cuándo se acreditó el pago. Es el
primer lugar donde mirar antes de preguntarle al socio.

---

## 8. Cómo se publica

El sitio y la API son dos contenedores y **un solo puerto publicado**, el
`8086`, pensado para quedar detrás de Nginx Proxy Manager. La API no se publica:
solo la alcanza nginx por la red interna.

```bash
cp .env.example .env     # completar SECRET_KEY y ADMIN_PASSWORD
openssl rand -base64 48  # para SECRET_KEY — generarla una vez y no cambiarla
docker compose pull
docker compose up -d
```

El `docker-compose.yml` **se niega a arrancar** sin `SECRET_KEY` y
`ADMIN_PASSWORD`. Es mejor que levante roto y lo sepamos ahora que meses
después, con links firmados que no abren.

`.env` nunca va al repositorio. `.env.example` es la plantilla y es lo único que
se versiona.

---

## 9. Antes de cobrar de verdad

- [ ] `SECRET_KEY` generada, guardada y **fija** en `.env`
- [ ] `ADMIN_PASSWORD` puesta, y el rango de IP descomentado en `nginx.conf`
- [ ] `PUBLIC_BASE_URL` con el dominio real (los links salen de ahí)
- [ ] Stripe aprobado para venta de alcohol — **lo pide expresamente**
- [ ] Webhook apuntado a `/api/payments/webhook` y suscripto a
      `checkout.session.completed`
- [ ] SMTP configurado y probado con una aprobación de prueba a un mail propio
- [ ] `SHIPPABLE_STATES` revisada contra los permisos reales de la bodega
- [ ] Respaldo del volumen `club-data` automatizado

Dos cosas que **no** están implementadas y conviene tener presentes: los límites
de volumen por cliente y por estado que vienen con los permisos DTC, y el
inventario — el sistema levanta órdenes sin preguntar si hay botellas. Están
anotadas en `api/FLOW.md`.
