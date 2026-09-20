"use strict";
/* La centralita en el navegador: oidos y boca, nada mas.
 *
 * La conversacion la lleva el servidor. Aqui solo se convierte voz en texto,
 * se manda el turno y se dice en voz alta lo que conteste. El estado va y
 * vuelve en el cuerpo de cada peticion: esta pagina no guarda sesion, asi
 * que recargar no rompe nada y `curl` puede hacer lo mismo que el micro.
 */

const $ = (id) => document.getElementById(id);
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

/* Donde vive la API. En local (`make centralita`) la pagina la sirve el
 * propio servidor en "/" y la API cuelga de "/api". En Vercel no hay
 * servidor: la pagina es estatica en "/phone_calls/" y la conversacion la
 * lleva una Function de Python en "/api/centralita". Se deduce del propio
 * path -- ni build flags, ni plantillas, ni dos copias de este fichero.
 */
const EN_WEB = location.pathname.startsWith("/phone_calls");
const API = EN_WEB ? "/api/centralita" : "/api";
// Los guiones son 3 KB de JSON: en Vercel se sirven estaticos, sin gastar
// una Function entera en leer un fichero de disco.
const GUIONES = EN_WEB ? "/phone_calls/guiones_demo.json" : "/api/guiones";

let estado = {};
let rec = null;
let escuchando = false;
let quiereEscuchar = false;
let mudoHasta = 0;          // ventana en la que se ignora el propio eco
let enLlamada = false;
let voz = null;
let reproduciendo = false;
let muletilla = null;   // {texto, audio}: se dice mientras se consulta
let agente = "";        // como se presenta; lo manda el servidor

const esperar = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------- pantalla */
function burbuja(quien, texto, clase) {
  const vacio = $("conversacion").querySelector(".vacio");
  if (vacio) vacio.remove();
  const div = document.createElement("div");
  div.className = `burbuja ${clase || quien}`;
  div.innerHTML = `<span class="quien"></span><span class="dicho"></span>`;
  div.querySelector(".quien").textContent =
    quien === "agente" ? (agente || "Agente") : "Proveedor";
  div.querySelector(".dicho").textContent = texto;
  $("conversacion").append(div);
  $("conversacion").scrollTop = $("conversacion").scrollHeight;
  return div;
}

let interina = null;
function pintarInterino(texto) {
  if (!texto) return;
  if (!interina) interina = burbuja("proveedor", texto, "proveedor interina");
  else interina.querySelector(".dicho").textContent = texto;
  $("conversacion").scrollTop = $("conversacion").scrollHeight;
}
function cerrarInterino() { interina?.remove(); interina = null; }

function estadoVoz(t) { $("estadoVoz").textContent = t; }
function avisar(t) { $("aviso").textContent = t; $("aviso").hidden = !t; }

function pintarRetenido(retiene) {
  const hay = retiene && Object.keys(retiene).length;
  $("silencio").hidden = !hay;
  if (!hay) return;
  $("retenido").replaceChildren(...Object.entries(retiene).map(([k, v]) => {
    const div = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = k.replace(/_/g, " ");
    const dd = document.createElement("dd");
    dd.textContent = v;
    div.append(dt, dd);
    return div;
  }));
  $("porque").textContent =
    "El agente tiene estos datos delante y no los dice. Quien llama no esta " +
    "verificado, y decirle cual es el numero bueno es el guion exacto de un " +
    "fraude de cambio de cuenta.";
}

