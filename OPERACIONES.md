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

## 2. Las diez pestañas

| Pestaña | Para qué |
| --- | --- |
| **Applications** | Las solicitudes del formulario del sitio, y las cargadas a mano |
| **Waiting** | Gente en estados sin permiso, que pidió que se le avise |
| **Members** | Quién está activo, en pausa o dado de baja |
| **Orders** | Las órdenes y sus facturas en PDF |
| **Shipments** | Los envíos, de "listo para empacar" a "entregado" |
| **Releases** | Los despachos de temporada, y qué puede cumplir cada uno |
| **Cellar** | Las botellas de las que el club puede disponer, y adónde fueron |
| **Books** | QuickBooks: qué facturas ya están en los libros y qué falta |
| **Mail** | Cómo está configurado el correo saliente, y un botón para probarlo |
| **Campaigns** | Qué envió el mailing, y quién pidió que no se le escriba más |

Las solicitudes pendientes y los envíos sin entregar suben solos al principio de
sus listas. Lo que requiere atención queda arriba.

---

## 3. El recorrido de un socio nuevo

```
formulario → Applications → aprobar → el socio elige nivel → paga
          → orden → factura → envío → entregado
```

**La aprobación puede ser automática.** Con `AUTO_APPROVE_APPLICATIONS=true` el
formulario aprueba lo que recibe y manda la invitación de vuelta en el momento,
sin que intervenga nadie. Apagado salvo que se encienda.

No afloja ninguno de los controles que vienen antes, porque ninguno era tarea
de quien aprobaba: un estado sin licencia lo sigue rechazando el formulario, el
límite de tasa sigue vigente, una dirección mal formada se sigue rechazando, y
una solicitud repetida sigue devolviendo la que ya está en la base en vez de
abrir una segunda invitación. Lo que se resigna es que una persona lea la
solicitud antes de que el club abra la puerta — y esa puerta se puede cerrar
después igual, revocando la oferta o pausando la membresía.

Una diferencia visible: al aprobado automáticamente le llega **la invitación**
en lugar de la carta de "recibimos tu solicitud". Dos mensajes por un mismo
formulario serían ruido, y el que lleva el link es el mejor de los dos. Si esa
carta no se puede enviar, la solicitud queda aprobada igual y sale el acuse
simple, así nadie se queda en silencio. La aprobación queda registrada contra la
solicitud con `auto` como quien decidió, así que **Applications** sigue
mostrando quién dejó entrar a cada socio.

**La bodega se entera de cada socio nuevo.** Un mensaje a `ALERT_TO` (o a la
casilla del club) cuando la membresía existe de verdad —tarjeta guardada,
nivel elegido—, encabezado por el nivel, que es para lo que la bodega tiene
que encontrar botellas. No cuando se llena el formulario: mucha gente pregunta
y nunca termina, y un aviso por cada una enseñaría a quien los lee a dejar de
leerlos. Si ese aviso no se puede mandar, la membresía queda igual y el
silencio se registra contra ella.

### 3.1 Llega la solicitud

Cae en **Applications** en estado `pending` —o, con la aprobación automática
encendida, en `approved` y con su invitación ya enviada—. "Open" abre la ficha: nombre,
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

Hay gente que escribe a `wineclub@warmspringscellar.com`, llama por teléfono o
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

### 3.1c Gente a la que todavía no podemos despachar

Al visitante que elige un estado sin permiso se le dice en el momento, y se le
ofrece lo único que hay para ofrecer: **le escribimos el día que eso cambie.**
El botón del formulario pasa a decir eso, y los mismos campos van a la lista de
espera en vez de a Applications — no se le pide nivel, porque no se le está
ofreciendo ninguno. Sin eso, lo único que un visitante de Alabama escucha es
que no, y un nombre que valía el día que llega el permiso se pierde.

Aparecen en **Waiting**, agrupados por estado, porque la licencia llega para un
estado y no para una persona. A quien responda la carta que la bodega manda a
esos estados se lo carga ahí a mano; ese formulario ofrece solo los estados sin
permiso, el espejo del §3.1b que ofrece solo los habilitados.

A propósito **no son solicitudes**. Una solicitud es un pedido de allocation y
recibe respuesta; esta no se puede responder hasta que llegue una licencia que
nadie acá controla, y una fila pendiente que nunca se puede decidir es una
mentira en la cola.

**El día que un estado se abre**, sacalo de `UNLICENSED_STATES` en
`api/app/config.py` y de la lista `UNLICENSED` en `index.html` — las dos
puntas, o el formulario y el servidor se contradicen. **Waiting** muestra
entonces una línea verde nombrando ese estado, porque puede estar habilitado
semanas antes de que alguien note que hay nombres abajo. **"Into the queue"**
convierte la fila en una solicitud común; aprobarla en **Applications** es lo
que le escribe, igual que a todos. Mover la fila no manda nada.

### 3.2 Aprobar o declinar

