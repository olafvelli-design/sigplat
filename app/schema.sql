-- SIGPLAT · esquema del prototipo (SQLite, sin costo).
-- Todas las tablas de negocio llevan ente_id: un mismo despliegue sirve a varios entes.
-- Fechas en UTC con formato ISO 8601 'YYYY-MM-DDTHH:MM:SSZ'; fechas planificadas como 'YYYY-MM-DD'.

CREATE TABLE IF NOT EXISTS entes (
    id          INTEGER PRIMARY KEY,
    codigo      TEXT NOT NULL UNIQUE,
    nombre      TEXT NOT NULL,
    config_json TEXT NOT NULL,
    ficticio    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS usuarios (
    id            INTEGER PRIMARY KEY,
    ente_id       INTEGER NOT NULL REFERENCES entes(id),
    nombre        TEXT NOT NULL,
    login         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    rol           TEXT NOT NULL CHECK (rol IN ('inspector','responsable','autoridad','administrador','vecino')),
    activo        INTEGER NOT NULL DEFAULT 1
);

-- Sesiones: sólo se guarda el hash del token. Se revocan, no se borran.
CREATE TABLE IF NOT EXISTS sesiones (
    id          INTEGER PRIMARY KEY,
    token_hash  TEXT NOT NULL UNIQUE,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
    creada_at   TEXT NOT NULL,
    expira_at   TEXT NOT NULL,
    revocada    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS incidencias (
    id              INTEGER PRIMARY KEY,
    ente_id         INTEGER NOT NULL REFERENCES entes(id),
    codigo          TEXT NOT NULL UNIQUE,
    categoria       TEXT NOT NULL,
    descripcion     TEXT NOT NULL,
    lat             REAL NOT NULL,
    lon             REAL NOT NULL,
    precision_m     REAL,
    canal           TEXT NOT NULL,
    fecha_reporte   TEXT NOT NULL,
    estado          TEXT NOT NULL CHECK (estado IN ('recibida','confirmada','descartada','duplicada','con_intervencion','resuelta')),
    gravedad        INTEGER,
    duplicada_de    INTEGER REFERENCES incidencias(id),
    reportes_count  INTEGER NOT NULL DEFAULT 1,
    intervencion_id INTEGER REFERENCES intervenciones(id),
    fecha_resuelta  TEXT,
    reportada_por   INTEGER REFERENCES usuarios(id)  -- sólo si reportó un vecino con cuenta; nunca se muestra en vistas internas
);

CREATE TABLE IF NOT EXISTS inspecciones (
    id              INTEGER PRIMARY KEY,
    incidencia_id   INTEGER NOT NULL REFERENCES incidencias(id),
    inspector_id    INTEGER NOT NULL REFERENCES usuarios(id),
    fecha           TEXT NOT NULL,
    resultado       TEXT NOT NULL,
    gravedad        INTEGER,
    observaciones   TEXT
);

CREATE TABLE IF NOT EXISTS intervenciones (
    id                    INTEGER PRIMARY KEY,
    ente_id               INTEGER NOT NULL REFERENCES entes(id),
    tipo                  TEXT NOT NULL,
    titulo                TEXT NOT NULL,
    lat                   REAL,
    lon                   REAL,
    responsable_id        INTEGER NOT NULL REFERENCES usuarios(id),
    estado                TEXT NOT NULL CHECK (estado IN ('planificada','en_ejecucion','finalizada_declarada','validada','cerrada')),
    creada_por            INTEGER NOT NULL REFERENCES usuarios(id),
    fecha_creacion        TEXT NOT NULL,
    fecha_planificada_fin TEXT NOT NULL,
    sistema_externo       TEXT CHECK (sistema_externo IN ('SIGO','SIGUE') OR sistema_externo IS NULL),
    expediente_externo    TEXT,
    fecha_inicio_real     TEXT,
    fecha_fin_declarada   TEXT,
    fecha_fin_real        TEXT,
    fecha_cierre          TEXT
);

CREATE TABLE IF NOT EXISTS actividades (
    id               INTEGER PRIMARY KEY,
    intervencion_id  INTEGER NOT NULL REFERENCES intervenciones(id),
    nombre           TEXT NOT NULL,
    pct_declarado    REAL NOT NULL DEFAULT 0,
    pct_validado     REAL NOT NULL DEFAULT 0
);

-- Cada declaración de avance queda registrada. Nunca se sobrescribe: sólo cambia su estado de revisión.
CREATE TABLE IF NOT EXISTS avances (
    id            INTEGER PRIMARY KEY,
    actividad_id  INTEGER NOT NULL REFERENCES actividades(id),
    pct           REAL NOT NULL,
    declarado_por INTEGER NOT NULL REFERENCES usuarios(id),
    declarado_at  TEXT NOT NULL,
    nota          TEXT,
    estado        TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente','validado','observado')),
    validado_por  INTEGER REFERENCES usuarios(id),
    validado_at   TEXT,
    observacion   TEXT
);

-- Las evidencias no se borran ni se reemplazan: una nueva foto referencia a la anterior con reemplaza_a.
CREATE TABLE IF NOT EXISTS evidencias (
    id              INTEGER PRIMARY KEY,
    ente_id         INTEGER NOT NULL REFERENCES entes(id),
    intervencion_id INTEGER REFERENCES intervenciones(id),
    incidencia_id   INTEGER REFERENCES incidencias(id),
    tipo            TEXT NOT NULL CHECK (tipo IN ('foto_reporte','avance','cierre','inspeccion')),
    actividad_id    INTEGER REFERENCES actividades(id),
    sha256          TEXT NOT NULL,
    ruta            TEXT NOT NULL,
    lat             REAL,
    lon             REAL,
    precision_m     REAL,
    capturada_at   TEXT,
    recibida_at     TEXT NOT NULL,
    subida_por      INTEGER REFERENCES usuarios(id),
    revision_estado TEXT NOT NULL DEFAULT 'pendiente' CHECK (revision_estado IN ('pendiente','validada','observada')),
    revisada_por    INTEGER REFERENCES usuarios(id),
    revisada_at     TEXT,
    observacion     TEXT,
    reemplaza_a     INTEGER REFERENCES evidencias(id),
    banderas        TEXT NOT NULL DEFAULT '[]'
);

-- Historial de auditoría: sólo inserciones.
CREATE TABLE IF NOT EXISTS historial (
    id          INTEGER PRIMARY KEY,
    ente_id     INTEGER NOT NULL REFERENCES entes(id),
    entidad     TEXT NOT NULL,
    entidad_id  INTEGER NOT NULL,
    accion      TEXT NOT NULL,
    usuario_id  INTEGER REFERENCES usuarios(id),
    ocurrido_at TEXT NOT NULL,
    detalle     TEXT NOT NULL DEFAULT '{}'
);

CREATE TRIGGER IF NOT EXISTS historial_no_update BEFORE UPDATE ON historial
BEGIN SELECT RAISE(ABORT, 'historial es append-only'); END;
CREATE TRIGGER IF NOT EXISTS historial_no_delete BEFORE DELETE ON historial
BEGIN SELECT RAISE(ABORT, 'historial es append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidencias_no_delete BEFORE DELETE ON evidencias
BEGIN SELECT RAISE(ABORT, 'las evidencias no se eliminan'); END;
CREATE TRIGGER IF NOT EXISTS avances_no_delete BEFORE DELETE ON avances
BEGIN SELECT RAISE(ABORT, 'los avances declarados no se eliminan'); END;