function pintarPanel(p) {
  $("sinPanel").hidden = !!p;
  $("panel").hidden = !p;
  if (!p) return;
  $("pFile").textContent = p.file_id;
  $("pPedido").textContent = p.pedido || "—";
  $("pProv").textContent = p.proveedor || "no consta en el maestro";
  $("pTotal").textContent = p.total;
  $("pResult").textContent = p.result;
  $("pResult").className = p.result;
  $("pMotivo").textContent = p.motivo || "";
  $("pReglas").replaceChildren(...p.reglas.map((r) => {
    const li = document.createElement("li");
    const b = document.createElement("b");
    b.className = r.veredicto;
    b.textContent = r.veredicto;
    const id = document.createElement("span");
    id.className = "id";
    id.textContent = r.id;
    const ev = document.createElement("code");
    ev.textContent = JSON.stringify(r.evidencia);
    li.append(b, id, ev);
    return li;
  }));
  const clave = (p.proveedor || "").toLowerCase().split(/\s+/)
    .filter((w) => w.length > 4);
  const generales = (p.notas_generales || []).filter(
    (n) => clave.some((w) => n.toLowerCase().includes(w)));
  const notas = [...(p.notas || []), ...generales];
  $("pNotas").hidden = !notas.length;
  $("pNotas").textContent = notas.length
    ? "Apuntado a mano por Alberto: " + notas.map((n) => `«${n}»`).join(" · ")
    : "";
  $("pProc").textContent =
    `norma ${p.norma_version}  ·  erp ${p.snapshot_erp}  ·  ` +
    `maestro ${p.snapshot_maestro}`;
  $("sello").textContent = `norma ${p.norma_version}`;
}

/* ------------------------------------------------------------------ voz */
/* El motor importa: WebKit y Blink tienen bugs distintos y opuestos. */
const ES_WEBKIT = /^((?!chrome|chromium|android).)*safari/i.test(navigator.userAgent);

/* Un WAV mudo de 64 muestras. Sirve para gastar el gesto de usuario en el
 * elemento <audio>: Safari concede el autoplay POR ELEMENTO, no por
 * documento, asi que hay que desbloquear ESTE y luego reutilizarlo siempre. */
const MUDO = "data:audio/wav;base64,UklGRmQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YUAAAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA";

/* Las 'novelty' de macOS estan todas en es-ES y ganarian cualquier busqueda
 * por idioma. Rocko atendiendo a un proveedor no se puede ver. */
const NOVELTY = /\b(eddy|flo|grandma|grandpa|reed|rocko|sandy|shelley|bells|boing|bubbles|jester|organ|superstar|trinoids|whisper|wobble|zarvox)\b/i;

/* La calidad SOLO se lee en `voiceURI` ('com.apple.voice.premium.es-ES.X').
 * `name` es identico en la compacta y en la premium, asi que si estan las dos
 * instaladas getVoices() devuelve DOS entradas con el mismo nombre y un
 * `find` se queda con la compacta -- o sea que instalar la buena no serviria
 * de nada. */
function puntuarVoz(v) {
  if (!/^es(\b|[-_])/i.test(v.lang || "")) return -1;
  if (NOVELTY.test(v.name || "")) return -1;
  const id = `${v.voiceURI || ""} ${v.name || ""}`;
  let p = /es[-_]ES/i.test(v.lang) ? 100 : 0;
  if (/premium/i.test(id)) p += 40;
  else if (/enhanced/i.test(id)) p += 25;
  return p;
}

function elegirVoz() {
  const vs = speechSynthesis.getVoices();
  if (!vs.length) return false;
  const orden = vs.map((v) => [puntuarVoz(v), v]).filter(([p]) => p >= 0)
                  .sort((a, b) => b[0] - a[0]);
  voz = orden.length ? orden[0][1] : null;
  return !!voz;
}
if (window.speechSynthesis) {
  speechSynthesis.onvoiceschanged = elegirVoz;
  elegirVoz();   // en recarga caliente ya estan cacheadas y el evento no llega
}

/* Trocear por frases. Da prosodia (cada frase arranca con contorno nuevo) y
 * de paso ninguna locucion llega a durar lo suficiente para topar con el
 * corte de Chrome en frases largas.
 *
 * El punto de `deletrear()` -- 'A. S., cero cero cuatro' -- NO es fin de
 * frase; el de 'P. O.' si. Los distingue el lookahead: tras la inicial
 * suelta viene minuscula. El lookbehind cubre la inicial seguida de
 * mayuscula. */
