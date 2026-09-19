# TODO

Estado: el pipeline va de PDF a `outcomes.jsonl` verificado en ~10 s.
`431 PAGAR · 49 ESCALAR · 20 NO_PAGAR` · 46 tests en verde · 1.489 líneas.

Cada tarea es **un commit**. Marca la casilla al mergear.
`Ⓟ` = puntos de rúbrica que compra · `👤` = dueño sugerido · `⏱` = tamaño.

**El backlog es la rúbrica.** Lo que no compra puntos ni protege la puerta de
entrada va al final, o no se hace.

| Criterio | Puntos |
|---|---:|
| Producto, arquitectura y ADRs | 35 |
| Trazabilidad y observabilidad | 20 |
| Escalabilidad y coste | 25 |
| Resiliencia y recuperación | 10 |
| Calidad de ejecución | 10 |
| Bonus | +10 |

Recordatorio: **acertar no da puntos.** La validación es binaria — si se falla no
se opta al premio, pero acertar no puntúa. Perseguir el 100% es robarle tiempo a
los 45 puntos de trazabilidad y coste.

---

## Lo que ya está construido (y cómo usarlo)

Si acabas de llegar al repo, lee esto antes de coger una tarea. Todo lo de abajo
funciona hoy y está cubierto por los 46 tests.

### El camino completo

`alberto todo` encadena los verbos en orden. Cada uno es **idempotente** y deja
su estado en `alberto.db` (SQLite), así que se puede parar y reanudar por donde
iba, y reprocesar sin borrar nada.

| Verbo | Módulo | Qué deja en la BD |
|---|---|---|
| `ingesta` | [alberto/ingesta.py](alberto/ingesta.py) | `documentos`: `doc_id` = sha256 del fichero, `file_id` en NFC, y si tiene capa de texto — decidido por contenido, nunca por el nombre |
| `snapshot` | [alberto/erp/snapshot.py](alberto/erp/snapshot.py) | `snapshots_erp` + `asientos`: los 516 asientos del ERP, versionados |
| `maestro` | [alberto/maestros.py](alberto/maestros.py) | `maestro_versiones`, `proveedores`, `pedidos_excel`, `notas` |
| `extrae` | [alberto/extraccion/campos.py](alberto/extraccion/campos.py) | `extracciones`: campos, vía usada, si cuadra la aritmética, coste y latencia |
| `decide` | [alberto/reglas/motor.py](alberto/reglas/motor.py) | `decisiones`: resultado, motivo y el veredicto de cada regla con su evidencia |
| `emite` | [alberto/salida.py](alberto/salida.py) | escribe `outcomes.jsonl` y **lo verifica** antes de darlo por bueno |

Y dos verbos de lectura: `estado` (en qué punto está cada documento) y
`explica <trozo del nombre>` ([alberto/explica.py](alberto/explica.py)), que
sigue UNA factura de principio a fin. `explica` es la herramienta de depuración
y a la vez el minuto 4-8 de la defensa; la web de T06 es esa misma lógica
proyectada, no una reimplementación.

### Los tres invariantes congelados

Están en [alberto/contratos.py](alberto/contratos.py) y no se tocan sin acuerdo
de los cuatro:

1. **`Decimal` en todo importe.** Nunca `float` — el Excel ya trae
   `9299.620000000001` de regalo.
2. **`file_id` normalizado a NFC** al escribir el JSONL. macOS devuelve NFD al
   listar directorios y el verificador privado compara cadenas: un NFD
   silencioso suspende la entrega.
3. **`doc_id = sha256(fichero)`** como única clave de idempotencia.

Además, `Decision.__post_init__` hace imposible emitir `RECHAZAR` y exige motivo
al escalar. El vocabulario inválido no es un test: es un `ValueError`.

### Por qué se puede reprocesar sin miedo

La clave primaria de una decisión es
`(doc_id, norma_version, snapshot_erp, snapshot_maestro)`. Reprocesar **no
borra** la decisión anterior: añade otra. Por eso una sola consulta enseña qué
cambió entre dos normas o entre dos versiones del maestro, y por qué. Ese
mecanismo es el mismo para el lote 2, para la norma v4 y para el «Alberto cambia
un dato» del domingo.

### Las reglas son datos, no código

