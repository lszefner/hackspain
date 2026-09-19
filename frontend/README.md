# tito.ai · frontend

La web del trabajador digital: el listado crudo de facturas, la traza de cada
decisión, la bandeja de escalados, los logs del pipeline y el manual de
normas versionado.

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000 · código de acceso: albertito
```

Candado de acceso: `SITE_ACCESS_CODE` (ver `.env.example`).

## Rutas

| Ruta | Qué es | Rúbrica |
|---|---|---|
| `/` | Redirige a `/facturas` | — |
| `/facturas` | El listado crudo: etapa del pipeline, vía, proveedor, pedido, total, decisión, motivo, latencia y coste | traza |
| `/expediente/[fileId]` | Una decisión de punta a punta: reglas con evidencia, campos, custodia (sha256), versiones, eventos | 20 traza |
| `/bandeja` | Los 49 escalados; resolver como Alberto y borrador de email al proveedor (nunca toca outcomes.jsonl) | +10 bonus |
| `/logs` | El registro de eventos: cuándo se ingirió, extrajo y decidió cada factura, filtrable | 20 traza |
| `/finanzas` | Coste por ruta medido, proyecciones, diff entre normas, salud, parte de trabajo | 25 + 10 |
| `/logica` | Las normas como fichas legibles, versionadas e inmutables, con versión activa | 35 producto |
| `/logica/editor` | Redactar norma nueva: borrador → **ensayo en seco sobre las 500** → publicar y reprocesar | bonus |
| `/muro` | La pared de 500 baldosas coloreada por resultado — la portada de la demo | demo |
| `/phone_calls` | La centralita de voz: atiende al proveedor por teléfono y le contesta lo que el motor ya decidió — sin decirle lo que no debe | bonus |

## Arquitectura: la costura

Las páginas consumen únicamente la interfaz `DataSource` de
`src/lib/data.ts`. Los tipos de `src/lib/types.ts` calcan el esquema real
(importes en céntimos = invariante Decimal; claves `doc_id`, `norma_version`,
`snapshot_erp`, `snapshot_maestro`).

**Hoy** la implementa un mock determinista (`src/lib/mock/dataset.ts`) fiel al
estado medido: 431 PAGAR · 49 ESCALAR · 20 NO_PAGAR y los € al céntimo.
**Conectar el backend real** = escribir una segunda `DataSource` (fetch a
`/api/resumen` y compañía, o better-sqlite3) sin tocar ninguna vista.

Las normas (`src/lib/normas.ts`) se suben como YAML pero se presentan como
manual: versiones inmutables, ensayo en seco antes de publicar, y la última
publicada es la activa por defecto en todo el sitio.

## La centralita (`/phone_calls`)

No es una página de Next. Es **la misma página** que sirve `make centralita` en
el 8011, publicada tal cual en `public/` por `make web-centralita`, más una
Vercel Function de Python en `api/centralita/turno.py` que importa `guion.py`
**verbatim** desde `centralita_py/`. La política de lo que se dice y lo que se
calla es un único fichero, y corre igual en los dos sitios.

```bash
make web-centralita      # desde la raíz del repo, tras tocar phone_calls/
```

`centralita_py/` y `public/phone_calls/` son **artefactos generados**: no se
editan a mano. `tests/test_publicacion_web.py` compara byte a byte contra el
original y falla si alguien toca `guion.py` y no republica.

El audio va precomputado (`public/phone_calls/voz/`, 9 frases de Inés). En
producción **no hay ninguna llamada a ElevenLabs**: `voz.py` no está
desplegado, y un test lo comprueba con un grep. Lo que se improvise fuera del
guion lo dirá la voz del navegador.

> **No crear `requirements.txt` ni `pyproject.toml` aquí.** Vercel detectaría
> un framework preset de Python y éste se quedaría con **todas** las rutas del
> proyecto, tumbando el sitio entero. El árbol vendorizado es stdlib puro y no
> necesita ninguno; la versión se fija en `.python-version`.
