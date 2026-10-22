"""SIGPLAT · reglas de negocio del prototipo.

Sin dependencias externas (sólo biblioteca estándar). Cada función recibe una conexión
SQLite y un contexto `ctx` con las claves: q (query string), body (JSON), uid (header
X-Usuario-Id) y args (grupos de la ruta). Devuelve (estado_http, payload).

Advertencia: la autenticación del prototipo es por cabecera X-Usuario-Id, sin contraseña.
Es sólo para demostración local. No es seguridad de producción.
"""
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(APP_DIR)  # carpeta 01_PROTOTIPO
SCHEMA_PATH = os.path.join(APP_DIR, 'schema.sql')
CONFIG_DIR = os.path.join(BASE_DIR, 'config', 'entes')
FOTOS_DIR = os.path.join(BASE_DIR, 'data', 'fotos')
MAX_FOTO_BYTES = 3 * 1024 * 1024
MIME_EXT = {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}
FOTO_RE = re.compile(r'^data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$')

ROLES = ('inspector', 'responsable', 'autoridad', 'administrador')  # personal del ente
ROL_VECINO = 'vecino'  # cuenta ciudadana: reporta y ve sólo sus reportes; no está en PERMISOS, así que no accede a nada interno
ESTADO_PUBLICO = {
    'recibida': 'Recibida, en revisión',
    'confirmada': 'Confirmada por inspección',
    'descartada': 'No corresponde',
    'duplicada': 'Agrupada con otro reporte',
    'con_intervencion': 'En intervención',
    'resuelta': 'Resuelta',
}
PERMISOS = {
    'inspeccionar': {'inspector', 'autoridad'},
    'crear_intervencion': {'autoridad'},
    'declarar_avance': {'responsable'},
    'validar_avance': {'inspector', 'autoridad'},
    'cargar_evidencia': {'responsable', 'inspector', 'autoridad'},
    'revisar_evidencia': {'inspector', 'autoridad'},
    'ver_interno': set(ROLES),
    'ver_incidencias': {'inspector', 'autoridad', 'administrador'},
    'ver_tablero': {'inspector', 'autoridad', 'administrador'},
    # Modo obra (simulación de SIGO y SIGUE). Ver MODO_OBRA.md y app/obra.py.
    'ver_obras': {'inspector', 'autoridad', 'administrativo', 'administrador', 'contratista'},
    'medir': {'inspector'},
    'emitir_certificado': {'inspector'},  # el inspector mide y certifica; otra persona (la autoridad) autoriza
    'autorizar_certificado': {'autoridad'},
    'anular_certificado': {'inspector', 'administrativo'},
    'asignar_expediente': {'administrativo'},
    'plazo_cert_vencida': {'administrativo', 'autoridad'},
    'cargar_modificacion': {'administrativo'},
    'autorizar_modificacion': {'autoridad'},
    'emitir_os': {'inspector'},
    'responder_os': {'contratista'},
    'cerrar_os': {'inspector'},
    'alta_obra': {'administrativo'},
    'asignar_obra': {'administrativo'},
    'subir_legajo': {'administrativo'},
    'publicar_legajo': {'administrativo'},
    'solicitar_nota': {'contratista'},  # el representante técnico de la contratista pide notas de pedido
    'responder_nota': {'inspector'},
    'subir_documento': {'contratista', 'inspector'},
    'revisar_documento': {'inspector'},
    'cargar_recepcion': {'contratista', 'inspector'},
    'revisar_recepcion': {'inspector'},
}
# Quién puede ejecutar cada transición de intervención (además, el responsable debe estar asignado).
TRANSICIONES_INT = {
    'iniciar': {'from': ('planificada',), 'to': 'en_ejecucion', 'roles': ('autoridad', 'responsable')},
    'declarar_finalizacion': {'from': ('en_ejecucion',), 'to': 'finalizada_declarada', 'roles': ('responsable',)},
    'observar_cierre': {'from': ('finalizada_declarada',), 'to': 'en_ejecucion', 'roles': ('inspector', 'autoridad')},
    'validar_cierre': {'from': ('finalizada_declarada',), 'to': 'validada', 'roles': ('inspector', 'autoridad')},
    'cerrar': {'from': ('validada',), 'to': 'cerrada', 'roles': ('autoridad',)},
}
CLOCK = {'now': None}  # el seed puede fijar la hora para cargar historia pasada

# Autenticación: contraseñas con PBKDF2-SHA256 (biblioteca estándar), sesiones por token opaco.
PBKDF2_ITER = 310000  # OWASP 2021 para PBKDF2-SHA256; las pruebas lo reducen
SESION_HORAS = 8
LOGIN_MAX_FALLOS = 5
LOGIN_BLOQUEO_MIN = 5
FALLOS_LOGIN = {}  # login -> [cantidad, bloqueado_hasta]; en memoria, se reinicia con el servidor


