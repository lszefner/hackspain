# `rules_ingestion/` — conservado, no cableado

Este directorio es el pipeline de autoría de reglas tal como está en `main`.
Se conserva **íntegro y sin tocar** para no perderlo en la consolidación de
ramas.

**No lo importa nadie de `alberto/`.** El subconjunto que sí usamos está
portado en [`alberto/reglas/autoria/portado/`](../alberto/reglas/autoria/portado/):
`loader`, `catalog`, `classify`, `normalize`, `store`, `merge` y
`policy_metrics`, con un exportador propio que traduce el resultado al
esquema de norma que entiende `alberto/reglas/motor.py`.

Sí, son dos copias de los mismos siete ficheros. Es deliberado: son ports
congelados que nadie va a editar, y duplicarlos costaba menos que romper una
cadena de imports que funciona y está cubierta por tests.

Lo que **solo** está aquí y no se portó:

| Módulo | Por qué no |
|---|---|
| `codegen.py` | Genera código Python con un LLM y lo ejecuta. Riesgo alto, y las reglas nuevas se exportan como condiciones declarativas |
| `checks.py`, `invoice.py` | Son el motor de decisión de `main`. Aquí hay uno propio, en `alberto/reglas/motor.py`, con tests detrás |
| `build_rules.py` | Su CLI. Aquí el verbo es `alberto reglas` |
| `eval/` | Fixtures y scoring del pipeline de `main` |

Para usarlo: `alberto reglas --perfil strict --descubrir CARPETA`.
