"""La centralita no se va de la lengua, no se atasca y no depende de la red.

Lo que de verdad se prueba aqui es lo primero. Una politica de divulgacion
que solo existe en un docstring es una promesa; `test_no_se_filtra_lo_retenido`
la convierte en algo que falla en pytest si alguien la rompe.

Los datos salen de `phone_calls/datos/`, el export congelado: ni base de
datos, ni Supabase, ni red. Los tests que lo necesitan se saltan si no esta
-- se regenera con `make export` -- y los parsers y la politica no lo
necesitan y corren siempre.
"""
import json
import re
import httpx
import pytest

from phone_calls import consulta, voz
from phone_calls.guion import (
    SILENCIO, avanzar, deletrear, eur_texto, eur_voz, nombre_voz, parsear_nif,
    parsear_pedido, parsear_proveedor, pedido_voz, respuesta,
)

RUTA_GUIONES = consulta.DATOS.parent / "guiones_demo.json"

necesita_export = pytest.mark.skipif(
    not (consulta.DATOS / "maestro.json").is_file(),
    reason="necesita el export; corre `make export`")


@pytest.fixture(scope="module")
def caja():
    try:
        return consulta.cargar()
    except consulta.SinDatos as exc:
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def guiones():
    return json.loads(RUTA_GUIONES.read_text(encoding="utf-8"))["llamadas"]


# ------------------------------------------------------------------ parsers
@pytest.mark.parametrize("dicho,espera", [
    (["PO-2026-0475"],                        "PO-2026-0475"),
    (["PO 2026 0475"],                        "PO-2026-0475"),
    (["peo 2026 cero cuatro siete cinco"],    "PO-2026-0475"),
    (["P.O. 2.026 0475"],                     "PO-2026-0475"),
    (["2026 475"],                            "PO-2026-0475"),
    (["el 475"],                              "PO-2026-0475"),
    (["cero cero cero siete"],                "PO-2026-0007"),
    (["el pedido siete"],                     "PO-2026-0007"),
    (["PO 2020 0717"],                        "PO-2020-0717"),
    # El limite, documentado a proposito: el reconocedor devuelve '475' para
    # 'cuatrocientos setenta y cinco', pero si no lo hace no se inventa nada.
    (["cuatrocientos setenta y cinco"],       None),
    (["buenos dias"],                         None),
    ([""],                                    None),
])
def test_parsear_pedido(dicho, espera):
    assert parsear_pedido(dicho) == espera


def test_una_alternativa_buena_gana_a_una_mala():
    """Chrome devuelve varias transcripciones y no siempre acierta la primera.

    Se recorren todas las alternativas en cada pasada antes de bajar a la
    siguiente: si no, la alternativa mala ganaria por encajar en una regla
    mas laxa.
    """
    assert parsear_pedido(["dice 9", "PO 2026 0475"]) == "PO-2026-0475"


@necesita_export
def test_los_proveedores_se_reconocen_por_su_nombre(caja):
    """Y ninguno casa con el nombre de otro: el maestro no es ambiguo."""
    provs = consulta.proveedores(caja)
    assert len(provs) >= 10
    for nif, p in provs.items():
        assert parsear_proveedor([p["razon_social"]], provs) == nif
        assert parsear_nif([nif], provs) == nif


def test_el_nif_casa_por_los_digitos_aunque_falle_la_letra():
    """El reconocedor destroza la inicial deletreada; los 8 digitos, no."""
    provs = {"A41220987": {"razon_social": "Transportes Guadaira S.A."}}
    assert parsear_nif(["ve 41 22 09 87"], provs) == "A41220987"


# ---------------------------------------------------------------------- voz
def test_los_importes_no_se_leen_en_crudo():
    """'12874.40' leido tal cual sale 'punto cuatro cero'.

    Y sin la coletilla 'centimos': por telefono se dice 'con cuarenta'.
    """
    assert eur_voz("12874.40") == "12874 euros con 40"
    assert eur_voz("398.00") == "398 euros"
    assert eur_voz(39804) == "398 euros con cero 4"   # 398,04, no 398,40
    assert eur_texto("12874.40") == "12.874,40 €"


