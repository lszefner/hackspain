"""El fichero de oro, ejecutado por pytest.

Antes existia y solo se consultaba si alguien escribia `alberto valida` a
mano. Un fichero de referencia que nadie ejecuta no es una red: es un
fichero.
"""
from pathlib import Path

import pytest

from alberto.db import conectar
from alberto.validacion import RUTA_ORO, medir, regresiones

RAIZ = Path(__file__).resolve().parents[1]
BD = RAIZ / "alberto.db"

pytestmark = pytest.mark.skipif(
    not BD.exists() or not (RAIZ / RUTA_ORO).exists(),
    reason="necesita alberto.db y el fichero de oro; se genera con `alberto valida --congelar`")


@pytest.fixture(scope="module")
def con():
    return conectar(BD)


def test_ningun_documento_pierde_un_campo(con):
    fallos = regresiones(con, RAIZ / RUTA_ORO)
    assert fallos == [], "\n".join(fallos[:20])


def test_la_cobertura_no_baja(con):
    m = medir(con)
    assert m["listos"] >= 467, (
        f"solo {m['listos']} de {m['total']} documentos tienen los campos "
        f"que exigen las reglas; la linea base medida era 467")
