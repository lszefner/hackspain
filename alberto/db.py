"""SQLite: todo el estado vive aqui. Nada en memoria entre etapas.

Los importes se guardan como TEXTO, nunca REAL: un float con tolerancia de
0,01 EUR es como se pierden las facturas.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

RUTA_DB = Path("alberto.db")

ESQUEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documentos (
  doc_id TEXT PRIMARY KEY,
  file_id TEXT NOT NULL UNIQUE,
  ruta TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  tiene_texto INTEGER NOT NULL,
  lote TEXT NOT NULL DEFAULT 'lote1',
  estado TEXT NOT NULL DEFAULT 'pendiente',
  intentos INTEGER NOT NULL DEFAULT 0,
  ultimo_error TEXT,
  creado_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_doc_estado ON documentos(estado, lote);

CREATE TABLE IF NOT EXISTS extracciones (
  doc_id TEXT NOT NULL REFERENCES documentos(doc_id),
  intento INTEGER NOT NULL,        -- 1 determinista, 2 vision
  plantilla TEXT, via TEXT,
  campos_json TEXT NOT NULL,
  campos_faltantes TEXT,
  cuadra_interna INTEGER,
  coste_eur TEXT NOT NULL DEFAULT '0',
  latencia_ms INTEGER DEFAULT 0,
  aceptada INTEGER NOT NULL DEFAULT 1,   -- 0 = se intento y se rechazo
  motivo_rechazo TEXT,
  modelo TEXT,
  tokens_entrada INTEGER NOT NULL DEFAULT 0,
  tokens_salida INTEGER NOT NULL DEFAULT 0,
  n_llamadas INTEGER NOT NULL DEFAULT 0,
  creado_at TEXT NOT NULL,
  PRIMARY KEY (doc_id, intento)
);

-- === capa raw: lo que el sistema VIO, antes de interpretarlo =============
-- Direccionada por CONTENIDO: el mismo PNG re-renderizado al reanudar no
-- duplica ni una fila ni un byte. Un PNG a 200 dpi pesa 0,5-1 MB y hay ~33
-- documentos en la fase 2: los PNG van a disco y aqui queda su sha256; el
-- texto de pdfplumber y el JSON de las llamadas caben inline.
CREATE TABLE IF NOT EXISTS artefactos (
  sha256 TEXT PRIMARY KEY,         -- sha256 del CONTENIDO, no del documento
  tipo TEXT NOT NULL,              -- texto_pdf|pagina_png|llamada|lectura
  mime TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  contenido BLOB,                  -- inline
  ruta TEXT,                       -- en disco
  creado_at TEXT NOT NULL,
  CHECK ((contenido IS NULL) <> (ruta IS NULL))
);

-- Sin clave ajena compuesta contra extracciones(doc_id,intento) A PROPOSITO:
-- el PNG se guarda ANTES de llamar al modelo, para que una pasada cortada a
-- la mitad siga siendo inspeccionable. Con PRAGMA foreign_keys=ON esa clave
-- impediria justo lo que da reanudabilidad.
CREATE TABLE IF NOT EXISTS extraccion_artefactos (
  doc_id TEXT NOT NULL REFERENCES documentos(doc_id),
  intento INTEGER NOT NULL,
  rol TEXT NOT NULL,               -- texto_pdf|pagina_png|llamada|lectura
  orden INTEGER NOT NULL DEFAULT 0,-- n de pagina, o n de llamada
  sha256 TEXT NOT NULL REFERENCES artefactos(sha256),
  modelo TEXT,
  creado_at TEXT NOT NULL,
  PRIMARY KEY (doc_id, intento, rol, orden)
);
CREATE INDEX IF NOT EXISTS ix_art_doc ON extraccion_artefactos(doc_id, intento);

CREATE TABLE IF NOT EXISTS snapshots_erp (
  snapshot_id TEXT PRIMARY KEY,
  origen TEXT NOT NULL,
  n_asientos INTEGER NOT NULL,
  total_declarado INTEGER,
  completo INTEGER NOT NULL DEFAULT 0,
  creado_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asientos (
  snapshot_id TEXT NOT NULL REFERENCES snapshots_erp(snapshot_id),
  asiento_id TEXT NOT NULL,
  pedido TEXT NOT NULL,
  nif TEXT, proveedor_id TEXT,
  importe_esperado TEXT NOT NULL,
  estado TEXT NOT NULL,
  fecha_registro TEXT,
  PRIMARY KEY (snapshot_id, asiento_id)
);
CREATE INDEX IF NOT EXISTS ix_asi_pedido ON asientos(snapshot_id, pedido);

CREATE TABLE IF NOT EXISTS maestro_versiones (
  version_id TEXT PRIMARY KEY,
  origen TEXT NOT NULL, autor TEXT, motivo TEXT, creado_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proveedores (
  version_id TEXT NOT NULL REFERENCES maestro_versiones(version_id),
  nif TEXT NOT NULL,
  proveedor_id TEXT, razon_social TEXT,
  iban TEXT NOT NULL, condiciones_dias INTEGER,
  PRIMARY KEY (version_id, nif)
);
CREATE TABLE IF NOT EXISTS pedidos_excel (
  version_id TEXT NOT NULL REFERENCES maestro_versiones(version_id),
  pedido TEXT NOT NULL,
  proveedor_id TEXT, nif TEXT,
  importe TEXT NOT NULL, estado TEXT, fecha TEXT,
  PRIMARY KEY (version_id, pedido)
);

CREATE TABLE IF NOT EXISTS decisiones (
  doc_id TEXT NOT NULL REFERENCES documentos(doc_id),
  norma_version TEXT NOT NULL,
  snapshot_erp TEXT NOT NULL,
  snapshot_maestro TEXT NOT NULL,
  result TEXT NOT NULL CHECK (result IN ('PAGAR','NO_PAGAR','ESCALAR')),
  motivo TEXT,
  reglas_json TEXT NOT NULL,
  coste_eur TEXT NOT NULL DEFAULT '0',
  latencia_ms INTEGER DEFAULT 0,
  creado_at TEXT NOT NULL,
  PRIMARY KEY (doc_id, norma_version, snapshot_erp, snapshot_maestro)
);

CREATE TABLE IF NOT EXISTS eventos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id TEXT, etapa TEXT NOT NULL, nivel TEXT NOT NULL,
  mensaje TEXT NOT NULL, datos_json TEXT, at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ev_doc ON eventos(doc_id, id);

CREATE TABLE IF NOT EXISTS resoluciones (
  doc_id TEXT PRIMARY KEY REFERENCES documentos(doc_id),
  result TEXT NOT NULL CHECK (result IN ('PAGAR','NO_PAGAR','ESCALAR')),
  motivo TEXT NOT NULL, resuelto_por TEXT, at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ambito TEXT NOT NULL,          -- proveedor | pedido | regla | general
  clave TEXT,                    -- P002 | PO-2026-0007 | R3_iva
  texto TEXT NOT NULL,
  origen TEXT NOT NULL DEFAULT 'excel',
  creado_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notas ON notas(ambito, clave);
"""


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Columnas anadidas despues de la primera version del esquema. SQLite no
# admite ADD COLUMN NOT NULL sin DEFAULT, asi que todas lo llevan.
COLUMNAS_NUEVAS: tuple[tuple[str, str, str], ...] = (
    ("extracciones", "aceptada", "aceptada INTEGER NOT NULL DEFAULT 1"),
    ("extracciones", "motivo_rechazo", "motivo_rechazo TEXT"),
    ("extracciones", "modelo", "modelo TEXT"),
    ("extracciones", "tokens_entrada", "tokens_entrada INTEGER NOT NULL DEFAULT 0"),
    ("extracciones", "tokens_salida", "tokens_salida INTEGER NOT NULL DEFAULT 0"),
    ("extracciones", "n_llamadas", "n_llamadas INTEGER NOT NULL DEFAULT 0"),
)

