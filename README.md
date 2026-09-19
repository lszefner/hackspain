# Alberto · pipeline de decisión de pago

Track Maisa, HackSpain '26. Decide `PAGAR` / `NO_PAGAR` / `ESCALAR` sobre las
facturas de La Caja, con traza completa de cada decisión.

## Son DOS repos

| Repo | Qué es | Quién lo toca |
|---|---|---|
| **este repo** | Nuestra solución **y La Caja**: las 500 facturas, el Excel y `alberto_erp.py` (el bridge de 2009) viven en `caja_de_alberto/v1/` | Los 4, todo el fin de semana |
| **`la-caja-outcomes`** | La entrega. Público, con **exactamente 3 ficheros** en la raíz: `outcomes.jsonl`, `outcomes_lote2.jsonl`, `albertitos_plan.pdf` | Se crea el domingo por la mañana |

El segundo es el que se entrega. **Nunca subimos ahí nuestro código**: las bases
del reto lo prohíben explícitamente (`no subáis vuestra solución, credenciales ni
una aplicación ejecutable`). Esa prohibición es sobre el repo de entrega, no
sobre este: aquí La Caja está commiteada, igual que en `main`, para que el repo
sea autosuficiente y nadie tenga que configurar rutas.

## Dónde está La Caja

Hay **dos cosas distintas** y conviene no confundirlas:

| | Qué es | En git |
|---|---|---|
| `caja_de_alberto/vN/` | Instantáneas de lo que publica la organización, con `MANIFIESTO.sha256`. **No se editan** | Sí |
| `caja/` | La copia **viva**: la que lee el motor por defecto | No |

Están separadas porque la copia viva es la que se siembra, se rehace y se
ensucia: con una sola copia, cualquier cambio invalidaba el manifiesto y la
captura dejaba de servir como prueba de con qué datos se generó una entrega.

`backend.caja_paths` la encuentra solo, en este orden: `ALBERTO_CAJA` si está
puesta, luego `caja/`, y si no la instantánea más reciente (así un clon recién
hecho puede ejecutar los comandos de solo lectura sin sembrar nada). Un
`--input-dir` / `REVISION_INPUT_DIR` explícito o un `--sources` propio pisan
estos defaults.

## Puesta en marcha

```bash
# una vez
uv sync --locked --extra worker --extra backend
make caja      # siembra caja/ desde la instantánea más reciente
```

`make erp` siembra `caja/` solo si falta, así que en la práctica basta con
arrancar el ERP. Para rehacerla desde cero: `rm -rf caja && make caja`.

Para apuntar a otra captura o a un clon tuyo:

```bash
export ALBERTO_CAJA=caja_de_alberto/v2
```

## El motor

El camino canónico de punta a punta:

```bash
uv run --locked --extra worker --extra backend python -m backend.run_revision \
    invoice.pdf --request-key INTENT --evaluation-date YYYY-MM-DD \
    --input-dir PDF_DIR --sources SOURCES_YAML
```

Requiere Supabase y configuración explícita por entorno (nunca dotenv): las
credenciales de Supabase/ingestion más `REVIEW_ENDPOINT`, `REVIEW_MODEL` y
`REVIEW_API_KEY`; la generación de reglas por defecto además pide `JEV_API_KEY`
y `DEEPSEEK_API_KEY`. Construye las reglas con el pipeline de IA, ejecuta la
extracción persistida, el evaluador v2 y la revisión contextual obligatoria.
`--status-key INTENT` recupera una corrida durable sin los PDFs locales, y
`python -m backend.server` sirve la API JSON sobre el mismo camino.

## Dos terminales

```bash
# Terminal 1 — el bridge ERP de 2009. Se queda abierto.
make erp                     # make erp-fast le quita la latencia artificial

# Terminal 2 — la API del motor
uv run --locked --extra worker --extra backend python -m backend.server
```

## Las reglas, por separado

Las normas compiladas las construye `rules_ingestion`:

```bash
make rules            # outcome/<ver>/balanced/rules.json
make rules-offline    # lo mismo, sin JEV ni LLM
```

## Cómo probar

```bash
uv run --locked --extra worker --extra backend python -m pytest -q tests
```

Los tests de Postgres levantan una instancia local desechable y se saltan si
faltan los binarios. Los proveedores de pago van mockeados: ningún test llama
a APIs de pago ni a bases compartidas.
