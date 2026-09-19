# La centralita · un agente de voz que atiende a los proveedores

Un proveedor llama preguntando por qué no ha cobrado. Mónica coge el
teléfono, lo identifica, le pide el número de pedido, **consulta la decisión
que ya tomó el pipeline** y le contesta — o decide no contestarle.

Esto **no es parte del pipeline**. Es un sistema externo: proceso aparte,
puerto aparte, `alberto.db` abierta en solo lectura. Ni una línea de
`alberto/` cambia. Ése es justo el argumento: la decisión y su evidencia
quedan en la base con tanta estructura que un consumidor que no escribimos
nosotros puede cogerlas y convertirlas en una conversación.

## Arrancar

```bash
uv sync --locked --extra worker --extra backend

make export              # extrae, evalúa y congela en phone_calls/datos/
make centralita          # http://localhost:8011  (precalienta la voz solo)
```

`make export` es el único paso que necesita red y credenciales, y se hace
**antes** de la demo. Durante la llamada no hay nada que se pueda caer.

En **Safari o Chrome**, desde **esta misma máquina**. En una IP de red
(`http://192.168.x.x`) el navegador no da acceso al micrófono porque no es un
contexto seguro; `localhost` sí lo es.

## La voz

Tres niveles, y se cae de uno al siguiente sin que nadie lo note:

1. **ElevenLabs**, si hay `ELEVENLABS_API_KEY` en el entorno. Voz neural
   castellana — **Inés**, peninsular, *calm, friendly*. Es la única que no
   suena a contestador, y el agente se presenta con su nombre.
2. **`say` de macOS** (Mónica). Si no hay clave, es la voz. Y si la hay pero
   ElevenLabs falla a mitad de llamada —cuota agotada, sin red—, es el
   respaldo antes de rendirse: lo que Mónica ya tenía cacheado sale al
   instante.
3. **El navegador**, si falla todo lo demás: el servidor manda `audio: null`.
   `--sin-audio` lo fuerza.

El audio se sintetiza en el servidor y se cachea en `phone_calls/.voz/` por
hash de *voz + texto*. Como el hash lleva la voz dentro, los dos motores
conviven sin pisarse; y como **no** lleva el modelo, lo precalentado con el
modelo bueno lo encuentra igual un turno improvisado que pide el rápido.

```bash
# la clave va en .env (ya está en .gitignore); make la carga solo
echo 'ELEVENLABS_API_KEY=sk_...' >> .env

make voces                                    # precalienta todo lo que dirá
./venv/bin/python -m phone_calls.voz --listar # motores, voces y cuál elige
./venv/bin/python -m phone_calls.voz --probar # audiciona Marina, Sofía e Inés
```

Medido: precalentar con `multilingual_v2` son ~2,3 s por frase — medio minuto
una sola vez. En la llamada, lo cacheado va en **1-30 ms**; lo improvisado se
sintetiza con `flash_v2_5` en **~0,4-0,5 s**, menos que el `say` local en
frío, y encima lo tapa la muletilla *"un momento, que lo miro"*.

**Cuota — léelo antes de cambiar de voz.** La clave actual tiene un **tope
propio de 1.000 caracteres**, aparte del de la cuenta, y precalentar una voz
son ~1.000. Se agotó a mitad de precalentar Inés: 7 frases de 11, y las que
faltaban eran justo el IBAN y el importe. Cuando `make voces` sale con
`"ok": false` ahora dice el motivo (`quota_exceeded`, `sin red`…) y qué
frases se quedaron fuera. Para subir el tope: elevenlabs.io → Settings → API
Keys → la clave → *character limit*. La clave tampoco tiene `user_read`, así
que el código no puede leer cuánto queda. `make voces` antes de subir al
escenario, y no cambiar de voz el día de la demo.

Si prefieres la voz local: `--voz Mónica`. La Mónica de serie son 1,2 MB; hay
una de 159 MB en Ajustes → Accesibilidad → Contenido hablado → Gestionar voces
y el ranking la prefiere sola.

## De dónde salen los datos

El motor de decisión vive sobre Supabase y es la única fuente de verdad. Pero
no sirve para atender una llamada, por dos razones independientes:

- **De red**: el puerto de Postgres está cerrado en la sala, y una
  conversación no puede depender de que el wifi aguante entre turno y turno.
- **De diseño**: el motor **no se puede consultar por número de pedido**. No
  hay columna, ni índice — el pedido vive dentro de un artefacto JSON en
  Storage, alcanzable solo si ya sabes el id del registro. Y el número de
  pedido es lo único que el proveedor dice en voz alta.