def test_las_referencias_se_deletrean():
    assert deletrear("AS-00475") == "A. S., cero cero cuatro, siete cinco"
    assert pedido_voz("PO-2026-0475") == "P. O. 2026, cero cuatro siete cinco"


# ------------------------------------------------- politica de divulgacion
def _exp(decision, reglas, **extra):
    """Un expediente sintetico con la forma que deja `exportar.py`."""
    return {"pedido": "PO-2026-0001", "file_id": "x.pdf", "decision": decision,
            "reglas": reglas, "motivos": [], "factura": {"total": "100.00"},
            "proveedor": {"razon_social": "X S.L.", "condiciones": "60 dias",
                          "nif": "B00000000"},
            "pedido_maestro": {"importe_total": "100.00", "nif": "B00000000"},
            "asiento": None, "notas": {"generales": [], "en_revision": False},
            "procedencia": {}} | extra


def _regla(rule_id, status, codigos=(), entradas=()):
    return {"rule_id": rule_id, "status": status, "consecuencia": None,
            "reason_codes": list(codigos), "explicacion": "",
            "entradas": [{"campo": c, "estado": "present", "valor": v}
                         for c, v in entradas]}


def test_lo_que_no_pasa_no_es_lo_que_no_aplica():
    """Siete estados, no tres. `BLOCKED` no es `PASS` ni es `NOT_APPLICABLE`.

    Una regla BLOQUEADA no se pudo evaluar, que no es lo mismo que cumplirla.
    Tratarla como conforme le diria al proveedor que cobra cuando nadie lo
    ha comprobado.
    """
    r = respuesta(_exp("ESCALAR", [_regla("R1", "BLOCKED", ["UNUSABLE_INPUT"])]))
    assert "conforme" not in r["decir"]
    r2 = respuesta(_exp("PAGAR", [_regla("R1", "NOT_APPLICABLE", ["X"])]))
    assert "conforme" in r2["decir"]


def test_escalado_con_todo_en_pass_no_dice_conforme():
    """El motor puede escalar sin ninguna regla rota -- por una anotacion en
    la factura, por ejemplo. Entrar por reglas le diria que cobra."""
    r = respuesta(_exp("ESCALAR", [_regla("R1", "PASS"), _regla("R3", "PASS")]))
    assert "conforme" not in r["decir"]
    assert "revisando" in r["decir"] or "llamamos" in r["decir"]


def test_el_iban_no_se_dice_nunca():
    reglas = [_regla("R1", "VIOLATED", ["IBAN_MISMATCH"],
                     [("invoice.iban", "ES9100754477116000219042"),
                      ("supplier.iban", "ES7621000813610123456789")])]
    r = respuesta(_exp("ESCALAR", reglas))
    assert "ES9100754477116000219042" not in r["decir"]
    assert "ES7621000813610123456789" not in r["decir"]
    assert r["retiene"]["iban_del_maestro"] == "ES7621000813610123456789"


def test_el_iban_gana_a_cualquier_otro_codigo():
    """Si hay un IBAN que no cuadra, da igual que ademas falte una fecha:
    lo que se calla manda sobre lo que se cuenta."""
    reglas = [_regla("R6", "VIOLATED", ["REQUIRED_FIELD_MISSING"]),
              _regla("R1", "VIOLATED", ["IBAN_MISMATCH"],
                     [("invoice.iban", "ES1"), ("supplier.iban", "ES2")])]
    r = respuesta(_exp("ESCALAR", reglas))
    assert "datos de cobro" in r["decir"]
    assert "faltan" not in r["decir"].lower()


def test_el_nif_desconocido_no_revienta_ni_promete_una_ficha():
    """Su evidencia no trae IBAN, y sin ficha no hay a donde devolver la
    llamada: 'le llamamos al numero de su ficha' seria absurdo."""
    r = respuesta(_exp("ESCALAR", [_regla("R1", "VIOLATED", ["SUPPLIER_UNRESOLVED"])]))
    assert "su ficha" not in r["decir"]
    assert "compras" in r["decir"]


def test_el_pedido_ajeno_tambien_se_calla():
    """Confirmar que existe y que es de un tercero ya es dato del tercero."""
    r = respuesta(_exp("ESCALAR", [_regla("R3", "VIOLATED", ["ORDER_IDENTITY_UNUSABLE"],
                                          [("order.supplier_id", "P002")])]))
    assert "P002" not in r["decir"]
    assert r["retiene"]["de_quien_es_el_pedido"] == "P002"


