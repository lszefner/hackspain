# Mesa de Alberto · capa agentica (v1)

Prototipo **aislado**: vive entero en `desk/` y no toca `ingestion/`, `rules_ingestion/`
ni `webui/`. Solo los lee (el maestro de proveedores y pedidos sale del mismo
`FINAL_v7_DEFINITIVO_ahorasi.xlsx` via `rules_ingestion.loader`).

```bash
python3 desk/server.py                    # http://127.0.0.1:8011
python3 desk/server.py --reseed           # regenerar el dataset
python3 desk/server.py --investigate      # investigar las escalaciones al arrancar
```

Sin dependencias: stdlib de Python y un HTML/JS plano, igual que `alberto_erp.py`.

## La idea

El agente es el puesto de trabajo; la barra lateral es el archivo. Alberto no
recorre 500 facturas: el sistema las trabaja y solo le trae lo que necesita
criterio, ya agrupado y ya investigado.

- **Agente** — chat. Las respuestas son objetos vivos (tarjetas de patron con
  sus botones, expedientes con su traza, propuestas de norma con su backtest).
- **Facturas** — el archivo. Buscar, filtrar, abrir el expediente de cualquiera.
- **Pagos** — remesa SEPA, pagos duplicados bloqueados, bandeja de salida, cierre.
- **Reglas / Actividad** — el ruleset vigente con su historial, y el ledger crudo.

## Las tres piezas que sostienen todo

**1. Ledger append-only** (`ledger.py`). Una tabla, nunca se hace UPDATE. El
estado actual es un *pliegue* de los eventos (`state.py`), no una fila mutada.
De ahi salen gratis: la traza, el coste por factura, la vista de actividad, el
informe del cierre, y el poder decir "fue ESCALAR bajo v3.1 y PAGAR bajo v3.2"
con los dos veredictos vivos en el expediente.

**2. El motor decide, los agentes no.** El veredicto sale de reglas
deterministas. Un agente investiga, recomienda, redacta y ejecuta, pero para
cambiar un veredicto hay que aprobarlo (una vez) o cambiar la regla (con diff y
backtest). `explain.py` redacta el *por que* **desde el ledger**: nunca vuelve a
leer el PDF ni a razonar, porque una explicacion que puede contradecir a la
decision no es una traza.

**3. Idempotencia en dos capas** (`agents.approve`). La clave
`sha256(file_id | ruleset_version | approval_id)` frena el *replay* de una misma
aprobacion (reintento, doble clic, lote caido a medias). El segundo guardia frena
una *segunda aprobacion* de una factura que ya tiene pago. Y un `NO_PAGAR` no se
paga aprobandolo: se cambia la regla. Los tres casos quedan escritos como
`PAYMENT_BLOCKED` / `PAYMENT_REFUSED`, nunca en silencio.

## Los agentes

| Agente | Que hace | Limite |
| --- | --- | --- |
| `extractor` | OCR + interpretacion (hoy sembrado) | no decide |
| `conciliador` | cruza con el ERP, absorbe los `ORA-00600` | no decide |
| `investigador` | intenta **cerrar** la escalacion: busca el pedido que falta en el maestro, compara la factura gemela, mira el historial de IBAN | recomienda, no aplica |
| `comunicador` | redacta y retiene los correos | ventana de deshacer |
| `tesorero` | remesa SEPA pain.001, pagos, avisos de pago | no paga un NO_PAGAR |
| `legislador` | norma en lenguaje natural -> diff -> backtest -> version nueva | no aplica sin aprobacion |
| `reportero` | cierre del dia que Alberto reenvia a direccion | una vez al dia por destinatario |

## Que es real y que esta simulado

Real: proveedores, NIF, IBAN y los 516 pedidos del Excel; las reglas y su
precedencia; el ledger, la idempotencia, el backtest, el reproceso y el XML
SEPA; los costes por etapa con la forma que emite el pipeline.

Simulado: la poblacion de facturas y sus averias (`seed.py`) — el pipeline real
necesita claves y 500 llamadas de OCR; los correos se retienen y se marcan como
enviados por un transporte `smtp-sandbox`; no hay pasarela bancaria (la remesa
es el artefacto: el fichero que se sube al banco).

`adapters` futuro: cuando `webui/data/revisiones.db` tenga filas reales, se
vuelcan al ledger con los mismos tipos de evento y ni la UI ni los agentes notan
la diferencia.

## Sin LLM en el camino critico

El enrutado de intenciones y el parseo de normas (`chat.py`, `rules.parse`) son
deterministas. Un modelo puede ir despues encima, para redactar mejor y para las
normas que el parser no entienda, pero con el proveedor caido la mesa sigue
funcionando: misma postura que el pipeline de ingesta.
