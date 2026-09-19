# Mesa de Alberto · design decisions

What the interface looks like, and why. Written from the working prototype of the
Agent screen, so every value here is one that is actually on screen — not an
aspiration. If you change a token, change it here too, and say what it broke.

The rule underneath all of it: **the screen is an operator's desk, not a
dashboard.** Alberto is not exploring data, he is clearing a queue before lunch.
Everything below follows from that.

---

## 1. Colour

### The system

Warm, paper-like, brown rather than black. Black on white is the default of every
admin tool ever built; brown on cream reads as a ledger and a desk, which is what
this is. It is also easier to sit in front of for four hours.

| Token | Value | Where it goes |
| --- | --- | --- |
| `--bg` | `#fbf9f5` | the page, the sidebar |
| `--panel` | `#ffffff` | cards, tables, the composer |
| `--ink` | `#2b1f16` | all body text, the globe |
| `--mut` | `#7b6a5c` | secondary lines, supplier names |
| `--faint` | `#a49486` | labels, counts, metadata |
| `--line` | `#e9e2d8` | every divider and border |
| `--line2` | `#d9cec0` | hover borders, the table header rule |
| `--accent` | `#4a3324` | solid buttons, the logo tile |
| `--chip` | `#f3efe8` | user bubbles, hover fills, panel footers |
| `--chip2` | `#eae4da` | the one step darker, for button hover |

`--ink` on `--bg` is roughly 13:1. `#fff` on `--accent` is roughly 11:1. Both are
comfortably past AA at every size used.

### The three verdicts

These are the only saturated colours in the product, and they are reserved. If
something is green here it means *pay*, always. Never use them decoratively.

| Verdict | Text | Tint | Meaning |
| --- | --- | --- | --- |
| PAY | `--pay` `#4a6b2f` | `--pay-soft` `#eef3e7` | clean against the rules |
| ESCALATE | `--esc` `#9a6412` | `--esc-soft` `#faf1e2` | needs Alberto |
| DO NOT PAY | `--no` `#a33124` | `--no-soft` `#faebe8` | a rule blocked it |

They are muted on purpose — olive, ochre, brick rather than traffic-light. A
screen with twelve escalations on it should not look like an alarm panel.

### Loading blue

`--load` `#2f62d8`, on `--load-soft` `#eef2fd` with a `--load-line` `#d5e0fa`
border. The **only** cool colour in the interface, used for exactly one thing:
work in progress. Because it appears nowhere else, a blue pill anywhere on screen
means *waiting*, with no legend needed.

### No dark mode

Dropped deliberately. One theme, tuned properly, beats two tuned adequately, and
the warm palette is the identity. Revisit only if someone actually asks.

---

## 2. Type

**Inter** for everything a human reads. **JetBrains Mono** for everything a human
compares. Both from Google Fonts, with full system stacks behind them so the page
survives offline.

The split is the decision that matters: **every figure is monospaced.** File
names, invoice numbers, amounts, IBANs, dates, costs, token counts. Amounts in a
column have to line up digit over digit or you cannot scan them, and tabular
numerals are set globally via `font-variant-numeric`. Prose stays proportional.

### The ramp

| Size | Weight | Use |
| --- | --- | --- |
| 32px | 600 | the greeting, once, on the empty screen |
| 20px | 600 | view titles (Invoices, Rules) |
| 15.5px | 400 | what the desk says — the most-read text on screen |
| 14.5px | 500 | row titles, nav, body default |
| 13px | 500 | buttons |
| 12.5px | 400 | row subtitles, supplier names |
| 12px | 400 mono | table cells |
| 11px | 600 | panel headers, uppercase, `.11em` tracking |
| 9.5px | 600 | table column headers, uppercase, `.11em` tracking |

Body line-height is 1.55; the desk's own sentences get 1.62 because they are the
thing being read rather than scanned. Headings take negative tracking
(`-.028em` at 32px, `-.008em` at row size); small uppercase labels take positive
(`.11em`). Nothing is bolder than 600.

---

## 3. Shape and depth

Radii climb with the size of the thing: 6–7px on chips and tiny tiles, 9px on
buttons, 10px on nav items, 12px on attachment cards, 14px on panels, 16px on the
composer, `999px` on pills.

Two shadows only, both warm-tinted (`rgba(60,40,24,…)`, never neutral black):

- `--shadow` — resting cards. Barely there.
- `--shadow-lift` — hover, and the focused composer.

Depth is carried by **borders**, not shadows. Every panel has a 1px `--line`
edge. Shadow is a hint that something is liftable, not the structure itself.

---

## 4. Motion

One easing curve everywhere: `--ease: cubic-bezier(.2,.7,.2,1)`. Fast out of the
gate, long settle. Apple-ish. Never use `ease-in-out`.