def test_no_se_pronuncian_los_veredictos_internos():
    """El proveedor oye consecuencias, no vocabulario del motor.

    Y hay una razon de fondo: el motor marca `payment_authorized: false`
    siempre y llama a su veredicto `preliminary_decision`. Nada de esto
    autoriza un pago.
    """
    casos = [("PAGAR", [_regla("R1", "PASS")]),
             ("ESCALAR", [_regla("R3", "VIOLATED", ["ORDER_AMOUNT_MISMATCH"],
                                 [("invoice.total", "120"), ("order.total", "100")])]),
             ("NO_PAGAR", [_regla("R2", "VIOLATED", ["ERP_ALREADY_PAID"])])]
    for decision, reglas in casos:
        r = respuesta(_exp(decision, reglas))
        for palabra in ("PAGAR", "NO_PAGAR", "ESCALAR", "escalo", "escalado",
                        "preliminary", "autorizado"):
            assert palabra not in r["decir"], (decision, palabra)


def test_la_politica_se_ata_a_codigos_no_a_numeros_de_regla():
    """Las reglas se renumeraron al cambiar de motor: el R2 de hoy son
    duplicados y estado del ERP, no el importe. Mapear por numero daria
    respuestas falsas sin que nadie se entere."""
    codigos = {c for r in (
        [_regla("R9_inventada", "VIOLATED", ["ERP_ALREADY_PAID"])],) for c in r[0]["reason_codes"]}
    assert codigos == {"ERP_ALREADY_PAID"}
    r = respuesta(_exp("NO_PAGAR",
                       [_regla("R9_inventada", "VIOLATED", ["ERP_ALREADY_PAID"])],
                       asiento={"asiento_id": "AS-1", "fecha_registro": "2026-05-24",
                                "importe_esperado": "398.04", "estado": "PAGADA"}))
    assert "pagada" in r["decir"]


# ------------------------------------------------------------- la voz
# Ninguno de estos llama a ElevenLabs ni a `say`: AGENTS.md prohibe que los
# tests toquen APIs de pago, y ademas lo que se prueba aqui es la cache, el
# despacho y las caidas, no la calidad del audio.

@pytest.fixture
def cache_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(voz, "CACHE", tmp_path / "voz")
    return tmp_path / "voz"


def test_la_voz_lleva_el_motor_en_el_hash():
    """Misma frase, distinta voz -> distinto fichero. Los motores conviven."""
    a = voz.clave("Buenos d\u00edas.", "11l:M7m4UdXA2zH2Dhz4OzqV")
    b = voz.clave("Buenos d\u00edas.", "say:M\u00f3nica")
    assert a != b
    assert a == voz.clave("Buenos d\u00edas.", "11l:M7m4UdXA2zH2Dhz4OzqV")


def test_el_modelo_no_entra_en_el_hash(cache_tmp, monkeypatch):
    """`make voces` graba con el modelo bueno; un turno improvisado pide el
    rapido. Los dos tienen que encontrar el MISMO fichero, o el precalentado
    no sirve de nada."""
    llamadas = []
    monkeypatch.setattr(voz, "_elevenlabs",
                        lambda t, v, rapido: llamadas.append(rapido) or b"mp3")
    h1 = voz.sintetizar("Hola.", "11l:abc", rapido=False)
    h2 = voz.sintetizar("Hola.", "11l:abc", rapido=True)
    assert h1 == h2
    assert llamadas == [False]          # la segunda fue cache, no red
    assert voz.ruta(h1).suffix == ".mp3"


def test_sintetizar_nunca_lanza(cache_tmp, monkeypatch):
    """Un fallo de red en mitad de la llamada devuelve None, no una traza.
    El servidor manda `audio: null` y habla el navegador."""
    def revienta(*a, **k):
        raise httpx.ConnectError("sin red")
    monkeypatch.setattr(voz, "_elevenlabs", revienta)
    assert voz.sintetizar("Hola.", "11l:abc") is None
    assert voz.sintetizar("Hola.", "motor_inventado:x") is None
    assert voz.sintetizar("", "11l:abc") is None
    assert not list(cache_tmp.glob("*")) if cache_tmp.exists() else True