- [alberto/reglas/norma_v3.yaml](alberto/reglas/norma_v3.yaml) — **qué** se
  comprueba (las cinco reglas de la norma vigente).
- [alberto/reglas/politica.yaml](alberto/reglas/politica.yaml) — **qué se hace**
  con cada veredicto: `R1_nif_iban` y `R5_estado_erp` fallan a `NO_PAGAR`, el
  resto a `ESCALAR`; por defecto `PAGAR`.

La norma v4 del sábado es copiar el YAML, editarlo y relanzar `decide --norma v4`.
Si hay que tocar código para eso, el diseño ha fallado.

### Dónde está la verdad de cada dato

| Dato | Fuente autoritativa | Por qué |
|---|---|---|
| Estado del pedido (`PENDIENTE`/`PAGADA`) | **el ERP** | el Excel dice `ABIERTO` en los 516; el ERP dice que 9 están `PAGADA` |
| Proveedor, NIF, IBAN, condiciones | el Excel (maestro) | deduplicado: P007 aparece dos veces |
| Importe del pedido | el ERP, contra el **TOTAL** de la factura | medido sobre La Caja: nunca casa con la base |

### Estado medido ahora mismo

```
431 PAGAR · 49 ESCALAR · 20 NO_PAGAR        500 documentos, ~10 s, 46 tests en verde
471 por vía determinista (5,4 ms, 0 €) · 29 sin capa de texto (son imágenes)
ERP: 516 asientos = 507 PENDIENTE + 9 PAGADA
```

| | € | |
|---|---:|---|
| Aprobado | 2.407.583,91 | 431 facturas |
| **Bloqueado** | **60.034,52** | 20 facturas |
| En espera de un humano | 92.068,14 | 20 facturas · las otras 29 no tienen ni importe conocido |

Los 60.034,52 € bloqueados, por motivo: **16.159,92 €** de pedidos que el ERP ya
daba por `PAGADA` (pagos duplicados evitados, y cuadra al céntimo con los 9
asientos del ERP), **24.829,20 €** que iban a un IBAN que no es el del maestro, y
**19.045,40 €** de NIF que no existe con pedido que no consta.

Esas cifras salen de la BD, no de una estimación. Para reproducirlas:

```bash
sqlite3 alberto.db "SELECT result, count(*) FROM decisiones GROUP BY result"
sqlite3 alberto.db "SELECT motivo, count(*) FROM decisiones WHERE result!='PAGAR' GROUP BY motivo ORDER BY 2 DESC"
```

### Cómo levantarlo

```bash
git clone https://github.com/ikurotime/500-sombras-de-alberto.git caja
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt

# Terminal 1 — el bridge ERP de 2009, se queda abierto
cd caja && make erp                       # make erp-fast quita la latencia artificial

# Terminal 2
./venv/bin/python -m alberto.cli todo     # ~10 s, deja outcomes.jsonl verificado
./venv/bin/python -m alberto.cli explica 2026-07-08_P010     # IBAN que no cuadra
./venv/bin/python -m pytest tests -q -m "not lento"          # 39 tests, 0,1 s
```

Si tienes La Caja en otro sitio: `export ALBERTO_CAJA=~/ruta/a/500-sombras`.
Todos los verbos aceptan `--caja`, `--erp-url`, `--db` y `--lote`.

### Lo que NO está

Las 29 facturas que son imagen (hoy escalan con motivo, es T02), la web (T06),
el modelo de coste medido (T07) y cualquier cosa que envíe correo (T27).
`legacy/` es el primer prototipo: se conserva como referencia de qué no hacer,
**no se usa** y no se refactoriza.

---

## P0 · Sin esto no hay entrega

- [x] **T01 · `git init` y repo público** 👤P3 ⏱15m
  Crear el repo en GitHub **público** (el detector de stack de HackSpain se niega
  a escanear repos privados), `hackspain team repo <url>`, y que **cada uno haga
  su primer commit desde su propia cuenta vinculada**.
  *Hecho cuando:* el repo aparece en el feed y los 4 figuran en `git shortlog -sn`.

