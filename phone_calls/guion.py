"""La conversacion: que se entiende, que se contesta y que NO se cuenta.

Tres piezas:

  - los parsers, que convierten lo que el reconocedor de voz creyo oir en un
    proveedor o en un pedido;
  - `respuesta()`, la politica de divulgacion: dado un expediente, que se
    dice en voz alta y que se calla;
  - `avanzar()`, la maquina de estados, que es una funcion casi pura: el
    estado entra y sale en el cuerpo de la peticion, el servidor no guarda
    sesiones.

La politica es la parte que importa. Un proveedor al telefono no esta
verificado: no sabemos que quien llama es quien dice ser. Contarle a esa voz
cual es el IBAN bueno es exactamente el guion de un fraude de cambio de
cuenta, asi que hay cosas que el sistema sabe y no dice. `retiene` las lleva
con su valor real para que se puedan ENSENAR en pantalla mientras la voz
calla, y para que un test compruebe que no se filtraron.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from phone_calls import consulta

MAX_FALLOS = 3

# Lo que se dice mientras se consulta la base. Es el unico hueco de la
# conversacion en el que un humano callaria, y taparlo es lo que hace que
# deje de sonar a formulario.
MULETILLA = "Un momento, que lo miro."

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre")

_DIGITO = {"cero": "0", "uno": "1", "una": "1", "un": "1", "dos": "2",
           "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6",
           "siete": "7", "ocho": "8", "nueve": "9"}

_NOMBRE_DIGITO = {"0": "cero", "1": "uno", "2": "dos", "3": "tres",
                  "4": "cuatro", "5": "cinco", "6": "seis", "7": "siete",
                  "8": "ocho", "9": "nueve"}

# Palabras de forma societaria: no distinguen a un proveedor de otro.
_SOCIETARIO = {"sl", "sa", "sc", "s", "l", "a", "de", "del", "la", "las",
               "los", "y"}

RE_PO = re.compile(r"\bp\s?o\s?[\s-]?(\d{4})[\s-]?(\d{4})\b")
# Numeros escritos en letra que `_DIGITO` no cubre. Ver `_numeral_opaco`.
_NUMERAL_OPACO = re.compile(
    r"\b(diez|once|doce|trece|catorce|quince|dieci\w*|veinte|veinti\w*|"
    r"treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa|"
    r"cien|ciento|\w*cientos|quinientos|mil|millon\w*)\b")
RE_FACTURA = re.compile(r"\b(?:fa|f)[\s.-]?\d{3,}\b")


# ------------------------------------------------------------- normalizar
def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _normalizar(t: str) -> str:
    """Minusculas, sin tildes y sin puntuacion.

    El punto se sustituye ANTES de cualquier `isdigit()`: el reconocedor
    devuelve '2.026' tan a menudo como '2026'.
    """
    return re.sub(r"\s+", " ", re.sub(r"[.\-_/,;:]", " ", _sin_tildes(t))).strip()


def _a_digitos(t: str) -> str:
    """'p o 2026 cero cuatro 75' -> '20260475'. Solo digitos, en orden.

    Los digitos se sacan de DENTRO del token, no solo de los tokens que son
    enteramente numericos: un CIF vuelve pegado ('a41220987') y token a
    token se perderia completo.
    """
    fuera = []
    for tok in _normalizar(t).split():
        if tok in _DIGITO:
            fuera.append(_DIGITO[tok])
        else:
            fuera.append("".join(c for c in tok if c.isdigit()))
    return "".join(fuera)


def _numeral_opaco(t: str) -> bool:
    """¿Hay un numero escrito en letra que no sabemos convertir?

    'cuatrocientos setenta y cinco' solo aporta el 'cinco' a `_a_digitos`, y
    la pasada tolerante lo convertiria en PO-2026-0005: un pedido que existe
    y no es el suyo. Inventarse una referencia valida es mucho peor que no
    entender, porque nadie se entera. Si aparece uno de estos, esa
    alternativa no llega a la pasada tolerante.
    """
    return bool(_NUMERAL_OPACO.search(_normalizar(t)))


# ---------------------------------------------------------------- parsers
def parsear_pedido(textos: list[str], anio: str = "2026") -> str | None:
    """'peo 2026 cero cuatro siete cinco' -> 'PO-2026-0475'.

    `textos` son TODAS las alternativas que dio el reconocedor, en su orden
    de confianza. Se hacen tres pasadas, de la mas fiable a la mas
    tolerante, y dentro de cada pasada se recorren todas las alternativas:
    asi una alternativa mala nunca gana a una buena por el mero hecho de
    encajar en una regla mas laxa.

    La tercera pasada es la que salva la llamada. El reconocedor se come los
    ceros a la izquierda -- 'el cuatro setenta y cinco' vuelve como '475' --
    y el `zfill` los repone.

    `anio` es parametro y no constante porque `PO-2020-0717` existe.
    """
    for t in textos:                                   # 1) ya viene formado
        m = RE_PO.search(_normalizar(t))
        if m:
            return f"PO-{m.group(1)}-{m.group(2)}"
    for t in textos:                                   # 2) ocho digitos
        m = re.search(r"(19|20)(\d{2})(\d{4})", _a_digitos(t))
        if m:
            return f"PO-{m.group(1)}{m.group(2)}-{m.group(3)}"
    for t in textos:                                   # 3) solo la cola
        if _numeral_opaco(t):
            continue
        d = _a_digitos(t)
        if d.startswith(anio):
            d = d[len(anio):]
        if 1 <= len(d) <= 4:
            return f"PO-{anio}-{d.zfill(4)}"
    return None


def _raices(razon_social: str) -> list[str]:
    """Las palabras que distinguen a un proveedor, recortadas a la raiz."""
    return [w[:5] for w in _normalizar(razon_social).split()
            if len(w) > 3 and w not in _SOCIETARIO]


def parsear_proveedor(textos: list[str],
                      provs: dict[str, dict]) -> str | None:
    """'transporte guadaira' -> 'A41220987'.

    Se compara por RAIZ y no por palabra entera porque el reconocedor casi
    nunca devuelve la razon social clavada: 'transportes' vuelve en singular
    y 'Montcada' pierde la te. Exigir la palabra exacta hace que la llamada
    se atasque en el primer turno.

    Y se puntua en vez de exigir todas las raices, con la condicion de que
    haya UN ganador destacado. Asi 'electricidad moncada' identifica a
    Electricidad Montcada con una sola raiz -- es la unica que la tiene --
    pero 'benimaclet' a secas no devuelve nada, porque Construcciones
    Benimaclet e Informatica Benimamet empatan y adivinar seria peor que
    preguntar otra vez.
    """
    for t in textos:
        piezas = _normalizar(t).split()
        marcador = sorted(
            ((sum(any(p.startswith(r) for p in piezas)
                  for r in _raices(v["razon_social"])), nif)
             for nif, v in provs.items()), reverse=True)
        if marcador and marcador[0][0] > 0 and (
                len(marcador) == 1 or marcador[0][0] > marcador[1][0]):
            return marcador[0][1]
    return None


def parsear_nif(textos: list[str], provs: dict[str, dict]) -> str | None:
    """Red de seguridad: casa por los 8 digitos e ignora la letra.

    El reconocedor destroza la inicial deletreada ('be' vuelve como 'b',
    've' o 'v'), pero los ocho digitos son unicos entre los once
    proveedores del maestro.
    """
    for t in textos:
        d = _a_digitos(t)
        for nif in provs:
            if len(nif) >= 9 and nif[1:9] in d:
                return nif
    return None


def _afirma(textos: list[str]) -> bool | None:
    for t in textos:
        n = f" {_normalizar(t)} "
        if re.search(r" (si|correcto|exacto|eso es|afirmativo|claro) ", n):
            return True
        if re.search(r" (no|negativo|incorrecto|para nada) ", n):
            return False
    return None


# ----------------------------------------------------------------- voz
def _dec(valor) -> tuple[str, str]:
    """'12874.40' o 1287440 centimos -> ('12874', '40')."""
    if isinstance(valor, int):
        valor = f"{valor // 100}.{valor % 100:02d}"
    ent, _, dec = str(valor or "0").partition(".")
    return (ent or "0").lstrip("+"), (dec + "00")[:2]


def eur_voz(valor) -> str:
    """Para el sintetizador. '12874.40' -> '12874 euros con 40'.

    Leido en crudo, el punto decimal sale como 'punto' y la cifra suena a
    robot de 2005. Es el detalle que mas se nota y el mas facil de olvidar.

    Y se dice 'con cuarenta', no 'con cuarenta centimos': nadie remata una
    cifra por telefono con la palabra centimos, y repetirla dos veces en la
    misma frase -- que es lo que hace el caso del importe -- es lo mas
    cantarin de todo el guion.
    """
    ent, dec = _dec(valor)
    if not int(dec):
        return f"{ent} euros"
    # '398 euros con 4' se oye igual que 398,40. Los decimales por debajo de
    # diez se dicen con el cero delante, como se dictan los precios.
    return f"{ent} euros con " + (f"cero {int(dec)}" if int(dec) < 10 else dec)


def eur_texto(valor) -> str:
    """Para la pantalla. '12874.40' -> '12.874,40 EUR'."""
    ent, dec = _dec(valor)
    signo, ent = ("-", ent[1:]) if ent.startswith("-") else ("", ent)
    return f"{signo}{int(ent):,}".replace(",", ".") + f",{dec} €"


def deletrear(s: str, grupo: int = 3) -> str:
    """'AS-00475' -> 'A. S., cero cero cuatro, siete cinco'.

    El guion se descarta en vez de convertirse en un punto: un punto suelto
    entre dos letras hace que el sintetizador diga 'a ese punto'.

    Y los digitos se agrupan de tres en tres. No es cosmetico: nueve digitos
    seguidos salen con contorno plano y son imposibles de seguir de oido. La
    coma le hace al motor una pausa con reinicio de contorno, que es como
    los agrupa un humano al dictar.
    """
    piezas, tanda, eran_letras = [], [], None
    def cerrar():
        if tanda:
            piezas.append(" ".join(tanda)); tanda.clear()
    for c in (s or ""):
        if not c.isalnum():
            continue
        # Las letras seguidas van juntas ('A. S.'): una coma entre ellas mete
        # una pausa donde nadie la hace. Los digitos, de tres en tres.
        if c.isalpha() != eran_letras:
            cerrar(); eran_letras = c.isalpha()
        tanda.append(f"{c}." if c.isalpha() else _NOMBRE_DIGITO[c])
        if not eran_letras and len(tanda) == grupo:
            cerrar()
    cerrar()
    return ", ".join(piezas)


def pedido_voz(pedido: str) -> str:
    """'PO-2026-0475' -> 'P. O. 2026, cero cuatro siete cinco'.

    `deletrear` sirve para un asiento, pero aplicado a un pedido convierte
    el ano en 'dos cero dos seis'. Al telefono el ano se dice entero y solo
    la cola se deletrea.

    'PE-O' se escribe 'P. O.' porque el motor espanol lee 'PE-O' como la
    palabra 'peo'. El punto tras letra suelta fuerza el nombre de la letra.
    """
    m = re.fullmatch(r"PO-(\d{4})-(\d{4})", pedido or "")
    if not m:
        return deletrear(pedido)
    cola = " ".join(_NOMBRE_DIGITO[c] for c in m.group(2))
    return f"P. O. {m.group(1)}, {cola}"


def nombre_voz(razon_social: str) -> str:
    """'Suministros Levante S.L.' -> 'Suministros Levante'.

    La base guarda la forma societaria y ninguna recepcionista la dice en
    voz alta: 'Suministros Levante ese ele' delata a la maquina. En la
    pantalla se queda entera.
    """
    return re.sub(r"[,\s]*\b(S\.?\s?[LAlaCc]\.?|S\.?\s?Coop\.?|S\.?L\.?U\.?)\s*$",
                  "", (razon_social or "").strip()).strip(" ,.")


def fecha_voz(fecha: str) -> str:
    """'2026-05-24' o '24/05/2026' -> '24 de mayo'.

    El ERP devuelve las fechas en DD/MM/YYYY y el resto de la base en ISO.
    Leida en crudo, cualquiera de las dos sale con 'barra' o con 'guion'.
    """
    t = (fecha or "").strip()
    try:
        d = (date(*(int(x) for x in reversed(t.split("/")))) if "/" in t
             else date.fromisoformat(t))
    except (ValueError, TypeError):
        return t
    return f"{d.day} de {MESES[d.month - 1]}"


# --------------------------------------------------- politica de divulgacion
def _di(decir: str, texto: str | None = None, *,
        divulga: list[str], retiene: dict) -> dict:
    return {"decir": decir, "texto": texto or decir,
            "divulga": divulga, "retiene": {k: v for k, v in retiene.items()
                                            if v not in (None, "")}}


# El motor devuelve `reason_codes` estables por regla. La politica se ata a
# ELLOS y no al texto del motivo: los codigos son contrato, las frases no.
#
# Y se ata al codigo, no al numero de regla, porque las reglas se
# renumeraron al cambiar de motor -- el R2 de hoy son duplicados y estado
# del ERP, no el importe. Un mapeo por numero daria respuestas falsas por
# telefono sin que nadie se entere.

# Lo que NO se le cuenta a una voz sin verificar, con lo que se retiene.
SILENCIO = {
    # Quien llama puede no ser quien dice ser, y el IBAN del maestro es el
    # dato con el que se comete el fraude de cambio de cuenta. No se
    # confirma, no se desmiente, y sobre todo no se dice cual es el bueno.
    "IBAN_MISMATCH": ("Tenemos una incidencia con sus datos de cobro que no le "
                      "puedo tratar por tel\u00e9fono. Vamos a llamarle nosotros al "
                      "n\u00famero que consta en su ficha para verificarlo.",
                      (("invoice.iban", "iban_en_la_factura"),
                       ("supplier.iban", "iban_del_maestro"))),
    # Decir "ese pedido es de otro" ya confirma que existe y que es ajeno.
    "ORDER_IDENTITY_UNUSABLE": ("Ese pedido no le corresponde seg\u00fan nuestros "
                                "registros. Lo paso a verificaci\u00f3n y le "
                                "llamamos nosotros.",
                                (("order.supplier_id", "de_quien_es_el_pedido"),)),
    "DUPLICATE_APPROVED_RECORD": ("Hay una incidencia con esa factura que "
                                  "estamos verificando. Le llamamos nosotros.",
                                  ()),
    "SOFT_DUPLICATE_MATCH": ("Hay una incidencia con esa factura que estamos "
                            "verificando. Le llamamos nosotros.", ()),
}


def _valor(regla: dict, campo: str):
    """El valor concreto de una entrada de la regla.

    Sustituye a la vieja `evidencia`: el motor nuevo guarda cada entrada con
    su estado y su valor, que es de donde salen las cifras que se dicen.
    """
    for e in regla.get("entradas") or []:
        if e.get("campo") == campo and e.get("estado") == "present":
            return e.get("valor")
    return None


def _codigos(reglas: list[dict]) -> list[tuple[str, dict]]:
    """(codigo, regla) de todo lo que no paso, en orden de severidad.

    `PASS` y `NOT_APPLICABLE` no cuentan. Los demas estados si: `BLOCKED`,
    `UNSUPPORTED` y `ERROR` significan que la regla NO se pudo evaluar, que
    no es lo mismo que cumplirla. Tratarlos como conformes le diria al
    proveedor que cobra cuando nadie lo ha comprobado.
    """
    fuera = []
    for r in reglas:
        if r.get("status") in ("PASS", "NOT_APPLICABLE"):
            continue
        for c in r.get("reason_codes") or []:
            fuera.append((c, r))
    return fuera


def respuesta(exp: dict) -> dict:
    """Que se le dice al proveedor, y que se le calla.

    Se entra por la DECISION y se afina por codigo, nunca al reves. El motor
    puede decidir ESCALAR con todas las reglas en PASS -- por una anotacion
    en la factura, por ejemplo -- y entrar por reglas le diria a ese
    proveedor que su factura esta conforme.

    Tampoco se pronuncian 'PAGAR', 'NO_PAGAR' ni 'ESCALAR': son vocabulario
    interno. El proveedor oye consecuencias. Y hay una razon de fondo: el
    motor marca `payment_authorized: false` siempre y llama a su veredicto
    `preliminary_decision`. Nada de esto autoriza un pago, asi que la
    centralita no puede prometer uno.
    """
    codigos = _codigos(exp.get("reglas") or [])
    por_codigo = dict(codigos)

    # 1. Lo que se calla gana a todo lo demas: si hay un IBAN que no cuadra,
    #    da igual que ademas falte una fecha.
    for codigo, (frase, campos) in SILENCIO.items():
        if codigo in por_codigo:
            r = por_codigo[codigo]
            retiene = {etiqueta: _valor(r, campo) for campo, etiqueta in campos}
            if codigo == "IBAN_MISMATCH":
                retiene["cual_es_el_bueno"] = "el del maestro"
            return _di(frase, divulga=["existe_una_incidencia"], retiene=retiene)

    # 2. Conforme, solo si el motor lo dice.
    if exp.get("decision") == "PAGAR" and not codigos:
        return _conforme(exp)

    # 3. Lo que si se cuenta, por orden de utilidad para quien llama.
    for codigo, r in codigos:
        hecha = _explicable(codigo, r, exp)
        if hecha:
            return hecha

    return _generica(exp)


def _explicable(codigo: str, r: dict, exp: dict) -> dict | None:
    """Los codigos que se pueden contar, y como se cuentan."""
    if codigo == "ERP_ALREADY_PAID":
        asi = exp.get("asiento") or {}
        if not asi:
            return _di("Esa factura ya consta como pagada. Revise sus "
                       "extractos.", divulga=["ya_pagada"], retiene={})
        return _di(f"Esa factura ya est\u00e1 pagada. Figura en el asiento "
                   f"{deletrear(asi['asiento_id'])}, con fecha "
                   f"{fecha_voz(asi['fecha_registro'])}, por "
                   f"{eur_voz(asi['importe_esperado'])}. Revise el extracto "
                   f"de esa fecha.",
                   f"Pagada: asiento {asi['asiento_id']}, "
                   f"{asi['fecha_registro']}, {eur_texto(asi['importe_esperado'])}.",
                   divulga=["asiento_id", "fecha_registro", "importe"], retiene={})

    if codigo == "ORDER_AMOUNT_MISMATCH":
        fac = _valor(r, "invoice.total") or (exp.get("factura") or {}).get("total")
        ped = _valor(r, "order.total") or (exp.get("pedido_maestro") or {}).get("importe_total")
        if fac is None or ped is None:
            return None
        desvio = abs(float(fac) - float(ped))
        return _di(f"Su factura viene por {eur_voz(fac)} y el pedido est\u00e1 "
                   f"aprobado por {eur_voz(ped)}. Hay {eur_voz(desvio)} de "
                   f"diferencia. Si el pedido se ampli\u00f3 necesitamos la "
                   f"ampliaci\u00f3n firmada; si no, un abono por esa diferencia.",
                   f"Factura {eur_texto(fac)} frente a pedido {eur_texto(ped)}: "
                   f"{eur_texto(desvio)} de diferencia.",
                   divulga=["importe_factura", "importe_pedido", "desvio"],
                   retiene={})

    if codigo in ("VAT_AMOUNT_MISMATCH", "VAT_RATE_INVALID", "LINE_SUM_MISMATCH",
                  "TOTAL_MISMATCH"):
        return _di("Las cifras de su factura no cuadran entre s\u00ed: la base, el "
                   "IVA y el total. Rev\u00edsela y reem\u00edtala, y la tramitamos.",
                   divulga=["aritmetica"], retiene={})

    if codigo == "REQUIRED_FIELD_MISSING":
        return _di("A su factura le faltan datos obligatorios y no la podemos "
                   "tramitar. Reenv\u00edela completa, por favor.",
                   divulga=["faltan_datos"], retiene={})

    if codigo == "FUTURE_INVOICE_DATE":
        return _di("La fecha de emisi\u00f3n de su factura es posterior a hoy. "
                   "Corr\u00edjala y reenv\u00edela.", divulga=["fecha"], retiene={})

    if codigo == "PAYMENT_TERMS_EXCEEDED":
        return _di("Su factura lleva m\u00e1s tiempo del que marcan sus "
                   "condiciones de pago, as\u00ed que la hemos apartado para que "
                   "la mire una persona. Le llamamos nosotros.",
                   divulga=["fuera_de_plazo"], retiene={})

    if codigo in ("SUPPLIER_UNRESOLVED", "TAX_ID_MISMATCH"):
        return _di("Ese CIF no nos consta dado de alta como proveedor. Si es "
                   "un alta nueva, tiene que pasar por el departamento de "
                   "compras antes de que podamos tramitar ninguna factura.",
                   divulga=["nif_no_dado_de_alta"], retiene={})

    if codigo == "SUPPLIER_INACTIVE":
        return _di("Su ficha de proveedor figura como inactiva. Hable con "
                   "compras para reactivarla.", divulga=["inactivo"], retiene={})

    if codigo in ("ERP_PAYMENT_STATE_UNUSABLE", "HISTORY_COVERAGE_INCOMPLETE",
                  "DUPLICATE_SUBMISSION", "ALREADY_PAID"):
        return _di("Estamos verificando el estado de esa factura y todav\u00eda no "
                   "le puedo confirmar nada. Le llamamos nosotros.",
                   divulga=[], retiene={})

    return None


def _conforme(exp: dict) -> dict:
    fecha = (exp.get("factura") or {}).get("issue_date")
    dias = _dias_de(exp.get("proveedor"))
    if fecha and dias:
        try:
            vence = date.fromisoformat(fecha) + timedelta(days=int(dias))
        except ValueError:
            vence = None
        if vence:
            return _di(f"Su factura est\u00e1 conforme y aprobada para pago. Con "
                       f"sus condiciones de {dias} d\u00edas, el vencimiento es el "
                       f"{fecha_voz(vence.isoformat())}.",
                       divulga=["conforme", "condiciones_dias", "vencimiento"],
                       retiene={})
    return _di("Su factura est\u00e1 conforme y aprobada para pago.",
               divulga=["conforme"], retiene={})


def _dias_de(proveedor: dict | None) -> int | None:
    """'60 dias' -> 60. El Excel no guarda un entero."""
    if not proveedor:
        return None
    from rules_ingestion.normalize import payment_terms_days
    return payment_terms_days(proveedor.get("condiciones"))


def _generica(exp: dict) -> dict:
    return _di("Lo estamos revisando. Le llamamos nosotros en cuanto tengamos "
               "una respuesta.", divulga=[],
               retiene={"decision_interna": exp.get("decision"),
                        "motivos_internos": ", ".join(
                            m.get("code", "") for m in exp.get("motivos") or [])})


# ------------------------------------------------------ maquina de estados
# Quien coge el telefono. Coincide a proposito con la voz que lo dice:
# presentarse con un nombre y sonar a otro se nota en dos segundos.
AGENTE = "Marco"

SALUDO = (f"Contabilidad de proveedores, buenos días. Le atiende {AGENTE}. "
          f"¿Me dice el nombre de su empresa?")


def _paso(estado: dict, paso: str, **kw) -> dict:
    return estado | {"paso": paso} | kw


def _no_entiendo(estado: dict, decir: str, texto: str | None = None) -> dict:
    """Reformula, y cuenta. Al tercer fallo, persona.

    Sin el contador, un parser que no acierta deja al agente repitiendo la
    misma pregunta para siempre. Delante de un jurado eso es peor que no
    tener agente.
    """
    fallos = int(estado.get("fallos") or 0) + 1
    if fallos >= MAX_FALLOS:
        return {"estado": _paso(estado, "FIN", fallos=fallos),
                "respuesta": _di("Disculpe, no consigo entenderle. Le paso "
                                 "con una persona del equipo.",
                                 divulga=["pasa_a_humano"], retiene={}),
                "expediente": None}
    return {"estado": _paso(estado, estado.get("paso") or "IDENTIFICA",
                            fallos=fallos),
            "respuesta": _di(decir, texto, divulga=[], retiene={}),
            "expediente": None}


def avanzar(estado: dict, oye: list[str], caja: dict) -> dict:
    """Un turno de conversacion. -> {estado, respuesta, expediente}.

    El estado entra y sale por el cuerpo de la peticion: el servidor no
    guarda sesiones, asi que no hay cerrojos, ni sesiones zombis, ni fugas
    de memoria, y `curl` puede conducir una llamada entera pegando el estado
    anterior.
    """
    estado = dict(estado or {})
    oye = [t for t in (oye or []) if t and t.strip()]
    paso = estado.get("paso")

    if not paso:
        return {"estado": _paso(estado, "IDENTIFICA", fallos=0),
                "respuesta": _di(SALUDO, divulga=[], retiene={}),
                "expediente": None}

    provs = consulta.proveedores(caja)

    if paso == "IDENTIFICA":
        nif = parsear_proveedor(oye, provs) or parsear_nif(oye, provs)
        if nif:
            p = provs[nif]
            return {"estado": _paso(estado, "CONFIRMA", nif=nif, fallos=0),
                    "respuesta": _di(
                        f"{nombre_voz(p['razon_social'])}, CIF "
                        f"{deletrear(nif)}. ¿Es correcto?",
                        f"{p['razon_social']}, CIF {nif}. ¿Es correcto?",
                        divulga=["razon_social", "nif"], retiene={}),
                    "expediente": None}
        return _no_entiendo(estado, "No le he cogido el nombre. ¿Me dice "
                                    "la razón social de su empresa, o su CIF?")

    if paso == "CONFIRMA":
        dice = _afirma(oye)
        if dice is False:
            return {"estado": _paso(estado, "IDENTIFICA", nif=None, fallos=0),
                    "respuesta": _di("Disculpe. ¿Me repite el nombre de "
                                     "su empresa?", divulga=[], retiene={}),
                    "expediente": None}
        if dice is None:
            return _no_entiendo(estado, "¿Me lo confirma, sí o no?")
        return {"estado": _paso(estado, "PEDIDO", fallos=0),
                "respuesta": _di("Gracias. ¿De qué pedido me habla? El "
                                 "número que empieza por P. O.",
                                 "Gracias. ¿De qué pedido me habla? El "
                                 "número PO-....",
                                 divulga=[], retiene={}),
                "expediente": None}

    if paso == "PEDIDO":
        return _resolver_pedido(estado, oye, caja, provs)

    if paso == "RESPUESTA":
        return {"estado": _paso(estado, "FIN"),
                "respuesta": _di("A usted. Que tenga buen día.",
                                 divulga=[], retiene={}),
                "expediente": None}

    return {"estado": _paso(estado, "FIN"),
            "respuesta": _di("Gracias por su llamada.", divulga=[], retiene={}),
            "expediente": None}


def _resolver_pedido(estado: dict, oye: list[str], caja: dict,
                     provs: dict[str, dict]) -> dict:
    # `num_factura` es NULL en las 500: un numero de factura no se puede
    # buscar. Hay que descartarlo ANTES de parsear, porque la pasada
    # tolerante convierte alegremente 'la factura FA-5077' en PO-2026-5077.
    # Solo se descarta si no dieron ademas un PO explicito.
    if (any(RE_FACTURA.search(_normalizar(t)) for t in oye)
            and not any(RE_PO.search(_normalizar(t)) for t in oye)):
        return _no_entiendo(
            estado, "Nosotros las referenciamos por número de pedido, el "
                    "que empieza por P. O. ¿Lo tiene a mano?")

    pedido = parsear_pedido(oye)
    if pedido is None:
        return _no_entiendo(estado, "No le he cogido el número. ¿Me lo "
                                    "dice dígito a dígito?")

    exp = consulta.expediente_de_pedido(caja, pedido)
    if exp is None:
        return _no_entiendo(estado,
                            f"No encuentro el pedido {pedido_voz(pedido)}. "
                            f"¿Me lo repite?",
                            f"No encuentro el pedido {pedido}. "
                            f"¿Me lo repite?")

    # Que el pedido sea de quien llama se comprueba ANTES de contestar nada:
    # el expediente lleva importes, fechas y estado de pago de un tercero.
    nif_llama = estado.get("nif")
    nif_factura = consulta.nif_del_pedido(exp)
    if nif_llama and nif_factura and nif_llama != nif_factura:
        return {"estado": _paso(estado, "FIN", pedido=pedido),
                "respuesta": _di(
                    "Ese pedido no figura a nombre de su empresa. No puedo "
                    "darle información de él. Si cree que hay un error, "
                    "escríbanos y lo revisamos.",
                    divulga=[],
                    retiene={"pedido_es_de": nif_factura,
                             "quien_llama_dijo_ser": nif_llama}),
                "expediente": None}

    return {"estado": _paso(estado, "RESPUESTA", fallos=0, pedido=pedido),
            "respuesta": respuesta(exp),
            "expediente": _panel(exp, caja)}


def _panel(exp: dict, caja: dict) -> dict:
    """Lo que se pinta al lado de la conversacion: la fuente, en crudo.

    Incluye la procedencia -- el id del contexto y del ruleset con que se
    decidio -- porque es lo que hace verificable lo que se dice. Sin eso, el
    panel es una captura bonita.
    """
    f = exp.get("factura") or {}
    proc = exp.get("procedencia") or {}
    return {
        "file_id": exp.get("file_id"),
        "pedido": exp.get("pedido"),
        "proveedor": (exp.get("proveedor") or {}).get("razon_social"),
        "total": eur_texto(f.get("total")) if f.get("total") else "\u2014",
        "result": exp.get("decision"),
        "motivo": "; ".join(m.get("code", "") for m in exp.get("motivos") or []),
        "norma_version": (proc.get("ruleset") or {}).get("ruleset_version", "?"),
        "snapshot_erp": proc.get("context_id", "")[:20],
        "snapshot_maestro": (proc.get("evaluation_id") or "")[:20],
        "reglas": [{"id": r.get("rule_id"), "veredicto": r.get("status"),
                    "evidencia": {e["campo"]: e["valor"]
                                  for e in (r.get("entradas") or [])
                                  if e.get("estado") == "present" and e.get("campo")}}
                   for r in exp.get("reglas") or []],
        "asiento": exp.get("asiento"),
        "notas": (["Marcado para revisión en pendiente_revisar"]
                  if (exp.get("notas") or {}).get("en_revision") else []),
        "notas_generales": consulta.notas_generales(caja),
    }


# ----------------------------------------------------------------- consola
def _repl() -> int:
    """La llamada entera por teclado, sin navegador ni microfono.

    Es la herramienta de depuracion y la prueba de que la conversacion no
    depende de la voz: si esto no habla, el navegador no tiene nada que
    decir.
    """
    caja = consulta.cargar()
    estado: dict = {}
    turno = avanzar(estado, [], caja)
    while True:
        estado = turno["estado"]
        print(f"\n  ▶ {turno['respuesta']['texto']}")
        if turno["respuesta"]["retiene"]:
            print(f"    (no dicho: {turno['respuesta']['retiene']})")
        if turno["expediente"]:
            p = turno["expediente"]
            print(f"    [{p['file_id']} · {p['result']} · {p['motivo']}]")
        if estado.get("paso") == "FIN":
            return 0
        try:
            dicho = input("  ◀ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        turno = avanzar(estado, [dicho], caja)


if __name__ == "__main__":
    raise SystemExit(_repl())
