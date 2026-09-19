# CHANGELOG

Decisiones tomadas, no lista de commits. Una entrada por hito.

## 2026-09-19 13:05 CEST · La web lee lo decidido, no vuelve a decidir

Trae el frontend de Next.js desde `main` y le pone debajo un backend que
sirve los mismos cuatro endpoints desde `alberto.db`.

**Estado:** 108 tests rápidos (antes 101) · el frontend no cambia ni una
línea.

### El problema que resuelve

El `backend/` de `main` importaba `ingestion.pipeline.Pipeline` y
`rules_ingestion.checks.run_checks`: **recalculaba las decisiones con otro
motor** mientras el JSONL salía de este. La misma factura podía aparecer
con un resultado en pantalla y otro en el fichero entregado, y nadie se
habría enterado hasta la defensa. Es el mismo fallo que tenían `emite` y
`explica` esta mañana, pero a escala de repositorio.

Ahora `alberto/web/datos.py` **no decide nada**: lee `extraccion_vigente` y
`decisiones`, que es exactamente lo que se entrega. Hay un test que compara
las dos cosas y falla si divergen.

### Lo que no se trajo, y por qué

`backend/` de `main` (8 ficheros) depende de `ingestion/`, `rules_ingestion/`
y `benchmark/schemas`. Traerlo habría significado arrastrar el codebase
viejo entero. Reescribir los 4 endpoints contra la BD fue menos trabajo que
portar su árbol de dependencias.

`POST /api/lanzar` es un **no-op deliberado**: reprocesar es
`alberto decide`, que abre una pasada y registra su contexto. Un botón que
dispara un reproceso sin dejar constancia iría contra todo lo demás.

### De regalo

El detalle de cada factura lleva ahora un bloque `procedencia` con la
huella de la norma, los snapshots, la vía de extracción, el `hoy` y el
commit. El frontend todavía no lo pinta, pero ya viaja.

`alberto web --instantanea frontend/src/data/snapshot.json` regenera el
JSON estático que consume el despliegue de Vercel: la foto de la última
pasada real, no una demo inventada.

---

## 2026-09-19 12:39 CEST · Autoría de reglas desde CSV y Excel

Trae de `main` el pipeline que convierte reglas en texto libre en una norma
que este motor entiende. Corre en tiempo de **autoría**: produce un YAML, y
a partir de ahí la decisión sigue siendo función pura de un fichero
congelado. **Si el clasificador se cae, no hay ninguna decisión afectada.**

**Estado:** 101 tests rápidos (antes 93) · 1000/1000 decisiones
reproducibles con dos normas conviviendo.

### Qué se trajo y qué no

~2.230 líneas de las 2.984 de `rules_ingestion`. **Sin dependencias nuevas**:
usa `yaml`, `openpyxl` y `urllib` de la stdlib.

Se quedaron fuera `codegen.py` —genera código Python con un LLM y lo
ejecuta; riesgo alto y no hace falta— y `checks.py`/`invoice.py`, que son el
motor de decisión de `main`: aquí ya hay uno con tests detrás.

### El mapeo, que es donde estaba el trabajo

Los vocabularios no son 1:1:

| `main` | aquí |
|---|---|
| `VENDOR` | `R1_nif_iban` |
| `AMOUNT` | `R2_pedido` **+** `R3_iva` (aquí van separadas) |
| `DATES` | `R4_fecha` (+ `R6_vencimiento` si el perfil exige plazos) |
| `DUPLICATES` | `R5_estado_erp` (parcial: no detecta nº de factura repetido) |
| `MISSING` | no es regla: es `datos_incompletos` en la política |
| `AUTHORIZATION` | **no existe**. Se avisa, no se inventa |

Un test comprueba que las 6 canónicas están cubiertas o declaradas como no
soportadas: ninguna puede quedarse olvidada en silencio.

### Decisiones

**El nivel léxico por defecto, no el remoto.** Es determinista y no necesita
red ni claves. Una norma que depende de que un proveedor responda no es una
norma. `--con-jev` activa el clasificador remoto si se quiere.

**Las reglas nuevas se registran pero no se activan.** El motor no las sabe
aplicar todavía; activarlas sería fingir que se comprueba algo que no se
comprueba. Van a `reglas_nuevas_sin_aplicar`, fuera de `reglas:`, con su
texto, su procedencia (fichero, hoja, celda) y su confianza.

**Cada norma trae su política.** `politica_vN.yaml` si existe, y si no la de
siempre: emitir la v4 y seguir decidiendo con la política de la v3 sería
mezclar dos reglamentos.

### Probado contra el Excel real, sin red

Las 6 canónicas clasificadas desde la hoja `Norma_Pagos_v3`, **cada una con
la celda de la que salió**. Y con un CSV de notas mezcladas: «el parking
cierra a las 22h» y «la máquina de café sigue rota» se rechazan por no ser
reglas de pago; «si el importe supera los 15000 € hace falta autorización»
se descubre con confianza 0,67 y **queda sin activar**, porque no llega a la
puerta de 0,80.