def test_sin_clave_no_se_elige_elevenlabs(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    elegida = voz.mejor_voz()
    assert elegida is None or elegida.startswith("say:")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_prueba")
    assert voz.mejor_voz() == "11l:" + voz.VOCES_11L[voz.VOZ_11L]


def test_ruta_rechaza_lo_que_no_es_un_hash(cache_tmp):
    for malo in ("..", "../../etc/passwd", "abc", "", "7ef969d71ec52311x",
                 "7EF969D71EC52311"):
        assert voz.ruta(malo) is None


def test_si_elevenlabs_falla_se_prueba_con_say_antes_que_el_navegador(cache_tmp, monkeypatch):
    """Es el caso que paso de verdad: cuota agotada a mitad de precalentado.

    Sin este peldano, las dos respuestas estrella de la demo -- el IBAN y el
    importe -- se habrian dicho con la voz del navegador mientras el resto
    sonaba a Ines.
    """
    from phone_calls.servidor import url_audio
    def sin_cuota(*a, **k):
        r = httpx.Response(401, json={"detail": {"status": "quota_exceeded",
                                                 "message": "9 credits"}},
                           request=httpx.Request("POST", "http://x"))
        raise httpx.HTTPStatusError("401", request=r.request, response=r)
    monkeypatch.setattr(voz, "_elevenlabs", sin_cuota)
    monkeypatch.setattr(voz, "_say", lambda t, n: b"m4a")
    url = url_audio("Hola.", "11l:abc", respaldo="say:M\u00f3nica")
    assert url and url.endswith(".m4a")
    assert "quota_exceeded" in voz.ultimo_error
    # y sin respaldo, None: que hable el navegador, no una traza
    assert url_audio("Hola.", "11l:abc", respaldo=None) is None


def test_el_nombre_resuelve_al_motor():
    from phone_calls.servidor import resolver_voz
    assert resolver_voz("Marina") == "11l:" + voz.VOCES_11L["Marina"]
    assert resolver_voz("M\u00f3nica") == "say:M\u00f3nica"
    assert resolver_voz("11l:xyz") == "11l:xyz"
    assert resolver_voz(None) is None
    assert voz.nombre_voz("11l:" + voz.VOCES_11L["Marina"]) == "Marina (ElevenLabs)"


def test_la_voz_por_defecto_es_marco(monkeypatch):
    """La voz seleccionada y la identidad del agente coinciden."""
    from phone_calls.guion import AGENTE
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_prueba")
    assert voz.mejor_voz() == "11l:woeaOojf4khJahry1fqM"
    assert AGENTE == voz.VOZ_11L == "Marco"


# Palabras que el motor de voz acentuaria en la silaba equivocada si van sin
# tilde: 'numero' sale 'nuMEro', 'centimos' 'cenTImos', 'esta pagada' 'ESta
# pagada'. De ahi venia que sonara a robot, y ninguna voz mejor lo arregla.
SIN_TILDE = re.compile(
    r"\b(numero|esta|razon|dias|dia|centimos|revision|emision|confirmelo|"
    r"ampliacion|amplio|telefono|informacion|mirandola|reemitala|reenviela|"
    r"escribanos|segun|digito|que pedido|si o no|reviselo|revisela|"
    r"verificacion|aqui|asi|credito|deposito|periodo)\b", re.I)


# ---------------------------------------- barrido sobre el export real
@necesita_export
def test_todos_los_pedidos_producen_una_respuesta(caja):
    """Ningun expediente hace saltar una excepcion."""
    fallos = []
    for pedido in consulta.pedidos(caja):
        exp = consulta.expediente_de_pedido(caja, pedido)
        try:
            assert respuesta(exp)["decir"].strip()
        except Exception as exc:                        # noqa: BLE001
            fallos.append((pedido, f"{type(exc).__name__}: {exc}"))
    assert not fallos, fallos


@necesita_export
def test_no_se_filtra_lo_retenido(caja):
    """EL test. Ningun valor retenido aparece en lo que se dice ni se pinta.

    Es lo que separa 'prometo que no digo el IBAN' de saberlo.
    """
    for pedido in consulta.pedidos(caja):
        r = respuesta(consulta.expediente_de_pedido(caja, pedido))
        for clave, valor in r["retiene"].items():
            assert str(valor) not in r["decir"], (pedido, clave)
            assert str(valor) not in r["texto"], (pedido, clave)


@necesita_export
def test_ningun_decir_lleva_una_palabra_sin_tilde(caja):
    """Lo que se dice en voz alta va acentuado: sin tildes el motor de voz
    acentua la silaba equivocada y suena a robot."""
    malas = []
    for pedido in consulta.pedidos(caja):
        r = respuesta(consulta.expediente_de_pedido(caja, pedido))
        malas += SIN_TILDE.findall(r["decir"])
    assert not malas, sorted(set(malas))


@necesita_export
def test_el_export_trae_lo_que_el_telefono_necesita(caja):
    """Cada expediente tiene decision, reglas y procedencia.

    La procedencia importa: sin el id del contexto y del ruleset, lo que el
    agente dice no es verificable contra el motor que lo decidio.
    """
    for pedido in consulta.pedidos(caja):
        exp = consulta.expediente_de_pedido(caja, pedido)
        assert exp["decision"] in ("PAGAR", "ESCALAR", "NO_PAGAR")
        assert exp["reglas"], pedido
        assert exp["procedencia"].get("context_id"), pedido
        assert consulta.nif_del_pedido(exp), pedido


# ------------------------------------------------------ la llamada completa
@necesita_export
def test_las_llamadas_del_guion_llegan_hasta_el_final(caja, guiones):
    """El fichero de la demo es el test de la demo.

    Se alimenta al parser con las transcripciones SUCIAS del json, las
    mismas que oiria en vivo.
    """
    for llamada in guiones:
        estado = avanzar({}, [], caja)["estado"]
        turno = None
        for t in llamada["turnos"]:
            turno = avanzar(estado, t["oye"], caja)
            estado = turno["estado"]
        assert estado["paso"] in ("RESPUESTA", "FIN"), llamada["id"]
        panel = turno["expediente"]
        assert panel is not None, llamada["id"]
        assert panel["pedido"] == llamada["espera"]["pedido"], llamada["id"]
        assert panel["result"] == llamada["espera"]["result"], llamada["id"]


@necesita_export
def test_solo_se_contesta_del_pedido_de_quien_llama(caja):
    """El expediente lleva importes y estado de pago de un tercero."""
    pedidos = consulta.pedidos(caja)
    otro = consulta.expediente_de_pedido(caja, pedidos[-1])
    ajeno = consulta.nif_del_pedido(otro)
    mio = next((consulta.nif_del_pedido(consulta.expediente_de_pedido(caja, p))
                for p in pedidos if consulta.nif_del_pedido(
                    consulta.expediente_de_pedido(caja, p)) != ajeno), None)
    if mio is None:
        pytest.skip("el export solo tiene un proveedor")
    turno = avanzar({"paso": "PEDIDO", "nif": mio, "fallos": 0},
                    [pedidos[-1]], caja)
    assert turno["expediente"] is None
    assert ajeno not in turno["respuesta"]["decir"]


@necesita_export
def test_tres_fallos_pasan_a_humano(caja):
    """Sin contador, un parser que no acierta repite la pregunta para siempre."""
    estado = avanzar({}, [], caja)["estado"]
    for _ in range(3):
        turno = avanzar(estado, ["mmm no se"], caja)
        estado = turno["estado"]
    assert estado["paso"] == "FIN"
    assert "persona" in turno["respuesta"]["decir"]


@necesita_export
def test_el_estado_va_y_vuelve(caja):
    """El servidor no guarda sesiones: el estado viaja en el cuerpo, y tiene
    que sobrevivir a un ida y vuelta por JSON."""
    estado = avanzar({}, [], caja)["estado"]
    ida = avanzar(json.loads(json.dumps(estado)), ["Transportes Guadaira"], caja)
    assert ida["estado"]["nif"] == "A41220987"
    vuelta = avanzar(json.loads(json.dumps(ida["estado"])), ["si"], caja)
    assert vuelta["estado"]["paso"] == "PEDIDO"
