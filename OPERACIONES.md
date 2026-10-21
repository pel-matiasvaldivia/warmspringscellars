# Manual de operaciones — Cellar desk

Cómo entrar al sistema y atender las solicitudes, los pagos, las facturas y los
envíos del Warm Springs Cellar Wine Club.

La pantalla está en inglés porque es la que ve quien atiende el escritorio; en este manual
los nombres de los botones van entre comillas tal como aparecen ahí.

*There is an English version of this manual at [`OPERATIONS.md`](OPERATIONS.md).*

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
- `QBO_CLIENT_ID`/`QBO_CLIENT_SECRET` sin definir → las facturas **se acumulan
  en cola** en vez de llegar a QuickBooks. No se pierde nada: la cola está en
  **Books** y se exporta en CSV (§7).

Mientras haya recuadros, lo que se ve en pantalla es un ensayo, no la operación.
Para leer los mails que no salieron:

```bash
docker compose exec api ls -t /srv/var/outbox | head
docker compose exec api cat /srv/var/outbox/<archivo>.eml
```

---

## 2. Las siete pestañas

| Pestaña | Para qué |
| --- | --- |
| **Applications** | Las solicitudes del formulario del sitio, y las cargadas a mano |
| **Members** | Quién está activo, en pausa o dado de baja |
| **Orders** | Las órdenes y sus facturas en PDF |
| **Shipments** | Los envíos, de "listo para empacar" a "entregado" |
| **Releases** | Los despachos de temporada (abril y octubre) |
| **Books** | QuickBooks: qué facturas ya están en los libros y qué falta |
| **Mail** | Cómo está configurado el correo saliente, y un botón para probarlo |

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

### 3.1b Alguien que escribió en vez de completar el formulario

Hay gente que escribe a `club@warmspringscellar.com`, llama por teléfono o
pregunta en la bodega. **"Add one by hand"**, arriba a la derecha de
**Applications**, los mete en la misma cola: nombre, apellido, mail y estado, y
el resto opcional. Desde ese momento son una solicitud como cualquier otra —
pendiente, aprobada, invitación y link de pago. No hay un camino aparte, y
cargar la fila no le manda nada a esa persona: el mail sale al aprobar, igual
que para todos.

Tres cosas de esa pantalla conviene saber:

* **Solo se listan los estados habilitados.** Si alguien escribe desde un
  estado al que la bodega no puede despachar, no hay solicitud que cargar, y es
  mejor decírselo en la respuesta que descubrirlo al aprobar.
* **"How it reached us"** es lo que le da sentido a la pantalla. Si se
  completa, la columna de referidos termina contestando qué puerta trae socios:
  el correo, el teléfono, la sala de degustación o el sitio.
* **Nadie entra dos veces.** Si después esa persona completa el formulario por
  su cuenta mientras la fila sigue pendiente, el sistema devuelve la fila que
  ya existe. La pantalla también lo avisa si ese mail ya tiene una abierta.

En el registro de la ficha queda `entered_by_hand` con el usuario que estaba
conectado, al lado del `received` de siempre, así una fila cargada en el
escritorio nunca se confunde con una que produjo el sitio.

### 3.2 Aprobar o declinar

En **"The invitation"** se tildan los niveles que esa persona va a poder elegir.
Se listan todos los niveles del catálogo, y vienen tildados los que el socio
puede elegir por su cuenta. **Un nivel por invitación viene destildado** —
tildalo solo si de verdad querés ofrecerlo. Hoy no hay ninguno: el catálogo
tiene The Founder's Reserve y The Full Case, los dos abiertos.

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
**dice a dónde quiere que vaya el vino**, y recién ahí va al checkout y paga.

La dirección de entrega se pide en ese punto, entre la elección y el pago, y la
guarda el club en vez de leerla de la pasarela de pago. Tres motivos, ordenados
por lo que cuestan si se ignoran:

* **De la dirección depende si podemos despachar.** Lo define el estado, y no
  tiene sentido cobrar una caja que no se puede entregar.