const CORTE = /(?<!(?:^|[\s(])[A-ZÁÉÍÓÚÑ])([.!?…])\s+(?=[¿¡«"(]?[A-ZÁÉÍÓÚÜÑ0-9])/gu;
const MARCA = "⁣";

function frasear(texto) {
  const crudas = (texto || "").trim().replace(CORTE, `$1${MARCA}`)
    .split(MARCA).map((t) => t.trim()).filter(Boolean);
  const fuera = [];
  for (const f of crudas) {
    // Un fragmento de una sola palabra suena a corte, no a frase.
    if (fuera.length && f.split(/\s+/).length < 2) fuera[fuera.length - 1] += " " + f;
    else fuera.push(f);
  }
  return fuera.length ? fuera : [(texto || "").trim()];
}

/* `generacion` sube cada vez que se corta. Cualquier callback en vuelo que
 * vea una generacion vieja se calla y no abre el microfono: colgar a mitad
 * de frase no puede dejar el micro abierto despues. */
let generacion = 0;
let cortar = null;

function callar() {
  generacion++;
  try { window.speechSynthesis?.cancel(); } catch { /* ya estaba muerto */ }
  try { if (altavoz) { altavoz.pause(); } } catch { /* idem */ }
  cortar?.(); cortar = null;
}

/* El tono de llamada, a 425 Hz, que es el de la red europea. Generado, sin
 * fichero, y dentro del click -- que es el gesto que desbloquea el audio.
 *
 * Dos toques largos en vez de dos pitidos cortos: con 300 ms no da tiempo a
 * leerlo como un telefono, suena a notificacion de aplicacion.
 *
 * Devuelve lo que va a durar, en ms, para que el saludo espere exactamente
 * eso y no un numero a ojo que se queda desfasado al retocar el patron. */
const TONO = { hz: 425, suena: 0.85, calla: 0.3, veces: 2, volumen: 0.16 };
let ctxAudio = null;

function pitidos() {
  const ciclo = TONO.suena + TONO.calla;
  const total = (ciclo * TONO.veces - TONO.calla) * 1000;
  try {
    ctxAudio = ctxAudio || new (window.AudioContext || window.webkitAudioContext)();
    if (ctxAudio.state === "suspended") ctxAudio.resume();
    const t0 = ctxAudio.currentTime + 0.02;
    for (let i = 0; i < TONO.veces; i++) {
      const osc = ctxAudio.createOscillator();
      const gan = ctxAudio.createGain();
      osc.type = "sine";
      osc.frequency.value = TONO.hz;
      const a = t0 + i * ciclo;
      // Rampas de 15 ms a cada lado: un gain que salta de golpe hace 'clic'.
      gan.gain.setValueAtTime(0, a);
      gan.gain.linearRampToValueAtTime(TONO.volumen, a + 0.015);
      gan.gain.setValueAtTime(TONO.volumen, a + TONO.suena - 0.015);
      gan.gain.linearRampToValueAtTime(0, a + TONO.suena);
      osc.connect(gan); gan.connect(ctxAudio.destination);
      osc.start(a); osc.stop(a + TONO.suena + 0.02);
    }
  } catch { /* sin Web Audio no hay tono, y la llamada sigue igual */ }
  return total;
}

/* Velocidad del audio del servidor. ElevenLabs a 1.0 va lento para un
 * telefono; esto lo acelera al reproducir, sin regenerar nada y sin gastar
 * cuota. El navegador conserva el tono (preservesPitch), asi que no suena a
 * ardilla. Sube o baja aqui: 1.0 es la original, 1.3 ya cansa. */
const VELOCIDAD = 1.23;

let altavoz = null;
function prepararAltavoz() {
  if (altavoz) return;
  altavoz = new Audio(MUDO);
  altavoz.preload = "auto";
  altavoz.play().catch(() => {});   // gasta el gesto en ESTE elemento
}

/* Audio sintetizado en el servidor. `onended` de <audio> es determinista, al
 * contrario que `utterance.onend`, asi que la ventana anti-eco baja de 350 a
 * 150 ms y el traspaso al microfono deja de ser una apuesta. */
function reproducirUrl(url, mia) {
  return new Promise((resolve) => {
    prepararAltavoz();
    const fin = (ok) => {
      altavoz.removeEventListener("ended", vale);
      altavoz.removeEventListener("error", falla);
      if (mia !== generacion) return resolve(false);
      if (!ok) return resolve(null);       // null = intentalo con el navegador
      mudoHasta = performance.now() + 150;
      setTimeout(() => resolve(true), 150);
    };
    const vale = () => fin(true);
    const falla = () => fin(false);
    altavoz.addEventListener("ended", vale);
    altavoz.addEventListener("error", falla);
    altavoz.src = url;
    altavoz.preservesPitch = true;
    altavoz.playbackRate = VELOCIDAD;
    altavoz.play().catch(falla);
  });
}

/* Sintetizador del navegador. Es el respaldo, y el camino por defecto si el
 * servidor no tiene `say`. */
function hablarLocal(texto, mia) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) return resolve(true);
    // Safari a veces llena la lista de voces tarde y sin disparar
    // `voiceschanged`; sin `voice`, elige la del sistema, que con el macOS en
    // ingles puede no ser ni espanola.
    if (!voz) elegirVoz();

    let vigia = 0, resuelto = false;
    const fin = () => {
      if (resuelto) return;
      resuelto = true;
      clearInterval(vigia);
      cortar = null;
      if (mia !== generacion) return resolve(false);
      mudoHasta = performance.now() + 350;
      setTimeout(() => resolve(true), 350);
    };
    cortar = fin;

    const frases = frasear(texto);
    let vivas = frases.length;
    const encolar = () => {
      if (mia !== generacion) return fin();
      for (const f of frases) {
        const u = new SpeechSynthesisUtterance(f);
        if (voz) u.voice = voz; else u.lang = "es-ES";
        // Las cifras, mas despacio: hay que poder distinguir de oido
        // 'ochocientos setenta y cuatro' de 'ochocientos cuarenta y siete',
        // que es todo el caso de la demo.
        u.rate = /\d/.test(f) ? 0.93 : 0.98;
        u.onend = u.onerror = () => { if (--vivas <= 0) fin(); };
        speechSynthesis.speak(u);
      }
    };

    // `cancel()` SOLO si hay algo que cortar: en WebKit un cancel seguido de
    // speak() en el mismo tick puede tragarse la locucion nueva.
    if (speechSynthesis.speaking || speechSynthesis.pending) {
      speechSynthesis.cancel();
      setTimeout(encolar, 60);
    } else {
      encolar();
    }

    // Red de seguridad SIN reloj de pared. Un timeout fijo se equivoca
    // siempre: la misma frase dura 7,6 s o 16 s segun cuantas cifras lleve
    // (14,3 contra 20,8 caracteres por segundo, medido). Esto solo pregunta
    // si el motor sigue hablando; si ya no habla y el evento no ha llegado,
    // el evento se perdio. Dos lecturas calladas seguidas porque entre dos
    // frases de la cola `speaking` parpadea.
    let callado = 0, ciclos = 0;
    vigia = setInterval(() => {
      if (mia !== generacion) return clearInterval(vigia);
      if (++ciclos > 240) return fin();                 // 60 s: techo absoluto
      if (ciclos < 4) return;                           // deja arrancar al motor
      callado = (speechSynthesis.speaking || speechSynthesis.pending)
        ? 0 : callado + 1;
      if (callado >= 2) fin();
    }, 250);
  });
}

