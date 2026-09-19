"""Congela la centralita dentro de `frontend/` para que Vercel la sirva.

La centralita en local es un proceso Python en el 8011. En Vercel no hay
proceso: hay una Function que se levanta por peticion y un CDN que sirve
ficheros. Este modulo traduce lo uno en lo otro, y lo hace ANTES -- igual que
`exportar.py` congela los expedientes antes de la demo -- para que en
produccion no haya red, ni credenciales, ni cuota que se pueda agotar.

Tres cosas se publican:

  - el motor: `guion.py` y `consulta.py` VERBATIM, con sus datos. No se
    reescriben ni se adaptan; la Function los importa tal cual. Lo que decide
    que se dice y que se calla es el mismo fichero en los dos sitios.
  - la pagina: `estatico/` verbatim, servida como ficheros sueltos.
  - la voz: los mp3 que ya estan en `.voz/`, copiados a `public/`. Se publica
    UNA voz (Ines): la cache mezcla mp3 de ElevenLabs y m4a de `say`, y
    publicar las dos haria que la agente cambiara de voz a mitad de llamada.

Lo que NO se publica es `voz.py`, que es el unico fichero que sabe hablar con
api.elevenlabs.io. No es que la web no vaya a llamar a ElevenLabs: es que el
codigo que sabe hacerlo no esta desplegado. `tests/test_publicacion_web.py` lo
comprueba con un grep, y comprueba tambien que las copias no han envejecido.

Nunca sintetiza. Si falta una frase lo dice y sigue: la clave tiene tope
propio de 1.000 caracteres y ya se agoto una vez a mitad de precalentar.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from phone_calls import consulta, guion, voz

RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).parent
FRONTEND = RAIZ / "frontend"

# El arbol que importa la Function. Fuera de `api/` a proposito: cualquier .py
# que cuelgue de `api/` corre el riesgo de que Vercel lo convierta en una
# Function suelta. Aqui no hay nada que enrutar, solo un paquete que importar.
BUNDLE = FRONTEND / "centralita_py"
# La pagina y el audio, que los sirve el CDN sin invocar nada.
PUBLICO = FRONTEND / "public" / "phone_calls"

# Los tres modulos que hacen falta para conducir una llamada. `voz.py` no esta
# en la lista, y `exportar.py` tampoco: el export ya esta hecho y congelado.
MODULOS = ("__init__.py", "consulta.py", "guion.py")

# `consulta.cargar` solo lee estos dos patrones. `datos/extraccion/` son las
# caches de extraccion de Helmcode -- 4,5 MB que la centralita no abre nunca.
DATOS = ("PO-*.json", "maestro.json")

# La unica dependencia fuera de stdlib de todo el arbol: `condiciones_dias`
# llama a `payment_terms_days`, que son cuatro lineas de regex. Se copia el
# modulo entero, pero NO su `__init__.py`: el de verdad importa `invoice` y
# `checks`, que arrastran el paquete completo. Un stub vacio corta ahi.
STUB = ('"""Stub. Solo `normalize` viaja a la web; el `__init__` de verdad\n'
        'importa `invoice` y `checks`, que arrastran el paquete entero.\n'
        'Generado por phone_calls/publicar_web.py -- no editar a mano.\n'
        '"""\n')


def frases(caja: dict) -> set[str]:
    """Todo lo que dira la demo, conduciendo la maquina de estados.

    Mismo recorrido que `voz.precalentar()`, y por el mismo motivo: si alguien
    cambia una frase en `guion.py`, esto la recoge sola. Copiar los literales
    a mano seria garantizar que un dia dejan de coincidir.
    """
    dichas = {guion.SALUDO, guion.MULETILLA}
    guiones = json.loads((AQUI / "guiones_demo.json").read_text("utf-8"))
    for llamada in guiones["llamadas"]:
        estado = guion.avanzar({}, [], caja)["estado"]
        for turno in llamada["turnos"]:
            r = guion.avanzar(estado, turno["oye"], caja)
            estado = r["estado"]
            dichas.add(r["respuesta"]["decir"])
    return dichas


def _limpiar(d: Path) -> None:
    """De cero cada vez: si se borra un fichero del origen, la copia no puede
    quedarse con el huerfano dentro y seguir sirviendolo."""
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)


def _motor() -> int:
    """Copia el arbol que importara la Function. Ni una linea se modifica."""
    _limpiar(BUNDLE)
    destino = BUNDLE / "phone_calls"
    (destino / "datos").mkdir(parents=True)
    for m in MODULOS:
        shutil.copy2(AQUI / m, destino / m)
    copiados = len(MODULOS)
    for patron in DATOS:
        for f in sorted((AQUI / "datos").glob(patron)):
            shutil.copy2(f, destino / "datos" / f.name)
            copiados += 1

    reglas = BUNDLE / "rules_ingestion"
    reglas.mkdir()
    (reglas / "__init__.py").write_text(STUB, encoding="utf-8")
    shutil.copy2(RAIZ / "rules_ingestion" / "normalize.py",
                 reglas / "normalize.py")
    return copiados + 2