* **El transportista no deja vino en la puerta.** Tiene que firmarlo alguien
  mayor de 21, así que **una casilla de correo se rechaza**, con el motivo
  explicado. Casa o trabajo, lo que le quede mejor.
* **Una dirección, no dos.** A propósito no se le pide a Stripe que junte una:
  dos direcciones que no coinciden dejan a todos sin saber desde cuál se arma
  la caja.

Del ZIP se controla la forma (cinco dígitos, o ZIP+4), no que exista — no vamos
a pagar verificación de direcciones para unos cientos de socios, y el socio la
lee de vuelta en la página después de pagar y en la factura.

Si alguien carga la dirección y abandona el checkout, **queda guardada**: el
mismo link se la devuelve completa, y en el escritorio se ve a dónde iba el
vino aunque el pago nunca se haya hecho.

El link **vence a los 14 días** (`OFFER_DAYS`) y **se gasta al usarse**: si
intenta abrirlo de nuevo lee "already been accepted". Eso es correcto, no un
error. Si de verdad hace falta otro, lo que corresponde es una solicitud nueva.

Con Stripe activo, el alta solo se confirma cuando llega el webhook. Si el socio
dice que pagó y en **Orders** no aparece `paid`, el problema está en el webhook,
no en el socio (ver §9).

### 3.4 Lo que se genera solo

Con el pago acreditado, el sistema crea sin intervención: la **membresía**
(`active`), la **orden** (`paid`), la **factura** (con número correlativo sin
huecos) y el **envío** (`ready`). No hay nada que apretar en este paso.

La factura también **se encola para QuickBooks** en el mismo momento, en la
misma transacción. Llegar a los libros es otro paso, y es el §7.

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

### Cuando un socio se muda

La dirección de la membresía es desde la que se arma la caja, así que se edita
en **Members**: "Change it", debajo de la dirección, abre los campos y guarda
con las mismas reglas que el socio vio en el checkout — estado habilitado,
calle de verdad, ZIP con la forma correcta. El cambio queda en el registro con
la dirección vieja y la nueva.

**Vale para lo que todavía no salió de la bodega.** Una orden ya despachada
conserva la dirección a la que fue, porque ahí está el vino. Si se cambia antes
de levantar el release, el próximo envío lo sigue.

Una membresía creada antes de que el club pidiera dirección tiene solo el
estado; las pantallas de Members y Shipments dicen **"no address on file"** en
rojo en vez de imprimir media etiqueta.

### Pausas y bajas

En **Members**, cada fila ofrece los cambios de estado válidos: `active` ⇄
`paused`, y `cancelled` desde cualquiera de los dos. **Pausar antes de apretar
"Raise orders"** — una vez levantada la orden, el cobro ya está hecho y hay que
reembolsar. `cancelled` no tiene vuelta.

---

## 7. Los libros: QuickBooks

La pestaña **Books**. Cada factura y cada pago viajan a QuickBooks Online como
dos documentos: la factura (*invoice*) y el pago que la cancela (*payment*).

**Los libros van siempre detrás, nunca en el camino.** El socio que pagó es
socio, haya contestado Intuit o no. Por eso emitir la factura no llama a
QuickBooks: encola el documento y el envío se hace después. De ahí salen tres
cosas que conviene saber:

- Una orden que se confirmó **siempre** tiene su papeleo en cola, y una que se
  deshizo nunca lo tiene. No pueden contradecirse: son el mismo commit.
- Un envío que falla **queda a la vista**. La cola de Books es la respuesta
  permanente a "¿qué no saben todavía los libros?", que es mejor preguntar en
  marzo que descubrir en enero.
- **Reintentar es gratis.** Si la factura ya está en QuickBooks con ese número,
  el reintento la adopta en lugar de escribir una segunda.

### 7.1 Conectar la primera vez