Los tres perfiles producen normas distintas de verdad: `strict` deja el
IBAN que no cuadra en `NO_PAGAR` —como la norma escrita a mano—,
`conservative` afloja la tolerancia a 1,00 € y quita el vencimiento.

### Un dato para esta tarde

La norma generada con `strict` activa **`R6_vencimiento`**, y eso convierte
**424 `PAGAR` en `ESCALAR`**: con fecha de hoy, las facturas de enero a
julio están fuera de los plazos de 30/45/60 días del maestro. La regla
funciona; si es lo que Alberto quiere es una decisión de producto, no
técnica. El entregable no se ve afectado porque `emite` sigue usando v3.

---

## 2026-09-19 12:25 CEST · Trazabilidad: toda decisión reproducible

Una decisión pasa a ser función pura de entradas registradas. Antes la fila
de `decisiones` fijaba 4 de las ~12 entradas reales, y una de las 4 mentía.

**Estado:** 500/500 decisiones se vuelven a derivar desde lo guardado ·
92 tests rápidos (antes 72) · el reparto no cambia.

### El bug que podía costar la entrega

`emite` emparejaba solo por `norma_version`, pero la clave de `decisiones`
lleva además los dos snapshots. Con un segundo snapshot —**el domingo, cuando
Alberto cambie un dato**— devolvía N filas por documento y ganaba la primera
que sacara SQLite. Y `explica` rompía el mismo empate al revés, así que **la
herramienta de la defensa y el entregable podían enseñar decisiones distintas
de la misma factura**. Ahora los dos usan `alberto/resolucion.py`, que es el
único sitio donde se decide qué decisión vale.

### Qué se decidió y por qué

**La identidad de una configuración es su contenido, no su nombre.**
`norma_version` pasa de `v3` a `v3@806e4d`, con la huella cubriendo la norma
**y** la política. Cierra tres agujeros de golpe: la política no estaba
registrada en ninguna parte —y es la mitad del «por qué NO_PAGAR»—, editar
`norma_v3.yaml` dejaba dos reglamentos llamados igual, y como la clave no
distinguía esos casos, `INSERT OR REPLACE` **borraba la decisión anterior en
silencio**. Comprobado: cambiar `R2_pedido` de `ESCALAR` a `NO_PAGAR` ahora
crea `v3@be558b`, las dos conviven, y una consulta enseña las **14 decisiones
que cambiaron**.

Se eligió la huella dentro de la etiqueta, y no ampliar la clave primaria,
porque un compañero está escribiendo en `decisiones` ahora mismo. **Nada de
esto cambia la forma de esa tabla.** Todo lo nuevo va en tablas aparte.

**Las notas estaban sin versionar y la consulta no filtraba**, así que
`revisar` era la unión de todas las cargas del Excel que se hubieran hecho
jamás: `snapshot_maestro` en la clave **mentía** sobre lo que entró en la
decisión. Era el único sitio donde el registro engañaba en vez de callar.
Ahora `notas` lleva `version_id`, recargar el maestro ya no duplica, y
`decidir()` filtra por la versión que dice usar.

**`hoy` era reloj de pared.** Se resuelve una vez por pasada y se registra.
Con la regla de vencimiento de la v4 esto pasa de molesto a decidir distinto
cada día.

### Lo que ahora se puede responder

| | |
|---|---|
| `alberto audita` | Vuelve a tomar cada decisión desde lo registrado y compara. **500/500** |
| `alberto coste` | Coste y latencia por vía, sin contar dos veces |
| `alberto explica` | Añade huellas, `hoy`, commit, pasada, marcas de tiempo e intentos fallidos |
| `pasadas` | Qué se ejecutó, con qué argumentos y con qué commit (marcado `-sucio` si había cambios sin commitear) |
| `decision_contexto` | Todo lo necesario para reconstruir el motor exacto |

**Las métricas del ERP dejan de tirarse.** Reintentos por `ORA-00600`,
esperas por 429 y relogins se persisten en `snapshots_erp`: era la mejor
evidencia de resiliencia del proyecto y se descartaba con una comprensión de
diccionario, en dos sitios.

**`eventos` deja de ser write-only**: 69 señales por documento en una pasada,
donde antes había 5 por etapa y `doc_id` nulo en todas.

**El fichero de oro comprueba valores, no solo presencia** —un cambio que
convirtiera todos los totales en `1,00` lo dejaba en verde— y **pytest lo
ejecuta**, que antes no.

### Pendiente

Sigue sin ejecutarse la pasada real de visión: esta sesión no tiene las
credenciales HelmCode. `alberto coste` da 0 € porque la fase 2 nunca ha
llamado al proveedor.

---

---

## 2026-09-19 11:59 CEST · Cascada de extracción en dos fases

Une el extractor determinista de `develop` con el lector de visión de `main`.
La capa de reglas la construye otro y consume una fila por documento.

