# `ingestion/` — conservado para quien lo use, no cableado al pipeline

Este directorio es el pipeline de visión y OCR tal como está en `main`. Se
conserva **íntegro y sin tocar**, con `benchmark/schemas/` al lado porque
`contracts.py` los busca ahí (`parents[1] / "benchmark" / "schemas"`).

**`alberto/` no lo importa.** La fase 2 de la cascada usa una copia recortada
en [`alberto/extraccion/vision/portado/`](../alberto/extraccion/vision/portado/):
`vision.py`, `deepseek.py` y `pdf.py`, sin Supabase, sin Postgres, sin el
sistema de jobs y sin `jsonschema`.

Sí, son dos copias de esos tres ficheros. Igual que con `rules_ingestion/`:
son ports congelados que nadie va a editar, y duplicarlos costaba menos que
acoplar `alberto/` a un árbol de dependencias que no necesita.

## Para usarlo

```bash
./venv/bin/pip install -r requirements-ingestion.txt
```

Añade `jsonschema`, `psycopg[binary,pool]` y `python-dotenv`, que el
pipeline principal no necesita. Sin ellas, `import ingestion.contracts` y
`import ingestion.pipeline` fallan — es esperado, no está roto.

Y necesita las variables de `.env.example`, incluidas las de Supabase, que
`ingestion/config.py::credentials` exige siempre.

## Qué ruta usa cada cosa

| | `ingestion/` | `alberto/extraccion/vision/` |
|---|---|---|
| Lectura | fal GOT-OCR2, o visión por regiones | visión por regiones |
| Persistencia | Supabase Storage + Postgres | SQLite, tabla `artefactos` |
| Reintentos | sistema de jobs con leases | cola derivada de `extracciones` |
| Campos | intérprete DeepSeek → JSON | `extraer_campos` sobre el texto |

Medido en `docs/hard-two-repair-audit-20260919.md`: la ruta de visión por
regiones puntúa 19-20/20 sobre los dos escaneos difíciles, frente a 5/20 y
0/20 de la ruta GOT-OCR anterior.