class ApiError(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# ---------- utilidades de tiempo, texto y geografía ----------

def now():
    return CLOCK['now'] or datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def texto(valor, minimo, maximo, campo):
    if not isinstance(valor, str) or not (minimo <= len(valor.strip()) <= maximo):
        raise ApiError(400, f'{campo}: entre {minimo} y {maximo} caracteres')
    return valor.strip()


def coords(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise ApiError(400, 'ubicación inválida: se requieren lat y lon numéricas')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ApiError(400, 'ubicación fuera de rango')
    return lat, lon


def numero_opcional(valor, minimo, maximo, campo):
    if valor is None:
        return None
    try:
        n = float(valor)
    except (TypeError, ValueError):
        raise ApiError(400, f'{campo}: debe ser numérico')
    if not (minimo <= n <= maximo):
        raise ApiError(400, f'{campo}: fuera de rango {minimo}-{maximo}')
    return n


def mediana(valores):
    return round(statistics.median(valores), 1) if valores else None


def indicador(valor, n, nota):
    return {'valor': valor if n else None, 'n': n, 'nota': nota if n else 'Sin datos suficientes'}


# ---------- base de datos ----------

def conectar(ruta):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    c = sqlite3.connect(ruta, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys = ON')
    with open(SCHEMA_PATH, encoding='utf-8') as f:
        c.executescript(f.read())
    return c


def fila(r):
    d = dict(r) if r is not None else None
    if d is not None:
        d.pop('reportada_por', None)  # quién reportó no sale en ninguna vista
    return d


def audit(c, ente_id, entidad, eid, accion, usuario_id, detalle=None):
    c.execute(
        'INSERT INTO historial(ente_id,entidad,entidad_id,accion,usuario_id,ocurrido_at,detalle) VALUES(?,?,?,?,?,?,?)',
        (ente_id, entidad, eid, accion, usuario_id, iso(now()), json.dumps(detalle or {}, ensure_ascii=False)),
    )


def ente_por_codigo(c, codigo):
    r = c.execute('SELECT * FROM entes WHERE codigo=?', (codigo,)).fetchone()
    if r is None:
        raise ApiError(404, 'ente inexistente')
    return fila(r)


def usuario_actual(c, ctx):
    """ctx['uid'] lo fija el servidor a partir de la cookie de sesión válida; nunca viene del cliente."""
    if not ctx.get('uid'):
        raise ApiError(401, 'se requiere iniciar sesión')
    r = c.execute('SELECT * FROM usuarios WHERE id=? AND activo=1', (ctx['uid'],)).fetchone()
    if r is None:
        raise ApiError(401, 'sesión no válida')
    u = fila(r)
    ente = c.execute('SELECT * FROM entes WHERE id=?', (u['ente_id'],)).fetchone()
    return u, fila(ente)


def exigir(usuario, permiso):
    if usuario['rol'] not in PERMISOS[permiso]:
        raise ApiError(403, f'el rol {usuario["rol"]} no puede realizar esta acción')


def cfg_de(ente):
    return json.loads(ente['config_json'])


def fecha_hoy_iso():
    return now().strftime('%Y-%m-%d')


# ---------- fotos y evidencias ----------

def guardar_foto(dataurl):
    if not isinstance(dataurl, str):
        raise ApiError(400, 'foto inválida')
    m = FOTO_RE.match(dataurl)
    if not m:
        raise ApiError(400, 'la foto debe ser JPEG, PNG o WebP codificada en base64')
    try:
        data = base64.b64decode(m.group(2))
    except binascii.Error:
        raise ApiError(400, 'base64 inválido')
    if len(data) > MAX_FOTO_BYTES:
        raise ApiError(413, 'la foto supera 3 MB')
    sha = hashlib.sha256(data).hexdigest()
    ruta = f'{sha}.{MIME_EXT[m.group(1)]}'
    os.makedirs(FOTOS_DIR, exist_ok=True)
    camino = os.path.join(FOTOS_DIR, ruta)
    if not os.path.exists(camino):
        with open(camino, 'wb') as f:
            f.write(data)
    return sha, ruta


def guardar_evidencia(c, ente, datos, ref_lat, ref_lon, subida_por, intervencion_id=None, incidencia_id=None):
    """Registra una evidencia nueva. Calcula banderas de consistencia; no las corrige."""
    cfg = cfg_de(ente)
    sha, ruta = guardar_foto(datos.get('foto'))
    recibida = now()
    banderas = []
    capturada = datos.get('capturada_at')
    capturada_dt = None
    if capturada:
        try:
            capturada_dt = parse_iso(capturada)
        except (TypeError, ValueError):
            raise ApiError(400, 'capturada_at debe tener formato ISO 8601 UTC')
        if capturada_dt > recibida + timedelta(minutes=5):
            banderas.append('fecha_captura_posterior_a_recepcion')
        if recibida - capturada_dt > timedelta(hours=48):
            banderas.append('fecha_captura_antigua')
    else:
        banderas.append('sin_fecha_de_captura')
    lat, lon = coords(datos.get('lat'), datos.get('lon'))
    precision = numero_opcional(datos.get('precision_m'), 0, 100000, 'precision_m')
    if precision is None or precision > 100:
        banderas.append('precision_geografica_baja_o_desconocida')
    if ref_lat is not None and haversine_m(lat, lon, ref_lat, ref_lon) > cfg['radio_evidencia_m']:
        banderas.append('lejos_de_la_ubicacion_de_referencia')
    repetida = c.execute('SELECT id FROM evidencias WHERE ente_id=? AND sha256=? LIMIT 1', (ente['id'], sha)).fetchone()
    if repetida:
        banderas.append(f'foto_identica_a_evidencia_{repetida["id"]}')
    reemplaza = datos.get('reemplaza_a')
    if reemplaza is not None:
        anterior = c.execute('SELECT * FROM evidencias WHERE id=? AND ente_id=?', (reemplaza, ente['id'])).fetchone()
        if anterior is None:
            raise ApiError(400, 'reemplaza_a no existe')
    cur = c.execute(
        '''INSERT INTO evidencias(ente_id,intervencion_id,incidencia_id,tipo,actividad_id,sha256,ruta,lat,lon,
           precision_m,capturada_at,recibida_at,subida_por,reemplaza_a,banderas)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (ente['id'], intervencion_id, incidencia_id, datos['tipo'], datos.get('actividad_id'), sha, ruta, lat, lon,
         precision, capturada, iso(recibida), subida_por, reemplaza, json.dumps(banderas)),
    )
    audit(c, ente['id'], 'evidencia', cur.lastrowid, 'recibida', subida_por,
          {'tipo': datos['tipo'], 'banderas': banderas, 'reemplaza_a': reemplaza})
    return cur.lastrowid


def evidencia_dict(r):
    d = fila(r)
    d['banderas'] = json.loads(d['banderas'])
    d['url'] = f'/api/fotos/{d["ruta"]}'
    return d


# ---------- incidencias ----------

def posibles_duplicados(c, ente, cfg, categoria, lat, lon, t):
    desde = iso(t - timedelta(days=cfg['duplicados']['dias']))
    filas = c.execute(
        """SELECT codigo, lat, lon, estado FROM incidencias WHERE ente_id=? AND categoria=? AND fecha_reporte>=?
           AND estado IN ('recibida','confirmada','con_intervencion')""",
        (ente['id'], categoria, desde),
    ).fetchall()
    out = []
    for f in filas:
        d = haversine_m(lat, lon, f['lat'], f['lon'])
        if d <= cfg['duplicados']['radio_m']:
            out.append({'codigo': f['codigo'], 'distancia_m': round(d), 'estado': f['estado']})
    return sorted(out, key=lambda x: x['distancia_m'])


def reportar(c, ctx):
    """Ciudadano (público): alta de incidencia. No requiere identificación."""
    b = ctx['body']
    ente = ente_por_codigo(c, b.get('ente'))
    cfg = cfg_de(ente)
    categoria = b.get('categoria')
    if categoria not in cfg['categorias']:
        raise ApiError(400, 'categoría no válida para este ente')
    descripcion = texto(b.get('descripcion'), 10, 500, 'descripción')
    lat, lon = coords(b.get('lat'), b.get('lon'))
    precision = numero_opcional(b.get('precision_m'), 0, 100000, 'precision_m')
    canal = b.get('canal') if b.get('canal') in ('app', 'cidi', 'web') else 'web'
    reportada_por = None
    if ctx.get('uid'):  # un vecino con cuenta: el reporte queda en "Mis reportes". Sin cuenta sigue siendo anónimo.
        u = c.execute('SELECT * FROM usuarios WHERE id=? AND activo=1', (ctx['uid'],)).fetchone()
        if u is not None and u['rol'] == ROL_VECINO:
            if u['ente_id'] != ente['id']:
                raise ApiError(403, 'tu cuenta pertenece a otro ente')
            reportada_por = u['id']
    t = now()
    duplicados = posibles_duplicados(c, ente, cfg, categoria, lat, lon, t)
    codigo = 'INC-' + secrets.token_hex(3).upper()
    cur = c.execute(
        '''INSERT INTO incidencias(ente_id,codigo,categoria,descripcion,lat,lon,precision_m,canal,fecha_reporte,estado,reportada_por)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (ente['id'], codigo, categoria, descripcion, lat, lon, precision, canal, iso(t), 'recibida', reportada_por),
    )
    inc_id = cur.lastrowid
    if b.get('foto'):
        guardar_evidencia(c, ente, {'tipo': 'foto_reporte', 'foto': b['foto'], 'lat': lat, 'lon': lon,
                                    'precision_m': precision, 'capturada_at': b.get('capturada_at')},
                          lat, lon, None, incidencia_id=inc_id)
    audit(c, ente['id'], 'incidencia', inc_id, 'reportada', None, {'canal': canal, 'categoria': categoria})
    return 201, {'codigo': codigo, 'estado': 'recibida', 'posibles_duplicados': duplicados}


CODIGO_RE = re.compile(r'^INC-[0-9A-F]{6}$')


def normalizar_codigo(texto):
    """Acepta el código como lo escribe una persona: minúsculas, espacios, sin 'INC-' o con O/I/L por 0/1/1.
    Los caracteres O, I y L nunca forman parte de un código (son hexadecimales), así que la corrección no es ambigua."""
    t = re.sub(r'[\s\-_.]+', '', unquote(str(texto or ''))).upper()  # la ruta llega con %20 sin decodificar
    if t.startswith('INC'):
        t = t[3:]
    t = t.replace('O', '0').replace('I', '1').replace('L', '1')
    return 'INC-' + t


def seguimiento_publico(c, ctx, codigo):
    """Vista pública por código de seguimiento: sin datos internos ni de personas."""
    codigo = normalizar_codigo(codigo)
    if not CODIGO_RE.match(codigo):
        raise ApiError(400, 'el código tiene 6 caracteres después de "INC-" (números y letras A a F), por ejemplo INC-3F9A2C')
    r = c.execute('SELECT i.*, e.nombre AS ente_nombre FROM incidencias i JOIN entes e ON e.id=i.ente_id WHERE i.codigo=?',
                  (codigo,)).fetchone()
    if r is None:
        raise ApiError(404, f'no existe un reporte con el código {codigo}')
    d = {'codigo': r['codigo'], 'categoria': r['categoria'], 'ente': r['ente_nombre'],
         'fecha_reporte': r['fecha_reporte'], 'estado': ESTADO_PUBLICO[r['estado']],
         'estado_codigo': r['estado'], 'fecha_resuelta': r['fecha_resuelta']}
    if r['intervencion_id']:
        it = c.execute('SELECT estado, fecha_planificada_fin, fecha_cierre FROM intervenciones WHERE id=?',
                       (r['intervencion_id'],)).fetchone()
        d['intervencion'] = {'fecha_planificada_fin': it['fecha_planificada_fin'],
                             'fecha_cierre': it['fecha_cierre'],
                             'estado_publico': 'Finalizada' if it['estado'] in ('validada', 'cerrada') else 'En curso'}
    return 200, d


def mis_reportes(c, ctx):
    """Reportes hechos por la cuenta de vecino que inició sesión, con el mismo estado público que ve cualquiera."""
    u, _ = usuario_actual(c, ctx)
    if u['rol'] != ROL_VECINO:
        raise ApiError(403, 'sólo las cuentas de vecino tienen "Mis reportes"')
    filas = c.execute('SELECT codigo, descripcion FROM incidencias WHERE reportada_por=? ORDER BY fecha_reporte DESC, id DESC',
                      (u['id'],)).fetchall()
    out = []
    for f in filas:
        _, d = seguimiento_publico(c, ctx, f['codigo'])
        d['descripcion'] = f['descripcion']
        out.append(d)
    return 200, out


def listar_incidencias(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_incidencias')
    estado = ctx['q'].get('estado')
    sql, params = 'SELECT * FROM incidencias WHERE ente_id=?', [ente['id']]
    if estado:
        sql += ' AND estado=?'
        params.append(estado)
    return 200, [fila(r) for r in c.execute(sql + ' ORDER BY fecha_reporte DESC', params)]


def inspeccionar(c, ctx, inc_id):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'inspeccionar')
    inc = c.execute('SELECT * FROM incidencias WHERE id=? AND ente_id=?', (inc_id, ente['id'])).fetchone()
    if inc is None:
        raise ApiError(404, 'incidencia inexistente')
    if inc['estado'] != 'recibida':
        raise ApiError(409, f'la incidencia está en estado {inc["estado"]}, no puede inspeccionarse')
    b = ctx['body']
    resultado = b.get('resultado')
    if resultado not in ('confirmada', 'descartada', 'duplicada'):
        raise ApiError(400, 'resultado debe ser confirmada, descartada o duplicada')
    gravedad = b.get('gravedad')
    if resultado == 'confirmada' and gravedad not in (1, 2, 3):
        raise ApiError(400, 'una incidencia confirmada requiere gravedad 1, 2 o 3')
    obs = texto(b.get('observaciones') or 'Sin observaciones', 1, 1000, 'observaciones')
    principal = None
    if resultado == 'duplicada':
        principal = c.execute('SELECT * FROM incidencias WHERE id=? AND ente_id=?',
                              (b.get('duplicada_de'), ente['id'])).fetchone()
        if principal is None or principal['id'] == inc_id:
            raise ApiError(400, 'duplicada_de debe apuntar a otra incidencia del mismo ente')
        c.execute('UPDATE incidencias SET reportes_count = reportes_count + ? WHERE id=?',
                  (inc['reportes_count'], principal['id']))
    t = now()
    c.execute('INSERT INTO inspecciones(incidencia_id,inspector_id,fecha,resultado,gravedad,observaciones) VALUES(?,?,?,?,?,?)',
              (inc_id, u['id'], iso(t), resultado, gravedad if resultado == 'confirmada' else None, obs))
    c.execute('UPDATE incidencias SET estado=?, gravedad=?, duplicada_de=? WHERE id=?',
              (resultado, gravedad if resultado == 'confirmada' else None,
               principal['id'] if principal else None, inc_id))
    audit(c, ente['id'], 'incidencia', inc_id, f'inspeccionada_{resultado}', u['id'], {'gravedad': gravedad})
    return 200, fila(c.execute('SELECT * FROM incidencias WHERE id=?', (inc_id,)).fetchone())


def ficha_incidencia(c, ctx, inc_id):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_incidencias')
    inc = c.execute('SELECT * FROM incidencias WHERE id=? AND ente_id=?', (inc_id, ente['id'])).fetchone()
    if inc is None:
        raise ApiError(404, 'incidencia inexistente')
    d = fila(inc)
    d['inspecciones'] = [fila(r) for r in c.execute('SELECT * FROM inspecciones WHERE incidencia_id=? ORDER BY fecha', (inc_id,))]
    d['evidencias'] = [evidencia_dict(r) for r in c.execute('SELECT * FROM evidencias WHERE incidencia_id=?', (inc_id,))]
    d['historial'] = [fila(r) for r in c.execute(
        "SELECT * FROM historial WHERE entidad='incidencia' AND entidad_id=? ORDER BY id", (inc_id,))]
    return 200, d


def subir_evidencia_incidencia(c, ctx, inc_id):
    """Foto tomada por el personal de inspección sobre una incidencia (antes o durante la inspección)."""
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'inspeccionar')
    inc = c.execute('SELECT * FROM incidencias WHERE id=? AND ente_id=?', (inc_id, ente['id'])).fetchone()
    if inc is None:
        raise ApiError(404, 'incidencia inexistente')
    if inc['estado'] not in ('recibida', 'confirmada'):
        raise ApiError(409, 'sólo se agregan fotos de inspección a incidencias recibidas o confirmadas')
    datos = dict(ctx['body'], tipo='inspeccion')
    eid = guardar_evidencia(c, ente, datos, inc['lat'], inc['lon'], u['id'], intervencion_id=None, incidencia_id=inc_id)
    return 201, evidencia_dict(c.execute('SELECT * FROM evidencias WHERE id=?', (eid,)).fetchone())


def mi_trabajo(c, ctx):
    """Lo pendiente del personal de inspección: qué visitar y qué revisar. Nada que haya cargado la misma persona
    aparece para revisar (separación de funciones)."""
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'validar_avance')
    eid, t = ente['id'], now()
    para_inspeccionar = [
        dict(fila(r), dias=round((t - parse_iso(r['fecha_reporte'])).total_seconds() / 86400, 1))
        for r in c.execute("""SELECT id,codigo,categoria,descripcion,lat,lon,fecha_reporte,reportes_count FROM incidencias
                              WHERE ente_id=? AND estado='recibida' ORDER BY fecha_reporte, id""", (eid,))]
    avances = [fila(r) for r in c.execute(
        """SELECT av.id AS avance_id, av.pct, av.nota, av.declarado_at, a.id AS actividad_id, a.nombre AS actividad,
                  a.pct_validado, i.id AS intervencion_id, i.titulo
           FROM avances av JOIN actividades a ON a.id=av.actividad_id JOIN intervenciones i ON i.id=a.intervencion_id
           WHERE i.ente_id=? AND av.estado='pendiente' AND av.declarado_por != ? ORDER BY av.declarado_at""", (eid, u['id']))]
    evidencias = []
    for r in c.execute(
            """SELECT e.id, e.tipo, e.recibida_at, e.banderas, e.ruta, e.actividad_id, i.id AS intervencion_id, i.titulo
               FROM evidencias e JOIN intervenciones i ON i.id=e.intervencion_id
               WHERE e.ente_id=? AND e.revision_estado='pendiente' AND e.subida_por != ? ORDER BY e.recibida_at""",
            (eid, u['id'])):
        d = fila(r)
        d['banderas'] = json.loads(d['banderas'])
        d['url'] = f'/api/fotos/{d.pop("ruta")}'
        evidencias.append(d)
    cierres = [fila(r) for r in c.execute(
        "SELECT id, titulo, fecha_fin_declarada FROM intervenciones WHERE ente_id=? AND estado='finalizada_declarada' ORDER BY id",
        (eid,))]
    return 200, {'para_inspeccionar': para_inspeccionar, 'avances_pendientes': avances,
                 'evidencias_pendientes': evidencias, 'cierres_pendientes': cierres,
                 'contadores': {'para_inspeccionar': len(para_inspeccionar), 'avances': len(avances),
                                'evidencias': len(evidencias), 'cierres': len(cierres)}}


# ---------- intervenciones ----------

def crear_intervencion(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'crear_intervencion')
    cfg = cfg_de(ente)
    b = ctx['body']
    tipo = b.get('tipo')
    if tipo not in cfg['tipos_intervencion']:
        raise ApiError(400, 'tipo de intervención no válido para este ente')
    circuito = cfg['tipos_intervencion'][tipo]
    titulo = texto(b.get('titulo'), 5, 120, 'título')
    resp = c.execute('SELECT * FROM usuarios WHERE id=? AND ente_id=? AND rol=?',
                     (b.get('responsable_id'), ente['id'], 'responsable')).fetchone()
    if resp is None:
        raise ApiError(400, 'responsable_id debe ser un usuario con rol responsable del mismo ente')
    try:
        fin = datetime.strptime(b.get('fecha_planificada_fin'), '%Y-%m-%d').strftime('%Y-%m-%d')
    except (TypeError, ValueError):
        raise ApiError(400, 'fecha_planificada_fin debe tener formato AAAA-MM-DD')
    actividades = b.get('actividades') or []
    if circuito['requiere_actividades'] and not actividades:
        raise ApiError(400, f'el circuito "{tipo}" requiere al menos una actividad')
    actividades = [texto(a, 3, 120, 'actividad') for a in actividades]
    incidencias = b.get('incidencia_ids') or []
    lat = lon = None
    filas_inc = []
    for iid in incidencias:
        inc = c.execute('SELECT * FROM incidencias WHERE id=? AND ente_id=?', (iid, ente['id'])).fetchone()
        if inc is None or inc['estado'] != 'confirmada':
            raise ApiError(400, f'la incidencia {iid} debe existir, pertenecer al ente y estar confirmada')
        filas_inc.append(inc)
    if filas_inc:
        lat, lon = filas_inc[0]['lat'], filas_inc[0]['lon']
    if b.get('lat') is not None and b.get('lon') is not None:
        lat, lon = coords(b['lat'], b['lon'])
    sistema = b.get('sistema_externo') or None
    if sistema not in (None, 'SIGO', 'SIGUE'):
        raise ApiError(400, 'sistema_externo debe ser SIGO, SIGUE o vacío')
    expediente = texto(b['expediente_externo'], 3, 40, 'expediente_externo') if b.get('expediente_externo') else None
    cur = c.execute(
        '''INSERT INTO intervenciones(ente_id,tipo,titulo,lat,lon,responsable_id,estado,creada_por,fecha_creacion,
           fecha_planificada_fin,sistema_externo,expediente_externo)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
        (ente['id'], tipo, titulo, lat, lon, resp['id'], 'planificada', u['id'], iso(now()), fin, sistema, expediente),
    )
    iid_nuevo = cur.lastrowid
    for nombre in actividades:
        c.execute('INSERT INTO actividades(intervencion_id,nombre) VALUES(?,?)', (iid_nuevo, nombre))
    for inc in filas_inc:
        c.execute("UPDATE incidencias SET estado='con_intervencion', intervencion_id=? WHERE id=?", (iid_nuevo, inc['id']))
    audit(c, ente['id'], 'intervencion', iid_nuevo, 'creada', u['id'], {'tipo': tipo, 'incidencias': incidencias})
    return 201, ficha_intervencion_datos(c, ente['id'], iid_nuevo)


def _intervencion(c, ente, iid):
    r = c.execute('SELECT * FROM intervenciones WHERE id=? AND ente_id=?', (iid, ente['id'])).fetchone()
    if r is None:
        raise ApiError(404, 'intervención inexistente')
    return fila(r)


def transicion_intervencion(c, ctx, iid):
    u, ente = usuario_actual(c, ctx)
    inter = _intervencion(c, ente, iid)
    b = ctx['body']
    accion = b.get('accion')
    if accion not in TRANSICIONES_INT:
        raise ApiError(400, 'acción no válida')
    regla = TRANSICIONES_INT[accion]
    if u['rol'] not in regla['roles']:
        raise ApiError(403, f'el rol {u["rol"]} no puede ejecutar "{accion}"')
    if u['rol'] == 'responsable' and u['id'] != inter['responsable_id']:
        raise ApiError(403, 'sólo el responsable asignado puede operar esta intervención')
    if inter['estado'] not in regla['from']:
        raise ApiError(409, f'"{accion}" no es válido en estado {inter["estado"]}')
    t = iso(now())
    actividades = [fila(r) for r in c.execute('SELECT * FROM actividades WHERE intervencion_id=?', (iid,))]
    if accion == 'iniciar':
        c.execute('UPDATE intervenciones SET fecha_inicio_real=? WHERE id=?', (t, iid))
    elif accion == 'declarar_finalizacion':
        if any(a['pct_declarado'] < 100 for a in actividades):
            raise ApiError(409, 'todas las actividades deben tener avance declarado de 100%')
        if not c.execute("SELECT 1 FROM evidencias WHERE intervencion_id=? AND tipo='cierre'", (iid,)).fetchone():
            raise ApiError(409, 'se requiere una foto de cierre georreferenciada')
        c.execute('UPDATE intervenciones SET fecha_fin_declarada=? WHERE id=?', (t, iid))
    elif accion == 'observar_cierre':
        obs = texto(b.get('observacion'), 5, 1000, 'observación del cierre')
        audit(c, ente['id'], 'intervencion', iid, 'cierre_observado', u['id'], {'observacion': obs})
    elif accion == 'validar_cierre':
        if any(a['pct_validado'] < 100 for a in actividades):
            raise ApiError(409, 'todas las actividades deben estar validadas al 100%')
        if not c.execute("SELECT 1 FROM evidencias WHERE intervencion_id=? AND tipo='cierre' AND revision_estado='validada'",
                         (iid,)).fetchone():
            raise ApiError(409, 'la foto de cierre debe estar validada')
        c.execute('UPDATE intervenciones SET fecha_fin_real=? WHERE id=?', (t, iid))
    elif accion == 'cerrar':
        c.execute('UPDATE intervenciones SET fecha_cierre=? WHERE id=?', (t, iid))
        c.execute("UPDATE incidencias SET estado='resuelta', fecha_resuelta=? WHERE intervencion_id=?", (t, iid))
    c.execute('UPDATE intervenciones SET estado=? WHERE id=?', (regla['to'], iid))
    audit(c, ente['id'], 'intervencion', iid, accion, u['id'], {'de': inter['estado'], 'a': regla['to']})
    return 200, ficha_intervencion_datos(c, ente['id'], iid)


def declarar_avance(c, ctx, iid):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'declarar_avance')
    inter = _intervencion(c, ente, iid)
    if u['id'] != inter['responsable_id']:
        raise ApiError(403, 'sólo el responsable asignado declara avances')
    if inter['estado'] != 'en_ejecucion':
        raise ApiError(409, 'los avances sólo se declaran con la intervención en ejecución')
    b = ctx['body']
    act = c.execute('SELECT * FROM actividades WHERE id=? AND intervencion_id=?', (b.get('actividad_id'), iid)).fetchone()
    if act is None:
        raise ApiError(400, 'actividad no pertenece a la intervención')
    pct = numero_opcional(b.get('pct'), 0, 100, 'pct')
    if pct is None:
        raise ApiError(400, 'pct es obligatorio')
    if pct < act['pct_validado']:
        raise ApiError(409, 'el avance declarado no puede ser menor que el validado')
    nota = (b.get('nota') or '').strip()[:500] or None
    cur = c.execute('INSERT INTO avances(actividad_id,pct,declarado_por,declarado_at,nota) VALUES(?,?,?,?,?)',
                    (act['id'], pct, u['id'], iso(now()), nota))
    c.execute('UPDATE actividades SET pct_declarado=? WHERE id=?', (pct, act['id']))
    audit(c, ente['id'], 'avance', cur.lastrowid, 'declarado', u['id'], {'actividad': act['id'], 'pct': pct})
    return 201, ficha_intervencion_datos(c, ente['id'], iid)


def revisar_avance(c, ctx, avance_id):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'validar_avance')
    av = c.execute('''SELECT av.*, a.intervencion_id, a.pct_validado AS act_validado
                      FROM avances av JOIN actividades a ON a.id=av.actividad_id
                      JOIN intervenciones i ON i.id=a.intervencion_id
                      WHERE av.id=? AND i.ente_id=?''', (avance_id, ente['id'])).fetchone()
    if av is None:
        raise ApiError(404, 'avance inexistente')
    if av['declarado_por'] == u['id']:
        raise ApiError(403, 'quien declara un avance no puede validarlo')
    if av['estado'] != 'pendiente':
        raise ApiError(409, f'el avance ya está {av["estado"]}')
    b = ctx['body']
    t = iso(now())
    if b.get('accion') == 'observar':
        obs = texto(b.get('observacion'), 5, 1000, 'observación')
        c.execute("UPDATE avances SET estado='observado', validado_por=?, validado_at=?, observacion=? WHERE id=?",
                  (u['id'], t, obs, avance_id))
        audit(c, ente['id'], 'avance', avance_id, 'observado', u['id'], {'observacion': obs})
    elif b.get('accion') == 'validar':
        pct_val = numero_opcional(b.get('pct_validado', av['pct']), 0, av['pct'], 'pct_validado')
        if pct_val < av['act_validado']:
            raise ApiError(409, 'el validado no puede bajar del validado anterior')
        c.execute("UPDATE avances SET estado='validado', validado_por=?, validado_at=?, observacion=? WHERE id=?",
                  (u['id'], t, (b.get('observacion') or '').strip()[:1000] or None, avance_id))
        c.execute('UPDATE actividades SET pct_validado=? WHERE id=?', (pct_val, av['actividad_id']))
        audit(c, ente['id'], 'avance', avance_id, 'validado', u['id'], {'pct_validado': pct_val})
    else:
        raise ApiError(400, 'accion debe ser validar u observar')
    return 200, ficha_intervencion_datos(c, ente['id'], av['intervencion_id'])


def subir_evidencia(c, ctx, iid):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'cargar_evidencia')
    inter = _intervencion(c, ente, iid)
    if u['rol'] == 'responsable' and u['id'] != inter['responsable_id']:
        raise ApiError(403, 'sólo el responsable asignado carga evidencias de esta intervención')
    b = ctx['body']
    tipo = b.get('tipo')
    if tipo not in ('avance', 'cierre', 'inspeccion'):
        raise ApiError(400, 'tipo debe ser avance, cierre o inspeccion')
    if b.get('actividad_id') is not None:
        if not c.execute('SELECT 1 FROM actividades WHERE id=? AND intervencion_id=?', (b['actividad_id'], iid)).fetchone():
            raise ApiError(400, 'actividad no pertenece a la intervención')
    if b.get('reemplaza_a') is not None:
        ant = c.execute('SELECT id FROM evidencias WHERE id=? AND intervencion_id=? AND tipo=?',
                        (b['reemplaza_a'], iid, tipo)).fetchone()
        if ant is None:
            raise ApiError(400, 'reemplaza_a debe ser una evidencia previa del mismo tipo en esta intervención')
    datos = dict(b, tipo=tipo)
    eid = guardar_evidencia(c, ente, datos, inter['lat'], inter['lon'], u['id'], intervencion_id=iid,
                            incidencia_id=None)
    return 201, evidencia_dict(c.execute('SELECT * FROM evidencias WHERE id=?', (eid,)).fetchone())


def revisar_evidencia(c, ctx, ev_id):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'revisar_evidencia')
    ev = c.execute('SELECT * FROM evidencias WHERE id=? AND ente_id=?', (ev_id, ente['id'])).fetchone()
    if ev is None:
        raise ApiError(404, 'evidencia inexistente')
    if ev['subida_por'] == u['id']:
        raise ApiError(403, 'quien carga una evidencia no puede revisarla')
    if ev['revision_estado'] != 'pendiente':
        raise ApiError(409, 'la evidencia ya fue revisada')
    b = ctx['body']
    accion = b.get('accion')
    if accion not in ('validada', 'observada'):
        raise ApiError(400, 'accion debe ser validada u observada')
    obs = (b.get('observacion') or '').strip()[:1000] or None
    if accion == 'observada' and not obs:
        raise ApiError(400, 'una evidencia observada requiere observación')
    c.execute('UPDATE evidencias SET revision_estado=?, revisada_por=?, revisada_at=?, observacion=? WHERE id=?',
              (accion, u['id'], iso(now()), obs, ev_id))
    audit(c, ente['id'], 'evidencia', ev_id, accion, u['id'], {'observacion': obs})
    return 200, evidencia_dict(c.execute('SELECT * FROM evidencias WHERE id=?', (ev_id,)).fetchone())


def ficha_intervencion_datos(c, ente_id, iid):
    inter = c.execute('SELECT * FROM intervenciones WHERE id=? AND ente_id=?', (iid, ente_id)).fetchone()
    if inter is None:
        raise ApiError(404, 'intervención inexistente')
    d = fila(inter)
    d['atrasada'] = d['estado'] not in ('validada', 'cerrada') and fecha_hoy_iso() > d['fecha_planificada_fin']
    d['responsable'] = fila(c.execute('SELECT id,nombre FROM usuarios WHERE id=?', (d['responsable_id'],)).fetchone())
    d['incidencias'] = [fila(r) for r in c.execute(
        'SELECT id,codigo,categoria,estado,gravedad FROM incidencias WHERE intervencion_id=?', (iid,))]
    actividades = []
    for a in c.execute('SELECT * FROM actividades WHERE intervencion_id=? ORDER BY id', (iid,)):
        ad = fila(a)
        ad['avances'] = [fila(r) for r in c.execute('SELECT * FROM avances WHERE actividad_id=? ORDER BY id', (a['id'],))]
        hay_validado = any(x['estado'] == 'validado' for x in ad['avances'])
        ad['diferencia_declarado_validado'] = round(ad['pct_declarado'] - ad['pct_validado'], 1) if hay_validado else None
        ad['pendientes_revision'] = sum(1 for x in ad['avances'] if x['estado'] == 'pendiente')
        actividades.append(ad)
    d['actividades'] = actividades
    d['evidencias'] = [evidencia_dict(r) for r in c.execute(
        'SELECT * FROM evidencias WHERE intervencion_id=? ORDER BY id', (iid,))]
    # Incluye los hechos de sus avances y evidencias, para poder contar la historia completa en lenguaje claro.
    d['historial'] = [fila(r) for r in c.execute(
        """SELECT * FROM historial WHERE ente_id=? AND (
               (entidad='intervencion' AND entidad_id=?)
            OR (entidad='avance' AND entidad_id IN (SELECT av.id FROM avances av
                                                    JOIN actividades a ON a.id=av.actividad_id WHERE a.intervencion_id=?))
            OR (entidad='evidencia' AND entidad_id IN (SELECT id FROM evidencias WHERE intervencion_id=?)))
           ORDER BY id""", (ente_id, iid, iid, iid))]
    return d


def _visible_para(u, inter):
    """Un responsable sólo ve las intervenciones que le fueron asignadas. Autoridad e inspección ven todas las del ente."""
    return u['rol'] != 'responsable' or inter['responsable_id'] == u['id']


def ficha_intervencion(c, ctx, iid):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_interno')
    inter = _intervencion(c, ente, iid)
    if not _visible_para(u, inter):
        raise ApiError(404, 'intervención inexistente')  # no revela que existe
    return 200, ficha_intervencion_datos(c, ente['id'], iid)


def listar_intervenciones(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_interno')
    filas = c.execute('SELECT * FROM intervenciones WHERE ente_id=? ORDER BY id DESC', (ente['id'],)).fetchall()
    return 200, [ficha_intervencion_datos(c, ente['id'], f['id']) for f in filas if _visible_para(u, fila(f))]


# ---------- indicadores, mapa e historial ----------

def indicadores(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_tablero')
    cfg = cfg_de(ente)
    t = now()
    eid = ente['id']
    incs = [fila(r) for r in c.execute('SELECT * FROM incidencias WHERE ente_id=?', (eid,))]
    ints = [fila(r) for r in c.execute('SELECT * FROM intervenciones WHERE ente_id=?', (eid,))]
    pendientes = [i for i in incs if i['estado'] == 'recibida']
    antig = [(t - parse_iso(i['fecha_reporte'])).total_seconds() / 86400 for i in pendientes]
    horas = [(parse_iso(r['fecha']) - parse_iso(r['fecha_reporte'])).total_seconds() / 3600
             for r in c.execute('''SELECT i.fecha, inc.fecha_reporte FROM inspecciones i
                                   JOIN incidencias inc ON inc.id=i.incidencia_id WHERE inc.ente_id=?''', (eid,))]
    activas = [i for i in ints if i['estado'] in ('en_ejecucion', 'finalizada_declarada')]
    atrasadas = [i for i in ints if i['estado'] not in ('validada', 'cerrada') and fecha_hoy_iso() > i['fecha_planificada_fin']]
    pend_rev = c.execute("""SELECT COUNT(*) FROM avances av JOIN actividades a ON a.id=av.actividad_id
                            JOIN intervenciones i ON i.id=a.intervencion_id WHERE i.ente_id=? AND av.estado='pendiente'""",
                         (eid,)).fetchone()[0]
    difs = [r[0] for r in c.execute("""SELECT a.pct_declarado - a.pct_validado FROM actividades a
                                       JOIN intervenciones i ON i.id=a.intervencion_id
                                       WHERE i.ente_id=? AND EXISTS (SELECT 1 FROM avances av
                                       WHERE av.actividad_id=a.id AND av.estado='validado')""", (eid,))]
    finalizadas = [i for i in ints if i['estado'] in ('validada', 'cerrada') and i['fecha_fin_real']]
    a_tiempo = [i for i in finalizadas if i['fecha_fin_real'][:10] <= i['fecha_planificada_fin']]
    confirmadas = [i for i in incs if i['estado'] in ('confirmada', 'con_intervencion', 'resuelta')]
    reinc = 0
    for x in confirmadas:
        for y in confirmadas:
            if x['id'] != y['id'] and x['categoria'] == y['categoria'] and \
                    haversine_m(x['lat'], x['lon'], y['lat'], y['lon']) <= cfg['duplicados']['radio_m'] * 2:
                reinc += 1
                break
    dup = sum(1 for i in incs if i['estado'] == 'duplicada')
    d = {
        'incidencias_pendientes': indicador(len(pendientes), len(pendientes), 'Reportes sin inspección'),
        'antiguedad_mediana_pendientes_dias': indicador(mediana(antig), len(antig), 'Mediana de días desde el reporte'),
        'tiempo_reporte_a_inspeccion_mediano_h': indicador(mediana(horas), len(horas), 'Mediana de horas'),
        'intervenciones_activas': indicador(len(activas), len(ints), 'En ejecución o con cierre declarado'),
        'obras_atrasadas': indicador(len(atrasadas), len(ints), 'Fecha planificada vencida sin validación'),
        'avances_pendientes_revision': indicador(pend_rev, pend_rev, 'Declarados y sin validar'),
        'diferencia_declarado_validado_pp': indicador(round(sum(difs) / len(difs), 1) if difs else None, len(difs),
                                                       'Promedio de puntos porcentuales, sólo actividades con validación'),
        'intervenciones_finalizadas': indicador(len(finalizadas), len(finalizadas), 'Validadas o cerradas'),
        'cumplimiento_plazos_pct': indicador(
            round(100 * len(a_tiempo) / len(finalizadas), 1) if len(finalizadas) >= 5 else None,
            len(finalizadas) if len(finalizadas) >= 5 else 0,
            'Requiere al menos 5 intervenciones finalizadas'),
        'reincidencias_territoriales': indicador(reinc, len(confirmadas),
                                                 'Confirmadas con otra de la misma categoría a no más del doble del radio de duplicados'),
        'reportes_agrupados_como_duplicados': indicador(dup, len(incs), 'Reportes absorbidos en otra incidencia'),
    }
    return 200, d


def mapa(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_tablero')
    incs = [fila(r) for r in c.execute(
        'SELECT id,codigo,categoria,estado,gravedad,lat,lon,fecha_reporte,intervencion_id FROM incidencias WHERE ente_id=?',
        (ente['id'],))]
    ints = [fila(r) for r in c.execute(
        'SELECT id,titulo,tipo,estado,lat,lon,fecha_planificada_fin FROM intervenciones WHERE ente_id=? AND lat IS NOT NULL',
        (ente['id'],))]
    for i in ints:
        i['atrasada'] = i['estado'] not in ('validada', 'cerrada') and fecha_hoy_iso() > i['fecha_planificada_fin']
    return 200, {'centro': cfg_de(ente)['centro'], 'incidencias': incs, 'intervenciones': ints}


def historial_entidad(c, ctx):
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_interno')
    ent = ctx['q'].get('entidad')
    eid = ctx['q'].get('id')
    if ent not in ('incidencia', 'intervencion', 'avance', 'evidencia') or not str(eid or '').isdigit():
        raise ApiError(400, 'parámetros entidad e id requeridos')
    filas = c.execute('SELECT * FROM historial WHERE ente_id=? AND entidad=? AND entidad_id=? ORDER BY id',
                      (ente['id'], ent, int(eid))).fetchall()
    return 200, [fila(r) for r in filas]


# ---------- catálogos públicos del prototipo ----------

def listar_entes(c, ctx):
    return 200, [{'codigo': r['codigo'], 'nombre': r['nombre'], 'config': cfg_de(r)} for r in
                 c.execute('SELECT * FROM entes ORDER BY id')]


def listar_usuarios(c, ctx):
    """Personal del mismo ente (para asignar responsables). Sin contraseñas ni logins."""
    u, ente = usuario_actual(c, ctx)
    exigir(u, 'ver_interno')
    return 200, [fila(r) for r in c.execute('SELECT id,nombre,rol FROM usuarios WHERE ente_id=? AND activo=1 ORDER BY id',
                                            (ente['id'],))]


# ---------- autenticación ----------

def hash_password(password):
    sal = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), sal, PBKDF2_ITER)
    return f'pbkdf2_sha256${PBKDF2_ITER}${sal.hex()}${dk.hex()}'


def verificar_password(password, almacenado):
    try:
        _, iteraciones, sal_hex, dk_hex = almacenado.split('$')
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(sal_hex), int(iteraciones))
    return hmac.compare_digest(dk.hex(), dk_hex)


def _hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def login(c, ctx):
    b = ctx['body']
    nombre_login = str(b.get('login') or '').strip()
    password = str(b.get('password') or '')
    if not nombre_login or not password:
        raise ApiError(400, 'usuario y contraseña son obligatorios')
    t = now()
    estado = FALLOS_LOGIN.get(nombre_login)
    if estado and estado[1] and t < parse_iso(estado[1]):
        raise ApiError(429, 'demasiados intentos fallidos; esperá unos minutos')
    r = c.execute('SELECT * FROM usuarios WHERE login=?', (nombre_login,)).fetchone()
    if r is None or not verificar_password(password, r['password_hash']) or not r['activo']:
        contador = (estado[0] if estado else 0) + 1
        bloqueo = iso(t + timedelta(minutes=LOGIN_BLOQUEO_MIN)) if contador >= LOGIN_MAX_FALLOS else None
        FALLOS_LOGIN[nombre_login] = [0 if bloqueo else contador, bloqueo]
        if r is not None:
            audit(c, r['ente_id'], 'usuario', r['id'], 'login_fallido', r['id'], {'intentos': contador})
        c.commit()  # el registro del intento fallido debe persistir aunque la respuesta sea 401
        raise ApiError(401, 'usuario o contraseña incorrectos')
    FALLOS_LOGIN.pop(nombre_login, None)
    token = secrets.token_urlsafe(32)
    c.execute('INSERT INTO sesiones(token_hash,usuario_id,creada_at,expira_at) VALUES(?,?,?,?)',
              (_hash_token(token), r['id'], iso(t), iso(t + timedelta(hours=SESION_HORAS))))
    audit(c, r['ente_id'], 'usuario', r['id'], 'login_ok', r['id'])
    u = fila(r)
    u.pop('password_hash')
    return 200, {'usuario': u, '_token': token}


def logout(c, ctx):
    if ctx.get('token'):
        c.execute('UPDATE sesiones SET revocada=1 WHERE token_hash=?', (_hash_token(ctx['token']),))
    return 200, {'ok': True}


def me(c, ctx):
    u, ente = usuario_actual(c, ctx)
    u.pop('password_hash')
    return 200, {'usuario': u, 'ente': ente['codigo']}


def uid_de_sesion(c, token):
    """Devuelve el id de usuario de una sesión vigente, o None."""
    if not token:
        return None
    r = c.execute('SELECT usuario_id, expira_at FROM sesiones WHERE token_hash=? AND revocada=0',
                  (_hash_token(token),)).fetchone()
    if r is None or now() >= parse_iso(r['expira_at']):
        return None
    return r['usuario_id']


def crear_usuario(c, ente_id, nombre, login_nombre, password, rol):
    """Alta de usuario (seed y administración). La contraseña se guarda sólo como hash."""
    cur = c.execute('INSERT INTO usuarios(ente_id,nombre,login,password_hash,rol) VALUES(?,?,?,?,?)',
                    (ente_id, nombre, login_nombre, hash_password(password), rol))
    return cur.lastrowid


# ---------- enrutado ----------

RUTAS = [
    ('GET', r'^/entes$', listar_entes),
    ('POST', r'^/login$', login),
    ('POST', r'^/logout$', logout),
    ('GET', r'^/me$', me),
    ('GET', r'^/usuarios$', listar_usuarios),
    ('POST', r'^/incidencias$', reportar),
    ('GET', r'^/seguimiento/([^/]*)$', seguimiento_publico),
    ('GET', r'^/mis-reportes$', mis_reportes),
    ('GET', r'^/incidencias$', listar_incidencias),
    ('GET', r'^/incidencias/(\d+)$', ficha_incidencia),
    ('POST', r'^/incidencias/(\d+)/inspeccion$', inspeccionar),
    ('POST', r'^/incidencias/(\d+)/evidencias$', subir_evidencia_incidencia),
    ('GET', r'^/mi-trabajo$', mi_trabajo),
    ('GET', r'^/intervenciones$', listar_intervenciones),
    ('POST', r'^/intervenciones$', crear_intervencion),
    ('GET', r'^/intervenciones/(\d+)$', ficha_intervencion),
    ('POST', r'^/intervenciones/(\d+)/transicion$', transicion_intervencion),
    ('POST', r'^/intervenciones/(\d+)/avances$', declarar_avance),
    ('POST', r'^/intervenciones/(\d+)/evidencias$', subir_evidencia),
    ('POST', r'^/avances/(\d+)/revision$', revisar_avance),
    ('POST', r'^/evidencias/(\d+)/revision$', revisar_evidencia),
    ('GET', r'^/indicadores$', indicadores),
    ('GET', r'^/mapa$', mapa),
    ('GET', r'^/historial$', historial_entidad),
]


def _rutas():
    """Rutas del circuito de reportes más las del Modo obra (app/obra.py, que importa este módulo)."""
    import obra  # import tardío: obra.py importa core
    return RUTAS + obra.RUTAS


def despachar(c, metodo, ruta, q, body, uid, token=None):
    """Devuelve (status, payload). uid y token los resuelve el servidor desde la cookie de sesión."""
    for m, patron, fn in _rutas():
        mt = re.match(patron, ruta)
        if mt and m == metodo:
            ctx = {'q': q, 'body': body, 'uid': uid, 'token': token}
            args = mt.groups()
            if fn is seguimiento_publico:
                return fn(c, ctx, args[0])
            if args:
                return fn(c, ctx, int(args[0]))
            return fn(c, ctx)
    raise ApiError(404, 'ruta inexistente')


def cargar_configs(c):
    """Carga o actualiza los entes definidos en config/entes/*.json (idempotente)."""
    for nombre in sorted(os.listdir(CONFIG_DIR)):
        if not nombre.endswith('.json'):
            continue
        with open(os.path.join(CONFIG_DIR, nombre), encoding='utf-8') as f:
            cfg = json.load(f)
        c.execute(
            '''INSERT INTO entes(codigo,nombre,config_json,ficticio) VALUES(?,?,?,?)
               ON CONFLICT(codigo) DO UPDATE SET nombre=excluded.nombre, config_json=excluded.config_json''',
            (cfg['codigo'], cfg['nombre'], json.dumps(cfg, ensure_ascii=False), 1 if cfg.get('ficticio') else 0),
        )