/* -> true si termino de hablar, false si lo cortaron. */
async function hablar(texto, url) {
  quiereEscuchar = false;
  rec?.abort();
  const mia = ++generacion;
  if (url) {
    const r = await reproducirUrl(url, mia);
    if (r !== null) return r;      // null = el fichero fallo; sigue el navegador
  }
  return hablarLocal(texto, mia);
}

/* --------------------------------------------------------- reconocimiento */
function crearRec() {
  if (!SR) return null;
  const r = new SR();
  r.lang = "es-ES";
  // Un reconocimiento = un turno. Con `continuous` el stream sigue abierto,
  // el array de resultados crece y el eco empeora; asi el navegador corta
  // solo al detectar fin de habla, que es el modelo half-duplex del telefono.
  r.continuous = false;
  r.interimResults = true;
  // Chrome devuelve a menudo 'peo 2026 cero cuatro siete cinco' como primera
  // alternativa y 'PO 2026 0475' como la tercera, y el servidor las prueba
  // todas. Safari suele devolver una sola.
  r.maxAlternatives = 5;

  r.onstart = () => {
    escuchando = true;
    $("micro").classList.add("escuchando");
    estadoVoz("escuchando…");
  };
  r.onend = () => {
    escuchando = false;
    $("micro").classList.remove("escuchando");
    if (quiereEscuchar && !speechSynthesis?.speaking) setTimeout(arrancar, 120);
    else estadoVoz(enLlamada ? "en llamada" : "en espera");
  };
  r.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      quiereEscuchar = false;
      avisar("Sin permiso de micrófono. La llamada sigue por teclado.");
    } else if (e.error === "network") {
      quiereEscuchar = false;
      avisar(ES_WEBKIT
        ? "El reconocedor se ha quedado sin red. Escribe la respuesta: el agente sigue hablando."
        : "El reconocedor de Chrome se ha quedado sin red (sube el audio a Google). Escribe la respuesta: el agente sigue hablando.");
    }
    // 'no-speech' y 'aborted' son normales; no se avisa de ellos.
  };
  r.onresult = (ev) => {
    if (performance.now() < mudoHasta) return;      // es nuestro propio audio
    const ult = ev.results[ev.results.length - 1];
    const alternativas = [...ult].map((a) => a.transcript).filter(Boolean);
    if (!ult.isFinal) return pintarInterino(alternativas[0]);
    quiereEscuchar = false;
    r.abort();
    cerrarInterino();
    enviarTurno(alternativas);
  };
  return r;
}

