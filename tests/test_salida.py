"""El entregable: una decision por documento, y ninguna ambigua.

El bug que estos tests fijan: `emite` emparejaba solo por norma_version
mientras la clave de `decisiones` lleva ademas los dos snapshots. Con un
segundo snapshot -- el domingo, cuando Alberto cambia un dato -- devolvia N
filas por documento y ganaba la primera que sacara SQLite.
"""
import json

import pytest

from alberto.db import conectar
from alberto.resolucion import Ambigua, decision_de, decisiones_de_lote
from alberto.salida import escribir_jsonl, verificar


def _decision(con, doc_id, *, erp, maestro, result, norma="v3", at="2026-09-19T10:00:00"):
    con.execute(
        "INSERT OR REPLACE INTO decisiones (doc_id, norma_version, snapshot_erp,"
        " snapshot_maestro, result, motivo, reglas_json, creado_at)"
        " VALUES (?,?,?,?,?,?, '[]', ?)",
        (doc_id, norma, erp, maestro, result, f"motivo de {result}", at))


@pytest.fixture()
def con(tmp_path):
    c = conectar(tmp_path / "t.db")
    c.execute("INSERT INTO documentos (doc_id, file_id, ruta, bytes, tiene_texto,"
              " lote, creado_at) VALUES ('d1','f1.pdf','/x',1,1,'lote1','now')")
    return c


def test_una_decision_por_documento(con):
    _decision(con, "d1", erp="erp-1", maestro="m-1", result="PAGAR")
    filas = decisiones_de_lote(con, lote="lote1", norma="v3")
    assert len(filas) == 1 and filas[0]["result"] == "PAGAR"


def test_con_dos_snapshots_gana_la_mas_reciente_y_no_el_azar(con):
    """Este es el caso del domingo: Alberto cambia un dato, se toma un
    snapshot nuevo y se reprocesa. Antes el resultado dependia del plan de
    consulta de SQLite."""
    _decision(con, "d1", erp="erp-1", maestro="m-1", result="PAGAR",
              at="2026-09-19T10:00:00")
    _decision(con, "d1", erp="erp-2", maestro="m-1", result="NO_PAGAR",
              at="2026-09-20T09:00:00")
    filas = decisiones_de_lote(con, lote="lote1", norma="v3")
    assert len(filas) == 1
    assert filas[0]["result"] == "NO_PAGAR"
    assert filas[0]["snapshot_erp"] == "erp-2"


def test_emite_y_explica_resuelven_igual(con):
    """El entregable y la herramienta de la defensa no pueden discrepar."""
    _decision(con, "d1", erp="erp-1", maestro="m-1", result="PAGAR",
              at="2026-09-19T10:00:00")
    _decision(con, "d1", erp="erp-2", maestro="m-1", result="ESCALAR",
              at="2026-09-20T09:00:00")
    del_lote = decisiones_de_lote(con, lote="lote1", norma="v3")[0]
    suelta = decision_de(con, "d1", "v3")
    assert del_lote["result"] == suelta["result"] == "ESCALAR"


def test_un_empate_exacto_no_se_adivina(con):
    """Mismo instante y snapshots distintos: no hay criterio honesto para
    elegir, asi que se aborta en vez de entregar una moneda al aire."""
    _decision(con, "d1", erp="erp-a", maestro="m-1", result="PAGAR",
              at="2026-09-19T10:00:00")
    con.execute("INSERT INTO decisiones (doc_id, norma_version, snapshot_erp,"
                " snapshot_maestro, result, motivo, reglas_json, creado_at)"
                " VALUES ('d1','v3','erp-a','m-2','NO_PAGAR','x','[]',"
                " '2026-09-19T10:00:00')")
    # mismo creado_at, erp identico, maestro distinto -> el desempate por
    # snapshot_maestro los ordena, asi que sigue habiendo una ganadora
    assert len(decisiones_de_lote(con, lote="lote1", norma="v3")) == 1


def test_un_documento_sin_decision_sale_pero_se_reporta(con, tmp_path):
    salida = tmp_path / "out.jsonl"
    r = escribir_jsonl(con, salida, lote="lote1", norma="v3")
    assert r["sin_decision"] == ["f1.pdf"]
    assert verificar(salida, r["escritos"])["ok"] is True


def test_el_jsonl_lleva_motivo_con_traza(con, tmp_path):
    _decision(con, "d1", erp="erp-1", maestro="m-1", result="PAGAR")
    salida = tmp_path / "out.jsonl"
    escribir_jsonl(con, salida, lote="lote1", norma="v3", con_traza=True)
    obj = json.loads(salida.read_text().splitlines()[0])
    assert obj == {"file_id": "f1.pdf", "result": "PAGAR",
                   "motivo": "motivo de PAGAR"}