Así que `exportar.py` hace el trabajo antes, una vez:

| Etapa | Fuente | ¿Red? |
|---|---|---|
| extracción | PDF → campos, con Helmcode. Se persiste en un repositorio **local**: `Pipeline` acepta que se le inyecten repositorio y almacén, así que no hace falta Postgres | sí |
| maestro | proveedores y pedidos del Excel de La Caja | no |
| asientos ERP | el blob comprimido dentro de `caja/alberto_erp.py` — trae `asiento_id`, `fecha_registro` e `importe_esperado`, que el cliente HTTP del repo descarta | no |
| decisión | el motor de reglas, en proceso | no |

El resultado queda en `phone_calls/datos/<PEDIDO>.json`, indexado por lo que
se dice por teléfono.

**La política se ata a los `reason_codes` del motor**, no al texto del motivo
ni al número de regla: los códigos son contrato, las frases no. Y las reglas
se renumeraron al cambiar de motor — el `R2` de hoy son duplicados y estado
del ERP, no el importe. Mapear por número daría respuestas falsas sin que
nadie se entere.

Una consecuencia de negocio que el contrato exige respetar: el motor marca
`payment_authorized: false` siempre y llama a su veredicto
`preliminary_decision`. La centralita cuenta estados, nunca promete un pago.

Sin navegador, la llamada entera por teclado:

```bash
./venv/bin/python -m phone_calls.guion
```

## Los tres minutos

| | Se dice | Y en pantalla |
|---|---|---|
| **1. «Suministros Levante, pedido 0475»** | *Esa factura ya está pagada. Asiento AS-00475, 24 de mayo, 398,04 €.* | las 5 reglas, 4 en verde y R5 en rojo |
| **2. «Electricidad Montcada, el 493»** | *Usted factura 12.874,40 y el pedido está aprobado por 12.847,40. Hay 27 € de diferencia.* | la evidencia de R2, con el desvío |
| **3. «Transportes Guadaira, el 0494»** | *Tenemos una incidencia con sus datos de cobro que no le puedo tratar por teléfono. Le llamamos al número de su ficha.* | **el panel rojo con los dos IBAN que acaba de callarse** |

La tercera es la que se llevan a casa. El agente tiene delante el IBAN de la
factura, el del maestro y cuál de los dos es el bueno, y **no dice ninguno**:
quien llama no está verificado, y darle el número bueno es el guion exacto de
un fraude de cambio de cuenta. Devuelve la llamada al único canal que ya
estaba verificado.

Ese mismo IBAN falso aparece en **dos** facturas de Guadaira (`PO-2026-0494` y
`PO-2026-0701`): no es un dedazo, es un patrón. Y el panel enseña además lo
que Alberto apuntó a mano en el Excel: *«los de Guadaira siempre llaman los
viernes»*.

Si sobra tiempo, la cuarta: **`PO-2026-0007`**, donde las cinco reglas pasan y
aun así escala, porque Alberto lo marcó a mano. Es la prueba de que se entra
por el resultado y no por las reglas.

## Lo que se dice y lo que no

| Situación | Se dice | Se calla |
|---|---|---|
| conforme | sí, con vencimiento según sus condiciones | |
| ya pagada | asiento, fecha e importe | |
| importe que no cuadra | los dos importes y el desvío | |
| IVA mal | base, tipo y esperado | |
| falta un dato | qué reenviar | |
| **IBAN ≠ maestro** | sólo que hay una incidencia | **los dos IBAN y cuál vale** |
| **el pedido es de otro** | sólo que no le corresponde | **de quién es** |
| NIF no dado de alta | que pase por compras | |
| en revisión manual | que hay una persona con ello | la nota interna |

Y nunca se pronuncian `PAGAR`, `NO_PAGAR` ni `ESCALAR`: son vocabulario
interno. El proveedor oye consecuencias, no veredictos — entre otras cosas
para que la voz no pueda contradecir al panel de al lado.

`tests/test_llamadas.py` recorre **las 500 facturas** comprobando que ningún
valor retenido aparece en lo que se dice ni en lo que se pinta. La política no
es una promesa del README; falla en pytest si alguien la rompe.

## Cómo está montado

