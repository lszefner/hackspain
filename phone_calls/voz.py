"""Sintetiza la voz del agente y deja el audio en cache.

Tres niveles, de mejor a peor, y se cae de uno al siguiente sin que nadie lo
note:

  1. ElevenLabs, si hay `ELEVENLABS_API_KEY` en el entorno. Voz neural
     castellana; la unica que no suena a contestador.
  2. `say` de macOS, si no hay clave. Da el mismo audio siempre, que es lo
     que se quiere el dia que se graba el video.
  3. Nada: el servidor devuelve `audio: null` y habla el navegador.

Ninguna sintesis puede tumbar una llamada: `sintetizar()` devuelve `None`
ante cualquier fallo, nunca lanza. Una demo muda es peor que una con voz
mediocre.

La cache esta direccionada por contenido, con la identidad de la voz dentro
del hash. Por eso los dos motores conviven sin pisarse, y por eso cambiar de
voz no invalida nada: genera ficheros nuevos al lado de los viejos.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# Como el resto del repo (alberto/cli.py). Y por algo mas que consistencia:
# `urllib` con el Python del venv no encuentra los certificados raiz de macOS
# y muere con CERTIFICATE_VERIFY_FAILED; httpx trae los suyos (certifi).
import httpx

CACHE = Path(__file__).parent / ".voz"
TIEMPO_MAX = 90                     # una voz neural puede tardar segundos

# El identificador de voz lleva el motor delante: 'say:Mónica' o
# '11l:M7m4UdXA2zH2Dhz4OzqV'. `sintetizar()` despacha por ese prefijo y cada
# motor decide su formato: ElevenLabs devuelve mp3, `say` escribe m4a.
EXT = {"say": "m4a", "11l": "mp3"}
TIPO = {"m4a": "audio/mp4", "mp3": "audio/mpeg"}

# ---- say ----
PALABRAS = 175                      # -r de `say`; 175 es ritmo de telefono

# Las voces 'novelty' de macOS estan en es_ES y ganarian cualquier busqueda
# por idioma. Rocko atendiendo a un proveedor no se puede ver.
NOVELTY = re.compile(
    r"\b(eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|bells|boing|"
    r"bubbles|jester|organ|superstar|trinoids|whisper|wobble|zarvox)\b", re.I)

# ---- ElevenLabs ----
API = "https://api.elevenlabs.io/v1"
# Voces castellanas de la biblioteca compartida. Se usan directas por id, sin
# anadirlas a la cuenta (probado: no hace falta `voices_write`). Las 25 que
# trae la cuenta por defecto son inglesas y con `multilingual_v2` hablarian
# espanol con acento americano.
VOCES_11L = {
    "Marina": "M7m4UdXA2zH2Dhz4OzqV",   # peninsular, adulta: calm, warm, natural
    "Sofía": "RrEQHwbMIvoa9O0J5xAW",    # peninsular, joven: smooth, expressive
    "Inés": "b8hczxpWV1VCBo2sB5jd",     # peninsular, joven: calm, friendly
}
VOZ_11L = "Inés"
# Medido con la frase del caso del importe (117 caracteres), conexion caliente:
# multilingual_v2 2,27 s, turbo 0,45 s, flash 0,38 s -- flash es MAS rapido
# que el `say` local en frio (0,80 s). El bueno para precalentar, el rapido
# para lo que se improvise en mitad de la llamada.
MODELO_CALIDAD = "eleven_multilingual_v2"
MODELO_RAPIDO = "eleven_flash_v2_5"
FORMATO_11L = "mp3_44100_64"

_RE_HASH = re.compile(r"^[0-9a-f]{16}$")

# El ultimo motivo por el que `sintetizar()` devolvio None. `sintetizar` no
# lanza a proposito -- una llamada no puede caerse por la voz -- pero `make
# voces` si tiene que poder decir "cuota agotada" en vez de "ok: false".
ultimo_error: str | None = None


# --------------------------------------------------------------- comun
def clave_api() -> str | None:
    return os.environ.get("ELEVENLABS_API_KEY") or None


def motor(voz: str | None) -> str:
    return (voz or "").split(":", 1)[0]


def _nombre(voz: str) -> str:
    return voz.split(":", 1)[1] if ":" in voz else voz


def disponible() -> bool:
    return bool(clave_api()) or shutil.which("say") is not None


def clave(texto: str, voz: str) -> str:
    """Hash del audio. Lo que identifica al fichero es la VOZ, no el modelo.

    A proposito: `make voces` graba con el modelo bueno y un turno
    improvisado con el rapido, y los dos tienen que encontrar el mismo
    fichero. Si el modelo entrara en el hash, el precalentado no serviria
    de nada en cuanto el servidor pidiera la version rapida.
    """
    return hashlib.sha1(f"{voz}|{PALABRAS}|{texto}".encode()).hexdigest()[:16]


def ruta(h: str) -> Path | None:
    """El fichero de ese hash, sea del motor que sea, o None."""
    if not _RE_HASH.match(h or ""):
        return None
    for ext in EXT.values():
        f = CACHE / f"{h}.{ext}"
        if f.is_file():
            return f
    return None


def tipo(f: Path) -> str:
    """Content-Type por extension. `mimetypes` adivina 'audio/mp4a-latm' para
    un .m4a, que es otro formato y los navegadores lo rechazan."""
    return TIPO.get(f.suffix.lstrip("."), "application/octet-stream")


def _escribir(destino: Path, datos: bytes) -> None:
    """Aparte y luego `replace`: atomico en el mismo sistema de ficheros, asi
    que nadie sirve un audio a medias aunque dos peticiones pidan la misma
    frase a la vez."""
    CACHE.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    parcial.write_bytes(datos)
    parcial.replace(destino)


def sintetizar(texto: str, voz: str | None, *, rapido: bool = False) -> str | None:
    """-> hash del audio, o None si no se pudo. Nunca lanza."""
    texto = (texto or "").strip()
    if not texto or not voz:
        return None
    h = clave(texto, voz)
    if ruta(h):
        return h
    try:
        m = motor(voz)
        if m == "11l":
            datos = _elevenlabs(texto, _nombre(voz), rapido=rapido)
        elif m == "say":
            datos = _say(texto, _nombre(voz))
        else:
            return None
        if not datos:
            return None
        _escribir(CACHE / f"{h}.{EXT[m]}", datos)
        return h
    except httpx.HTTPStatusError as exc:
        global ultimo_error
        try:
            d = exc.response.json().get("detail", {})
            ultimo_error = f"{d.get('status')}: {d.get('message')}"
        except ValueError:
            ultimo_error = f"HTTP {exc.response.status_code}"
        return None
    except (OSError, subprocess.SubprocessError, httpx.HTTPError,
            ValueError, KeyError) as exc:
        ultimo_error = f"{type(exc).__name__}: {exc}"
        return None


# --------------------------------------------------------------- say
def _say(texto: str, nombre: str) -> bytes | None:
    if not shutil.which("say"):
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f"say-{os.getpid()}.m4a"
    subprocess.run(
        ["say", "-v", nombre, "-r", str(PALABRAS),
         "--file-format=m4af", "--data-format=aac", "--bit-rate=48000",
         "-o", str(tmp), "-f", "-"],
        input=texto.encode("utf-8"), check=True, timeout=TIEMPO_MAX,
        capture_output=True)
    datos = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return datos


def voces_say() -> list[tuple[str, str]]:
    """[(nombre, idioma)] de `say -v '?'`, solo las de espanol."""
    if not shutil.which("say"):
        return []
    try:
        salida = subprocess.run(["say", "-v", "?"], capture_output=True,
                                text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    fuera = []
    for linea in salida.splitlines():
        # Un solo espacio basta: las voces de nombre largo -- 'Marisol
        # (Premium)' -- no van alineadas en columna.
        m = re.match(r"^(.+?)\s+([a-z]{2}_[A-Z]{2})(?:\s|$)", linea)
        if m and m.group(2).startswith("es"):
            fuera.append((m.group(1).strip(), m.group(2)))
    return fuera


def _puntuar_say(nombre: str, idioma: str) -> int:
    if NOVELTY.search(nombre):
        return -1
    p = 100 if idioma == "es_ES" else 0
    if re.search(r"premium", nombre, re.I):
        p += 40
    elif re.search(r"enhanced", nombre, re.I):
        p += 25
    return p


def mejor_say() -> str | None:
    c = sorted(((_puntuar_say(n, i), n) for n, i in voces_say()), reverse=True)
    return f"say:{c[0][1]}" if c and c[0][0] >= 0 else None


# --------------------------------------------------------- elevenlabs
def _elevenlabs(texto: str, voice_id: str, *, rapido: bool) -> bytes | None:
    k = clave_api()
    if not k:
        return None
    r = httpx.post(
        f"{API}/text-to-speech/{voice_id}",
        params={"output_format": FORMATO_11L},
        headers={"xi-api-key": k, "Accept": "audio/mpeg"},
        json={
            "text": texto,
            "model_id": MODELO_RAPIDO if rapido else MODELO_CALIDAD,
            # Estabilidad alta y poco 'estilo': es una recepcionista, no
            # una narradora. Lo expresivo aqui suena a que se rie de ti.
            "voice_settings": {"stability": 0.55, "similarity_boost": 0.8,
                               "style": 0.1, "use_speaker_boost": True},
        },
        timeout=TIEMPO_MAX)
    r.raise_for_status()
    return r.content


def mejor_voz() -> str | None:
    """ElevenLabs si hay clave; si no, la mejor de `say`; si no, nada."""
    if clave_api():
        return f"11l:{VOCES_11L[VOZ_11L]}"
    return mejor_say()


def nombre_voz(voz: str | None) -> str:
    """Para ensenar: '11l:M7m4...' -> 'Marina (ElevenLabs)'."""
    if not voz:
        return "la del navegador"
    m, n = motor(voz), _nombre(voz)
    if m == "11l":
        n = next((k for k, v in VOCES_11L.items() if v == n), n)
        return f"{n} (ElevenLabs)"
    return f"{n} (say)"


# ------------------------------------------------------------ precalentar
def precalentar(voz: str | None = None) -> dict:
    """Sintetiza de antemano todo lo que dira la demo, con el modelo bueno.

    Recorre los guiones grabados con la misma maquina de estados que la
    llamada en vivo, asi que cachea exactamente las frases que se diran. Con
    la cache caliente, cada turno son milisegundos.
    """
    from phone_calls import consulta, guion

    voz = voz or mejor_voz()
    if not voz:
        return {"ok": False, "error": "ni ELEVENLABS_API_KEY ni `say`"}

    frases = {guion.SALUDO, guion.MULETILLA}
    try:
        caja = consulta.cargar()
    except consulta.SinDatos as exc:
        hechas = [f for f in sorted(frases) if sintetizar(f, voz)]
        return {"ok": False, "error": str(exc), "voz": nombre_voz(voz),
                "frases": len(frases), "cacheadas": len(hechas)}
    llamadas = json.loads(
        (Path(__file__).parent / "guiones_demo.json").read_text("utf-8"))
    for llamada in llamadas["llamadas"]:
        estado = guion.avanzar({}, [], caja)["estado"]
        for turno in llamada["turnos"]:
            r = guion.avanzar(estado, turno["oye"], caja)
            estado = r["estado"]
            frases.add(r["respuesta"]["decir"])

    global ultimo_error
    ultimo_error = None
    hechas = [f for f in sorted(frases) if sintetizar(f, voz)]
    r = {"ok": len(hechas) == len(frases), "voz": nombre_voz(voz),
         "frases": len(frases), "cacheadas": len(hechas), "cache": str(CACHE)}
    if not r["ok"]:
        r["error"] = ultimo_error or "sin detalle"
        r["sin_cachear"] = [f[:60] for f in sorted(frases) if f not in hechas]
    return r


def probar(frase: str | None = None) -> int:
    """Audiciona las voces castellanas de ElevenLabs, una tras otra."""
    from phone_calls import guion
    if not clave_api():
        print("sin ELEVENLABS_API_KEY en el entorno")
        return 1
    frase = frase or guion.SALUDO
    for nombre, vid in VOCES_11L.items():
        h = sintetizar(frase, f"11l:{vid}")
        f = ruta(h) if h else None
        print(f"  ▶ {nombre:8} {'ok' if f else 'FALLO'}")
        if f and shutil.which("afplay"):
            subprocess.run(["afplay", str(f)], check=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser("phone_calls.voz")
    p.add_argument("--voz", default=None,
                   help="'11l:<id>' o 'say:<nombre>'; por defecto la mejor")
    p.add_argument("--listar", action="store_true")
    p.add_argument("--probar", action="store_true",
                   help="reproduce el saludo con cada voz de ElevenLabs")
    a = p.parse_args(argv)
    if a.listar:
        elegida = mejor_voz()
        print(f"elegida: {nombre_voz(elegida)}  [{elegida}]")
        print("ElevenLabs:" + ("" if clave_api() else "  (sin clave)"))
        for n, vid in VOCES_11L.items():
            print(f"  {n:34} 11l:{vid}")
        print("say:")
        for n, i in voces_say():
            print(f"  {n:34} {i}")
        return 0
    if a.probar:
        return probar()
    r = precalentar(a.voz)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