En **"The invitation"** se tildan los niveles que esa persona va a poder elegir.
Se listan todos los niveles del catálogo, y vienen tildados los que el socio
puede elegir por su cuenta. **Un nivel por invitación viene destildado** —
tildalo solo si de verdad querés ofrecerlo. Hoy no hay ninguno: el catálogo
tiene The Tasting Case, The Founder's Reserve y The Full Case, los tres
abiertos.

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

### 3.4 Sumarse no cobra nada

Con la tarjeta guardada, el sistema crea la **membresía** (`active`) y se
detiene ahí. Sin orden, sin factura, sin envío y **sin cobrar**.

Ese es el modelo de cobro completo y conviene decirlo sin vueltas: **el club
cobra cuando sale un despacho, nunca cuando alguien se suscribe.** En el
momento en que un socio se anota, nadie sabe todavía si hay vino para él —eso
lo decide el release— y cobrar primero es exactamente cómo un club termina
debiendo botellas que no tiene.

Así que la tarjeta queda guardada en la pasarela hasta que el §6 la cobre. La
carta de bienvenida se lo dice en el segundo renglón, porque una tarjeta
cargada en una página de pago es una tarjeta que casi todos dan por cobrada, y
un resumen sin el cargo preocupa en vez de tranquilizar.

**No hay suscripción.** Nada dentro de Stripe le cobra a nadie por calendario,
así que cuando cambien las fechas de despacho no hay un segundo calendario que
mantener sincronizado.

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

## 6. Los tres despachos del año

Primavera, otoño y Navidad. En **Releases**:

1. **"Plan it"** con nombre (`Spring 2026`) y fecha de despacho.
2. **Componerlo** — abrir el despacho y decir qué lleva el envío de cada
   nivel. De eso trata §6.1, y es lo nuevo.
3. **"Raise orders"** levanta una orden **por cada socio para el que hay
   vino**, con su nivel y su precio, y saca esas botellas de la bodega. Salen
   en `pending_payment`: **ese botón no cobra nada.**
4. **"Tell them"**, entre los dos, le escribe a todos los de ese despacho
   diciendo qué viene y cuánto va a costar. No cobra nada.
5. **"Charge N"** cobra esas órdenes contra las tarjetas que los socios
   dejaron al sumarse. Ese es el botón que mueve la plata.

**El cobro espera un día detrás del aviso.** Si apretás Charge antes, se
niega y te dice a partir de qué hora se puede. Dos razones sin relación entre
sí quieren el mismo hueco, y por eso está impuesto en vez de quedar librado a
que alguien se acuerde: un aviso que llega minutos antes del cobro no es un
aviso sino un comprobante con pasos de más, en vez de la oportunidad de
cambiar una tarjeta o decir que no; y el aviso y el comprobante son dos
mensajes a la misma persona, así que mandarlos el mismo día duplica el día más
cargado, que es lo que primero agota el cupo diario de un relay (§8.5).

La negativa frena el *despacho*. Una orden suelta se puede cobrar igual desde
**Orders**, que es la salida para el socio que llama y pide que le cobren ya.
Un despacho del que nadie fue avisado no se frena: el hueco sigue a un aviso, y
no mandar ninguno es otra decisión. La espera es `RELEASE_NOTICE_GAP_HOURS`,
24 por defecto, y 0 la apaga.

**Tres botones, a propósito.** Entre componer y levantar se ve quiénes entran
y qué vino se corta primero. Entre levantar y cobrar se ve exactamente qué se
levantó. Esos dos huecos son todo el control que hay entre la bodega y una
temporada que no puede cumplir. El botón de cobro dice cuántas órdenes quedan,
y desaparece cuando no queda ninguna.

**Todos se pueden apretar dos veces sin miedo.** El par (socio, despacho) es
único en la base, así que volver a levantar saltea a quien ya tiene orden —y
saca botellas solo por las órdenes que esa corrida levantó—. Volver a cobrar
saltea a quien ya pagó, y a la pasarela se le pasa la referencia de la orden
como clave, así que un cobro que se cortó por timeout se salda una vez y no
dos.

### 6.1 Componer un despacho, y quién se queda afuera

Hacer clic en el nombre del despacho en **Releases**, o en el botón **Plan**.
La página tiene tres partes.

**Qué lleva el envío.** Una casilla por vino y por nivel. Vacío o cero quiere
decir que ese vino no va en ese envío. Si las botellas no dan el número que el
nivel promete —tres, seis o doce— la página lo dice, y lo sigue diciendo
hasta que den. El socio lee esa cantidad en su factura.

**El vino.** Una fila por vino: cuántas botellas lleva un envío de cada nivel,
cuántas necesita el club entero, cuántas hay, y cuántas faltan. *On hand* ya
descuenta lo que se llevaron los despachos anteriores.

**Lo que cuesta mandarlo.** El flete de los envíos que el plan puede cumplir,
por zona de Crown y en total, contra la planilla UPS Ground 2026 a tarifa
residencial. Todos los niveles cobran flete, así que una parte vuelve de los
socios y la pantalla dice cuánto; el resto: es lo que gasta la bodega para sostener esa promesa, y el
renglón de abajo lo da como porcentaje de lo que cobraría el despacho.

