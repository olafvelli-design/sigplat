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
    rol           TEXT NOT NULL CHECK (rol IN ('inspector','responsable','autoridad','administrador','vecino','contratista','administrativo',
                                              'jefe_inspeccion','analista_certificaciones','jefe_certificaciones','director','auditor')),
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

-- =====================================================================================================
-- Modo obra: simulación de SIGO (contrato y dinero) y SIGUE (campo). Ver MODO_OBRA.md.
-- Dinero en centavos enteros. Las cantidades de avance son del MES, no acumuladas.
-- =====================================================================================================
CREATE TABLE IF NOT EXISTS obras (
    id                   INTEGER PRIMARY KEY,
    ente_id              INTEGER NOT NULL REFERENCES entes(id),
    denominacion         TEXT NOT NULL,
    estado               TEXT NOT NULL CHECK (estado IN ('preadjudicada','adjudicada','en_ejecucion','finalizada')),
    contratista          TEXT NOT NULL,
    contratista_id       INTEGER REFERENCES usuarios(id),
    inspector_id         INTEGER REFERENCES usuarios(id),
    fecha_replanteo      TEXT NOT NULL,
    plazo_dias           INTEGER NOT NULL CHECK (plazo_dias > 0),
    anticipo_pct         REAL NOT NULL DEFAULT 0,
    fondo_reparo_pct     REAL NOT NULL DEFAULT 5,
    fondo_inspeccion_pct REAL NOT NULL DEFAULT 0,       -- 0 = "NO contiene fondo de inspección"
    plazo_venc_cert_dias INTEGER NOT NULL DEFAULT 30,
    lat                  REAL,
    lon                  REAL,
    curva_cerrada        INTEGER NOT NULL DEFAULT 0,
    -- identificación: el código de SIGPLAT enlaza con los números oficiales de la contratación (ver obra.py, 'códigos de obra')
    codigo                  TEXT,
    tipo_contratacion       TEXT,
    nro_contratacion        TEXT,
    expediente              TEXT,
    servicio_administrativo TEXT,
    legajo_estado           TEXT NOT NULL DEFAULT 'en_armado' CHECK (legajo_estado IN ('en_armado','publicado')),
    legajo_publicado_por    INTEGER REFERENCES usuarios(id),
    legajo_publicado_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS obras_codigo ON obras(ente_id, codigo) WHERE codigo IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS obras_contratacion ON obras(ente_id, servicio_administrativo, nro_contratacion) WHERE nro_contratacion IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS obras_expediente ON obras(ente_id, expediente) WHERE expediente IS NOT NULL;

-- Último número de código usado por ente y año: nunca baja, así un código no se reutiliza.
CREATE TABLE IF NOT EXISTS secuencias_obra (
    ente_id INTEGER NOT NULL REFERENCES entes(id),
    anio    INTEGER NOT NULL,
    ultimo  INTEGER NOT NULL,
    PRIMARY KEY (ente_id, anio)
);

-- Legajo técnico de la obra (el de la licitación): documentos por tipo, con versiones. Nada se borra.
CREATE TABLE IF NOT EXISTS legajo_obra (
    id                   INTEGER PRIMARY KEY,
    obra_id              INTEGER NOT NULL REFERENCES obras(id),
    ente_id              INTEGER NOT NULL REFERENCES entes(id),
    tipo                 TEXT NOT NULL CHECK (tipo IN ('acto_administrativo','pliego_condiciones','pliego_tecnico','plano','computo_presupuesto','circular','otro')),
    titulo               TEXT NOT NULL,
    numero               INTEGER,                      -- correlativo de las circulares (consultas y respuestas)
    nombre_archivo       TEXT,
    sha256               TEXT NOT NULL,
    ruta                 TEXT NOT NULL,
    mime                 TEXT NOT NULL,
    version              INTEGER NOT NULL DEFAULT 1,
    reemplaza_a          INTEGER REFERENCES legajo_obra(id),
    motivo_version       TEXT,
    posterior_publicacion INTEGER NOT NULL DEFAULT 0,  -- se cargó después de publicar el legajo
    subido_por           INTEGER NOT NULL REFERENCES usuarios(id),
    subido_at            TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS legajo_sin_repetidos ON legajo_obra(obra_id, sha256);

CREATE TABLE IF NOT EXISTS items_obra (
    id                INTEGER PRIMARY KEY,
    obra_id           INTEGER NOT NULL REFERENCES obras(id),
    nro               INTEGER NOT NULL,
    descripcion       TEXT NOT NULL,
    unidad            TEXT,
    cantidad_oferta   REAL NOT NULL DEFAULT 0,
    precio_oferta_cent INTEGER NOT NULL DEFAULT 0,
    es_titulo         INTEGER NOT NULL DEFAULT 0,        -- un rubro que agrupa ítems: sin unidad, cantidad ni precio
    UNIQUE (obra_id, nro)
);

-- Versiones de la curva de avance ideal: la Principal y una más por cada modificación
-- (MCI = modificación de curva ideal, AP = ampliación de plazo). La vigente es la última autorizada.
CREATE TABLE IF NOT EXISTS curvas_ideal (
    id             INTEGER PRIMARY KEY,
    obra_id        INTEGER NOT NULL REFERENCES obras(id),
    nombre         TEXT NOT NULL,                        -- 'Principal', 'MCI-1', 'AmpP-1'
    tipo           TEXT NOT NULL CHECK (tipo IN ('principal','MCI','AP')),
    numero         INTEGER NOT NULL DEFAULT 0,
    estado         TEXT NOT NULL CHECK (estado IN ('pendiente','autorizada')),
    tramite        TEXT,
    instrumento    TEXT,                                 -- instrumento legal que avala la modificación
    dias           INTEGER,                              -- días que suma una ampliación de plazo
    observaciones  TEXT,
    creada_por     INTEGER REFERENCES usuarios(id),
    creada_at      TEXT NOT NULL,
    autorizada_por INTEGER REFERENCES usuarios(id),
    autorizada_at  TEXT,
    UNIQUE (obra_id, nombre)
);

-- Lo que el contratista se comprometió a hacer cada mes (cantidad de ese mes), por versión de la curva.
CREATE TABLE IF NOT EXISTS avance_ideal (
    id       INTEGER PRIMARY KEY,
    curva_id INTEGER NOT NULL REFERENCES curvas_ideal(id),
    item_id  INTEGER NOT NULL REFERENCES items_obra(id),
    mes      TEXT NOT NULL,                              -- 'AAAA-MM'
    cantidad REAL NOT NULL,
    UNIQUE (curva_id, item_id, mes)
);

CREATE TABLE IF NOT EXISTS computos (
    id              INTEGER PRIMARY KEY,
    obra_id         INTEGER NOT NULL REFERENCES obras(id),
    mes             TEXT NOT NULL,
    estado          TEXT NOT NULL CHECK (estado IN ('borrador','presentado','eliminado')),
    personal_afectado INTEGER,
    es_final        INTEGER NOT NULL DEFAULT 0,
    observaciones   TEXT,
    creado_por      INTEGER NOT NULL REFERENCES usuarios(id),
    creado_at       TEXT NOT NULL,
    presentado_por  INTEGER REFERENCES usuarios(id),
    presentado_at   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS computos_un_mes ON computos(obra_id, mes) WHERE estado != 'eliminado';

CREATE TABLE IF NOT EXISTS computo_items (
    id          INTEGER PRIMARY KEY,
    computo_id  INTEGER NOT NULL REFERENCES computos(id),
    item_id     INTEGER NOT NULL REFERENCES items_obra(id),
    cantidad    REAL NOT NULL DEFAULT 0,
    UNIQUE (computo_id, item_id)
);

-- Habilita cargar cómputos con la obra vencida (período de intimación).
CREATE TABLE IF NOT EXISTS plazos_cert_vencida (
    id          INTEGER PRIMARY KEY,
    obra_id     INTEGER NOT NULL REFERENCES obras(id),
    desde       TEXT NOT NULL,
    dias        INTEGER NOT NULL CHECK (dias > 0),
    creado_por  INTEGER NOT NULL REFERENCES usuarios(id),
    creado_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certificados (
    id                 INTEGER PRIMARY KEY,
    obra_id            INTEGER NOT NULL REFERENCES obras(id),
    ente_id            INTEGER NOT NULL REFERENCES entes(id),
    numero             INTEGER NOT NULL,
    tipo               TEXT NOT NULL CHECK (tipo IN ('parcial','final')),
    computo_id         INTEGER NOT NULL REFERENCES computos(id),
    estado             TEXT NOT NULL CHECK (estado IN ('pendiente','aprobado','devuelto','autorizado','anulado')),  -- pendiente: espera al jefe; aprobado: espera a la autoridad
    monto_cent         INTEGER NOT NULL,                  -- importe del mes
    liquidacion_json   TEXT NOT NULL,
    expediente_externo TEXT,
    observaciones      TEXT,
    emitido_por        INTEGER NOT NULL REFERENCES usuarios(id),
    emitido_at         TEXT NOT NULL,
    fecha_autorizacion TEXT,
    autorizado_por     INTEGER REFERENCES usuarios(id),
    autorizado_at      TEXT,
    aprobado_por       INTEGER REFERENCES usuarios(id),
    aprobado_at        TEXT,
    vence_at           TEXT,
    anulado_por        INTEGER REFERENCES usuarios(id),
    anulado_at         TEXT,
    motivo_anulacion   TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS certificados_numero ON certificados(obra_id, numero) WHERE estado != 'anulado';

-- Libro de obra (SIGUE): órdenes de servicio correlativas, que no se editan ni se borran.
CREATE TABLE IF NOT EXISTS ordenes_servicio (
    id            INTEGER PRIMARY KEY,
    obra_id       INTEGER NOT NULL REFERENCES obras(id),
    ente_id       INTEGER NOT NULL REFERENCES entes(id),
    numero        INTEGER NOT NULL,
    fecha         TEXT NOT NULL,
    asunto        TEXT NOT NULL,
    texto         TEXT NOT NULL,
    emitida_por   INTEGER NOT NULL REFERENCES usuarios(id),
    emitida_at    TEXT NOT NULL,
    estado        TEXT NOT NULL CHECK (estado IN ('emitida','respondida','cerrada')),
    respuesta     TEXT,
    respondida_por INTEGER REFERENCES usuarios(id),
    respondida_at TEXT,
    cerrada_por   INTEGER REFERENCES usuarios(id),
    cerrada_at    TEXT,
    cierre_obs    TEXT,
    UNIQUE (obra_id, numero)
);

CREATE TABLE IF NOT EXISTS documentos_obra (
    id               INTEGER PRIMARY KEY,
    obra_id          INTEGER NOT NULL REFERENCES obras(id),
    ente_id          INTEGER NOT NULL REFERENCES entes(id),
    tipo             TEXT NOT NULL CHECK (tipo IN ('plano','material','dosificacion','ensayo','calibracion','planilla','otro')),
    titulo           TEXT NOT NULL,
    clave            TEXT,                                -- por ejemplo H21 en una dosificación
    datos_json       TEXT NOT NULL DEFAULT '{}',
    etiquetas        TEXT NOT NULL DEFAULT '[]',
    sha256           TEXT NOT NULL,
    ruta             TEXT NOT NULL,
    mime             TEXT NOT NULL,
    fecha_emision    TEXT,
    fecha_vencimiento TEXT,
    version          INTEGER NOT NULL DEFAULT 1,
    reemplaza_a      INTEGER REFERENCES documentos_obra(id),
    subido_por       INTEGER NOT NULL REFERENCES usuarios(id),
    subido_at        TEXT NOT NULL,
    revision_estado  TEXT NOT NULL DEFAULT 'pendiente' CHECK (revision_estado IN ('pendiente','aprobado','observado')),
    revisado_por     INTEGER REFERENCES usuarios(id),
    revisado_at      TEXT
);

CREATE TABLE IF NOT EXISTS observaciones_documento (
    id            INTEGER PRIMARY KEY,
    documento_id  INTEGER NOT NULL REFERENCES documentos_obra(id),
    texto         TEXT NOT NULL,
    autor_id      INTEGER NOT NULL REFERENCES usuarios(id),
    creada_at     TEXT NOT NULL,
    resuelta      INTEGER NOT NULL DEFAULT 0,
    resuelta_por  INTEGER REFERENCES usuarios(id),
    resuelta_at   TEXT
);

CREATE TABLE IF NOT EXISTS recepciones_hormigon (
    id               INTEGER PRIMARY KEY,
    obra_id          INTEGER NOT NULL REFERENCES obras(id),
    ente_id          INTEGER NOT NULL REFERENCES entes(id),
    fecha            TEXT NOT NULL,
    hora             TEXT NOT NULL,
    tipo             TEXT NOT NULL,
    volumen_m3       REAL NOT NULL,
    elemento         TEXT NOT NULL,
    probeta          TEXT,
    remito           TEXT NOT NULL,
    temp_ambiente    REAL,
    temp_hormigon    REAL,
    asentamiento_cm  REAL,
    cargada_por      INTEGER NOT NULL REFERENCES usuarios(id),
    cargada_at       TEXT NOT NULL,
    banderas         TEXT NOT NULL DEFAULT '[]',
    revision_estado  TEXT NOT NULL DEFAULT 'pendiente' CHECK (revision_estado IN ('pendiente','validada','observada')),
    revisada_por     INTEGER REFERENCES usuarios(id),
    revisada_at      TEXT,
    observacion      TEXT
);

-- Nada del Modo obra se borra: se anula con estado y auditoría.
CREATE TRIGGER IF NOT EXISTS computos_no_delete BEFORE DELETE ON computos
BEGIN SELECT RAISE(ABORT, 'los cómputos no se borran: se eliminan con estado'); END;
CREATE TRIGGER IF NOT EXISTS certificados_no_delete BEFORE DELETE ON certificados
BEGIN SELECT RAISE(ABORT, 'los certificados no se borran: se anulan con estado'); END;
CREATE TRIGGER IF NOT EXISTS ordenes_no_delete BEFORE DELETE ON ordenes_servicio
BEGIN SELECT RAISE(ABORT, 'las órdenes de servicio no se borran'); END;
CREATE TRIGGER IF NOT EXISTS ordenes_inmutables BEFORE UPDATE OF obra_id, numero, fecha, asunto, texto, emitida_por, emitida_at ON ordenes_servicio
BEGIN SELECT RAISE(ABORT, 'las órdenes de servicio no se editan'); END;
-- Notas de pedido (el contratista pide algo a la inspección: ampliación de plazo, modificación de obra u otra).
-- Numeradas sin saltos por obra, inmutables, y respondidas una sola vez por el inspector.
CREATE TABLE IF NOT EXISTS notas_pedido (
    id              INTEGER PRIMARY KEY,
    obra_id         INTEGER NOT NULL REFERENCES obras(id),
    ente_id         INTEGER NOT NULL REFERENCES entes(id),
    numero          INTEGER NOT NULL,
    tipo            TEXT NOT NULL CHECK (tipo IN ('ampliacion_plazo','modificacion_obra','otra')),
    motivo          TEXT NOT NULL,
    solicitada_por  INTEGER NOT NULL REFERENCES usuarios(id),
    solicitada_at   TEXT NOT NULL,
    estado          TEXT NOT NULL CHECK (estado IN ('pendiente','respondida')),
    respuesta       TEXT,
    respondida_por  INTEGER REFERENCES usuarios(id),
    respondida_at   TEXT,
    UNIQUE (obra_id, numero)
);

-- Equipo asignado a cada obra. Los roles de la cadena de obra (jefe de inspección, analista, jefe de certificaciones y director)
-- ven sólo las obras donde están asignados.
CREATE TABLE IF NOT EXISTS obra_asignados (
    obra_id     INTEGER NOT NULL REFERENCES obras(id),
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
    funcion     TEXT NOT NULL CHECK (funcion IN ('jefe_inspeccion','analista_certificaciones','jefe_certificaciones','director')),
    PRIMARY KEY (obra_id, usuario_id)
);

-- Registro de firmas y actos: quién, con qué rol, cuándo y, si corresponde, el motivo (rechazo o anulación).
-- Sólo se agrega: no se edita ni se borra.
CREATE TABLE IF NOT EXISTS firmas_registro (
    id          INTEGER PRIMARY KEY,
    ente_id     INTEGER NOT NULL REFERENCES entes(id),
    entidad     TEXT NOT NULL CHECK (entidad IN ('certificado','nota_pedido','orden_servicio','computo')),
    entidad_id  INTEGER NOT NULL,
    accion      TEXT NOT NULL,
    usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
    rol         TEXT NOT NULL,
    fecha       TEXT NOT NULL,
    motivo      TEXT
);

CREATE TRIGGER IF NOT EXISTS notas_no_delete BEFORE DELETE ON notas_pedido
BEGIN SELECT RAISE(ABORT, 'las notas de pedido no se borran'); END;
CREATE TRIGGER IF NOT EXISTS notas_inmutables BEFORE UPDATE OF obra_id, ente_id, numero, tipo, motivo, solicitada_por, solicitada_at ON notas_pedido
BEGIN SELECT RAISE(ABORT, 'las notas de pedido no se editan'); END;
CREATE TRIGGER IF NOT EXISTS notas_respuesta_una_vez BEFORE UPDATE ON notas_pedido WHEN OLD.estado = 'respondida'
BEGIN SELECT RAISE(ABORT, 'una nota de pedido se responde una sola vez'); END;
CREATE TRIGGER IF NOT EXISTS firmas_no_update BEFORE UPDATE ON firmas_registro
BEGIN SELECT RAISE(ABORT, 'el registro de firmas no se edita'); END;
CREATE TRIGGER IF NOT EXISTS firmas_no_delete BEFORE DELETE ON firmas_registro
BEGIN SELECT RAISE(ABORT, 'el registro de firmas no se borra'); END;

CREATE TRIGGER IF NOT EXISTS legajo_no_delete BEFORE DELETE ON legajo_obra
BEGIN SELECT RAISE(ABORT, 'el legajo no se borra: se reemplaza por una versión nueva'); END;
CREATE TRIGGER IF NOT EXISTS legajo_inmutable BEFORE UPDATE ON legajo_obra
BEGIN SELECT RAISE(ABORT, 'los documentos del legajo no se editan'); END;
CREATE TRIGGER IF NOT EXISTS documentos_no_delete BEFORE DELETE ON documentos_obra
BEGIN SELECT RAISE(ABORT, 'los documentos no se borran: se reemplazan por una versión nueva'); END;
CREATE TRIGGER IF NOT EXISTS observaciones_no_delete BEFORE DELETE ON observaciones_documento
BEGIN SELECT RAISE(ABORT, 'las observaciones no se borran'); END;
CREATE TRIGGER IF NOT EXISTS recepciones_no_delete BEFORE DELETE ON recepciones_hormigon
BEGIN SELECT RAISE(ABORT, 'las recepciones no se borran'); END;