- [ ] **T02 · Visión para las 29 facturas que son imagen** 👤P2 ⏱2h
  `alberto/extraccion/vision.py` con `claude-opus-5` y `output_config.format`
  contra el esquema de `FacturaExtraida`. Registrar `coste_eur` y `latencia_ms`
  reales por documento. **Nunca aceptar el resultado sin pasar el filtro
  aritmético**: si no cierra, `ESCALAR` con motivo «documento ilegible».
  *Hecho cuando:* de las 29, las legibles cierran la aritmética y las de 60 dpi
  escalan con motivo, no con un número inventado. Ⓟ puerta + 25 (coste)

- [ ] **T03 · Ensayo en seco del lote 2 — ANTES DE LAS 18:00** 👤P1 ⏱1h
  El camino `--lote lote2` **no se ha ejecutado nunca**. Simularlo ya: partir las
  500 en 460 + 40, correr el flujo completo con `make erp-lote2`, y comprobar que
  `outcomes_lote2.jsonl` sale limpio.
  *Hecho cuando:* dos JSONL válidos y una regla nueva aplicada solo por YAML.
  **Cuando llegue el lote 2 de verdad no es momento de descubrir bugs aquí.**

- [ ] **T04 · `alberto entrega`** 👤P1 ⏱45m
  Prepara el repo de outcomes: genera los dos JSONL, copia el PDF, y **falla si
  hay cualquier cosa que no sean esos tres ficheros** en la raíz. Las bases
  prohíben subir código, credenciales o ejecutables.
  *Hecho cuando:* el comando se niega a preparar un directorio con un cuarto fichero.

- [ ] **T05 · Test de oro sobre el reparto de decisiones** 👤P3 ⏱30m
  Congelar el reparto actual en un fichero. Si alguien toca `politica.yaml` y 40
  decisiones cambian, que salte el test y se vea **cuáles**.
  *Hecho cuando:* cambiar un mapeo en la política rompe el test con un diff legible.