# La extraccion VIGENTE de cada documento: el intento mas alto que fue
# ACEPTADO. Un intento rechazado se conserva -- es la prueba de que se intento
# la vision y de por que no se uso -- pero no decide nada.
#
# Esta vista ES la frontera con la capa de reglas: garantiza UNA fila por
# documento, elegida explicitamente y no por el orden del cursor.
VISTA_VIGENTE = """
DROP VIEW IF EXISTS extraccion_vigente;
CREATE VIEW extraccion_vigente AS
SELECT x.* FROM extracciones x
 WHERE x.aceptada = 1
   AND x.intento = (SELECT MAX(y.intento) FROM extracciones y
                     WHERE y.doc_id = x.doc_id AND y.aceptada = 1);
"""


def _migrar(con: sqlite3.Connection) -> None:
    """ALTER idempotente sobre una BD anterior. `alberto.db` se regenera en
    10 s SALVO el snapshot del ERP, que cuesta minutos contra el bridge de
    2009: no obliguemos a nadie a borrarla."""
    for tabla, columna, ddl in COLUMNAS_NUEVAS:
        existentes = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        if existentes and columna not in existentes:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {ddl}")


def conectar(ruta: Path | str = RUTA_DB) -> sqlite3.Connection:
    con = sqlite3.connect(str(ruta), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA)
    _migrar(con)
    # Se recrea SIEMPRE: asi una BD vieja no arrastra una definicion obsoleta.
    con.executescript(VISTA_VIGENTE)
    return con


def dec(valor: str | None) -> Decimal | None:
    """TEXTO -> Decimal. La unica puerta de entrada de importes."""
    return None if valor is None or valor == "" else Decimal(valor)


def txt(valor: Decimal | None) -> str | None:
    return None if valor is None else str(valor)


def log(con: sqlite3.Connection, etapa: str, mensaje: str, *,
        doc_id: str | None = None, nivel: str = "info", **datos) -> None:
    con.execute(
        "INSERT INTO eventos (doc_id, etapa, nivel, mensaje, datos_json, at)"
        " VALUES (?,?,?,?,?,?)",
        (doc_id, etapa, nivel, mensaje,
         json.dumps(datos, default=str, ensure_ascii=False) if datos else None,
         ahora()),
    )