1. En [developer.intuit.com](https://developer.intuit.com) → *My Apps* → crear
   una app con el scope **Accounting**.
2. Registrar en Intuit **exactamente** esta URI de retorno:
   `https://<dominio>/admin/accounting/callback` — la pantalla de Books la
   muestra escrita, para copiar y pegar.
3. Poner `QBO_CLIENT_ID` y `QBO_CLIENT_SECRET` en `.env`, y
   `QBO_ENVIRONMENT=sandbox` para practicar o `production` para los libros
   reales. `docker compose up -d` para que los tome.
4. En **Books** → **"Connect to QuickBooks"**. Intuit pide permiso, elegís la
   empresa y vuelve. Eso es todo: la empresa queda guardada.

**Antes del primer envío, el ítem `Wine Club Allocation` tiene que existir en
QuickBooks** (Sales → Products and services), con ese nombre exacto.

El sistema **no lo crea**. A qué cuenta de ingresos va cada peso es decisión
del contador, y un valor por defecto pondría dinero real en la cuenta
equivocada y en silencio. Si falta, el envío falla diciendo cuál falta.

Hay un segundo ítem, `Shipping`, que hoy **no hace falta**: todos los niveles
vigentes incluyen el envío, así que la orden sale con un solo renglón. Vuelve a
hacer falta el día que un nivel cobre el flete aparte — el vino y el flete van a
cuentas distintas, y por eso son renglones distintos.

### 7.2 El día a día

| Botón | Qué hace |
| --- | --- |
| **"Sync now"** | Manda todo lo pendiente: primero las facturas, después sus pagos |
| **"Retry"** | Reintenta un documento que falló (aparece solo en esas filas) |
| **"Export all (CSV)"** | Todas las facturas, una fila por renglón |
| **"Export unfiled"** | Solo lo que todavía no está en los libros |
| **"Disconnect"** | Corta la conexión. **La cola queda intacta** |

El número grande de **"Waiting to be filed"** es lo único que hay que mirar de
rutina: si crece y no baja, los libros se están quedando atrás. También sale en
`/api/health`, para monitorearlo desde afuera.

Con `QBO_AUTOSYNC=true` el envío sale solo con cada pago. Conviene dejarlo en
`false` las primeras semanas y apretar "Sync now" a mano, mirando qué pasa.

### 7.3 Los cien días

Intuit **reemplaza el token de refresco en cada uso** —por eso los tokens viven
en la base de datos y no en `.env`— y **lo vence si no se usa por cien días**.
Un club que factura tres veces al año es, justamente, un club que se va a
encontrar desconectado.

La pantalla muestra la fecha límite en **"Re-authorise before"**. No es un dato
de color: pasada esa fecha la única reparación es volver a apretar "Connect to
QuickBooks". La cola no se pierde, pero nada se envía hasta que alguien
reconecte.

### 7.4 QuickBooks Desktop, o un contador que prefiere un archivo

El CSV cubre los dos casos. QuickBooks **Desktop no tiene API**, y hay
contadores que prefieren un archivo antes que darle acceso a la empresa a una
aplicación. El export trae todo lo que hace falta: número, cliente, email,
fecha, ítem, cantidad, importe, moneda, referencia de la orden y si está pago.

> **Lo que no está probado.** El adaptador nunca habló con una empresa real de
> QuickBooks desde acá: este contenedor no tiene salida a Intuit. Su lógica
> propia sí está probada (rotación de tokens, buscar el cliente por email,
> adoptar una factura duplicada, refrescar ante un 401), pero que Intuit acepte
> cada campo solo lo prueba una empresa sandbox. **Conectá una sandbox y mandá
> una factura antes de confiarle los libros reales.**

---

## 8. El correo que sale

Todo lo que el club manda —la invitación firmada, el aviso de despacho, la
factura— sale por un solo servidor SMTP. Mientras `SMTP_HOST` esté vacío no
sale nada: cada mensaje se guarda como archivo `.eml` en `/srv/var/outbox` y el
escritorio muestra el link de invitación en la ficha. Eso no es una falla, es
el modo en que viaja el proyecto hasta que alguien decide por dónde manda.

### 8.1 Dos maneras de autenticarse

| | Con contraseña | Con OAuth2 |
| --- | --- | --- |
| Qué se carga | `SMTP_USER` + `SMTP_PASSWORD` | `SMTP_USER` + las tres `SMTP_OAUTH_*` |
| Quién se presenta | la casilla | una aplicación, autorizada sobre la casilla |
| En Microsoft 365 | funciona hasta que el tenant apague la autenticación básica | sigue funcionando después |

El servicio elige solo: si están las tres variables de OAuth usa un token, si
no usa la contraseña, y si no hay ninguna de las dos no se autentica (es lo que
corresponde en el caso de §8.4). Cambiar de una a la otra es editar `.env` y
reiniciar; no hay nada que tocar en el código.

### 8.2 Microsoft 365 con contraseña

```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=club@warmspringscellar.com
SMTP_PASSWORD=<la contraseña de esa casilla>
MAIL_FROM=club@warmspringscellar.com
MAIL_REPLY_TO=club@warmspringscellar.com
```

La casilla necesita licencia de Exchange Online —una casilla compartida sin
licencia no puede autenticarse— y el tenant necesita tener habilitado **SMTP
AUTH** para ella. Si la cuenta tiene MFA, la contraseña de la persona no sirve:
hay que usar la casilla de servicio.

> **El síntoma a reconocer.** `535 5.7.139 Authentication unsuccessful, the
> request did not meet the criteria to be authenticated successfully. Contact
> your administrator.` Casi siempre significa que la autenticación básica está
> apagada para esa casilla, no que la contraseña esté mal. Ahí se pasa a §8.3.

### 8.3 Microsoft 365 con OAuth2

Microsoft viene retirando la autenticación básica para SMTP. El día que la
apaga en un tenant, usuario y contraseña dejan de servir sin aviso previo. Por
eso el adaptador también habla **XOAUTH2**, que es el camino que sobrevive.

Se hace una vez, y hace falta un administrador del tenant:

1. **Entra ID → App registrations → New registration.** Nombre: `Warm Springs
   Cellar — club mail`. Sin redirect URI: esta aplicación no inicia sesión
   como nadie.
2. **Certificates & secrets → New client secret.** Copiar el *Value* —no el
   Secret ID— en el momento: después no se vuelve a mostrar. Anotar también la
   fecha de vencimiento, porque el día que vence el correo deja de salir.
3. **API permissions → Add a permission → APIs my organization uses →
   Office 365 Exchange Online → Application permissions → `SMTP.SendAsApp`**, y
   después **Grant admin consent**. Sin el consentimiento el token sale igual y
   el servidor lo rechaza, que es la forma más confusa de fallar.
4. **Darle la casilla**, en Exchange Online PowerShell:

   ```powershell
   Connect-ExchangeOnline
   New-ServicePrincipal -AppId <client id> -ServiceId <object id de la app>
   Add-MailboxPermission -Identity club@warmspringscellar.com `
       -User <object id de la app> -AccessRights FullAccess
   ```

   El *object id* es el de la Enterprise application, no el de la registración.
5. **En `.env`:**

   ```
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USER=club@warmspringscellar.com
   SMTP_PASSWORD=
   SMTP_OAUTH_TENANT_ID=<Directory (tenant) ID>
   SMTP_OAUTH_CLIENT_ID=<Application (client) ID>
   SMTP_OAUTH_CLIENT_SECRET=<el Value del paso 2>
   ```

   `SMTP_USER` **se deja puesto**. El token dice qué aplicación manda, nunca
   desde qué casilla; la casilla hay que nombrarla igual.

El servicio pide un token, lo guarda y lo reusa hasta dos minutos antes de que
venza. No pide uno por mensaje.

### 8.4 La tercera opción: el conector de retransmisión

Si el servidor tiene IP fija, Exchange Online puede aceptar el correo por IP y
sin autenticación ninguna: **Exchange admin center → Mail flow → Connectors →
from "Your organisation's email server"**, autorizando esa IP.

```
SMTP_HOST=<tenant>.mail.protection.outlook.com
SMTP_PORT=25
SMTP_USER=
SMTP_PASSWORD=
```

Sirve para mandar a los propios dominios del tenant sin más trámite. Para
mandar afuera —que es lo que hace este club— el dominio del remitente tiene que
estar verificado en el tenant y el SPF tiene que cubrir esa IP. Es la opción
sin secretos que vencer, y la que depende de que la IP del VPS no cambie.

### 8.5 Que el correo llegue, no solo que salga

Tres registros en el DNS de `warmspringscellar.com`, y los tres son del
dominio, no del servidor:

- **SPF**, un único registro TXT: `v=spf1 include:spf.protection.outlook.com
  -all`. Si se usa el conector de §8.4, agregar `ip4:<IP del VPS>` antes del
  `-all`. Dos registros SPF en un dominio equivalen a ninguno.
- **DKIM**: Microsoft 365 Defender → Policies → Email authentication → DKIM.
  Publicar los dos CNAME `selector1._domainkey` y `selector2._domainkey` que
  indica la pantalla, y recién después habilitar la firma.
- **DMARC**, TXT en `_dmarc`: empezar en `v=DMARC1; p=none;
  rua=mailto:dmarc@warmspringscellar.com`, leer los informes un par de semanas
  y recién ahí endurecer a `quarantine`.

Una invitación que cae en correo no deseado es una solicitud aprobada que nadie
contesta, y en el escritorio se ve igual que una que fue ignorada.

### 8.6 Probar antes de prometer: la pestaña **Mail**

No hace falta aprobar una solicitud de verdad para saber si el correo sale.
La pestaña **Mail** del escritorio responde las tres preguntas en el orden en
que se rompen:

**"How it is configured"** lee el `.env` y lo dice en palabras: por dónde sale,
si va cifrado, cómo se autentica, con qué casilla. Cada fila lleva una marca:
*right* está bien, *works today* funciona hoy y va a dejar de funcionar —es lo
que dice de una contraseña contra Microsoft 365—, *wrong* está mal, y *not
sending* es que no hay servidor configurado y nada sale. Se lee en cada carga
de la página y no toca la red.

**"Check the connection"** abre la conexión de verdad: conecta, cifra, se
autentica y corta. **No manda nada y nadie recibe nada.** Lista cada paso que
sí funcionó antes del que falló, que es la mitad del diagnóstico: saber que la
conexión se abrió y el STARTTLS pasó antes de que rechazaran la contraseña
dice algo muy distinto que "no anda el correo".

**"Send a test message"** manda un mensaje real a la dirección que se le
escriba. Conviene una de **afuera** del tenant —un Gmail sirve, y además
muestra si el SPF y el DKIM pasaron: "Show original"— porque un mensaje que
viaja dentro del tenant no prueba nada sobre los que salen. El mensaje dice qué
servidor y qué mecanismo lo mandaron, así que reenviado sirve de prueba. No
escribe a nadie de la lista del club ni cambia nada de un socio o una orden.

Abajo de todo, **"If it does not work"** tiene los errores que de verdad
aparecen —`535 5.7.139`, `AADSTS7000215`, el token aceptado pero el envío
rechazado— y qué significa cada uno.

> **Lo que la pestaña no puede decir.** Que la conexión funcione no es que el
> mensaje llegue. Eso es SPF, DKIM y el filtro de spam del otro lado (§8.5), y
> solo lo contesta un envío real a una dirección real.

Si algo no cierra: `docker compose logs api | grep -i smtp`. El adaptador
registra el rechazo tal como lo dijo el servidor, con el código incluido.

---

## 9. Cuando algo no cierra

| Síntoma | Dónde mirar |
| --- | --- |
| El socio no recibió la invitación | ¿Hay aviso de `SMTP_HOST` arriba? Si sí, el mail está en `/srv/var/outbox`. Copiale el link desde la ficha. |
| El servidor contesta `535 5.7.139` | Microsoft 365 apagó la autenticación básica para esa casilla. No es la contraseña: hay que pasar a OAuth2 (§8.3). |
| "this invitation link is not valid" | `SECRET_KEY` cambió (reinicio sin la variable fijada en `.env`). Toda invitación anterior murió: hay que aprobar de nuevo. |
| "this invitation has expired" | Pasaron más de 14 días. Solicitud nueva. |
| Pagó y la orden sigue `pending_payment` | El webhook no llegó. Stripe → Developers → Webhooks → Recent deliveries. Debe apuntar a `https://<dominio>/api/payments/webhook` y estar suscripto a `checkout.session.completed`. Reintentar la entrega desde ahí es seguro: un evento repetido no cobra ni factura dos veces. |
| El formulario del sitio devuelve error | Hay un límite de **8 solicitudes por hora por dirección IP**, y **los intentos rechazados también cuentan**. Desde la oficina, probando, se agota rápido. |
| El escritorio da 503 | Falta `ADMIN_PASSWORD`. |
| Un botón de estado no aparece | No es un bug: ese cambio de estado no está permitido desde donde está la fila. Ver la tabla de §5. |
| "QuickBooks is not connected" | O faltan las credenciales en `.env`, o nadie autorizó la empresa todavía: **Books → "Connect to QuickBooks"**. Nada se perdió. |
| "QuickBooks has no item called …" | Falta crear ese ítem en QuickBooks, apuntado a la cuenta de ingresos que corresponda (§7.1). El sistema no lo inventa a propósito. |
| "Duplicate Document Number" | Resuelto solo: el reintento adopta la factura que ya está en QuickBooks. Si la fila quedó en `failed`, "Retry" la cierra. |
| La cola de Books no baja | ¿Venció el token de los cien días? Mirar "Re-authorise before" y reconectar (§7.3). |
| El contador pide los datos y no hay conexión | "Export all (CSV)". Es un sustituto completo, no un parche (§7.4). |

### Lo que hay que respaldar

Todo vive en un único volumen, `club-data` (`/srv/var` dentro del
contenedor): la base `club.sqlite3` —con la cola de QuickBooks y los tokens de
Intuit adentro—, las facturas en PDF y el outbox.

```bash
docker compose exec api tar cz -C /srv var > respaldo-club-$(date +%F).tar.gz
```

Sin ese respaldo se pierden socios, órdenes y facturas. Con él, no.

### Ver qué pasó

La ficha de cada solicitud trae su propio registro al pie: quién aprobó, cuándo
salió el mail, cuándo se abrió la oferta, cuándo se acreditó el pago. Es el
primer lugar donde mirar antes de preguntarle al socio.

---

## 10. Cómo se publica

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

## 11. Antes de cobrar de verdad

- [ ] `SECRET_KEY` generada, guardada y **fija** en `.env`
- [ ] `ADMIN_PASSWORD` puesta, y el rango de IP descomentado en `nginx.conf`
- [ ] `PUBLIC_BASE_URL` con el dominio real (los links salen de ahí)
- [ ] Stripe aprobado para venta de alcohol — **lo pide expresamente**
- [ ] Webhook apuntado a `/api/payments/webhook` y suscripto a
      `checkout.session.completed`
- [ ] SMTP configurado y probado desde la pestaña **Mail**, con un envío a una
      dirección de afuera del tenant (§8.6). En Microsoft 365, con OAuth2
      (§8.3) antes que con contraseña
- [ ] SPF, DKIM y DMARC publicados en `warmspringscellar.com` (§8.5)
- [ ] `SHIPPABLE_STATES` revisada contra los permisos reales de la bodega
- [ ] QuickBooks: app creada, URI de retorno registrada, el ítem creado
      y **una factura enviada a una empresa sandbox** antes de pasar a
      `production`
- [ ] `QBO_ENVIRONMENT=production` el mismo día que Stripe pasa a vivo — el
      sistema avisa si uno está vivo y el otro en sandbox, pero mejor no
      llegar ahí
- [ ] Respaldo del volumen `club-data` automatizado

Dos cosas que **no** están implementadas y conviene tener presentes: los límites
de volumen por cliente y por estado que vienen con los permisos DTC, y el
inventario — el sistema levanta órdenes sin preguntar si hay botellas. Están
anotadas en `api/FLOW.md`.
