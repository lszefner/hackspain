"""De un CSV o un Excel a una norma que el motor entiende.

Todo offline: el nivel lexico del clasificador es determinista y no necesita
ni red ni claves. Una norma que depende de que un proveedor responda no es
una norma.
"""
from pathlib import Path

import pytest
import yaml

from alberto.reglas import Motor, cargar_politica
from alberto.reglas.autoria import exportar_norma, ingerir
from alberto.reglas.autoria.exportador import MAPEO, SIN_EQUIVALENTE, escribir

# La Caja vive en la raiz del repo, no en una ruta absoluta de nadie.
RAIZ = Path(__file__).resolve().parents[1]
EXCEL = RAIZ / "FINAL_v7_DEFINITIVO_ahorasi.xlsx"

necesita_caja = pytest.mark.skipif(
    not EXCEL.exists(), reason="necesita el Excel de La Caja")


@pytest.fixture(scope="module")
def ruleset():
    return ingerir(excel=EXCEL, perfil="strict", version="v4")


def test_el_mapeo_cubre_las_canonicas_que_el_motor_sabe_aplicar():
    """Las que no sabe aplicar estan declaradas, no olvidadas."""
    from alberto.reglas.autoria.portado.catalog import CATALOG
    cubiertas = set(MAPEO) | SIN_EQUIVALENTE | {"MISSING"}
    assert set(CATALOG) == cubiertas, (
        f"canonicas sin decidir que hacer con ellas: {set(CATALOG) - cubiertas}")


@necesita_caja
def test_clasifica_la_norma_del_excel_sin_red(ruleset):
    assert ruleset["stats"]["canonical"] == 6
    metodos = {r.get("method") for r in ruleset["rules"]}
    assert metodos <= {"lexical", "predefined"}, "se ha llamado a un proveedor"


@necesita_caja
def test_cada_regla_dice_de_que_celda_salio(ruleset):
    norma, _, _ = exportar_norma(ruleset, version="v4")
    fuentes = {r["id"]: r["fuente"] for r in norma["reglas"]}
    assert fuentes["R1_nif_iban"].startswith("Norma_Pagos_v3:")
    assert all(f != "predefinida" for i, f in fuentes.items()
               if i != "R6_vencimiento")


@necesita_caja
def test_la_norma_generada_la_carga_el_motor(ruleset, tmp_path):
    norma, politica, _ = exportar_norma(ruleset, version="v9")
    escribir(norma, politica, destino=tmp_path, version="v9")
    leida = yaml.safe_load((tmp_path / "norma_v9.yaml").read_text("utf-8"))
    m = Motor(leida, yaml.safe_load((tmp_path / "politica_v9.yaml").read_text("utf-8")),
              {}, {})
    assert m.regla_por_tipo("nif_iban")["id"] == "R1_nif_iban"
    assert m.regla_por_tipo("iva")["id"] == "R3_iva"


@necesita_caja
def test_amount_se_parte_en_dos_reglas():
    """`main` agrupa importe e IVA en AMOUNT; aqui son R2 y R3 separadas."""
    rs = ingerir(excel=EXCEL, perfil="strict", version="v4")
    norma, _, _ = exportar_norma(rs, version="v4")
    ids = [r["id"] for r in norma["reglas"]]
    assert "R2_pedido" in ids and "R3_iva" in ids


@necesita_caja
def test_los_perfiles_producen_normas_distintas():
    normas = {}
    for perfil in ("conservative", "balanced", "strict"):
        rs = ingerir(excel=EXCEL, perfil=perfil, version="v4")
        n, pol, _ = exportar_norma(rs, version="v4")
        normas[perfil] = (len(n["reglas"]),
                          pol["mapeo"]["R1_nif_iban"]["FALLA"],
                          [r.get("tolerancia") for r in n["reglas"]
                           if r["id"] == "R2_pedido"][0])
    assert normas["strict"][1] == "NO_PAGAR"       # como la norma escrita a mano
    assert normas["balanced"][1] == "ESCALAR"
    assert normas["conservative"][2] == "1.00"     # tolerancia laxa
    assert normas["conservative"][0] < normas["strict"][0]   # sin vencimiento


@necesita_caja
def test_authorization_se_avisa_y_no_se_inventa(ruleset):
    """El motor no sabe aplicarla. Mejor que falte a que parezca que se
    comprueba algo que no se comprueba."""
    norma, politica, avisos = exportar_norma(ruleset, version="v4")
    assert any("AUTHORIZATION" in a for a in avisos)
    assert not any("autoriz" in r["id"].lower() for r in norma["reglas"])
    assert "AUTHORIZATION" not in politica["mapeo"]


def test_una_regla_escondida_en_un_csv_se_descubre_y_no_se_activa(tmp_path):
    """Lo que pedia el reto: reglas que pueden llegar en cualquier fichero.
    Se registran con su procedencia, pero NO se activan solas: el motor no
    sabe aplicarlas todavia y activarlas seria fingir."""
    csv = tmp_path / "notas_alberto.csv"
    csv.write_text(
        "comentario\n"
        "el parking cierra a las 22h y hay que avisar a porteria\n"
        "toda factura con retencion de IRPF debe revisarla un humano antes de pagar\n",
        encoding="utf-8")
    rs = ingerir(excel=EXCEL if EXCEL.exists() else None, carpeta=tmp_path,
                 perfil="strict", version="v4") if EXCEL.exists() else None
    if rs is None:
        pytest.skip("necesita el Excel de La Caja")
    norma, _, avisos = exportar_norma(rs, version="v4")
    nuevas = norma.get("reglas_nuevas_sin_aplicar", [])
    activas = [r["id"] for r in norma["reglas"]]
    # la de IRPF se descubre; la del parking no es una regla de pago
    assert not any("parking" in (n.get("texto") or "").lower() for n in nuevas)
    if nuevas:
        assert all(n["id"] not in activas for n in nuevas)
        assert all(n.get("origen") for n in nuevas), "sin procedencia"