- [ ] **T28 · Blindar el camino del lote 2: tres fallos ya localizados** 👤P1 ⏱45m
  Van con T03, pero no hace falta ensayar a ciegas: los tres están leídos.
  1. [alberto/cli.py:64](alberto/cli.py#L64) — `next(a.caja.glob("*.xlsx"))` sin
     default. Si el directorio del lote 2 no trae Excel propio, `todo` revienta
     con `StopIteration` a las 18:05.
  2. [alberto/ingesta.py:31](alberto/ingesta.py#L31) — `ON CONFLICT(doc_id) DO
     NOTHING` no cubre `file_id`, que es `UNIQUE` en el esquema. Un fichero del
     lote 2 con nombre repetido y contenido distinto tumba la ingesta entera con
     `IntegrityError`.
  3. **El inverso, y es el peligroso:** un PDF del lote 2 byte-idéntico a uno del
     lote 1 se ignora en silencio y conserva `lote='lote1'` → **falta una línea
     en `outcomes_lote2.jsonl`** → suspende la validación sin que nadie lo vea.
  *Hecho cuando:* hay un test que mete un PDF duplicado y otro con nombre
  repetido, y los dos JSONL salen con el número de líneas correcto.

- [ ] **T29 · Que `emite` no pueda mentir** 👤P1 ⏱30m
  [alberto/salida.py](alberto/salida.py#L51): `verificar()` compara las líneas
  contra `escritos`, que es el número que acaba de producir la propia escritura.
  Si el deduplicado descarta un documento, el contador baja con él y la
  verificación devuelve `ok: true`. Y `f["result"] or "ESCALAR"` **escribe
  ESCALAR cuando no hay decisión**: lo reporta en `sin_decision` y no falla.
  El único control que nos separa de suspender la puerta binaria tiene hoy dos
  formas de mentir.
  *Hecho cuando:* cuenta contra los PDFs del disco, no contra sí mismo, y aborta
  con código distinto de 0 si `sin_decision` no está vacío.

- [ ] **T30 · Persistir las métricas del ERP** 👤P1 ⏱15m
  [alberto/pipeline.py:22](alberto/pipeline.py#L22) hace
  `{k: v for k, v in informe.items() if k != "metricas"}`: **descartamos a
  propósito** los contadores de reintentos `ORA-00600`, esperas 429 y relogins.
  Es la mejor evidencia de resiliencia que tenemos y hoy va a la basura.
  Guardarlos en `snapshots_erp` y enseñarlos en el parte de trabajo (T26).
  *Hecho cuando:* tras un `snapshot` se puede responder «reintentamos N veces y
  esperamos M segundos» con una consulta. Ⓟ 10 (resiliencia) por 15 minutos

- [ ] **T31 · Sacar de `decidir()` lo que debería ser norma** 👤P3 ⏱30m
  Dos grietas en la tesis «las reglas son datos», y la primera se rompe justo
  esta tarde:
  - [alberto/reglas/motor.py:144](alberto/reglas/motor.py#L144) —
    `self.norma["reglas"][2].get("iva_estandar")` es un **índice posicional**.
    Si la v4 inserta o reordena una regla, lee la regla equivocada en silencio.
  - El IVA no estándar, el marcado para revisión y el documento ilegible viven
    en `decidir()`, no como reglas: **no salen en la traza** como veredictos. Y
    `iva_no_estandar` dispara **0 veces** hoy, así que nos creemos cubiertos por
    código muerto.
  - Bonus barato: [motor.py:42](alberto/reglas/motor.py#L42) `self.hoy =
    date.today()` hace que una decisión dependa del reloj de pared sin quedar
    registrado. Irrelevante hoy, crítico si la v4 trae la regla de vencimiento
    (T23), que es el candidato más probable.
  *Hecho cuando:* las reglas se buscan por `id`, no por posición, y los tres
  chequeos aparecen en `reglas_json` con su evidencia.

---

## P1 · Los puntos gordos (45 de 100)

- [ ] **T06 · Web: panel, listado y traza** 👤P4 ⏱3h
  FastAPI + Jinja + HTMX. Tres vistas: panel con KPIs, listado filtrable por
  resultado y motivo, y **detalle de una decisión** — lo mismo que
  `alberto explica` pero proyectable. Reutilizar la lógica de
  `alberto/explica.py`, no duplicarla.
  *Hecho cuando:* se sigue una factura de punta a punta en pantalla.
  Ⓟ 20 + la demo + el vídeo

- [ ] **T25 · La pared de 500 baldosas** 👤P4 ⏱1h
  Una baldosa por documento, en cuadrícula, coloreada por resultado:
  **431 verdes · 49 ámbar · 20 rojas**. Es la portada de la web y la primera
  pantalla que ve el jurado: la escala y el reparto se entienden en dos segundos,
  antes de que nadie abra la boca.
  Al hacer clic, la baldosa abre el expediente de T06 — la misma vista de
  `alberto explica`, no otra pantalla. Leyenda con el recuento, y al pasar el
  ratón, el motivo.
  Los datos ya están: `SELECT result, motivo FROM decisiones JOIN documentos
  USING(doc_id)`. No hace falta tocar el pipeline.
  *Hecho cuando:* las 500 caben sin scroll en la pantalla de la sala y un clic en
  cualquiera lleva a su traza. Depende de T06. Ⓟ 20 + la demo + el vídeo

- [ ] **T26 · Albertito: ficha de empleado y parte de trabajo** 👤P4 ⏱1h30
  Dejar de presentar esto como un pipeline y presentarlo como lo que Maisa vende:
  **un trabajador digital** con un puesto, unos límites y un registro de lo que
  hizo. Es encuadre, no funcionalidad nueva, y es lo que separa «han hecho un
  script» de «han hecho un compañero para Alberto».

  **Ficha** (cabecera de la web, estática): nombre, puesto, de qué se ocupa,
  **qué NO hace**, y a quién escala. El golpe: *su manual de empleado es
  `alberto/reglas/norma_v3.yaml`* — enlazarlo y enseñarlo en crudo. El sábado a
  las 18:00 Alberto le actualiza el manual, no el código. Es el mejor minuto de
  arquitectura que podemos dar y no cuesta nada.

  **Parte de trabajo** (una fila por pasada, leído de `eventos` y `decisiones`):
  cuándo entró, cuántos documentos vio, cuánto tardó, cuánto costó, qué decidió,
  **qué escaló y por qué**, y de qué no se atrevió a opinar. Un trabajador que
  documenta sus propios límites es exactamente la tesis de la empresa, y de paso
  es observabilidad en el sentido de la rúbrica.
  *Hecho cuando:* alguien que no conoce el proyecto lee la ficha y el último
  parte y sabe qué hace, qué no hace y qué pasó en la última pasada.
  Ⓟ 35 (producto) + 20 (observabilidad)

- [ ] **T07 · Modelo de coste medido** 👤P2 ⏱1h
  `alberto coste`: coste y latencia **por ruta** (determinista / visión), coste
  por documento, proyección a 10k y 100k facturas/mes, y el **punto de cruce**
  donde el OCR local se amortizaría.
  *Hecho cuando:* la cifra sale de la BD, no de una estimación. Ⓟ 25

- [ ] **T08 · Resiliencia demostrable: `alberto caos`** 👤P1 ⏱1h30
  Un interruptor que tumba el proveedor de LLM (y otro el ERP) a mitad de una
  pasada. La cola conserva el trabajo, reintenta, degrada y **reanuda sin
  duplicar**.
  *Hecho cuando:* se mata el proveedor en vivo, se relanza, y el recuento final
  es idéntico. Ⓟ 10

- [ ] **T09 · Capacidad y rendimiento** 👤P1 ⏱45m
  Medir documentos/segundo con y sin `--rapido`, p50 y p95 de latencia, y añadir
  `--workers N` con el acelerón medido.
  *Hecho cuando:* se puede responder «cuántos por segundo y con qué hardware»
  con un número propio. Ⓟ 25

- [ ] **T10 · Diff entre normas: `alberto compara v3 v4`** 👤P3 ⏱45m
  La consulta SQL del README convertida en comando: qué decisiones cambian al
  pasar de v3 a v4 y por qué.
  *Hecho cuando:* imprime la tabla de cambios. **Es el minuto 3 de la defensa.** Ⓟ 35

- [ ] **T11 · Conector de fuentes: probar que el pipeline no depende del PDF** 👤P2 ⏱1h
  La rúbrica pregunta literalmente qué cambia si Alberto manda emails u hojas de
  cálculo. Extraer una interfaz `Fuente` y añadir un conector de email (stub con
  fixtures) que produce `Documento` igual que el de carpeta.
  *Hecho cuando:* un test procesa un `.eml` con la factura adjunta. Ⓟ 25

---

## P2 · El bonus (+10) — «el escritorio de Alberto»

Va **después de T06** y se corta el sábado a las 23:00 si no está. Un bonus a
medias que rompe la demo resta más de lo que suma.

Si solo da tiempo a una cosa de esta sección, es **T12 + T27**: esa es la
historia. T13 y T14 son mejoras de una historia que ya se cuenta sola.

- [ ] **T12 · Bandeja de escalados** 👤P4 ⏱1h30
  Las 49 excepciones con su traza y las notas relevantes al lado. Alberto
  resuelve y queda en `resoluciones` con autor y motivo.
  *Hecho cuando:* se resuelve un escalado en vivo y cambia el outcome.

- [ ] **T27 · Agente redactor: el escalado que se desbloquea solo** 👤P2 ⏱2h30
  Hoy `ESCALAR` es un callejón sin salida: 49 facturas van a un humano y ahí se
  acaba nuestro sistema. Este agente convierte cada escalado en **la acción
  concreta que lo desbloquea**: el email al proveedor. Dos roles, y solo dos —
  el que decide (determinista, sin LLM) y el que redacta (LLM, con plantilla).
  No hay un tercer agente supervisor porque no sabríamos responder para qué es.

  En la bandeja de T12, cada escalado ofrece «¿escribimos al proveedor?». El
  email se muestra **antes** de enviarse; Alberto aprueba, edita o descarta.

  Cuatro barandillas, ninguna negociable:

  1. **Los datos van por plantilla; el LLM solo escribe la prosa alrededor.**
     Nº de factura, pedido, importe, diferencia: inyectados desde `decisiones` y
     `extracciones`, nunca generados. Un LLM no inventa una cifra que sale de
     esta casa con el membrete de Alberto.
  2. **Nunca envía solo.** Aprobar / editar / descartar, y queda registrado en
     `resoluciones` con autor y motivo — la tabla ya existe en el esquema.
  3. **El email JAMÁS toca `outcomes.jsonl`.** La factura pasa a «esperando
     proveedor» con su marca de tiempo, pero el resultado sigue siendo `ESCALAR`.
     Si un email puede mover un outcome, nos jugamos la validación binaria.
  4. **En la demo, SMTP real pero al buzón del equipo**, con un aviso visible en
     pantalla de que el destinatario está sustituido. Se ve llegar de verdad y no
     le escribimos a ningún proveedor que no existe.

  **Qué casos hay hoy** (contados sobre `decisiones`, no estimados): **20
  escalados tienen email posible** — 14 por discrepancia de importe contra el
  pedido, 4 por campo faltante (3 fecha, 1 IBAN) y 2 marcados para revisión en el
  Excel, que van a Alberto y no al proveedor. Los otros **29 son las imágenes: no
  sabemos ni el NIF, así que no sabemos a quién escribir** hasta que T02 esté.
  Para el guion, usar un caso de **discrepancia de importe** («su factura dice
  4.549,60 € y nuestro pedido PO-XXXX dice otra cosa»): es el más convincente y
  el que más casos tiene. El de campo faltante suena mejor pero es justo donde
  puede no haber destinatario.

  **Regalo de resiliencia:** si el proveedor de LLM cae, el email se sigue
  redactando desde la plantilla, más seco. Degradación elegante demostrable en el
  minuto 9 sin escribir una línea extra.

  **Para el vídeo (T18):** esto es el clímax. El resto de la demo son números y
  tablas; esto es lo único que se mueve, tiene tensión (¿lo envía o no?) y acaba
  en algo que cualquiera reconoce: un correo que llega. Plano sugerido, treinta
  segundos sin cortes y sin terminal — pared de baldosas (T25) → clic en una
  ámbar → la traza con el motivo → «redactar» → aparece el email → aprobar → la
  bandeja del buzón con el correo dentro.

  *Hecho cuando:* se aprueba un email en vivo, llega al buzón, queda en
  `resoluciones` con quién lo aprobó, y la factura pasa a «esperando proveedor»
  con el outcome intacto. Depende de T12. Ⓟ bonus +10 · 10 (resiliencia)

- [ ] **T13 · Maestro editable y versionado** 👤P4 ⏱1h
  Editar el IBAN de un proveedor crea una **versión nueva**, no muta la anterior.
  El esquema ya lo soporta (`maestro_versiones`, y `snapshot_maestro` en
  `decisiones`).
  *Hecho cuando:* se corrige un IBAN, se reprocesa, y se ve qué decisiones
  cambiaron. Reutiliza el mismo mecanismo que T10.

- [ ] **T14 · Notas como anotaciones enganchadas** 👤P4 ⏱45m
  Las notas de Alberto dejan de ser un tablón y salen **cuando esa entidad está
  en juego**: la del IVA reducido al escalar por IVA, la de `pendiente_revisar`
  sobre sus dos pedidos.
  *Hecho cuando:* la traza de `PO-2026-0007` muestra su nota y ya no salen las
  cuatro notas generales en todas las facturas.

---

## P3 · El documento y la defensa (35 puntos)

- [ ] **T15 · ADR-002 a ADR-005** 👤P4 ⏱1h
  Ya hay ADR-001 (extractor genérico). Faltan: las reglas como datos, SQLite como
  almacén, la cascada de extracción, y el filtro aritmético como sustituto de la
  confianza en el extractor. **Se escriben cuando se decide, no al final.**

- [ ] **T16 · `albertitos_plan.pdf`** 👤P4 ⏱2h
  Dos secciones: Arquitectura (componentes, flujo de datos, estado, reparto entre
  agentes, modelos y personas, y cómo se observan y recuperan los fallos) y
  ADRs con contexto, alternativas, decisión, consecuencias y evidencia. Ⓟ 35

- [ ] **T17 · Guion de defensa de 10 minutos** 👤P4 ⏱1h
  2 demo · 2 arquitectura · **4 traza/escala/coste** · 2 resiliencia y preguntas.
  Cronometrado y ensayado 5 veces. Cada bloque lo defiende quien lo construyó.

---

## P4 · HackSpain general (es otra entrega distinta)

- [ ] **T18 · Vídeo para el jurado** 👤P4 ⏱2h
  **YouTube oculto, Loom o `.mp4` directo — Vimeo y Google Drive no embeben.**
  El jurado puntúa 1–10 viendo esto y un proyecto sin puntuar cae por debajo de
  todos. Grabar la web, no el terminal.
  Estructura: la ficha de T26 en el primer plano (que en diez segundos se
  entienda que esto es un trabajador digital y no un script) → la pared de T25
  para dar la escala → una traza → **el email de T27 como clímax** → el contador
  de euros protegidos para cerrar.

- [ ] **T19 · `hackspain submit`** 👤P3 ⏱30m
  Draft ya, definitivo al final. `--track maisa` + los perks reclamados (salen en
  la tarjeta del jurado). Logo y nombre de equipo también salen: cuestan 10
  minutos y casi nadie los pone.

- [ ] **T20 · Milestones y telemetría** 👤todos ⏱15m
  `hackspain watch` abierto en las 4 máquinas (CLI ≥ 0.5.0) y
  `milestone add firstCommit|firstBuild|firstDemo` en cuanto toquen.

---

## P5 · Si sobra tiempo

- [ ] **T21 · CI en GitHub Actions** ⏱30m — typecheck + tests en cada PR. Los PRs
  también salen en el feed público, así que el CI es visibilidad además de red.
- [ ] **T22 · Nivel OCR local (Tesseract)** ⏱2h — solo para **medir el punto de
  cruce** de T07. Con 29 documentos no se amortiza; el valor está en el número.
- [ ] **T23 · Regla de vencimiento preparada** ⏱30m — el motor ya tiene el tipo
  `vencimiento` y el maestro trae `condiciones_dias` (30/45/60). Es el candidato
  más probable a norma v4. Dejarla escrita y desactivada.
- [ ] **T24 · Comprobación de PII antes de entregar** ⏱20m — que no se cuele un
  IBAN o un NIF en un log, en un evento o en el PDF.

---

## Deliberadamente NO vamos a hacer

Escrito ahora para poder decir que no a las 4 de la mañana:

- **Autenticación y multiusuario en la web.** Es una demo de 10 minutos.
- **Reescribir la extracción con un LLM para «mejorar».** 468 de 471 con coste 0 €.
- **Una base de datos «de verdad».** SQLite es la decisión, está en el ADR, y
  cambiarla no da un solo punto.
- **Refactorizar `legacy/`.** Está ahí como referencia de qué no hacer, no se toca.
- **Perseguir el 100% de acierto.** La validación es binaria y no da puntos.

---

## Orden recomendado · revisado el sábado 10:30

El orden anterior (`T01 → T03 → T02 → T06 → T25 → T26 → T07 → T08 → T04 → T10 →
T12 → T27 → T16 → T18`) estaba ordenado por **lo que es interesante construir**.
Esto está ordenado por **lo que nos puede eliminar**.

El diagnóstico, en una línea: hemos construido con criterio de ingeniero de
producción y nos juzgan con criterio de jurado. Lo que falta no es sistema, es
**evidencia del sistema** — docs/s, fórmula de coste, contador de reintentos, el
PDF. Casi todo está ya en la BD; falta sacarlo.

Estimación honesta si defendiéramos hoy: **~50-55 de 110**, con riesgo real de
**no superar la puerta de validación** por las 29 imágenes.

| Criterio | Pts | Hoy | Por qué |
|---|---:|---:|---|
| Producto, arquitectura y ADRs | 35 | ~15 | Falta el PDF (obligatorio) y 4 de los 5 ADRs. Nos presentamos como *pipeline*, no como *trabajador digital* |
| Trazabilidad y observabilidad | 20 | ~13 | `explica` es excelente. Las señales operativas (reintentos, pendiente, coste) no existen en la BD |
| Escalabilidad y coste | 25 | ~10 | Sin cifra propia de docs/s, sin fórmula de coste, sin respuesta a «¿y si llegan emails?» |
| Resiliencia y recuperación | 10 | ~6 | El cliente ERP es bueno y está testeado, pero tira la evidencia (T30) y no hay LLM al que se le pueda caer |
| Calidad de ejecución | 10 | ~8 | Código limpio y proporcionado, 46 tests |
| Bonus | +10 | 0 | Nada |

### Bloque 1 · Supervivencia — antes de las 18:00 (~5 h)

```
T28 → T29 → T02 → T30
```

T01 ✅ hecho: el repo es público y los cuatro figuran en `git shortlog -sn`.

1. **T28 · blindar el lote 2.** Los tres fallos están localizados y leídos. Es
   T03 pero sin ensayar a ciegas.
2. **T29 · que `emite` no pueda mentir.** 30 minutos sobre el único control que
   nos separa de suspender la puerta binaria.
3. **T02 · visión para las 29 imágenes.** **Es la puerta, no son puntos.** 29 de
   500 (5,8 %) van hoy a `ESCALAR` sin haber mirado el documento: si la
   referencia privada espera `PAGAR` en alguna, no optamos al premio. Y encima
   envenenan la traza — 29 de nuestros 49 escalados son «no supe leerlo», que es
   el peor caso posible que enseñar en el minuto 4.
4. **T30 · persistir las métricas del ERP.** Quince minutos por evidencia
   directa de un criterio de 10 puntos.

### Bloque 2 · El lote 2 y los puntos gordos — 18:00 a 23:00

```
T31 → (lote 2 + norma v4) → T10 → T07 + T09
```

T31 va **antes** de la v4: es exactamente donde se rompe el índice posicional.

**T07 + T09 son 25 puntos casi vacíos y la peor relación puntos/esfuerzo
restante de todo el backlog.** Tenemos los datos (5,4 ms/doc, 0 €, 471
documentos) y no tenemos la respuesta. La rúbrica pregunta literalmente «cuántos
archivos por segundo y con qué hardware» y «qué cambia si Alberto incorpora
emails u hojas de cálculo». Hoy ninguna de las dos tiene respuesta defendible.
La frase «500 facturas, 5,4 ms cada una, 0 €, y el LLM solo toca 29» es nuestra
mejor diapositiva y todavía no existe.

### Bloque 3 · La historia — 23:00 a 02:00

```
T26 → T25 (sobre un T06 recortado)
```

**T26 sigue siendo la mejor relación puntos/hora del backlog.** Maisa vende
trabajadores digitales cuyo razonamiento es auditable, no cajas negras que
alucinan: extracción determinista, reglas en YAML, y cada decisión con el
veredicto de cada regla y su evidencia. Estamos contando su tesis sin decirlo, y
un equipo que llegue con «le metimos un LLM a las 500 facturas» está contando la
contraria. *«Su manual de empleado es un YAML y el sábado Alberto se lo actualizó
sin tocar código»* es la frase que nos pueden recordar al día siguiente.

**Recortar T06 a lo imprescindible.** La rúbrica dice explícitamente que un
backend pequeño bien razonado gana a una app grande sin criterio, y
`alberto explica` ya cubre el minuto 4-8. La pared de T25 es portada y escala;
el detalle es `explica` proyectado. Nada más.

### Bloque 4 · Domingo 02:00 a 10:30

```
T15 → T16
```

**Es el único documento obligatorio y vale 35 puntos.** Si el domingo a las 8 no
está, hemos regalado un tercio de la nota por construir algo que nadie va a leer.

### Qué cortamos

- **T27 (agente redactor)**, salvo que todo lo anterior esté cerrado. 2 h 30 con
  SMTP real a las 3 de la mañana es exactamente cómo se rompe una demo, y los
  +10 del bonus valen menos que los 35 del PDF. **Descrito en el PDF como diseño
  puntúa en arquitectura casi igual.**
- **T13, T14, T22.** Mejoran una historia que ya se cuenta sola.
- **Perseguir acierto más allá de las 29 imágenes.** Ya está escrito abajo y
  sigue siendo verdad.

### La pregunta que no tiene respuesta hoy

La rúbrica dedica 10 puntos y 2 minutos de defensa a «qué ocurre si vuestro
proveedor de LLM falla», y pide el «reparto entre agentes, modelos y personas».
Hoy la respuesta es *no aplica*, y eso se lee como madurez o como que no hemos
hecho el reto según cómo lo contemos. Con T02 hecho, la respuesta pasa a ser:
**«el LLM está en dos sitios acotados, ninguno en el camino crítico de decidir;
si se cae, esas 29 escalan con motivo y el email sale por plantilla»**. Esa frase
vale los 10 puntos enteros — y es otra razón para que T02 no se caiga.

---

## Hitos con hora fija

| Cuándo | Qué |
|---|---|
| **Sábado 18:00** | Llegan lote 2, ERP actualizado y **norma v4**. T03 tiene que estar hecho antes |
| **Sábado 23:00** | Corte del bonus (T12–T14). Si no está, se cuenta como diseño en el PDF |
| **Domingo 02:00** | **Congelación de código.** A partir de aquí solo documento y ensayo |
| **Domingo 10:30** | La organización clona el repo de outcomes y registra el commit |