def _pagina() -> int:
    """La pagina, verbatim. En Vercel es estatica: no la sirve ninguna
    Function, la sirve el CDN igual que un png.

    Se respeta el subdirectorio `estatico/` porque `index.html` referencia sus
    assets en absoluto (`/estatico/estilo.css`). Tocar el HTML para acomodar
    al despliegue seria empezar a tener dos paginas distintas; en vez de eso
    hay un rewrite en `next.config.ts` que manda `/estatico/*` aqui.
    """
    _limpiar(PUBLICO)
    estatico = PUBLICO / "estatico"
    estatico.mkdir()
    copiados = 0
    for f in sorted((AQUI / "estatico").iterdir()):
        if f.name == "index.html":
            shutil.copy2(f, PUBLICO / f.name)      # la pagina, en la raiz
        elif f.is_file():
            shutil.copy2(f, estatico / f.name)     # sus assets, donde los pide
        else:
            continue
        copiados += 1
    # Los guiones grabados tambien son estaticos: son 3 KB de JSON y gastar
    # una Function entera en leerlos de disco no tiene sentido.
    shutil.copy2(AQUI / "guiones_demo.json", PUBLICO / "guiones_demo.json")
    return copiados + 1


def _voz(dichas: set[str], identidad: str) -> tuple[dict, list[str], int]:
    """Copia de `.voz/` lo que ya este sintetizado. No sintetiza nada.

    El indice va por TEXTO y no por hash: asi la Function no tiene que
    replicar `voz.clave()` ni acoplarse a los internos de `voz.py`. Mira el
    diccionario y devuelve la URL, o None para que hable el navegador.
    """
    audio = PUBLICO / "voz"
    audio.mkdir()
    indice, faltan, bytes_ = {}, [], 0
    for texto in sorted(dichas):
        f = voz.ruta(voz.clave(texto, identidad))
        if f is None:
            faltan.append(texto)
            continue
        shutil.copy2(f, audio / f.name)
        indice[texto] = f.name
        bytes_ += f.stat().st_size
    (BUNDLE / "voz_indice.json").write_text(
        json.dumps(indice, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return indice, faltan, bytes_


def _huella() -> str:
    """sha256 de lo publicado, no un commit de git.

    Un sha de git aqui no converge nunca: este propio fichero vive en
    `phone_calls/`, asi que commitear la publicacion cambia el valor que la
    publicacion acaba de escribir, y el arbol se queda sucio para siempre.
    Y de todas formas la pregunta util no es "de que commit salio" sino "es
    esta copia la misma que el original", que es justo lo que contesta un
    hash del contenido -- y lo contesta igual con el arbol sucio, que es
    cuando mas falta hace.
    """
    h = hashlib.sha256()
    for f in sorted(BUNDLE.rglob("*")) + sorted(PUBLICO.rglob("*")):
        if f.is_file() and f.name != "PROCEDENCIA.md":
            h.update(f.relative_to(FRONTEND).as_posix().encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def publicar() -> dict:
    caja = consulta.cargar()
    dichas = frases(caja)
    identidad = f"11l:{voz.VOCES_11L[voz.VOZ_11L]}"

    modulos = _motor()
    ficheros = _pagina()
    indice, faltan, bytes_ = _voz(dichas, identidad)

    (BUNDLE / "PROCEDENCIA.md").write_text(
        f"# Copia generada -- no editar a mano\n\n"
        f"La produce `make web-centralita` (`phone_calls/publicar_web.py`).\n"
        f"Cualquier cambio aqui se pierde en la siguiente publicacion, y\n"
        f"`tests/test_publicacion_web.py` falla byte a byte si diverge.\n\n"
        f"| | |\n|---|---|\n"
        f"| huella | `{_huella()}` |\n"
        f"| voz | {voz.nombre_voz(identidad)} |\n"
        f"| frases con audio | {len(indice)}/{len(dichas)} |\n",
        encoding="utf-8")

    py = sum(f.stat().st_size for f in BUNDLE.rglob("*") if f.is_file())
    return {"ok": not faltan, "modulos": modulos, "pagina": ficheros,
            "voz": voz.nombre_voz(identidad), "frases": len(dichas),
            "con_audio": len(indice), "sin_audio": faltan,
            "kb_audio": bytes_ / 1024, "kb_python": py / 1024,
            "huella": _huella()}


def main() -> int:
    try:
        r = publicar()
    except consulta.SinDatos as exc:
        print(f"{exc}\n         make export     (extrae, evalua y congela)")
        return 1
    print(f"Centralita publicada en frontend/  ({r['huella']})")
    print(f"  motor    {r['modulos']} ficheros, {r['kb_python']:.0f} KB"
          f"   -> centralita_py/")
    print(f"  pagina   {r['pagina']} ficheros"
          f"            -> public/phone_calls/")
    print(f"  voz      {r['con_audio']}/{r['frases']} frases con"
          f" {r['voz']}, {r['kb_audio']:.0f} KB")
    for f in r["sin_audio"]:
        print(f"           sin audio, la dira el navegador: {f[:58]}")
    if r["sin_audio"]:
        print("           (`make voces` las graba, pero GASTA cuota)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