| What | Duration | Note |
| --- | --- | --- |
| hover, colour, border | 150ms | |
| message entering | 360ms | fade + 9px rise |
| panel row stagger | 300ms | 50ms apart, capped after five rows |
| drawer open/close | 440ms | `grid-template-rows: 0fr → 1fr` |
| word appearing | 520ms | blur 7px → 0, 4px rise |
| composer dropping to the bottom | 600ms | on first message |

### Three motion decisions worth keeping

**Drawers animate their height.** `display:none` → `block` is a switch, not a
motion. The `0fr → 1fr` grid trick animates real height with no measured pixel
values and no JavaScript.

**Text arrives as a cloud, not a typewriter.** Each word fades up out of a 7px
blur, one every 24ms. A blinking caret and character-by-character typing is a
1980s affectation; this reads faster and feels composed rather than typed.

**Nothing answers in under four seconds.** `MIN_THINK = 4000`. The model often
returns in two, and it looked cheap — as though nothing had been checked. The
loader holds for four seconds minimum, and longer whenever the model genuinely
takes longer. Deliberately slower, because trust matters more than speed when the
answer decides whether money moves.

The loader itself is a dotted globe: 220 points on latitude rings, tipped toward
the viewer, spinning on its axis, each point fading and shrinking as it passes
behind. Canvas, 34px, `--load` blue. Its label names the actual stage — *digging
through the ledger*, *arguing with the rules* — rotating every 1.9s, so the wait
says something.

**It sits in the thread, never over it.** An overlay covering the previous answer
was wrong: old content scrolls up, new content appears at the bottom, like any
conversation.

---

## 5. Layout

- 228px fixed sidebar, fluid main. Below 760px the sidebar collapses to 54px of
  icons and the brand tile.
- Conversation column caps at 800px; the composer at 720px empty, 800px in
  conversation.
- 32px page gutters, 14px below the breakpoint.
- The app is `position:fixed; inset:0` so only the thread scrolls.
- Works at 1280px. Verified down to 700px.

---

## 6. Components

### The panel — list first, actions after

The single most important component, and the one we got wrong twice. Three fat
cards each with its own button row became **one panel**: a header with the count
and total, then compact rows, then the actions in a footer strip.

Rows expand in place to show the reasoning, the invoices behind them in a proper
five-lane table, and that row's own buttons. Inside that table each invoice
expands again to show what was read off the page.

**The last column of that table is the point of it.** Not the amount — the
finding. *no order on file*, *account ends 4455*, *€1,440.00 over PED-0412*. A
table without it is a spreadsheet.

The same component renders payments, the day report, duplicates, the ruleset and
a dropped batch. One shape for everything.

### Queue state

A 6px dot at the head of each row: filled ink for unseen, `--line2` for seen,
`--esc` for deferred. No badges, no counts.

### Actions

Immediate and deterministic. **No model sits in the path of a button** — clicking
Accept must not wait four seconds. The server mutates state, answers with one
written line, and the panels on screen redraw from what the desk now holds so you
can never act on a stale copy.

Every destructive action snapshots first and offers **Put it back**.

`Turn into a rule` appears only on clusters of three or more, and the server
refuses it on a single invoice: *one invoice is not a pattern*.

### Attachments

Attaching never sends. Files land in a tray above the composer, upload straight
away, and wait for you to write a message. Send stays disabled until they land.

The progress bar is deliberately walked over 2–6.5s. On localhost the bytes
arrive instantly and the transfer was illegible.

---

## 7. Voice

English, operational, first person. A colleague handing over a shift.

- Say what it did: *I checked*, *I wrote to*, *I stopped*.
- **Never** *agent*, *assistant*, *AI*, *model*. Never greet, never offer help.
- Two sentences, under 45 words.
- Never restate the list underneath it. The panel does the listing; the sentence
  says the one thing the list cannot.
- One dry aside is allowed. Never instead of a fact, and never about money going
  wrong.
- Errors name what happened and what did not move: *Nothing moved after all.*

Every figure comes from the state object handed to the model. If it is not in
there, the model says so and names the nearest thing it does have.

---

## 8. Constraints

- **No framework.** One HTML file, vanilla CSS and JS, no build step. The
  libraries.dev components were rejected for this reason — React-only, and
  adopting one would have pulled React into the frontend. The globe and the
  uploader were rebuilt in CSS and canvas instead.
- **No charting library.** Stacked bars are inline CSS.
- **No wizards, no multi-step modals.** Every action commits from one card.
- **The frontend contains no business logic.** No percentages, no status
  derivation, no aggregation in JavaScript. Everything derived is computed server
  side and served over `/api/*`.
- **Honesty about demo data.** Anything not genuinely computed carries a `demo`
  badge, and the model is told not to claim it read the pages.

---

## 9. Open

- Dark mode — dropped, not forgotten.
- The Invoices and Summary views are specified but unbuilt.
- The prototype talks to a fixture, not the ledger. Shapes match `desk/state.py`
  so the swap is one import.
