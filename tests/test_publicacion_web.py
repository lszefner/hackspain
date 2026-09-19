"""Lo que se despliega en Vercel es lo mismo que corre en el 8011.

`frontend/centralita_py/` y `frontend/public/phone_calls/` son copias que
genera `make web-centralita`. Una copia es una mentira en potencia: en cuanto
alguien toque `guion.py` y no republique, la web seguira demostrando una
politica que ya no es la nuestra, y lo hara en silencio y con buena cara. Este
fichero es lo que convierte ese silencio en un fallo de pytest.

Lo segundo que se prueba es la promesa de que en produccion no se llama a
ElevenLabs. No se prueba leyendo el codigo con buena fe: se prueba
comprobando que `voz.py` -- el unico fichero que sabe hablar con
api.elevenlabs.io -- no esta en el bundle, y que nada de lo que hay dentro lo
menciona. La cuota tiene tope de 1.000 caracteres y una URL publica la agota
en una tarde.
"""
import ast
import filecmp
import re
import json
import sys

import pytest

from phone_calls import consulta, guion, publicar_web as pub

pytestmark = pytest.mark.skipif(
    not pub.BUNDLE.is_dir(),
    reason="no esta publicado; corre `make web-centralita`")

# Lo que jamas puede viajar al bundle. `httpx` y la URL son el camino a
# ElevenLabs; `subprocess` es el camino a `say`, que en una Lambda no existe
# pero cuyo intento tardaria lo que tarda un timeout.
PROHIBIDO = ("httpx", "api.elevenlabs.io", "ELEVENLABS_API_KEY",
             "xi-api-key", "subprocess")


def _py_del_bundle():
    return sorted(pub.BUNDLE.rglob("*.py"))


# --------------------------------------------------------------- la copia
def test_los_modulos_son_copias_byte_a_byte():
    destino = pub.BUNDLE / "phone_calls"
    for nombre in pub.MODULOS:
        assert filecmp.cmp(pub.AQUI / nombre, destino / nombre, shallow=False), (
            f"{nombre} ha cambiado desde la ultima publicacion. "
            f"Corre `make web-centralita`.")


def test_los_datos_son_copias_byte_a_byte():
    destino = pub.BUNDLE / "phone_calls" / "datos"
    originales = [f for p in pub.DATOS for f in sorted((pub.AQUI / "datos").glob(p))]
    assert originales, "no hay export; corre `make export`"
    for f in originales:
        assert filecmp.cmp(f, destino / f.name, shallow=False), (
            f"datos/{f.name} ha cambiado. Corre `make web-centralita`.")
    assert {f.name for f in originales} == {f.name for f in destino.iterdir()}


def test_la_pagina_es_copia_byte_a_byte():
    publico = pub.PUBLICO
    assert filecmp.cmp(pub.AQUI / "estatico" / "index.html",
                       publico / "index.html", shallow=False)
    assert filecmp.cmp(pub.AQUI / "guiones_demo.json",
                       publico / "guiones_demo.json", shallow=False)
    for f in (pub.AQUI / "estatico").iterdir():
        if f.is_file() and f.name != "index.html":
            assert filecmp.cmp(f, publico / "estatico" / f.name, shallow=False), (
                f"estatico/{f.name} ha cambiado. Corre `make web-centralita`.")