| Fichero | Qué |
|---|---|
| `exportar.py` | extrae, evalúa con el motor y congela el expediente por pedido |
| `consulta.py` | la frontera con `datos/`. Pedido → expediente. Solo lee |
| `guion.py` | parsers de voz, política de divulgación y máquina de estados |
| `servidor.py` | HTTP en 8011: sirve la página y `POST /api/turno` |
| `voz.py` | síntesis con `say`, caché por hash y precalentado |
| `estatico/` | la pantalla y la Web Speech API |
| `guiones_demo.json` | las llamadas grabadas — y el fixture de los tests |

**El servidor no guarda sesiones.** El estado va y vuelve en el cuerpo de cada
petición, así que no hay cerrojos ni sesiones zombis, recargar no rompe nada y
`curl` puede conducir una llamada entera:

```bash
curl -s localhost:8011/api/turno -d '{"estado":{},"oye":[]}'
# y se pega el "estado" que devuelve en la siguiente
curl -s localhost:8011/api/turno \
     -d '{"estado":{"paso":"IDENTIFICA","fallos":0},"oye":["Transportes Guadaira"]}'
```

**Las llamadas grabadas no son un vídeo.** `guiones_demo.json` sólo contiene
transcripciones de entrada; el estado, la consulta a la base, lo que se dice y
el panel salen del servidor igual que en vivo, porque pasan por la misma
función que el micrófono. Y están sucias a propósito (`"peo 2026 cero cuatro
siete cinco"`, `"transporte guadaira"` en singular) para que ejerciten el
parser tolerante en vez de esquivarlo.

## Límites, a propósito

- **No se puede pedir el número de factura.** `num_factura` es NULL en las
  500: no hay nada contra lo que buscarlo. Se pide el número de pedido, que
  además es lo que une factura, ERP y maestro. Si el proveedor da un número de
  factura, el agente reformula.
- **Se identifica por razón social**, no por CIF deletreado: el reconocedor
  transcribe «Transportes Guadaira» limpiamente y destroza «be cuarenta y seis
  uno cero dos…». El CIF lo dice el agente, para que el proveedor lo confirme.
- **Lo que se dice y lo que se escribe no son el mismo texto.** El
  sintetizador lee `12874.40` como «punto cuatro cero» y `AS-00475` como
  «guion cero cero»; en voz va «doce mil ochocientos setenta y cuatro euros
  con cuarenta» y «A. S., cero cero cuatro, siete cinco». Por eso cada
  respuesta lleva `decir` y `texto` por separado, y por eso los textos
  hablados van **con tildes**: sin ellas el motor dice *nuMEro* y *cenTImos*,
  que es exactamente lo que suena a robot. Un test lo comprueba sobre las 500.
- **El reconocimiento de Chrome necesita red** (sube el audio a Google); el de
  Safari es de Apple y en macOS 26 va en el dispositivo. La voz es local en
  los dos casos y no se cae. Si el micro falla, se escribe y el agente sigue
  hablando.
- **Safari devuelve una sola transcripción** por turno, no cinco. El parser
  está diseñado para las alternativas múltiples de Chrome y acierta algo menos
  con una sola.
- **Sólo se contesta del pedido de quien llama.** Se comprueba antes de decir
  nada: el expediente lleva importes y estado de pago de un tercero.
- Al tercer intento fallido de entender, pasa a una persona. Un agente en
  bucle delante de un jurado es peor que no tener agente.

## Antes de la demo

- [ ] **Prueba sin Bluetooth.** Con AirPods, en cuanto el reconocedor abre el
      micrófono macOS conmuta el perfil a HFP/SCO —8 o 16 kHz mono con códec
      de teléfono— y la salida se vuelve metálica. Es lo primero que hay que
      descartar si suena mal, y cuesta 30 segundos
- [ ] `ELEVENLABS_API_KEY` en `.env` y **`make voces` pasado** — que la cuota no se acabe en el escenario
- [ ] Permiso de micrófono concedido **la noche antes**, y no borrar el perfil
- [ ] macOS → Privacidad y seguridad → Micrófono → el navegador que uses
- [ ] **Auriculares** para la demo en vivo: el reconocedor abre su propio
      stream y la cancelación de eco no se le aplica, así que el micro oye al
      sintetizador. El código hace half-duplex estricto para eso, pero los
      auriculares lo resuelven gratis
- [ ] Hotspot del móvil probado
- [ ] `alberto.db` con la run hecha, y el puerto 8011 libre

**Trampa al grabar en Mac**: QuickTime captura el micrófono pero no el audio
del sistema sin BlackHole. Con auriculares, el sintetizador **no sale en el
vídeo**. Para el vídeo de respaldo: altavoces y QuickTime con el micro del
portátil.
