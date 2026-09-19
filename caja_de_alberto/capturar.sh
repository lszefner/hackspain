#!/usr/bin/env bash
# Captura una version nueva de La Caja desde el repo de la organizacion.
#
#   ./caja_de_alberto/capturar.sh        -> la siguiente vN libre
#   ./caja_de_alberto/capturar.sh v3     -> esa, si no existe ya
#
# No sobrescribe: una captura es un registro, y un registro que se pisa no
# sirve para comparar contra el.
set -euo pipefail

ORIGEN="https://github.com/ikurotime/500-sombras-de-alberto.git"
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Version pedida, o la primera libre.
if [ $# -ge 1 ]; then
    VER="$1"
else
    n=1
    while [ -e "$AQUI/v$n" ]; do n=$((n + 1)); done
    VER="v$n"
fi

DESTINO="$AQUI/$VER"
if [ -e "$DESTINO" ]; then
    echo "error: $DESTINO ya existe. Las capturas no se sobrescriben." >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "clonando $ORIGEN ..."
git clone --quiet "$ORIGEN" "$TMP/caja"

cd "$TMP/caja"
COMMIT="$(git rev-parse HEAD)"
FECHA="$(git log -1 --format=%ad --date=format:'%d/%m/%Y %H:%M:%S %z')"
ASUNTO="$(git log -1 --format=%s)"
cd - >/dev/null

mkdir -p "$DESTINO"
rsync -a --exclude='.git/' --exclude='__pycache__/' --exclude='*.pyc' \
         --exclude='.DS_Store' "$TMP/caja/" "$DESTINO/"

cd "$DESTINO"
find . -type f -not -name 'MANIFIESTO.sha256' -not -name 'PROCEDENCIA.md' \
    | sort | xargs shasum -a 256 > MANIFIESTO.sha256

N_FICH="$(wc -l < MANIFIESTO.sha256 | tr -d ' ')"
N_FACT="$(ls facturas 2>/dev/null | wc -l | tr -d ' ')"
TAM="$(du -sh . | cut -f1 | tr -d ' ')"

cat > PROCEDENCIA.md <<FIN
# La Caja · $VER

Instantánea **literal** de lo que publicó la organización. No se edita nada de
aquí: si hay que cambiar algo, se cambia en nuestro código, no en la copia.

| | |
|---|---|
| Origen | \`$ORIGEN\` |
| Commit | \`$COMMIT\` |
| Fecha del commit | $FECHA |
| Asunto | \`$ASUNTO\` |
| Capturado | $(date '+%d/%m/%Y %H:%M:%S %z') |
| Tamaño | $TAM · $N_FICH ficheros · $N_FACT facturas |

## Comprobar que no se ha tocado

\`\`\`bash
cd caja_de_alberto/$VER && shasum -a 256 -c MANIFIESTO.sha256 | grep -v ': OK$'
\`\`\`

Sin salida es que está intacta.

## Qué cambió respecto a la anterior

\`\`\`bash
diff caja_de_alberto/<anterior>/MANIFIESTO.sha256 caja_de_alberto/$VER/MANIFIESTO.sha256
\`\`\`
FIN

echo
echo "  $VER capturada"
echo "  commit    $COMMIT"
echo "  ficheros  $N_FICH  ($N_FACT facturas)"
echo "  tamano    $TAM"
echo "  ruta      caja_de_alberto/$VER"
