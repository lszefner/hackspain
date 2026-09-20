# caja_de_alberto

Las versiones de La Caja tal y como las publica la organización, una carpeta
por captura. Sirven para dos cosas: tener congelado con qué datos exactos se
generó cada entrega, y poder ver **qué cambió** cuando suben material nuevo.

```
caja_de_alberto/
├── capturar.sh     crea la siguiente versión desde el repo de la organización
├── v1/             lote 1 · commit 76ad52a · viernes 18/09 21:48
│   ├── PROCEDENCIA.md     de dónde salió y qué hay dentro
│   └── MANIFIESTO.sha256  hash de cada fichero
└── v2/             (el sábado a las 18:00, con el lote 2)
```

Regla: **estas carpetas no se editan**. Son la copia de referencia. Nuestro
código vive fuera y las lee.

## Capturar una versión nueva

```bash
./caja_de_alberto/capturar.sh          # crea la siguiente vN libre
./caja_de_alberto/capturar.sh v3       # o una concreta
```

Clona el repo de la organización, copia el árbol sin `.git`, y escribe el
`MANIFIESTO.sha256` y el `PROCEDENCIA.md` con el commit del que salió. Se
niega a sobrescribir una versión que ya exista.

## Ver qué cambió entre dos versiones

```bash
diff caja_de_alberto/v1/MANIFIESTO.sha256 caja_de_alberto/v2/MANIFIESTO.sha256
```

Como el manifiesto lleva hash y ruta, el diff enseña de golpe lo que se añadió,
lo que desapareció y lo que cambió de contenido sin cambiar de nombre — que es
el caso peligroso.

Para ver solo los ficheros nuevos:

```bash
comm -13 <(cut -c67- caja_de_alberto/v1/MANIFIESTO.sha256 | sort) \
         <(cut -c67- caja_de_alberto/v2/MANIFIESTO.sha256 | sort)
```

## Apuntar el pipeline a una versión

Quien resuelve La Caja es `backend/caja_paths.py`, en este orden: `ALBERTO_CAJA`
si está puesta, luego la copia viva `caja/`, y si no la instantánea más nueva de
aquí. Por eso un clon recién hecho funciona sin `make caja`: cae en `v1`.

Para correr contra una captura concreta:

```bash
ALBERTO_CAJA=caja_de_alberto/v1 python -m backend.run_revision invoice.pdf \
    --request-key INTENT --evaluation-date 2026-09-19 --sources rules_ingestion/sources.yaml
```

`--input-dir` (o `REVISION_INPUT_DIR`) pisa lo anterior y apunta directamente a
una carpeta de PDFs, sin pasar por La Caja. `--sources` fija el workbook.

El bridge ERP sale de la copia viva con `make erp`. Para levantar el de una
captura concreta:

```bash
python3 caja_de_alberto/v1/alberto_erp.py --puerto 8009
```

## Ojo con la duplicación

La Caja del lote 1 está **dos veces** en el árbol de trabajo: en `v1/` y en
`caja/`. Son lo mismo salvo el manifiesto y la procedencia, que `make caja`
quita al sembrar. No es duplicación en la historia: `caja/` está en
`.gitignore`, porque la subida web escribe dentro y no queremos los PDF subidos
en el repo. Lo commiteado es sólo `v1/`.

Esto se sostiene mientras La Caja sea el corpus por defecto de un despliegue
único. En cuanto cada cliente traiga sus propias facturas por endpoint, `caja/`
deja de tener sentido como global y estas capturas pasan a ser lo que ya son de
hecho: la semilla del tenant de demo. No está hecho, y hoy no hace falta.