/* El microfono muerto se arregla AQUI, y solo aqui. Safari deja `speaking` a
 * true un rato despues de cancel(), y sin reintento este `return` era
 * definitivo: en el turno mas largo de la demo el micro no se abria nunca. */
function arrancar(quedan = 8) {
  if (!rec || escuchando || !quiereEscuchar) return;
  const sonando = altavoz && !altavoz.paused && !altavoz.ended;
  if (speechSynthesis?.speaking || speechSynthesis?.pending || sonando) {
    if (quedan > 0) setTimeout(() => arrancar(quedan - 1), 200);
    return;
  }
  try { rec.start(); } catch { /* InvalidStateError: ya estaba arrancado */ }
}
function escuchar() {
  if (!rec) return;
  quiereEscuchar = true;
  arrancar();
}

/* -------------------------------------------------------------- el turno */
async function enviarTurno(oye) {
  if (oye.length) burbuja("proveedor", oye[0]);
  estadoVoz("consultando alberto.db…");
  // El unico hueco de la conversacion donde un humano callaria es mientras
  // mira la pantalla. Sin `await`: suena a la vez que la consulta y le tapa
  // la latencia. El `generacion` del siguiente hablar() la corta sola.
  if (muletilla && estado.paso === "PEDIDO") hablar(muletilla.texto, muletilla.audio);
  let r;
  try {
    const res = await fetch(`${API}/turno`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ estado, oye }),
    });
    if (!(res.headers.get("content-type") || "").includes("application/json")) {
      throw new Error(`HTTP ${res.status} en ${API}/turno: el servidor no devolvió JSON. Comprueba que la API de llamadas esté en marcha.`);
    }
    r = await res.json();
    if (r.error) throw new Error(r.error);
    if (!res.ok) throw new Error(`HTTP ${res.status} en ${API}/turno`);
  } catch (e) {
    avisar(`No he podido consultar el servidor: ${e.message}`);
    estadoVoz("error");
    return;
  }
  avisar("");
  estado = r.estado;
  if (r.muletilla) muletilla = r.muletilla;
  if (r.agente) agente = r.agente;
  burbuja("agente", r.texto);
  pintarRetenido(r.retiene);
  pintarPanel(r.expediente);

  const completo = await hablar(r.decir, r.audio);
  if (!completo || !enLlamada) return;        // nos han colgado a mitad

  if (estado.paso === "FIN") return colgar();
  if (!reproduciendo) escuchar();
  else estadoVoz("en llamada");
}

