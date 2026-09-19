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

`alberto` busca La Caja en `ALBERTO_CAJA`, luego en `./caja`, y si no en la
raíz del repo. Para correr contra una captura concreta:

```bash
ALBERTO_CAJA=caja_de_alberto/v2 ./venv/bin/python -m alberto.cli --lote lote2 todo \
    --salida outcomes_lote2.jsonl
```

El bridge ERP de esa misma versión:

```bash
python3 caja_de_alberto/v2/alberto_erp.py --puerto 8009
```

## Ojo con la duplicación

La Caja del lote 1 está **dos veces** en el repo: en la raíz (`facturas/`, el
Excel, `alberto_erp.py`) y en `v1/`. Son idénticas byte a byte. Se conserva así
porque la raíz es lo que esperan `main` y los tests, y `v1/` es el registro de
procedencia. Si algún día molesta, lo que sobra es la de la raíz, no ésta.