**Los ice packs los paga la bodega**, nunca el socio. Un despacho arranca con
ellos prendidos si sale entre abril y octubre, y apagados si no; el botón
debajo de la tabla lo cambia, porque el calendario no puede saber qué pronóstico
va a haber del otro lado. Cambiarlo mueve solo lo que paga la bodega: lo que
paga cada socio queda igual.

Es una estimación, no una cotización. Deja afuera el recargo de un dólar por
libra de sobrepeso —que el propio ice pack puede disparar— y las tarifas que
solo aparecen cuando algo sale mal. Un socio cuyo estado no tiene precio en la
planilla terrestre —Alaska y Hawái son los dos que el club igual aceptaría—
aparece en rojo en vez de contarse como gratis.

**Quién no se puede cumplir.** Los nombres, y el motivo al lado de cada uno.
Cuatro motivos, y son conversaciones distintas:

| Motivo | Qué significa |
| --- | --- |
| out of *un vino* | Se acabó ese vino. El problema es el vino, no el socio |
| not licensed to ship to *un estado* | El estado se cerró después de que se sumó: no se va a poder enviar, así que tampoco se le asigna vino |
| this release does not say what that tier gets | Se compuso un nivel y el otro no |
| *un nivel* is no longer in the catalogue | Está en un nivel que se retiró |

**Cuando no alcanza, se cumple a quien se sumó primero.** Esa es toda la
regla, y es a propósito una que se le puede decir en voz alta al socio que se
queda afuera. Una sorpresa que conviene conocer: funciona por botella, no como
una sola fila. Alguien que se sumó después igual puede quedar cumplido si los
vinos de su nivel alcanzaron. Están tomando vino distinto, y retenerle el
envío no le pondría una botella en la mano al otro.

**A un socio sin tarjeta igual se le asigna vino**, y aparece en una lista
aparte para poder llamarlo antes. No se lo retiene: tiene una carta y un
enlace, y su orden espera en `failed` hasta que lo use. Pero una botella
asignada a una tarjeta que va a rebotar es una botella que se le quitó a
alguien que sí iba a pagar.

**Un despacho sin componer sigue funcionando como antes** — una orden por
socio activo, sin controlar nada. La pantalla lo dice en rojo, y el mensaje
después de apretar también. Se dejó posible porque un club que todavía no
escribió su inventario igual tiene que poder despachar.

### 6.2 La pestaña Cellar

Se agrega un vino con un código y un nombre. Después, por vino, dos botones
que se parecen y quieren decir cosas distintas:

- **Received** suma lo que escribiste a lo que ya había. Botellas que entran.
- **Counted** dice que el estante tiene esa cantidad, y escribe la *diferencia*.

La diferencia importa. Un conteo no pisa nada: deja la corrección como un
movimiento propio, con los dos números en la nota (`counted 114, books said
120`). Un conteo que estaba mal se puede encontrar después, en vez de haber
reemplazado la verdad en silencio. **Every movement**, abajo de todo, es la
historia completa, y *On hand* no es otra cosa que su suma.

Este es el estante del club, no el registro de producción de la bodega. Acá
nadie sabe de la sala de degustación ni de las ventas al comercio, así que un
vino que sale por otra puerta deja este número alto de más hasta que alguien
vuelva a contar. Contar antes de componer un despacho es la costumbre que hace
que todo lo demás sea cierto.

### Cuando una tarjeta es rechazada

La orden pasa a **`failed`** y se queda ahí, con el motivo al lado en
**Orders**, en las palabras de la pasarela: una tarjeta vencida y un banco que
quiere hablar con su cliente se ven idénticas sin eso. Al socio le llega una
sola carta: su allocation está reservada, no perdida, y un link para cargar
otra tarjeta. Ese link no cobra nada; una vez que la tarjeta nueva está
guardada, se aprieta **Charge** otra vez en esa orden.

Nada persigue a una orden fallida por calendario, y nada la da por perdida
tampoco. Espera en la pantalla de Orders hasta que una persona la resuelva.

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

La pestaña **Books**. Cada factura y cada pago viajan a QuickBooks como dos
documentos: la factura (*invoice*) y el pago que la cancela (*payment*).

QuickBooks es el sistema contable de la bodega y el único con el que habla el
servicio. Se ponen `QBO_CLIENT_ID` y `QBO_CLIENT_SECRET`, y después se
autoriza la empresa una vez desde la pestaña Books. Hasta que eso pase, la
cola guarda todo y el export CSV es un reemplazo completo.

**Los libros van siempre detrás, nunca en el camino.** El socio que pagó es
socio, haya contestado el sistema contable o no. Por eso emitir la factura no
lo llama: encola el documento y el envío se hace después. De ahí salen tres
cosas que conviene saber:

- Una orden que se confirmó **siempre** tiene su papeleo en cola, y una que se
  deshizo nunca lo tiene. No pueden contradecirse: son el mismo commit.
