# TODO

Cada tarea es **un commit**. Marca la casilla al mergear.
`Ⓟ` = puntos de rúbrica que compra · `👤` = dueño sugerido · `⏱` = tamaño.

**El backlog es la rúbrica.** Lo que no compra puntos ni protege la puerta de
entrada va al final, o no se hace.

| Criterio | Puntos |
|---|---:|
| Producto, arquitectura y ADRs | 35 |
| Trazabilidad y observabilidad | 20 |
| Escalabilidad y coste | 25 |
| Resiliencia y recuperación | 10 |
| Calidad de ejecución | 10 |
| Bonus | +10 |

Recordatorio: **acertar no da puntos.** La validación es binaria — si se falla no
se opta al premio, pero acertar no puntúa. Perseguir el 100% es robarle tiempo a
los 45 puntos de trazabilidad y coste.

---

## P0 · Sin esto no hay entrega

- [x] **T01 · `git init` y repo público** 👤P3 ⏱15m
  Crear el repo en GitHub **público** (el detector de stack de HackSpain se niega
  a escanear repos privados), `hackspain team repo <url>`, y que **cada uno haga
  su primer commit desde su propia cuenta vinculada**.
  *Hecho cuando:* el repo aparece en el feed y los 4 figuran en `git shortlog -sn`.

---

## P3 · El documento y la defensa (35 puntos)

- [ ] **T16 · `albertitos_plan.pdf`** 👤P4 ⏱2h
  Dos secciones: Arquitectura (componentes, flujo de datos, estado, reparto entre
  agentes, modelos y personas, y cómo se observan y recuperan los fallos) y
  ADRs con contexto, alternativas, decisión, consecuencias y evidencia. Ⓟ 35

- [ ] **T17 · Guion de defensa de 10 minutos** 👤P4 ⏱1h
  2 demo · 2 arquitectura · **4 traza/escala/coste** · 2 resiliencia y preguntas.
  Cronometrado y ensayado 5 veces. Cada bloque lo defiende quien lo construyó.

---

## P4 · HackSpain general (es otra entrega distinta)

- [ ] **T18 · Vídeo para el jurado** 👤P4 ⏱2h
  **YouTube oculto, Loom o `.mp4` directo — Vimeo y Google Drive no embeben.**
  El jurado puntúa 1–10 viendo esto y un proyecto sin puntuar cae por debajo de
  todos. Grabar la web, no el terminal.

- [ ] **T19 · `hackspain submit`** 👤P3 ⏱30m
  Draft ya, definitivo al final. `--track maisa` + los perks reclamados (salen en
  la tarjeta del jurado). Logo y nombre de equipo también salen: cuestan 10
  minutos y casi nadie los pone.

- [ ] **T20 · Milestones y telemetría** 👤todos ⏱15m
  `hackspain watch` abierto en las 4 máquinas (CLI ≥ 0.5.0) y
  `milestone add firstCommit|firstBuild|firstDemo` en cuanto toquen.

---

## P5 · Si sobra tiempo

- [ ] **T21 · CI en GitHub Actions** ⏱30m — typecheck + tests en cada PR. Los PRs
  también salen en el feed público, así que el CI es visibilidad además de red.
- [ ] **T24 · Comprobación de PII antes de entregar** ⏱20m — que no se cuele un
  IBAN o un NIF en un log, en un evento o en el PDF.

---

## Deliberadamente NO vamos a hacer

Escrito ahora para poder decir que no a las 4 de la mañana:

- **Autenticación y multiusuario en la web.** Es una demo de 10 minutos.
- **Perseguir el 100% de acierto.** La validación es binaria y no da puntos.

---

## Hitos con hora fija

| Cuándo | Qué |
|---|---|
| **Sábado 18:00** | Llegan lote 2, ERP actualizado y **norma v4** |
| **Sábado 23:00** | Corte del bonus. Si no está, se cuenta como diseño en el PDF |
| **Domingo 02:00** | **Congelación de código.** A partir de aquí solo documento y ensayo |
| **Domingo 10:30** | La organización clona el repo de outcomes y registra el commit |