def test_normalize_es_copia_y_el_init_es_un_stub():
    reglas = pub.BUNDLE / "rules_ingestion"
    assert filecmp.cmp(pub.RAIZ / "rules_ingestion" / "normalize.py",
                       reglas / "normalize.py", shallow=False)
    # El `__init__` de verdad importa `invoice` y `checks`, que arrastran el
    # paquete entero. Si algun dia se copiara tal cual, el bundle pasaria de
    # 83 KB a medio megabyte y empezaria a importar cosas que no usa.
    arbol = ast.parse((reglas / "__init__.py").read_text(encoding="utf-8"))
    assert not [n for n in ast.walk(arbol)
                if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert set(p.name for p in reglas.glob("*.py")) == {"__init__.py", "normalize.py"}


# ------------------------------------------------- cero ElevenLabs en runtime
def test_voz_py_no_esta_desplegado():
    desplegados = {f.name for f in _py_del_bundle()}
    desplegados |= {f.name for f in (pub.FRONTEND / "api").rglob("*.py")}
    assert "voz.py" not in desplegados
    assert "exportar.py" not in desplegados
    assert "servidor.py" not in desplegados


@pytest.mark.parametrize("aguja", PROHIBIDO)
def test_nada_de_lo_desplegado_sabe_llamar_a_elevenlabs(aguja):
    for f in _py_del_bundle() + list((pub.FRONTEND / "api").rglob("*.py")):
        assert aguja not in f.read_text(encoding="utf-8"), (
            f"{f.relative_to(pub.FRONTEND)} menciona '{aguja}'. En produccion "
            f"no puede haber ningun camino hacia ElevenLabs.")


def test_el_bundle_no_importa_nada_fuera_de_stdlib():
    # Si algun dia hiciera falta una dependencia, haria falta un
    # requirements.txt bajo frontend/ -- y eso dispara el framework preset de
    # Python en Vercel, que se traga TODAS las rutas del proyecto.
    assert not (pub.FRONTEND / "requirements.txt").exists()
    assert not (pub.FRONTEND / "pyproject.toml").exists()
    assert not (pub.FRONTEND / "Pipfile").exists()


# --------------------------------------------------------------------- voz
def test_el_indice_de_voz_apunta_a_ficheros_que_existen():
    indice = json.loads((pub.BUNDLE / "voz_indice.json").read_text("utf-8"))
    assert indice, "no se publico ni un audio"
    for texto, fichero in indice.items():
        assert (pub.PUBLICO / "voz" / fichero).is_file(), f"falta {fichero} ({texto[:40]})"


def test_el_audio_publicado_es_todo_de_una_sola_voz():
    # La cache mezcla mp3 de ElevenLabs y m4a de `say`. Publicar las dos haria
    # que la agente cambiara de voz a mitad de llamada.
    assert {f.suffix for f in (pub.PUBLICO / "voz").iterdir()} == {".mp3"}


def test_todo_lo_que_se_dira_en_la_demo_tiene_audio(caja):
    indice = json.loads((pub.BUNDLE / "voz_indice.json").read_text("utf-8"))
    faltan = [f for f in pub.frases(caja) if f not in indice]
    assert not faltan, (
        f"{len(faltan)} frases de la demo las dira el navegador, no Ines: "
        f"{[f[:50] for f in faltan]}")


# ------------------------------------------- el bundle conduce una llamada
@pytest.fixture(scope="module")
def caja():
    try:
        return consulta.cargar()
    except consulta.SinDatos as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def bundle():
    """Importa el arbol vendorizado, no el del repo: es el camino que correra
    en Lambda, con su `rules_ingestion` recortado y sus datos copiados."""
    sys.path.insert(0, str(pub.BUNDLE))
    for m in [k for k in sys.modules if k.split(".")[0]
              in ("phone_calls", "rules_ingestion")]:
        del sys.modules[m]
    try:
        import phone_calls.consulta as c
        import phone_calls.guion as g
        assert str(pub.BUNDLE) in g.__file__, "se importo el del repo, no la copia"
        yield g, c.cargar()
    finally:
        sys.path.remove(str(pub.BUNDLE))
        for m in [k for k in sys.modules if k.split(".")[0]
                  in ("phone_calls", "rules_ingestion")]:
            del sys.modules[m]


def test_la_copia_conduce_una_llamada_entera_sin_irse_de_la_lengua(bundle):
    g, caja_copia = bundle
    estado = g.avanzar({}, [], caja_copia)["estado"]
    for oye in (["Transportes Guadaira"], ["sí, correcto"],
                ["peo 2026 cero cuatro nueve cuatro"]):
        turno = g.avanzar(estado, oye, caja_copia)
        estado = turno["estado"]
        r = turno["respuesta"]
        for campo, valor in (r.get("retiene") or {}).items():
            assert str(valor) not in r["decir"], f"se dijo {campo}"
            assert str(valor) not in r["texto"], f"se pinto {campo}"
    assert turno["expediente"]["result"] == "ESCALAR"
    assert (r.get("retiene") or {}).keys() >= {"iban_en_la_factura",
                                               "iban_del_maestro"}


def test_la_copia_dice_exactamente_lo_mismo_que_el_repo(bundle, caja):
    """Byte a byte, turno a turno. Si divergen, la web miente."""
    g, caja_copia = bundle
    guiones = json.loads(
        (pub.AQUI / "guiones_demo.json").read_text("utf-8"))["llamadas"]
    for llamada in guiones:
        aqui = guion.avanzar({}, [], caja)["estado"]
        alla = g.avanzar({}, [], caja_copia)["estado"]
        for turno in llamada["turnos"]:
            a = guion.avanzar(aqui, turno["oye"], caja)
            b = g.avanzar(alla, turno["oye"], caja_copia)
            aqui, alla = a["estado"], b["estado"]
            assert a["respuesta"]["decir"] == b["respuesta"]["decir"]
            assert a["respuesta"]["texto"] == b["respuesta"]["texto"]
            assert aqui == alla


# ------------------------------------------------------------ procedencia
def test_la_procedencia_lleva_la_huella_de_lo_publicado():
    txt = (pub.BUNDLE / "PROCEDENCIA.md").read_text(encoding="utf-8")
    assert "no editar a mano" in txt
    # Un hash del contenido y no un commit de git: `publicar_web.py` vive en
    # `phone_calls/`, asi que un sha de HEAD cambiaria al commitear la propia
    # publicacion y el arbol no quedaria limpio jamas.
    assert re.search(r"huella \| `[0-9a-f]{16}`", txt)


def test_republicar_no_ensucia_el_arbol():
    """Publicar dos veces seguidas tiene que dar exactamente lo mismo, o
    `git status` mentiria en cada build y nadie volveria a mirarlo."""
    antes = (pub.BUNDLE / "PROCEDENCIA.md").read_bytes()
    pub.publicar()
    assert (pub.BUNDLE / "PROCEDENCIA.md").read_bytes() == antes
