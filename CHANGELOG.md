# CHANGELOG

Decisiones tomadas, no lista de commits. Una entrada por hito.

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
