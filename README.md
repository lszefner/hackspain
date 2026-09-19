# Alberto · pipeline de decisión de pago

Track Maisa, HackSpain '26. Decide `PAGAR` / `NO_PAGAR` / `ESCALAR` sobre las
facturas de La Caja, con traza completa de cada decisión.

## Son TRES repos, no dos

| Repo | Qué es | Quién lo toca |
|---|---|---|
| **`500-sombras-de-alberto`** (La Caja) | Entrada de solo lectura: las 500 facturas, el Excel y `alberto_erp.py` (el bridge de 2009). Lo publica la organización. | **Nadie.** Se clona y se deja quieto |
| **este repo** | Nuestra solución. Lee los ficheros de La Caja del disco y habla con el ERP por HTTP | Los 4, todo el fin de semana |
| **`la-caja-outcomes`** | La entrega. Público, con **exactamente 3 ficheros** en la raíz: `outcomes.jsonl`, `outcomes_lote2.jsonl`, `albertitos_plan.pdf` | Se crea el domingo por la mañana |

El tercero es el que se entrega. **Nunca subimos ahí nuestro código**: las bases
del reto lo prohíben explícitamente (`no subáis vuestra solución, credenciales ni
una aplicación ejecutable`).

La Caja **no se commitea en este repo**: está en `.gitignore`. Cada uno la clona
donde quiera.

## Puesta en marcha

```bash
# una vez
git clone https://github.com/ikurotime/500-sombras-de-alberto.git caja
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

Si tienes La Caja en otro sitio, no edites nada:

```bash
export ALBERTO_CAJA=~/HackSpain/500-sombras-de-alberto
```

## Dos terminales

```bash
# Terminal 1 — el bridge ERP de 2009. Se queda abierto.
cd caja && make erp          # make erp-fast le quita la latencia artificial

# Terminal 2 — el pipeline
./venv/bin/python -m alberto.cli todo
```

`todo` encadena las cinco etapas y deja `outcomes.jsonl` listo. Tarda ~10 s.

## Los verbos, por separado

| Comando | Qué hace |
|---|---|
| `alberto ingesta` | Recorre `facturas/`, hashea, normaliza a NFC, registra estado |
| `alberto snapshot` | Descarga los 516 asientos del ERP y los versiona |
| `alberto maestro` | Carga el Excel: proveedores (deduplicados) y notas de Alberto |
| `alberto extrae` | PDF → campos. `--forzar` para reextraer |
| `alberto decide` | Aplica `reglas/norma_v3.yaml` + `politica.yaml` |
| `alberto emite` | Escribe el JSONL y lo verifica antes de entregarlo |
| `alberto estado` | En qué punto está cada documento |

Cada uno acepta `--caja`, `--erp-url`, `--db`, `--lote`.

## El sábado a las 18:00 (lote 2 y norma v4)

Por diseño, esto **no debería tocar código**:

```bash
# 1. ERP actualizado
cd caja && make erp-lote2

# 2. las 40 facturas nuevas
./venv/bin/python -m alberto.cli --lote lote2 --caja ruta/al/lote2 todo \
    --salida outcomes_lote2.jsonl

# 3. la norma nueva: se copia el YAML, se edita, y se reprocesa
cp alberto/reglas/norma_v3.yaml alberto/reglas/norma_v4.yaml
$EDITOR alberto/reglas/norma_v4.yaml
./venv/bin/python -m alberto.cli decide --norma v4
```

Las decisiones v3 **no se borran**: la clave primaria es
`(doc_id, norma_version, snapshot_erp, snapshot_maestro)`. Una sola consulta
enseña qué cambió entre v3 y v4 y por qué — eso es el minuto 3 de la defensa.

```sql
SELECT a.file_id, a.result AS v3, b.result AS v4, b.motivo
FROM decisiones a JOIN decisiones b USING (doc_id)
WHERE a.norma_version='v3' AND b.norma_version='v4' AND a.result <> b.result;
```

## Cómo probar

**Tests automáticos.** Los rápidos no necesitan nada:

```bash
./venv/bin/python -m pytest tests -q -m "not lento"   # 39 tests, 0,1 s
./venv/bin/python -m pytest tests -q                  # los 46, con ERP levantado
```

Cubren el vocabulario (`RECHAZAR` es imposible), los dos formatos numéricos y
los tres de fecha, cada regla con su fallo, la idempotencia de la ingesta y el
reproceso, que los 9 pedidos ya pagados nunca se pagan, y que el JSONL es
entregable.

**Seguir UNA factura de principio a fin.** Es la herramienta de depuración y a
la vez el minuto 4-8 de la defensa:

```bash
./venv/bin/python -m alberto.cli explica 2026-01-08_P001      # una que se paga
./venv/bin/python -m alberto.cli explica 2026-07-08_P010      # IBAN que no cuadra
./venv/bin/python -m alberto.cli explica 2026-0811-B_catering # total inflado
```

Acepta cualquier trozo del nombre. Muestra los campos extraídos, si la
aritmética cierra, el veredicto de cada regla con su evidencia, la decisión con
su motivo, y las notas de Alberto que apliquen.

**Ver el reparto y hurgar en SQL:**

```bash
./venv/bin/python -m alberto.cli estado
sqlite3 alberto.db "SELECT result, count(*) FROM decisiones GROUP BY result"
sqlite3 alberto.db "SELECT file_id, motivo FROM decisiones JOIN documentos USING(doc_id) WHERE result='NO_PAGAR'"
```

## Antes de entregar

`alberto emite` ya verifica JSON válido, vocabulario (`PAGAR`/`NO_PAGAR`/`ESCALAR`),
`file_id` en NFC, sin duplicados y con el número de líneas esperado. Si sale
`"ok": true`, el fichero se puede entregar.

## Qué NO está hecho todavía

- **Visión para las 29 facturas que son imagen.** Hoy se escalan con motivo.
- La web de traza (seguir una decisión en pantalla) y la bandeja de escalados.
- El modelo de coste medido.

## `legacy/`

El primer prototipo que circuló por el equipo. Se conserva como referencia pero
**no se usa**: emitía `RECHAZAR` (que no es un resultado válido y suspende la
validación binaria) y acertaba el pedido en 219 de 471 facturas. El porqué está
en `docs/adr/ADR-001`.