/* ------------------------------------------------------------- controles */
async function descolgar() {
  // Este clic es el gesto de usuario que el navegador exige antes de dejar
  // sonar nada. Sin el, la primera frase sale muda. Aqui se gasta en las
  // tres cosas que lo necesitan: el AudioContext de los pitidos, el
  // elemento <audio> y el sintetizador.
  const suena = pitidos();
  prepararAltavoz();
  callar();
  enLlamada = true;
  estado = {};
  $("conversacion").replaceChildren();
  pintarPanel(null);
  pintarRetenido(null);
  avisar("");
  $("punto").classList.add("viva");
  $("descolgar").disabled = true;
  $("colgar").disabled = false;
  if (!rec) rec = crearRec();
  // Que el saludo no pise al tono de llamada.
  await esperar(suena + 250);
  await enviarTurno([]);
}

function colgar() {
  enLlamada = false;
  quiereEscuchar = false;
  reproduciendo = false;
  rec?.abort();
  callar();
  $("punto").classList.remove("viva");
  $("descolgar").disabled = false;
  $("colgar").disabled = true;
  estadoVoz("en espera");
}

/* Reproduce una llamada grabada. La entrada es falsa; todo lo demas -- el
 * estado, la consulta a la base, lo que se dice, el panel -- sale del
 * servidor igual que en vivo, porque pasa por la MISMA `enviarTurno`. */
async function reproducir(llamada) {
  // ANTES del descolgar: si no, el saludo corre con `reproduciendo` a false
  // y la llamada grabada abre el microfono nada mas empezar.
  reproduciendo = true;
  await descolgar();
  for (const t of llamada.turnos) {
    if (!reproduciendo) return;
    estadoVoz("escuchando…");
    await esperar(t.pausa_ms ?? 800);
    await enviarTurno(t.oye);
  }
  reproduciendo = false;
}

async function cargarGrabadas() {
  try {
    const { llamadas } = await (await fetch(GUIONES)).json();
    $("grabadas").replaceChildren(...llamadas.map((l) => {
      const b = document.createElement("button");
      b.textContent = `▶ ${l.titulo}`;
      b.title = l.pie || "";
      b.onclick = () => reproducir(l);
      return b;
    }));
  } catch { /* sin llamadas grabadas: se conduce a mano */ }
}

$("descolgar").onclick = () => { reproduciendo = false; descolgar(); };
$("colgar").onclick = colgar;
$("micro").onclick = () => { if (enLlamada) escuchar(); };
$("formulario").onsubmit = (e) => {
  e.preventDefault();
  const t = $("texto").value.trim();
  if (!t) return;
  $("texto").value = "";
  reproduciendo = false;
  if (!enLlamada) descolgar().then(() => enviarTurno([t]));
  else enviarTurno([t]);
};

if (!SR) {
  avisar("Este navegador no reconoce voz (usa Safari o Chrome). El agente " +
         "habla igual y la llamada se conduce por teclado.");
  $("micro").disabled = true;
}
cargarGrabadas();