- Un envío que falla **queda a la vista**. La cola de Books es la respuesta
  permanente a "¿qué no saben todavía los libros?", que es mejor preguntar en
  marzo que descubrir en enero.
- **Reintentar es gratis.** Si la factura ya está con ese número, el reintento
  la adopta en lugar de escribir una segunda. Los dos adaptadores buscan el
  número antes de escribir.

### 7.1 Conectar la primera vez

Hacer primero lo de §7.1b. Sin esos ítems la conexión funciona y **el primer
envío falla**.

1. En [developer.intuit.com](https://developer.intuit.com) → *My Apps* → crear
   una app con el scope **Accounting**.
2. Registrar en Intuit **exactamente** esta URI de retorno:
   `https://<tu dominio>/admin/accounting/callback` — la pantalla Books la
   imprime para copiar. Intuit tiene **dos casilleros separados**, uno en
   Development y otro en Production: registrala en el que corresponde a las
   claves que vas a usar, y en los dos si vas a usar los dos.
3. Poner `QBO_CLIENT_ID` y `QBO_CLIENT_SECRET` en `.env`, con
   `QBO_ENVIRONMENT=sandbox` para practicar o `production` para los libros
   reales. `docker compose up -d` para que las tome.
4. En **Books** → **"Connect to QuickBooks"**. Intuit pide permiso, elegís la
   empresa y vuelve. Eso es todo: la empresa queda guardada.

**Dos juegos de claves que no se cruzan.** Intuit entrega claves de Development
y de Production por separado. Las de desarrollo solo llegan a una empresa
sandbox, las de producción solo a la real, y `QBO_ENVIRONMENT` tiene que decir
cuál de las dos pegaste. Mezclarlas da un error de autenticación que parece una
contraseña mal puesta y no lo es.

**Las claves de producción no están ahí para agarrarlas.** Intuit pide un
cuestionario de evaluación y aprueba la app antes de entregarlas, tanto para
una app privada de una sola bodega como para una publicada. Conviene empezar
ese trámite temprano si hay una fecha que cumplir.

### 7.1a Cuando Intuit dice que la redirect_uri es inválida

> *The redirect_uri query parameter value is invalid. Make sure it is listed in
> the Redirect URIs section on your app's keys tab and matches it exactly.*

La dirección que mandó el servicio no es carácter por carácter la registrada.
Intuit compara la cadena entera, así que todas estas fallan:

| Qué difiere | Cómo se ve |
| --- | --- |
| Una barra final en `PUBLIC_BASE_URL` | `https://sitio.com//admin/accounting/callback`, con dos barras |
| `http` contra `https` | En producción **tiene que ser** `https` |
| El casillero equivocado en Intuit | Registrada en Development y conectando con claves de producción |
| El dominio | `warmspringscellar.com` contra `warmspringscellars.com` — el nombre de la bodega es singular |
| Una IP o un puerto | Intuit rechaza una IP, y tiene que ser el dominio real |
| La nuestra no está en la lista | Una app nueva viene con la URI del OAuth Playground de Intuit ya cargada, lo que hace parecer que la lista está completa |

**Leé la cadena exacta de la pantalla Books y pegá esa**, en vez de escribirla.
Está impresa debajo del botón Connect, y es literalmente lo que el servicio va
a mandar.

Una app nueva viene con `https://developer.intuit.com/v2/OAuth2Playground/RedirectUrl`
ya cargada: es la herramienta de prueba de Intuit. Puede quedarse; la URI de la
bodega se **agrega** al lado con *Add URI*. Se admiten hasta 25.

**Para ver qué recibió Intuit de verdad**, mirá la barra de direcciones de la
página que muestra el error: lleva el parámetro `redirect_uri=` que mandó el
servicio, codificado (`https%3A%2F%2F…`). Compará eso contra la lista de *Keys
& credentials → Redirect URIs*, teniendo en cuenta que cada una de las dos
secciones de Intuit tiene su propia lista.

### 7.1b Los dos ítems, que tienen que existir antes

**Antes del primer envío, el ítem `Wine Club Allocation` tiene que existir en
QuickBooks** (Sales → Products and services), con ese nombre exacto.

El sistema **no lo crea**. A qué cuenta de ingresos va cada peso es decisión
del contador, y un valor por defecto pondría dinero real en la cuenta
equivocada y en silencio. Si falta, el envío falla diciendo cuál falta.

Hay un segundo ítem, `Shipping`, que **ahora sí hace falta**, y esto cambió en
noviembre de 2026. Todos los niveles cobran el flete como renglón propio, así
que toda orden sale con dos y toda factura los lleva a los dos. El vino y el
flete van a cuentas distintas, y por eso son renglones distintos: si el ítem
falta, el envío a los libros falla diciéndolo.

Lo que el socio paga de envío es la tarifa del transportista para su propia
dirección, menos lo que pone su nivel: nada en The Tasting Case, $20 en The
Founder's Reserve, $40 en The Full Case. Así que dos socios del mismo nivel
pagan envíos distintos, y la bodega le pone lo mismo a cada uno.

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

> **No está disponible para esta bodega, y no es un interruptor que alguien
> pueda encender.** `wineclub@warmspringscellar.com` es una **casilla
> compartida**. Una casilla compartida no tiene contraseña y no puede iniciar
> sesión, así que no hay nada que poner en `SMTP_PASSWORD`. Todo lo que sigue
> describe una casilla común con licencia y queda para el día que el club use
> una. **Andá a §8.3.**
>
> El error, si alguien lo intenta igual, es `535 5.7.3` — el inquilino
> rechazando el inicio de sesión — y se parece exactamente a una contraseña
> equivocada. No lo es.


```
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=wineclub@warmspringscellar.com
SMTP_PASSWORD=<la contraseña de esa casilla>
MAIL_FROM=wineclub@warmspringscellar.com
MAIL_REPLY_TO=wineclub@warmspringscellar.com
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

**Es el único camino para esta bodega.** `wineclub@warmspringscellar.com` es
una casilla compartida, así que la §8.2 queda cerrada: no hay contraseña que
poner en ninguna parte.

Y es también el camino que dura. El cronograma de Microsoft, revisado en enero
de 2026, deja la autenticación básica para envío SMTP funcionando hasta
**diciembre de 2026**; después queda desactivada por defecto en los tenants
existentes —un administrador todavía puede volver a encenderla— y no
disponible en los nuevos, y la fecha de retiro definitivo se anuncia en la
segunda mitad de 2027. Contra OAuth2 no hay ningún aviso de retiro. Así que
esto no es un rodeo por no tener contraseña: es la configuración a la que el
club debería llegar de todos modos.

El nombre del permiso, el scope del token y la cadena SASL que manda el
adaptador están verificados contra las dos páginas de referencia de Microsoft,
*Authenticate an IMAP, POP or SMTP connection using OAuth* y *Enable or
disable authenticated client SMTP submission*. Coinciden exactamente.

**Dos cosas que esas páginas no resuelven, y que esta bodega sí tiene:**

* **Si una casilla compartida sin licencia puede hacer esto.** Ninguna de las
  dos lo dice. Las casillas compartidas no llevan licencia por defecto; las
  páginas no declaran que SMTP AUTH exija una, pero tampoco la eximen. Si todo
  lo de abajo está bien y el envío sigue rechazado, asignale una licencia de
  Exchange Online a esa casilla y probá de nuevo. Es lo primero que hay que
  probar, no lo último, y se hace en el centro de administración: no es un
  problema de código.
* **Una casilla compartida con un token de aplicación.** La instrucción de
  Microsoft para casillas compartidas —poner la dirección de la casilla en el
  campo `user=` de SASL— está en la mitad *delegada* de su página, donde hay
  una persona que inició sesión. No la repite para credenciales de cliente. El
  adaptador hace exactamente eso con `SMTP_USER`, y es la única forma que
  puede funcionar, pero tomalo como razonado y no como citado.

Se hace una vez, y hace falta un administrador del tenant. **Abajo aparecen
tres identificadores distintos y no son intercambiables**; cada paso dice cuál
quiere.

Los pasos 1 a 3 se hacen en el portal de Entra. **Los pasos 4 y 5 no tienen
equivalente en el portal**: la documentación de Microsoft dice que el service
principal *debe* registrarse por Exchange Online PowerShell y no da ninguna
ruta web para ninguno de los dos comandos, así que no hay una opción escondida
en el centro de administración que valga la pena buscar. El paso 6 sí tiene una
ruta web documentada, pero sólo para cuentas que aparecen en **Users → Active
users**, donde una casilla compartida no figura. Hacé los pasos 4, 5 y 6 en una
sola sesión y el problema no existe.

Esa sesión no necesita instalar nada. Abrí **shell.azure.com**, elegí
PowerShell, y:

```powershell
Install-Module ExchangeOnlineManagement -Scope CurrentUser -Force
Connect-ExchangeOnline -Organization warmspringscellar.com -Device
```

`-Device` imprime un código para ingresar en **microsoft.com/devicelogin**,
porque una consola dentro del navegador no puede abrir la ventana de inicio de
sesión habitual. Sin eso, una cuenta con MFA es justamente donde se traba un
intento hecho sólo desde la web. Microsoft no documenta Cloud Shell para esto,
así que tomalo como una comodidad y no como un camino soportado: las versiones
recientes del módulo quieren PowerShell 7.6 o posterior (mirá `$PSVersionTable`
primero), y si el inicio de sesión falla con un error de WAM hace falta una
sesión *nueva* con `-DisableWAM`, porque reintentar en la misma sesión, una vez
fijado el contexto de autenticación, no hace nada. PowerShell 7 instalado
localmente funciona igual —en macOS y Linux además de Windows— y no necesita
`-Device`.

1. **Entra ID → App registrations → New registration.** Nombre: `Warm Springs
   Cellar — club mail`. Sin redirect URI: esta aplicación no inicia sesión
   como nadie. De la página Overview guardá el **Application (client) ID** y
   el **Directory (tenant) ID**.
2. **Certificates & secrets → New client secret.** Copiar el *Value* —no el
   Secret ID— en el momento: después no se vuelve a mostrar. Anotar también la
   fecha de vencimiento, porque el día que vence el correo deja de salir.
   Microsoft permite 24 meses como máximo, así que esto va al calendario: no
   es algo que se haga una sola vez.
3. **API permissions → Add a permission → APIs my organization uses →
   Office 365 Exchange Online → Application permissions → `SMTP.SendAsApp`**, y
   después **Grant admin consent**. Sin el consentimiento el token sale igual y
   el servidor lo rechaza, que es la forma más confusa de fallar.
4. **Registrar la aplicación en Exchange**, en Exchange Online PowerShell:

   ```powershell
   Connect-ExchangeOnline -Organization warmspringscellar.com

   New-ServicePrincipal -AppId <Application (client) ID> `
       -ObjectId <Object ID de Enterprise applications> `
       -DisplayName "Warm Springs Cellar - club mail"
   ```

   > **El error que cuesta una tarde.** Ese `-ObjectId` es el de **Entra ID →
   > Enterprise applications →** esta app **→ Overview**, y *no* el Object ID
   > de su página de **App registrations**. Las dos pantallas son casi
   > idénticas. Las palabras de Microsoft: *"Using the incorrect Object ID
   > will cause an authentication failure"*. La falla no menciona ningún id:
   > llega como `535 5.7.3`, que parece un problema de credenciales y no lo
   > es.

5. **Darle la casilla a ese principal**, en la misma sesión:

   ```powershell
   $sp = Get-ServicePrincipal -Identity "Warm Springs Cellar - club mail"
   Add-MailboxPermission -Identity wineclub@warmspringscellar.com `
       -User $sp.Identity -AccessRights FullAccess
   ```

   Leé el id con `Get-ServicePrincipal` en lugar de pegarlo: el valor que
   quiere `Add-MailboxPermission` es un **tercer** id, distinto otra vez de
   los dos del paso 4. Microsoft lo advierte: *"Different IDs are used during
   creation of the Exchange service principal and also later when granting
   mailbox permissions"*. Dejando que PowerShell lo lleve, no hay nada que
   elegir.

   `FullAccess` es lo que la página documenta para este flujo. Si algún día el
   club necesita mandar *desde otra* dirección que la que usa para
   autenticarse, eso es `Add-RecipientPermission` y es otro permiso.

6. **Habilitar SMTP AUTH para esa casilla.** Este paso es fácil de pasar por
   alto y es la razón más probable de que una configuración correcta siga sin
   mandar:

   ```powershell
   # ¿Cómo está hoy la configuración de toda la organización?
   Get-TransportConfig | Format-List SmtpClientAuthenticationDisabled

   # Encenderlo para esta casilla sola, diga lo que diga esa configuración.
   Set-CASMailbox -Identity wineclub@warmspringscellar.com `
       -SmtpClientAuthenticationDisabled $false
   Get-CASMailbox -Identity wineclub@warmspringscellar.com |
       Format-List SmtpClientAuthenticationDisabled
   ```

   El valor por casilla tiene precedencia sobre el de la organización: `$null`
   significa *seguir a la organización*, mientras que `$true` o `$false` la
   sobreescriben. **No toques `Set-TransportConfig`.** La recomendación de
   Microsoft es dejar SMTP AUTH apagado en toda la organización y encenderlo
   sólo en las casillas que lo necesitan, que es exactamente lo que hace el
   comando de arriba.

   Si el tenant tiene **security defaults** activado, SMTP AUTH ya está
   apagado para toda la organización; el `$false` por casilla es lo que pasa
   por encima de eso. Aparte, una **authentication policy** que desactive la
   autenticación básica para SMTP bloquea SMTP AUTH "even if you enable the
   settings" —Microsoft no dice si eso alcanza también a los clientes OAuth—,
   así que si todo esto está bien y la autenticación sigue rechazada, buscá
   una política de ese tipo antes de sospechar del código.

7. **En `.env`:**

   ```
   SMTP_HOST=smtp.office365.com
   SMTP_PORT=587
   SMTP_USER=wineclub@warmspringscellar.com
   SMTP_PASSWORD=
   SMTP_OAUTH_TENANT_ID=<Directory (tenant) ID>
   SMTP_OAUTH_CLIENT_ID=<Application (client) ID>
   SMTP_OAUTH_CLIENT_SECRET=<el Value del paso 2>
   ```

   `SMTP_USER` **se deja puesto**. El token dice qué aplicación manda, nunca
   desde qué casilla; la casilla hay que nombrarla igual. Dejá
   `SMTP_OAUTH_SCOPE` en su valor por defecto: el adaptador pide
   `https://outlook.office365.com/.default`, que es el recurso que espera el
   envío SMTP. Un token de Microsoft Graph es un token válido que el endpoint
   de SMTP rechaza, y ésa es otra hora de confusión.

Después reiniciá y abrí **`/admin/mail`** en el escritorio. Lee la
configuración sin tocar la red, y ofrece una prueba de conexión que conecta,
cifra, autentica y corta sin mandar nada. Lo que querés ver del servidor es
`235 2.7.0 Authentication successful`; la falla a reconocer es
`535 5.7.3 Authentication unsuccessful`, y a esta altura sus causas más
probables son, en orden: el paso 6 sin hacer, el Object ID equivocado en el
paso 4, o el consentimiento del paso 3 sin otorgar.

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

### 8.5 Fuera de Microsoft 365: un proveedor transaccional

Las tres opciones anteriores terminan en las mismas dos puertas: un
administrador del tenant, y una casilla que no tiene contraseña. Cuando no hay
ninguna de las dos, el servicio no necesita nada de Microsoft: habla SMTP a
secas, así que cualquier proveedor que dé credenciales SMTP entra tal cual. Sin
tocar código: cuatro valores y reiniciar.

**SMTP2GO** es lo que usa el club. Gratis a este volumen, sin tarjeta, sin
vencimiento.

```
SMTP_HOST=mail.smtp2go.com
SMTP_PORT=587
SMTP_USER=<un usuario SMTP creado en el panel, NO el del login>
SMTP_PASSWORD=<la contraseña de ese usuario>
SMTP_STARTTLS=true
MAIL_FROM=wineclub@warmspringscellar.com
MAIL_REPLY_TO=wineclub@warmspringscellar.com
```

Dejá todas las `SMTP_OAUTH_*` vacías: un proveedor de token tiene precedencia
sobre la contraseña y acá no se quiere ninguno. Si el host bloquea el 587,
SMTP2GO también atiende STARTTLS en 2525 y 8025; 465, 8465 y 443 son TLS
implícito, que este servicio no habla.

Las respuestas no cambian. SMTP2GO sólo lleva el correo saliente; el entrante
sigue llegando a la casilla compartida de Microsoft 365 como siempre.

**El DNS es la parte que hay que hacer bien, y es menos trabajo del que
parece.** Verificá el dominio en el panel del proveedor y publicá los CNAME que
te dé: un selector DKIM y un return-path. **No agregues ningún `include:` al
registro SPF.** El SPF se evalúa contra el remitente del sobre y no contra la
dirección del From, y el CNAME de return-path hace que ese remitente del sobre
sea un subdominio de este dominio apuntado al proveedor. El `-all` de la §8.6
se evalúa contra el registro de ese subdominio, nunca contra el del raíz, así
que no se le aplica.

Cómo se ve un resultado correcto, tomado del primer mensaje que salió por este
camino. Vale la pena guardarlo, porque acá pasan tres cosas distintas por tres
razones distintas:

```
spf=pass    smtp.mailfrom=em1196132.warmspringscellar.com
dkim=pass   (signature was verified) header.d=warmspringscellar.com
dmarc=pass  action=none header.from=warmspringscellar.com
compauth=pass reason=100
```

El remitente del sobre es un subdominio nuestro, así que el SPF pasa **y**
alinea. El DKIM está firmado con `d=warmspringscellar.com` y no a nombre del
proveedor, que es lo que compra la verificación del dominio. El DMARC pasa
entonces porque cualquiera de los dos alinea con el dominio del From. Y
`compauth=pass reason=100` es la mejor respuesta de Microsoft: autenticación
explícita en vez de una conjetura. Lo que hace legítimo el correo para este dominio es la
firma DKIM, y eso es un CNAME. Agregar el include sería innecesario y gastaría
una de las diez consultas DNS que permite un SPF. Agregar un *segundo* registro
SPF rompería todo lo que hoy funciona.

Verificá el dominio antes del primer despacho: hasta que pase, el envío queda
limitado a 25 por hora, y un aviso previo a cien socios tardaría cuatro horas
en salir.

**El techo.** El club manda tres mensajes por socio en cada despacho —el aviso,
el comprobante y el aviso de envío— y son tres despachos al año. El total anual
es irrisorio; lo que aprieta es el *día* más cargado, porque los límites del
plan gratuito son 1.000 por mes y 200 por día:

| Límite | Socios que permite | Por qué |
| --- | --- | --- |
| 200/día | ~100 | aviso y cobros el mismo día son dos por socio de golpe |
| 200/día | ~200 | aviso un día y cobros al siguiente es uno por socio |
| 1.000/mes | ~333 | tres por socio en el mes de despacho |

Mandar los avisos y correr los cobros ya son dos acciones separadas en el
escritorio, así que hacerlas con un día de diferencia duplica el techo sin
costo — y avisa a la gente antes de cobrarle la tarjeta, que es mejor práctica
de todos modos.

Pasados unos doscientos socios: el plan Starter de SMTP2GO, o Amazon SES a
US$0,10 por mil sin suscripción, que a este volumen son centavos al año pero
pide cuenta de AWS y un trámite para salir del sandbox.

**Algo que quedó sin confirmar.** Las fuentes se contradicen sobre qué hace una
cuenta gratuita al llegar a 1.000 en el mes: el blog del proveedor dice que
encola los mensajes hasta que renueva el ciclo, y dos reseñas de terceros dicen
que el envío se detiene. Que se detenga en un mes de despacho significa que
alguien a quien Stripe ya le cobró nunca recibe su comprobante. Conviene
preguntarlo a su soporte antes del primer despacho en vez de enterarse.

### 8.6 Que el correo llegue, no solo que salga

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

### 8.7 Probar antes de prometer: la pestaña **Mail**

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
> mensaje llegue. Eso es SPF, DKIM y el filtro de spam del otro lado (§8.6), y
> solo lo contesta un envío real a una dirección real.

Si algo no cierra: `docker compose logs api | grep -i smtp`. El adaptador
registra el rechazo tal como lo dijo el servidor, con el código incluido.

### 8.8 La campaña, que no es esto

Todo lo anterior es el correo propio del club: un mensaje, a una persona,
porque algo pasó con su orden. **La campaña es otra cosa** — la misma carta a
varios cientos de personas que todavía no son socias — y no sale por nada de
eso, ni por este servicio. Sale desde una **plataforma de mailing**, que
tiene la lista, manda la carta e informa qué pasó.

El plan, el proveedor y los motivos están en **`mail-templates/CAMPAIGN.md`**
y no se repiten acá. En el escritorio importan cuatro cosas:

1. **La pestaña Campaigns no envía ni cuenta.** Entregados, aperturas, bajas:
   eso es la pantalla de la plataforma. Lo que una tanda realmente
   *produjo* se cuenta acá, en **Applications**, filtrando por referral: el
   link de cada tanda lleva su propio `src`, y ese es el único número que
   dice si la carta funcionó.

2. **Antes de subir una tanda, restale la lista.** Bajá el CSV de la pestaña
   Campaigns y sacá esas direcciones del archivo primero. La plataforma no
   puede hacerlo: no sabe quién le pidió a Deerfield que dejara de escribirle
   hace años, ni sabe de la persona que llamó por teléfono la semana pasada.

3. **Después de una tanda, traete las bajas de vuelta.** Exportá las bajas y
   los rebotes duros de la plataforma y pegalos en la pestaña Campaigns. Si
   nadie lo hace, esas bajas viven solamente adentro de una suscripción, y el
   día que la bodega cambie de proveedor desaparecen — mientras que la
   obligación de respetarlas, no.

4. **Darse de baja del mailing no es darse de baja del club.** Un socio que
   sale de las cartas sigue recibiendo sus facturas y sus avisos de envío:
   esos son comprobantes, no publicidad, y la lista de supresión no los toca.
   El socio que quiere *irse del club* es una baja en la pestaña **Members**,
   que es otra pantalla y otra conversación.

El resto —qué proveedor, qué hay que decirle antes de subir nada, las tandas
y qué mirar entre una y otra— está en `CAMPAIGN.md` y en
`mail-templates/INVITATIONS.md`.

---

## 9. Cuando algo no cierra

| Síntoma | Dónde mirar |
| --- | --- |
| El socio no recibió la invitación | ¿Hay aviso de `SMTP_HOST` arriba? Si sí, el mail está en `/srv/var/outbox`. Copiale el link desde la ficha. |
| El servidor contesta `535 5.7.139` | Microsoft 365 apagó la autenticación básica para esa casilla. No es la contraseña: hay que pasar a OAuth2 (§8.3). |
| El servidor contesta `535 5.7.3` | El inquilino rechazó el inicio de sesión en sí. SMTP autenticado apagado para esa casilla (centro de administración → el usuario → Correo → Administrar aplicaciones de correo), apagado para todo el inquilino, o Security Defaults / Acceso condicional bloqueando inicios heredados. **Una casilla compartida no tiene contraseña y no puede iniciar sesión**, así que si la dirección del club es una, OAuth2 (§8.3) es el único camino, no un interruptor que haya que buscar. |
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
      dirección de afuera del tenant (§8.7). En Microsoft 365, con OAuth2
      (§8.3) antes que con contraseña
- [ ] SPF, DKIM y DMARC publicados en `warmspringscellar.com` (§8.6)
- [ ] `SHIPPABLE_STATES` revisada contra los permisos reales de la bodega
- [ ] QuickBooks: app creada, URI de retorno registrada, el ítem creado
      y **una factura enviada a una empresa sandbox** antes de pasar a
      `production`
- [ ] `QBO_ENVIRONMENT=production` el mismo día que Stripe pasa a vivo — el
      sistema avisa si uno está vivo y el otro en sandbox, pero mejor no
      llegar ahí
- [ ] Respaldo del volumen `club-data` automatizado

Una cosa que **no** está implementada y conviene tener presente: los límites
de volumen por cliente y por estado que vienen con los permisos DTC. Está
anotada en `api/FLOW.md`.

El stock ahora sí se controla —un despacho compuesto levanta órdenes solo por
los socios para los que hay botellas (§6.1)— pero solo contra lo que alguien
haya contado en la pestaña **Cellar**. Es el estante del club y no el registro
de producción de la bodega, así que **hay que contar antes de componer el
primer despacho real**.