**Estado:** 500 documentos · 467 con los 7 campos que exigen las reglas ·
72 tests rápidos en verde (antes 39) · el reparto de decisiones no cambia
(431 PAGAR · 49 ESCALAR · 20 NO_PAGAR).

### Qué se decidió y por qué

**La fase 2 se dispara solo cuando faltan campos**, no cuando la aritmética
no cuadra. Son 33 documentos: 29 imágenes sin capa de texto y 4 con texto al
que le falta la fecha o el IBAN. Los 9 cuya aritmética falla tienen *todos*
los campos y los 9 fallan también contra el importe del ERP: son facturas
infladas, el hallazgo. Mandarlas a un modelo sería pedirle que lo borre.
Hay un test que salta si alguien cambia este criterio.

**La fase 2 solo rellena huecos; nunca pisa un dato de la fase 1.** En la
medición de `main`, el modelo leyó un NIF como `B9623341B` —una letra de
más— sin marca de incertidumbre. Si la visión pudiera sobrescribir, ese
glifo habría desplazado a un NIF correcto.

**Una lectura de modelo cuya aritmética no cierra se rechaza.** Sobre datos
de un modelo, una cuenta que no cuadra es una lectura sospechosa y no un
hallazgo: no hay lectura independiente que la respalde. El documento se
queda con la fase 1 y escala con motivo. Si los importes venían de la regex,
el desajuste sí es hallazgo y se conserva.

**Proveedor: DeepSeek tal cual está en `main`**, para conservar la medición
de 19-20/20 sobre los dos escaneos más difíciles. `vision_verify`
desactivado: 3 llamadas por página en vez de hasta 7, porque las mediciones
automáticas no lo usaron.

**Los campos los saca `extraer_campos`, no el modelo.** El lector devuelve
texto; las regex existentes sacan los campos. En los dos fallos que `main`
midió, la regex se comportó mejor: rechazó el NIF malformado por forma y
recuperó un IBAN que el intérprete había anulado.

**La BD es el almacén y una vista decide.** `extraccion_vigente` devuelve el
intento más alto *aceptado*: una fila por documento, elegida explícitamente
y no por el orden del cursor. Es la frontera con la capa de reglas. Un
intento rechazado se conserva con su motivo — es la prueba de que se
intentó la visión y de por qué no se usó.

### Tres trampas encontradas leyendo el código de `main`

Las tres habrían roto la implementación en silencio:

1. `VisionReader.run` devuelve una clave `text` que une **todos** los
   bloques, incluido el documento espejado que se transparenta en el
   escaneo. Usarla metería el IBAN de otra factura en esta. Se filtra por
   `-fg-`.
2. `raw["calls"]` lleva las imágenes en base64 dentro del cuerpo: decenas de
   MB por documento. Se sustituyen por su sha256 — de 100 KB a 184 bytes por
   llamada.
3. `usage` incluye `total_tokens`, que ya es la suma de los otros dos.
   Sumar «cualquier clave numérica» triplicaba el coste, y esa cifra iba a
   la diapositiva de escalabilidad. Ahora va por lista blanca y atribuyendo
   cada llamada a su modelo.

Y una cuarta, propia: `TOTAL: 3.0[unreadable]2,89` hacía que el extractor se
quedara con `2,89`. Las líneas con marcas de incertidumbre se descartan para
los importes; los identificadores se rescatan porque su regex ya filtra por
forma.

### Qué hay ahora

| | |
|---|---|
| `alberto vision` | Fase 2. `--en-seco` rasteriza sin llamar al modelo ni necesitar credenciales |
| `alberto valida` | Cobertura por campo, no regresión y veredicto de traspaso. `--congelar` fija la referencia |
| `alberto explica` | Sección «1b · VISIÓN»: qué campos aportó el modelo y cuáles no pudo tocar |
| Capa `raw` | `artefactos` + `extraccion_artefactos`, direccionadas por contenido |
| Coste | `precios.yaml` versionado. Tokens **medidos**, tarifa **declarada**; el endpoint no factura |

**Reanudabilidad:** la PK `(doc_id, intento)` impide duplicar, el PNG se
persiste antes de la llamada, y un fallo del proveedor no escribe fila sino
que incrementa `documentos.intentos` y puebla `ultimo_error`. Relanzar
continúa por donde iba.

**Dependencias nuevas: ninguna.** `Pillow` y `pypdfium2` ya entraban como
transitivas de `pdfplumber`; ahora están fijadas explícitas.

### Pendiente

La **pasada real contra el proveedor no se ha ejecutado**: esta sesión no
tenía las credenciales HelmCode en el entorno. Todo lo demás está probado,
incluida la rasterización de los 33 PDFs reales (20 MB, 2,3 s). Antes de
nada, con el `.env` puesto:

```bash
alberto vision --limite 3     # 3 documentos de verdad, tokens y coste reales
alberto vision                # los 33, concurrencia 4
alberto valida                # el veredicto
```
