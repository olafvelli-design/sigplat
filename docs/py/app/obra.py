"""SIGPLAT · Modo obra: simulación de cómo trabajaría un inspector con SIGO y SIGUE.

SIMULACIÓN con datos ficticios, sin validez legal y sin conexión con SIGO ni SIGUE. Ver MODO_OBRA.md.
Sólo biblioteca estándar: corre también en el navegador con Pyodide.

Convenciones:
- El dinero va en centavos enteros. Las multiplicaciones usan Decimal y se redondea "mitad hacia arriba".
- Las cantidades de avance son del MES (no acumuladas ni en porcentaje).
- Nada se borra: un cómputo "eliminado" o un certificado "anulado" quedan con estado y auditoría.
"""
import json
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import core
from core import ApiError

MSG_PLAZO_VENCIDO = 'Para dar de alta un plazo cert. obra vencida haga click aquí'
CONFIG_OBRA = {  # valores de EJEMPLO, a validar con un inspector real; se pisan desde config/entes/*.json ("obra")
    'hormigon': {'tipos': ['H13', 'H17', 'H21', 'H25'], 'asentamiento_tolerancia_cm': 2,
                 'temp_hormigon_min_c': 5, 'temp_hormigon_max_c': 30},
    'iva_pct': 21,
    'alerta_vencimiento_dias': 30,
    # tipos de contratación de obra pública, como los lista el portal de compras ("- O.P.")
    'tipos_contratacion': ['Licitación Presencial - O.P.', 'Compulsa Abreviada Presencial - O.P.', 'Directa Presencial Por Monto - O.P.',
                           'Directa Presencial Por Naturaleza - O.P.', 'Subasta Electrónica Inversa - O.P.'],
    'legajo_requeridos': ['acto_administrativo', 'pliego_condiciones', 'pliego_tecnico', 'plano', 'computo_presupuesto'],
    'sigla_codigo': None,  # por defecto, el primer tramo del código del ente (CBA-PILOTO -> CBA)
}


# ---------- utilidades ----------

def _d(x):
    return Decimal(str(x))


def centavos(x):
    """Redondeo comercial (mitad hacia arriba) a centavos enteros."""
    return int(_d(x).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def importe(cantidad, precio_cent):
    return centavos(_d(cantidad) * _d(precio_cent))


def q3(x):
    return float(_d(x).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP))


def pct(parte, total):
    return float((_d(parte) * 100 / _d(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) if total else 0.0


def hoy():
    return core.now().date()


def parse_fecha(valor, campo):
    try:
        return datetime.strptime(valor, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise ApiError(400, f'{campo} debe tener formato AAAA-MM-DD')


def mes_de(d):
    return d.strftime('%Y-%m') if isinstance(d, (date, datetime)) else str(d)[:7]


def sumar_meses(mes, n):
    a, m = int(mes[:4]), int(mes[5:7])
    t = a * 12 + (m - 1) + n
    return f'{t // 12:04d}-{t % 12 + 1:02d}'


def meses_entre(desde, hasta):
    out, m = [], desde
    while m <= hasta:
        out.append(m)
        m = sumar_meses(m, 1)
    return out


def cfg_obra(ente):
    base = json.loads(json.dumps(CONFIG_OBRA))
    extra = core.cfg_de(ente).get('obra') or {}
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k].update(v)
        else:
            base[k] = v
    return base


# ---------- acceso a datos y visibilidad ----------

def _obra(c, ente, oid):
    r = c.execute('SELECT * FROM obras WHERE id=? AND ente_id=?', (oid, ente['id'])).fetchone()
    if r is None:
        raise ApiError(404, 'obra inexistente')
    return core.fila(r)


FUNCIONES_OBRA = ('jefe_inspeccion', 'analista_certificaciones', 'jefe_certificaciones', 'director')  # sólo ven las obras asignadas


def _visible(c, u, obra):
    """Inspector y contratista: su obra. Jefe de inspección, analista, jefe de certificaciones y director: las obras donde están asignados.
    Autoridad, administrativo, administrador y auditor: todas las del ente (el auditor sólo lee)."""
    if u['rol'] == 'inspector':
        return obra['inspector_id'] == u['id']
    if u['rol'] == 'contratista':
        return obra['contratista_id'] == u['id']
    if u['rol'] in FUNCIONES_OBRA:
        return c.execute('SELECT 1 FROM obra_asignados WHERE obra_id=? AND usuario_id=?', (obra['id'], u['id'])).fetchone() is not None
    return u['rol'] in ('autoridad', 'administrativo', 'administrador', 'auditor')


def _obra_para(c, ctx, permiso='ver_obras', oid=None):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    obra = _obra(c, ente, oid)
    if not _visible(c, u, obra):
        raise ApiError(404, 'obra inexistente')  # no revela que existe
    return u, ente, obra


def _items(c, oid):
    return [core.fila(r) for r in c.execute('SELECT * FROM items_obra WHERE obra_id=? ORDER BY nro', (oid,))]


def monto_contrato_cent(items):
    return sum(importe(i['cantidad_oferta'], i['precio_oferta_cent']) for i in items if not i['es_titulo'])


def fecha_fin(obra):
    return parse_fecha(obra['fecha_replanteo'], 'fecha_replanteo') + timedelta(days=obra['plazo_dias'])


def _plazo_vencida_activo(c, oid):
    h = hoy()
    for r in c.execute('SELECT desde, dias FROM plazos_cert_vencida WHERE obra_id=?', (oid,)):
        if parse_fecha(r['desde'], 'desde') <= h <= parse_fecha(r['desde'], 'desde') + timedelta(days=r['dias']):
            return True
    return False


def _certificado_de(c, computo_id):
    r = c.execute("SELECT * FROM certificados WHERE computo_id=? AND estado!='anulado'", (computo_id,)).fetchone()
    return core.fila(r)


# ---------- códigos de obra ----------
# El portal de compras publica las contrataciones con tres numeraciones que no se parecen (2026/000112, 0886-000135/2025, 06/2025)
# y ninguna dice por sí sola qué obra es. SIGPLAT le da a cada obra un código propio, único y correlativo, y guarda
# al lado los números oficiales, que son los que se ven en el portal. Formato: OP-<sigla del ente>-<año>-<número>-<dígito>.
# El dígito final (módulo 11) detecta errores de tipeo: un carácter mal o dos intercambiados.

CODIGO_RE = re.compile(r'^OP-([A-Z]{2,6})-(\d{4})-(\d{4,})-([0-9K])$')
NRO_CONTRATACION_RE = re.compile(r'^(\d{4}/\d{6}(\.\d{2})?|\d{1,3}/\d{4})$')  # 2026/000112, 2026/000134.01 o 06/2025


def digito_codigo(cuerpo):
    s = ''.join(ch for ch in cuerpo if ch.isalnum())
    r = sum((i + 1) * (int(ch) if ch.isdigit() else ord(ch) - 55) for i, ch in enumerate(s)) % 11
    return 'K' if r == 10 else str(r)


def sigla_ente(ente):
    sigla = cfg_obra(ente).get('sigla_codigo') or re.sub(r'[^A-Z]', '', ente['codigo'].split('-')[0].upper())
    return sigla[:6] or 'OBR'


def codigo_valido(codigo):
    m = CODIGO_RE.match(str(codigo or '').strip().upper())
    return bool(m) and digito_codigo(codigo.strip().upper().rsplit('-', 1)[0]) == m.group(4)


def nuevo_codigo(c, ente):
    """Siguiente código del ente en el año. Usa una secuencia que sólo sube: un código no se reutiliza."""
    anio = core.now().year
    c.execute('INSERT INTO secuencias_obra(ente_id,anio,ultimo) VALUES(?,?,1) ON CONFLICT(ente_id,anio) DO UPDATE SET ultimo=ultimo+1',
              (ente['id'], anio))
    n = c.execute('SELECT ultimo FROM secuencias_obra WHERE ente_id=? AND anio=?', (ente['id'], anio)).fetchone()[0]
    cuerpo = f'OP-{sigla_ente(ente)}-{anio}-{n:04d}'
    return f'{cuerpo}-{digito_codigo(cuerpo)}'


def _identificacion(c, ente, b, excepto=None):
    """Valida los datos oficiales de la contratación que vienen del portal de compras."""
    cfg = cfg_obra(ente)
    tipo = b.get('tipo_contratacion')
    if tipo not in cfg['tipos_contratacion']:
        raise ApiError(400, 'tipo_contratacion debe ser uno de: ' + '; '.join(cfg['tipos_contratacion']))
    nro = (b.get('nro_contratacion') or '').strip()
    if not NRO_CONTRATACION_RE.match(nro):
        raise ApiError(400, 'el número de contratación tiene formato AAAA/NNNNNN (2026/000112), AAAA/NNNNNN.NN o NN/AAAA (06/2025)')
    exp = (b.get('expediente') or '').strip()
    if not EXPEDIENTE_RE.match(exp):
        raise ApiError(400, 'el expediente tiene formato 0000-000000/0000, con /A000 si es un anexo')
    servicio = core.texto(b.get('servicio_administrativo'), 3, 120, 'servicio administrativo')
    for sql, args, msg in (
            ('SELECT codigo FROM obras WHERE ente_id=? AND servicio_administrativo=? AND nro_contratacion=?', (ente['id'], servicio, nro),
             'ese servicio administrativo ya tiene una obra con el número de contratación ' + nro),
            ('SELECT codigo FROM obras WHERE ente_id=? AND expediente=?', (ente['id'], exp), 'el expediente ' + exp + ' ya pertenece a otra obra')):
        r = c.execute(sql, args).fetchone()
        if r and r['codigo'] != excepto:
            raise ApiError(409, msg + ' (' + str(r['codigo']) + ')')
    return {'tipo_contratacion': tipo, 'nro_contratacion': nro, 'expediente': exp, 'servicio_administrativo': servicio}


def alta_obra(c, ctx):
    """El administrativo da de alta la obra (preadjudicada). Se genera el código y se arma el legajo antes de adjudicar."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'alta_obra')
    b = ctx['body']
    ident = _identificacion(c, ente, b)
    den = core.texto(b.get('denominacion'), 5, 200, 'denominación')
    contratista = core.texto(b.get('contratista'), 3, 120, 'contratista')
    replanteo = _fecha_opcional(b.get('fecha_replanteo'), 'fecha_replanteo')
    if not replanteo:
        raise ApiError(400, 'falta la fecha de replanteo prevista')
    plazo = core.numero_opcional(b.get('plazo_dias'), 1, 3650, 'plazo en días')
    if plazo is None or int(plazo) != plazo:
        raise ApiError(400, 'el plazo debe ser un número entero de días')
    lat, lon = core.numero_opcional(b.get('lat'), -90, 90, 'lat'), core.numero_opcional(b.get('lon'), -180, 180, 'lon')
    if (lat is None) != (lon is None):
        raise ApiError(400, 'la ubicación necesita latitud y longitud juntas')
    oid = crear_obra(c, ente['id'], den, contratista, replanteo, int(plazo), [], {}, lat=lat, lon=lon, estado='preadjudicada',
                     usuario_id=u['id'], ident=ident)
    return 201, core.fila(c.execute('SELECT * FROM obras WHERE id=?', (oid,)).fetchone())


def asignar_obra(c, ctx, oid):
    """Asigna el inspector y/o la empresa contratista (usuarios del mismo ente) a una obra."""
    u, ente, obra = _obra_para(c, ctx, 'asignar_obra', oid)
    b, cambios = ctx['body'], {}
    for campo, rol in (('inspector_id', 'inspector'), ('contratista_id', 'contratista')):
        if b.get(campo) is None:
            continue
        r = c.execute('SELECT id FROM usuarios WHERE id=? AND ente_id=? AND rol=? AND activo=1', (b[campo], ente['id'], rol)).fetchone()
        if r is None:
            raise ApiError(400, f'{campo} debe ser un usuario activo con rol {rol} de este ente')
        cambios[campo] = r['id']
    if not cambios:
        raise ApiError(400, 'indicá inspector_id y/o contratista_id')
    for campo, valor in cambios.items():
        c.execute(f'UPDATE obras SET {campo}=? WHERE id=?', (valor, oid))
    core.audit(c, ente['id'], 'obra', oid, 'asignada', u['id'], cambios)
    return 200, core.fila(c.execute('SELECT * FROM obras WHERE id=?', (oid,)).fetchone())


ORDEN_ESTADOS_OBRA = ['preadjudicada', 'adjudicada', 'en_ejecucion', 'finalizada']


def cambiar_estado_obra(c, ctx, oid):
    """Avanza el estado de la obra de a un paso. Adjudicar (autoridad) exige el legajo publicado; ejecutar, un inspector asignado."""
    u, ente, obra = _obra_para(c, ctx, 'ver_obras', oid)
    nuevo = ctx['body'].get('estado')
    if nuevo not in ORDEN_ESTADOS_OBRA:
        raise ApiError(400, 'estado debe ser uno de: ' + ', '.join(ORDEN_ESTADOS_OBRA))
    core.exigir(u, 'adjudicar_obra' if nuevo == 'adjudicada' else 'asignar_obra')
    if ORDEN_ESTADOS_OBRA.index(nuevo) != ORDEN_ESTADOS_OBRA.index(obra['estado']) + 1:
        raise ApiError(409, f'la obra está {obra["estado"]}: sólo puede pasar al estado siguiente')
    if nuevo == 'adjudicada' and obra['legajo_estado'] != 'publicado':
        raise ApiError(409, 'no se puede adjudicar con el legajo sin publicar')
    if nuevo == 'en_ejecucion' and not obra['inspector_id']:
        raise ApiError(409, 'no se puede pasar a ejecución sin inspector asignado')
    c.execute('UPDATE obras SET estado=? WHERE id=?', (nuevo, oid))
    core.audit(c, ente['id'], 'obra', oid, 'estado', u['id'], {'de': obra['estado'], 'a': nuevo})
    return 200, core.fila(c.execute('SELECT * FROM obras WHERE id=?', (oid,)).fetchone())


# ---------- alta de una obra con ítems (seed y pruebas) ----------

def crear_obra(c, ente_id, denominacion, contratista, fecha_replanteo, plazo_dias, items, ideal, contratista_id=None,
               inspector_id=None, anticipo_pct=0, fondo_reparo_pct=5, fondo_inspeccion_pct=0, plazo_venc_cert_dias=30,
               lat=None, lon=None, estado='en_ejecucion', usuario_id=None, ident=None):
    """items: [{nro, descripcion, unidad, cantidad, precio_cent, titulo}]; ideal: {nro: {'AAAA-MM': cantidad}}.
    ident: {tipo_contratacion, nro_contratacion, expediente, servicio_administrativo}, ya validados. El código se genera acá."""
    parse_fecha(fecha_replanteo, 'fecha_replanteo')
    nros = [i['nro'] for i in items]
    if len(set(nros)) != len(nros):
        raise ApiError(400, 'los números de ítem no pueden repetirse')
    ident = ident or {}
    ente = core.fila(c.execute('SELECT * FROM entes WHERE id=?', (ente_id,)).fetchone())
    cur = c.execute(
        '''INSERT INTO obras(ente_id,denominacion,estado,contratista,contratista_id,inspector_id,fecha_replanteo,plazo_dias,
           anticipo_pct,fondo_reparo_pct,fondo_inspeccion_pct,plazo_venc_cert_dias,lat,lon,curva_cerrada,codigo,tipo_contratacion,
           nro_contratacion,expediente,servicio_administrativo)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)''',
        (ente_id, denominacion, estado, contratista, contratista_id, inspector_id, fecha_replanteo, plazo_dias,
         anticipo_pct, fondo_reparo_pct, fondo_inspeccion_pct, plazo_venc_cert_dias, lat, lon, nuevo_codigo(c, ente),
         ident.get('tipo_contratacion'), ident.get('nro_contratacion'), ident.get('expediente'), ident.get('servicio_administrativo')))
    oid = cur.lastrowid
    principal = c.execute("INSERT INTO curvas_ideal(obra_id,nombre,tipo,numero,estado,creada_por,creada_at) VALUES(?,?,?,?,?,?,?)",
                          (oid, 'Principal', 'principal', 0, 'autorizada', usuario_id, core.iso(core.now()))).lastrowid
    ids = {}
    for it in items:
        titulo = 1 if it.get('titulo') else 0
        if not titulo and (it.get('cantidad', 0) < 0 or it.get('precio_cent', 0) < 0):
            raise ApiError(400, 'cantidades y precios no pueden ser negativos')
        c2 = c.execute(
            'INSERT INTO items_obra(obra_id,nro,descripcion,unidad,cantidad_oferta,precio_oferta_cent,es_titulo) VALUES(?,?,?,?,?,?,?)',
            (oid, it['nro'], it['descripcion'], None if titulo else it.get('unidad'),
             0 if titulo else q3(it['cantidad']), 0 if titulo else int(it['precio_cent']), titulo))
        ids[it['nro']] = (c2.lastrowid, 0 if titulo else _d(it['cantidad']))
    for nro, meses in (ideal or {}).items():
        item_id, oferta = ids[nro]
        if sum(_d(v) for v in meses.values()) > oferta + Decimal('0.0005'):
            raise ApiError(400, f'el avance ideal del ítem {nro} supera la cantidad de oferta')
        for mes, cant in meses.items():
            if not re.match(r'^\d{4}-\d{2}$', mes):
                raise ApiError(400, 'los meses del avance ideal deben ser AAAA-MM')
            c.execute('INSERT INTO avance_ideal(curva_id,item_id,mes,cantidad) VALUES(?,?,?,?)', (principal, item_id, mes, q3(cant)))
    core.audit(c, ente_id, 'obra', oid, 'creada', usuario_id, {'denominacion': denominacion})
    return oid


# ---------- curva de avance ideal contra real ----------

def _curva_vigente(c, oid):
    """La curva ideal en vigor: la última autorizada (la Principal si no hubo modificaciones autorizadas)."""
    r = c.execute("SELECT * FROM curvas_ideal WHERE obra_id=? AND estado='autorizada' ORDER BY id DESC LIMIT 1", (oid,)).fetchone()
    return core.fila(r)


def _ideal_por_mes(c, oid, curva_id=None):
    if curva_id is None:
        v = _curva_vigente(c, oid)
        curva_id = v['id'] if v else -1
    out = {}
    for r in c.execute('''SELECT a.mes, a.cantidad, i.precio_oferta_cent FROM avance_ideal a
                          JOIN items_obra i ON i.id=a.item_id WHERE a.curva_id=?''', (curva_id,)):
        out[r['mes']] = out.get(r['mes'], 0) + importe(r['cantidad'], r['precio_oferta_cent'])
    return out


def _real_por_mes(c, oid, precios):
    """Importe de cada mes con certificado no anulado (la curva real se forma con los certificados)."""
    out = {}
    for k in c.execute("""SELECT k.id, k.mes FROM computos k JOIN certificados ce ON ce.computo_id=k.id
                          WHERE k.obra_id=? AND k.estado='presentado' AND ce.estado!='anulado'""", (oid,)):
        out[k['mes']] = sum(importe(r['cantidad'], precios[r['item_id']])
                            for r in c.execute('SELECT item_id, cantidad FROM computo_items WHERE computo_id=?', (k['id'],)))
    return out


def curva(c, obra, items=None):
    items = items if items is not None else _items(c, obra['id'])
    precios = {i['id']: i['precio_oferta_cent'] for i in items}
    monto = monto_contrato_cent(items)
    real = _real_por_mes(c, obra['id'], precios)
    curvas_db = [core.fila(r) for r in c.execute('SELECT * FROM curvas_ideal WHERE obra_id=? ORDER BY id', (obra['id'],))]
    por_curva = {cv['id']: _ideal_por_mes(c, obra['id'], cv['id']) for cv in curvas_db}
    vigente = _curva_vigente(c, obra['id'])
    ideal = por_curva.get(vigente['id'], {}) if vigente else {}
    m0 = mes_de(obra['fecha_replanteo'])
    todos = [m0, mes_de(fecha_fin(obra))] + list(real) + [m for d in por_curva.values() for m in d]
    eje = meses_entre(min(todos), max(todos))  # el mismo eje de meses para todas las curvas
    series = []
    for cv in curvas_db:
        acum, pts = 0, []
        for mes in eje:
            acum += por_curva[cv['id']].get(mes, 0)
            pts.append({'mes': mes, 'acum_cent': acum, 'acum_pct': pct(acum, monto)})
        series.append({'id': cv['id'], 'nombre': cv['nombre'], 'tipo': cv['tipo'], 'numero': cv['numero'], 'estado': cv['estado'],
                       'vigente': bool(vigente and cv['id'] == vigente['id']), 'tramite': cv['tramite'], 'instrumento': cv['instrumento'],
                       'dias': cv['dias'], 'puntos': pts})
    puntos, ia, ra = [], 0, 0
    ultimo_real = max(real) if real else None
    for mes in eje:
        ia += ideal.get(mes, 0)
        con_real = ultimo_real is not None and mes <= ultimo_real
        if con_real:
            ra += real.get(mes, 0)
        p = {'mes': mes, 'ideal_mes_cent': ideal.get(mes, 0), 'ideal_acum_cent': ia, 'ideal_acum_pct': pct(ia, monto),
             'real_mes_cent': real.get(mes, 0) if con_real else None, 'real_acum_cent': ra if con_real else None,
             'real_acum_pct': pct(ra, monto) if con_real else None, 'desvio_cent': None, 'desvio_pp': None}
        if con_real:
            p['desvio_cent'] = ra - ia
            p['desvio_pp'] = pct(ra - ia, monto)  # desde los centavos exactos, no restando porcentajes ya redondeados
        puntos.append(p)
    n = len(real)
    ult = next((p for p in reversed(puntos) if p['desvio_pp'] is not None), None)
    return {'monto_contrato_cent': monto, 'puntos': puntos, 'curvas': series,
            'real': [{'mes': p['mes'], 'mes_cent': p['real_mes_cent'], 'acum_cent': p['real_acum_cent'], 'acum_pct': p['real_acum_pct']}
                     for p in puntos if p['real_acum_cent'] is not None],
            'desvio': core.indicador(ult['desvio_pp'] if ult else None, n,
                                     'Puntos porcentuales entre lo certificado y lo comprometido, al último mes certificado')}


# ---------- cómputo métrico ----------

def _acumulado_previo(c, oid, mes):
    """Cantidad medida por ítem en cómputos PRESENTADOS de meses anteriores a `mes`."""
    acc = {}
    for r in c.execute("""SELECT ci.item_id, ci.cantidad FROM computo_items ci JOIN computos k ON k.id=ci.computo_id
                          WHERE k.obra_id=? AND k.estado='presentado' AND k.mes<?""", (oid, mes)):
        acc[r['item_id']] = acc.get(r['item_id'], Decimal(0)) + _d(r['cantidad'])
    return acc


def _vista_computo(c, obra, k):
    items = _items(c, obra['id'])
    ant = _acumulado_previo(c, obra['id'], k['mes'])
    pre = {r['item_id']: _d(r['cantidad']) for r in c.execute('SELECT item_id, cantidad FROM computo_items WHERE computo_id=?', (k['id'],))}
    filas, sup = [], []
    tot = {'importe_anterior_cent': 0, 'importe_presente_cent': 0, 'importe_acumulado_cent': 0}
    for it in items:
        if it['es_titulo']:
            filas.append({'item_id': it['id'], 'nro': it['nro'], 'descripcion': it['descripcion'], 'es_titulo': True})
            continue
        a, p = ant.get(it['id'], Decimal(0)), pre.get(it['id'], Decimal(0))
        acum, of, pr = a + p, _d(it['cantidad_oferta']), it['precio_oferta_cent']
        f = {'item_id': it['id'], 'nro': it['nro'], 'descripcion': it['descripcion'], 'es_titulo': False, 'unidad': it['unidad'],
             'cantidad_oferta': q3(of), 'precio_cent': pr, 'anterior': q3(a), 'presente': q3(p), 'acumulada': q3(acum),
             'pct_anterior': pct(a, of), 'pct_presente': pct(p, of), 'pct_acumulado': pct(acum, of),
             'importe_anterior_cent': importe(a, pr), 'importe_presente_cent': importe(p, pr), 'importe_acumulado_cent': importe(acum, pr),
             'supera_oferta': acum > of + Decimal('0.0005')}
        if f['supera_oferta']:
            sup.append(it['nro'])
        for kk in tot:
            tot[kk] += f[kk]
        filas.append(f)
    monto = monto_contrato_cent(items)
    tot['pct_avance_acumulado'] = pct(tot['importe_acumulado_cent'], monto)
    tot['pct_avance_mes'] = pct(tot['importe_presente_cent'], monto)
    cert = _certificado_de(c, k['id'])
    return {'id': k['id'], 'obra_id': obra['id'], 'mes': k['mes'], 'estado': k['estado'], 'personal_afectado': k['personal_afectado'],
            'es_final': bool(k['es_final']), 'observaciones': k['observaciones'], 'items': filas, 'totales': tot,
            'items_que_superan_oferta': sup, 'monto_contrato_cent': monto,
            'certificado': {'id': cert['id'], 'numero': cert['numero'], 'estado': cert['estado']} if cert else None,
            'editable': k['estado'] in ('borrador', 'presentado') and cert is None, 'registro': _firmas(c, 'computo', k['id'])}


def _computo_para(c, ctx, kid, escribir=False):
    """Devuelve (usuario, ente, obra, cómputo). Para escribir hace falta ser el inspector asignado."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'medir' if escribir else 'ver_obras')
    k = c.execute('SELECT * FROM computos WHERE id=?', (kid,)).fetchone()
    if k is None:
        raise ApiError(404, 'cómputo inexistente')
    obra = _obra(c, ente, k['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'cómputo inexistente')
    return u, ente, obra, core.fila(k)


def _exigir_editable(c, k):
    if k['estado'] == 'eliminado':
        raise ApiError(409, 'el cómputo está eliminado')
    if _certificado_de(c, k['id']):
        raise ApiError(409, 'ya existe el certificado de ese mes: el cómputo no se puede modificar ni eliminar')


def nuevo_computo(c, ctx, oid):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'medir')
    obra = _obra(c, ente, oid)
    if obra['inspector_id'] is None:
        raise ApiError(409, 'Asigná un inspector a la obra antes de cargar el primer cómputo.')
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'obra inexistente')
    if obra['estado'] != 'en_ejecucion':
        raise ApiError(409, 'la obra no está en ejecución')
    if hoy() > fecha_fin(obra) and not _plazo_vencida_activo(c, oid):
        raise ApiError(409, MSG_PLAZO_VENCIDO)
    ult = c.execute("SELECT * FROM computos WHERE obra_id=? AND estado!='eliminado' ORDER BY mes DESC LIMIT 1", (oid,)).fetchone()
    if ult is None:
        mes = mes_de(obra['fecha_replanteo'])
    else:
        if ult['es_final']:
            raise ApiError(409, 'la obra ya tiene su cómputo final')
        if not _certificado_de(c, ult['id']):
            raise ApiError(409, 'No se puede abrir un cómputo nuevo si no se generó el certificado del mes anterior.')
        mes = sumar_meses(ult['mes'], 1)
    if mes > mes_de(hoy()):
        raise ApiError(409, 'todavía no empezó ese mes: no hay avance para medir')
    cur = c.execute('INSERT INTO computos(obra_id,mes,estado,creado_por,creado_at) VALUES(?,?,?,?,?)',
                    (oid, mes, 'borrador', u['id'], core.iso(core.now())))
    core.audit(c, ente['id'], 'computo', cur.lastrowid, 'creado', u['id'], {'mes': mes})
    return 201, _vista_computo(c, obra, core.fila(c.execute('SELECT * FROM computos WHERE id=?', (cur.lastrowid,)).fetchone()))


def ver_computo(c, ctx, kid):
    u, ente, obra, k = _computo_para(c, ctx, kid)
    if u['rol'] == 'contratista' and k['estado'] != 'presentado':
        raise ApiError(404, 'cómputo inexistente')  # la contratista ve sólo las mediciones presentadas, para firmarlas
    return 200, _vista_computo(c, obra, k)


def guardar_computo(c, ctx, kid):
    u, ente, obra, k = _computo_para(c, ctx, kid, escribir=True)
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'cómputo inexistente')
    _exigir_editable(c, k)
    b = ctx['body']
    valores = {}
    for x in b.get('items') or []:
        item = c.execute('SELECT * FROM items_obra WHERE id=? AND obra_id=?', (x.get('item_id'), obra['id'])).fetchone()
        if item is None or item['es_titulo']:
            raise ApiError(400, 'cada ítem debe pertenecer a la obra y no ser un rubro')
        cant = core.numero_opcional(x.get('cantidad'), 0, 1e9, f'cantidad del ítem {item["nro"]}')
        if cant is None:
            raise ApiError(400, f'falta la cantidad del ítem {item["nro"]}')
        valores[item['id']] = q3(cant)
    personal = b.get('personal_afectado', k['personal_afectado'])
    if personal is not None and (isinstance(personal, bool) or not isinstance(personal, int) or not 0 <= personal <= 100000):
        raise ApiError(400, 'personal_afectado debe ser un número entero de personas')
    obs = k['observaciones'] if 'observaciones' not in b else ((b.get('observaciones') or '').strip()[:1000] or None)
    es_final = int(bool(b.get('es_final', k['es_final'])))
    for item_id, cant in valores.items():
        c.execute('''INSERT INTO computo_items(computo_id,item_id,cantidad) VALUES(?,?,?)
                     ON CONFLICT(computo_id,item_id) DO UPDATE SET cantidad=excluded.cantidad''', (kid, item_id, cant))
    c.execute('UPDATE computos SET personal_afectado=?, observaciones=?, es_final=? WHERE id=?', (personal, obs, es_final, kid))
    core.audit(c, ente['id'], 'computo', kid, 'modificado' if k['estado'] == 'presentado' else 'guardado', u['id'],
               {'items': len(valores)})
    return 200, _vista_computo(c, obra, core.fila(c.execute('SELECT * FROM computos WHERE id=?', (kid,)).fetchone()))


def presentar_computo(c, ctx, kid):
    u, ente, obra, k = _computo_para(c, ctx, kid, escribir=True)
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'cómputo inexistente')
    if k['estado'] != 'borrador':
        raise ApiError(409, f'el cómputo está {k["estado"]}: sólo se presenta un borrador')
    if k['personal_afectado'] is None:
        raise ApiError(400, 'Falta la cantidad de personal afectado a la obra: es un dato obligatorio del cómputo.')
    vista = _vista_computo(c, obra, k)
    if vista['items_que_superan_oferta'] and len((k['observaciones'] or '').strip()) < 5:
        raise ApiError(400, 'Hay ítems que superan la cantidad contratada (' + ', '.join(map(str, vista['items_que_superan_oferta']))
                       + '): explicá el motivo en las observaciones. Puede corresponder una ampliación o una economía y demasía.')
    c.execute("UPDATE computos SET estado='presentado', presentado_por=?, presentado_at=? WHERE id=?",
              (u['id'], core.iso(core.now()), kid))
    core.audit(c, ente['id'], 'computo', kid, 'presentado', u['id'], {'mes': k['mes'], 'final': bool(k['es_final'])})
    return 200, _vista_computo(c, obra, core.fila(c.execute('SELECT * FROM computos WHERE id=?', (kid,)).fetchone()))


def eliminar_computo(c, ctx, kid):
    u, ente, obra, k = _computo_para(c, ctx, kid, escribir=True)
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'cómputo inexistente')
    _exigir_editable(c, k)
    c.execute("UPDATE computos SET estado='eliminado' WHERE id=?", (kid,))
    core.audit(c, ente['id'], 'computo', kid, 'eliminado', u['id'], {'mes': k['mes']})
    return 200, {'id': kid, 'estado': 'eliminado'}


def registrar_plazo_vencida(c, ctx, oid):
    """Habilita cargar cómputos y certificados con la obra vencida (período de intimación)."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'plazo_cert_vencida')
    obra = _obra(c, ente, oid)
    if hoy() <= fecha_fin(obra):
        raise ApiError(409, 'la obra todavía no venció: no hace falta un plazo de certificación')
    dias = core.numero_opcional(ctx['body'].get('dias'), 1, 365, 'dias')
    if dias is None or int(dias) != dias:
        raise ApiError(400, 'dias debe ser un número entero entre 1 y 365')
    cur = c.execute('INSERT INTO plazos_cert_vencida(obra_id,desde,dias,creado_por,creado_at) VALUES(?,?,?,?,?)',
                    (oid, hoy().strftime('%Y-%m-%d'), int(dias), u['id'], core.iso(core.now())))
    core.audit(c, ente['id'], 'obra', oid, 'plazo_cert_vencida', u['id'], {'dias': int(dias)})
    return 201, {'id': cur.lastrowid, 'dias': int(dias), 'desde': hoy().strftime('%Y-%m-%d')}


# ---------- consulta de obras ----------

def _lista_computos(c, obra, items):
    precios = {i['id']: i['precio_oferta_cent'] for i in items}
    out = []
    for k in c.execute("SELECT * FROM computos WHERE obra_id=? AND estado!='eliminado' ORDER BY mes", (obra['id'],)):
        monto = sum(importe(r['cantidad'], precios[r['item_id']])
                    for r in c.execute('SELECT item_id, cantidad FROM computo_items WHERE computo_id=?', (k['id'],)))
        cert = _certificado_de(c, k['id'])
        out.append({'id': k['id'], 'mes': k['mes'], 'estado': k['estado'], 'personal_afectado': k['personal_afectado'],
                    'es_final': bool(k['es_final']), 'importe_mes_cent': monto,
                    'certificado': {'id': cert['id'], 'numero': cert['numero'], 'estado': cert['estado']} if cert else None})
    return out


def _resumen(c, obra, items=None):
    items = items if items is not None else _items(c, obra['id'])
    monto = monto_contrato_cent(items)
    cv = curva(c, obra, items)
    fin, h = fecha_fin(obra), hoy()
    real = [p for p in cv['puntos'] if p['real_acum_pct'] is not None]
    ideal_hoy = [p for p in cv['puntos'] if p['mes'] <= mes_de(h)]
    medido = sum(importe(r['cantidad'], r['precio_oferta_cent']) for r in c.execute(
        """SELECT ci.cantidad, i.precio_oferta_cent FROM computo_items ci JOIN computos k ON k.id=ci.computo_id
           JOIN items_obra i ON i.id=ci.item_id WHERE k.obra_id=? AND k.estado='presentado'""", (obra['id'],)))
    ultimo = c.execute("SELECT * FROM computos WHERE obra_id=? AND estado!='eliminado' ORDER BY mes DESC LIMIT 1", (obra['id'],)).fetchone()
    mes_a_medir = None
    if obra['estado'] == 'en_ejecucion' and obra['inspector_id']:
        if ultimo is None:
            mes_a_medir = mes_de(obra['fecha_replanteo'])
        elif ultimo['estado'] == 'borrador':
            mes_a_medir = ultimo['mes']
        elif not ultimo['es_final'] and _certificado_de(c, ultimo['id']):
            mes_a_medir = sumar_meses(ultimo['mes'], 1)
        if mes_a_medir and mes_a_medir > mes_de(h):
            mes_a_medir = None
    sin_certificado = sum(1 for k in c.execute("SELECT id FROM computos WHERE obra_id=? AND estado='presentado'", (obra['id'],))
                          if _certificado_de(c, k['id']) is None)
    cert_pendientes = c.execute("SELECT COUNT(*) FROM certificados WHERE obra_id=? AND estado='pendiente'", (obra['id'],)).fetchone()[0]
    os_sin_responder = c.execute("SELECT COUNT(*) FROM ordenes_servicio WHERE obra_id=? AND estado='emitida'", (obra['id'],)).fetchone()[0]
    docs_por_revisar = sum(1 for d in _docs_vigentes(c, obra['id']) if d['revision_estado'] != 'aprobado')
    recepciones_por_revisar = c.execute("SELECT COUNT(*) FROM recepciones_hormigon WHERE obra_id=? AND revision_estado='pendiente'",
                                        (obra['id'],)).fetchone()[0]
    autorizado = c.execute("SELECT COALESCE(SUM(monto_cent),0) FROM certificados WHERE obra_id=? AND estado='autorizado'", (obra['id'],)).fetchone()[0]
    pendiente = c.execute("SELECT COALESCE(SUM(monto_cent),0) FROM certificados WHERE obra_id=? AND estado='pendiente'", (obra['id'],)).fetchone()[0]
    plazo = _plazo_info(c, obra)
    transcurrido = min(max((h - parse_fecha(obra['fecha_replanteo'], 'fecha_replanteo')).days, 0), obra['plazo_dias'])
    return {'monto_contrato_cent': monto, 'plazo': plazo, 'plazo_transcurrido_pct': pct(transcurrido, obra['plazo_dias']),
            'certificado_autorizado_cent': autorizado, 'certificado_pendiente_cent': pendiente,
            'a_certificar_cent': max(monto - autorizado - pendiente, 0), 'avance_financiero_pct': pct(autorizado, monto),
            'modificacion_pendiente': c.execute("SELECT 1 FROM curvas_ideal WHERE obra_id=? AND estado='pendiente'", (obra['id'],)).fetchone() is not None,
            'curva_vigente': (_curva_vigente(c, obra['id']) or {}).get('nombre'),
            'computos_sin_certificado': sin_certificado, 'certificados_pendientes': cert_pendientes,
            'certificados_aprobados': c.execute("SELECT COUNT(*) FROM certificados WHERE obra_id=? AND estado='aprobado'", (obra['id'],)).fetchone()[0],
            'certificados_devueltos': c.execute("SELECT COUNT(*) FROM certificados WHERE obra_id=? AND estado='devuelto'", (obra['id'],)).fetchone()[0],
            'os_sin_responder': os_sin_responder, 'documentos_por_revisar': docs_por_revisar, 'recepciones_por_revisar': recepciones_por_revisar,
            'fecha_fin': fin.strftime('%Y-%m-%d'), 'dias_restantes': (fin - h).days, 'vencida': h > fin,
            'plazo_cert_vencida_activo': _plazo_vencida_activo(c, obra['id']),
            'avance_ideal_pct': ideal_hoy[-1]['ideal_acum_pct'] if ideal_hoy else 0.0,
            'avance_certificado_pct': real[-1]['real_acum_pct'] if real else None,
            'avance_medido_pct': pct(medido, monto), 'desvio': cv['desvio'], 'mes_a_medir': mes_a_medir,
            'notas_sin_responder': c.execute("SELECT COUNT(*) FROM notas_pedido WHERE obra_id=? AND estado='pendiente'", (obra['id'],)).fetchone()[0],
            'mediciones_sin_validar': sum(1 for k in c.execute("SELECT id FROM computos WHERE obra_id=? AND estado='presentado'", (obra['id'],))
                                          if _firma_de(c, 'computo', k['id'], 'jefe_inspeccion') is None),
            'computo_en_curso': ultimo['id'] if ultimo is not None and ultimo['estado'] == 'borrador' else None}


def _datos_obra(c, obra):
    d = dict(obra)
    for campo, clave in (('inspector_id', 'inspector'), ('contratista_id', 'representante')):
        r = c.execute('SELECT id, nombre FROM usuarios WHERE id=?', (obra[campo],)).fetchone() if obra[campo] else None
        d[clave] = core.fila(r)
    return d


def listar_obras(c, ctx):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'ver_obras')
    out = []
    for r in c.execute('SELECT * FROM obras WHERE ente_id=? ORDER BY id', (ente['id'],)):
        obra = core.fila(r)
        if _visible(c, u, obra):
            out.append(dict(_datos_obra(c, obra), resumen=_resumen_contratista(c, obra) if u['rol'] == 'contratista' else _resumen(c, obra)))
    return 200, out


def ficha_obra(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    if u['rol'] == 'contratista':
        return 200, _ficha_contratista(c, obra)
    items = _items(c, oid)
    acum = _acumulado_previo(c, oid, '9999-99')
    filas = []
    for i in items:
        f = dict(i, importe_oferta_cent=importe(i['cantidad_oferta'], i['precio_oferta_cent']))
        if not i['es_titulo']:
            a = acum.get(i['id'], Decimal(0))
            f['medido_acumulado'] = q3(a)
            f['pct_medido'] = pct(a, i['cantidad_oferta'])
        filas.append(f)
    d = _datos_obra(c, obra)
    d.update(items=filas, resumen=_resumen(c, obra, items), curva=curva(c, obra, items), computos=_lista_computos(c, obra, items),
             certificados=[_resumen_certificado(c, core.fila(r)) for r in c.execute(
                 'SELECT * FROM certificados WHERE obra_id=? ORDER BY numero, id', (oid,))])
    return 200, d


def curva_obra(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    _exigir_interno(u)
    return 200, curva(c, obra)


# ---------- certificados (manual pp. 66-78) ----------

EXPEDIENTE_RE = re.compile(r'^\d{4}-\d{6}/\d{4}(/A\d{1,4})?$')  # formato del expediente SUAC: ____-______/____ o con anexo /A___
FIRMAS_REALES = 4  # el certificado real lleva cuatro firmas; la simulación muestra las que corresponden al circuito


def liquidar(importe_mes_cent, anticipo_pct, fondo_reparo_pct, fondo_inspeccion_pct, iva_pct=21):
    """Liquidación SIMPLIFICADA del certificado (el real tiene 17 conceptos). Todo en centavos enteros.
    anticipo, fondo de reparo y de inspección se descuentan del importe del mes; el IVA se calcula sobre el neto."""
    importe_mes = int(importe_mes_cent)
    anticipo = centavos(_d(importe_mes) * _d(anticipo_pct) / 100)
    reparo = centavos(_d(importe_mes) * _d(fondo_reparo_pct) / 100)
    inspeccion = centavos(_d(importe_mes) * _d(fondo_inspeccion_pct) / 100)
    neto = importe_mes - anticipo - reparo - inspeccion
    iva = centavos(_d(neto) * _d(iva_pct) / 100)
    return {'importe_mes_cent': importe_mes, 'descuento_anticipo_cent': anticipo, 'fondo_reparo_cent': reparo,
            'fondo_inspeccion_cent': inspeccion, 'neto_cent': neto, 'iva_cent': iva, 'a_abonar_cent': neto + iva,
            'anticipo_pct': anticipo_pct, 'fondo_reparo_pct': fondo_reparo_pct, 'fondo_inspeccion_pct': fondo_inspeccion_pct,
            'iva_pct': iva_pct}


def _importe_computo(c, kid, precios):
    return sum(importe(r['cantidad'], precios[r['item_id']])
               for r in c.execute('SELECT item_id, cantidad FROM computo_items WHERE computo_id=?', (kid,)))


def _cert_para(c, ctx, cid, permiso='ver_obras'):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    r = c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()
    if r is None:
        raise ApiError(404, 'certificado inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'certificado inexistente')
    return u, ente, obra, core.fila(r)


def _nombre(c, uid):
    r = c.execute('SELECT nombre FROM usuarios WHERE id=?', (uid,)).fetchone() if uid else None
    return r['nombre'] if r else None


def _resumen_certificado(c, cert):
    k = c.execute('SELECT mes FROM computos WHERE id=?', (cert['computo_id'],)).fetchone()
    liq = json.loads(cert['liquidacion_json'] or '{}')
    vencido = bool(cert['estado'] == 'autorizado' and cert['vence_at'] and hoy() > parse_fecha(cert['vence_at'], 'vence_at'))
    return {'id': cert['id'], 'numero': cert['numero'], 'tipo': cert['tipo'], 'estado': cert['estado'], 'mes': k['mes'],
            'monto_cent': cert['monto_cent'], 'a_abonar_cent': liq.get('a_abonar_cent'), 'expediente_externo': cert['expediente_externo'],
            'fecha_autorizacion': cert['fecha_autorizacion'], 'vence_at': cert['vence_at'], 'vencido': vencido}


def emitir_certificado(c, ctx, oid):
    """El inspector asignado certifica un cómputo presentado. Nace PENDIENTE (se imprime 'NO AUTORIZADO') y lo autoriza otra persona."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'emitir_certificado')
    obra = _obra(c, ente, oid)
    if not _visible(c, u, obra):
        raise ApiError(404, 'obra inexistente')
    b = ctx['body']
    k = c.execute('SELECT * FROM computos WHERE id=? AND obra_id=?', (b.get('computo_id'), oid)).fetchone()
    if k is None or k['estado'] != 'presentado':
        raise ApiError(409, 'sólo se emite el certificado de un cómputo presentado de esta obra')
    if _certificado_de(c, k['id']):
        raise ApiError(409, 'ese cómputo ya tiene su certificado')
    if hoy() > fecha_fin(obra) and not _plazo_vencida_activo(c, oid):
        raise ApiError(409, MSG_PLAZO_VENCIDO)
    antes = c.execute("SELECT id FROM computos WHERE obra_id=? AND estado!='eliminado' AND mes<?", (oid, k['mes'])).fetchall()
    if any(_certificado_de(c, a['id']) is None for a in antes):
        raise ApiError(409, 'hay meses anteriores sin certificado: se certifica en orden')
    items = _items(c, oid)
    precios = {i['id']: i['precio_oferta_cent'] for i in items}
    monto = _importe_computo(c, k['id'], precios)
    obs = (b.get('observaciones') or '').strip()[:1000] or None
    if monto == 0 and (obs is None or len(obs) < 5):
        raise ApiError(400, 'El monto a certificar es $ 0: las observaciones son obligatorias.')
    previos = c.execute("SELECT id FROM computos WHERE obra_id=? AND estado='presentado' AND mes<?", (oid, k['mes'])).fetchall()
    anterior = sum(_importe_computo(c, p['id'], precios) for p in previos)
    liq = liquidar(monto, obra['anticipo_pct'], obra['fondo_reparo_pct'], obra['fondo_inspeccion_pct'], cfg_obra(ente)['iva_pct'])
    liq.update(importe_acumulado_cent=anterior + monto, importe_anterior_cent=anterior,
               pct_avance_acumulado=pct(anterior + monto, monto_contrato_cent(items)), personal_afectado=k['personal_afectado'],
               es_final=bool(k['es_final']))
    numero = (c.execute("SELECT COALESCE(MAX(numero),0) FROM certificados WHERE obra_id=? AND estado!='anulado'", (oid,)).fetchone()[0]) + 1
    cur = c.execute(
        '''INSERT INTO certificados(obra_id,ente_id,numero,tipo,computo_id,estado,monto_cent,liquidacion_json,observaciones,emitido_por,emitido_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
        (oid, ente['id'], numero, 'final' if k['es_final'] else 'parcial', k['id'], 'pendiente', monto,
         json.dumps(liq), obs, u['id'], core.iso(core.now())))
    core.audit(c, ente['id'], 'certificado', cur.lastrowid, 'emitido', u['id'], {'numero': numero, 'mes': k['mes'], 'monto_cent': monto})
    _registrar_firma(c, ente['id'], 'certificado', cur.lastrowid, 'emitido', u)
    return 201, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cur.lastrowid,)).fetchone()))


def _ficha_certificado(c, obra, cert):
    k = core.fila(c.execute('SELECT * FROM computos WHERE id=?', (cert['computo_id'],)).fetchone())
    d = _resumen_certificado(c, cert)
    d.update(
        obra={'id': obra['id'], 'denominacion': obra['denominacion'], 'contratista': obra['contratista'], 'fecha_replanteo': obra['fecha_replanteo'],
              'plazo_dias': obra['plazo_dias']},
        liquidacion=json.loads(cert['liquidacion_json'] or '{}'), observaciones=cert['observaciones'],
        leyenda={'pendiente': 'NO AUTORIZADO', 'aprobado': 'NO AUTORIZADO', 'devuelto': 'NO AUTORIZADO', 'anulado': 'ANULADO'}.get(cert['estado']), emitido_at=cert['emitido_at'],
        motivo_anulacion=cert['motivo_anulacion'],
        firmas=[{'cargo': 'Inspector de obra (mide y certifica)', 'nombre': _nombre(c, cert['emitido_por']), 'fecha': cert['emitido_at']},
                {'cargo': 'Jefe de certificaciones (aprueba)', 'nombre': _nombre(c, cert['aprobado_por']), 'fecha': cert['aprobado_at']},
                {'cargo': 'Autoridad (autoriza)', 'nombre': _nombre(c, cert['autorizado_por']), 'fecha': cert['autorizado_at']}],
        nota_firmas=f'Firmas simuladas: el certificado real lleva {FIRMAS_REALES}.', registro=_firmas(c, 'certificado', cert['id']))
    return d


def ficha_certificado(c, ctx, cid):
    u, ente, obra, cert = _cert_para(c, ctx, cid)
    if u['rol'] == 'contratista' and cert['estado'] == 'anulado':
        raise ApiError(404, 'certificado inexistente')
    return 200, _ficha_certificado(c, obra, cert)


def listar_certificados(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    _exigir_interno(u)
    return 200, [_resumen_certificado(c, core.fila(r)) for r in c.execute('SELECT * FROM certificados WHERE obra_id=? ORDER BY numero, id', (oid,))]


def _resolver_certificado(c, ctx, cid):
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'aprobar_certificado')
    if cert['estado'] != 'pendiente':
        raise ApiError(409, f'el certificado está {cert["estado"]}: sólo se aprueba o devuelve uno pendiente de revisión')
    if u['id'] == cert['emitido_por']:
        raise ApiError(403, 'quien emite un certificado no puede aprobarlo ni devolverlo')
    return u, ente, obra, cert


def aprobar_certificado(c, ctx, cid):
    """El jefe de certificaciones aprueba el certificado emitido por el inspector. Queda esperando la autorización de la autoridad."""
    u, ente, obra, cert = _resolver_certificado(c, ctx, cid)
    c.execute("UPDATE certificados SET estado='aprobado', aprobado_por=?, aprobado_at=? WHERE id=?", (u['id'], core.iso(core.now()), cid))
    _registrar_firma(c, ente['id'], 'certificado', cid, 'aprobado', u)
    core.audit(c, ente['id'], 'certificado', cid, 'aprobado', u['id'], {'numero': cert['numero']})
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def devolver_certificado(c, ctx, cid):
    """El jefe de certificaciones devuelve el certificado al inspector con un motivo. El inspector lo anula (si es el último) y emite uno nuevo."""
    u, ente, obra, cert = _resolver_certificado(c, ctx, cid)
    motivo = core.texto(ctx['body'].get('motivo'), 10, 500, 'motivo de la devolución')
    c.execute("UPDATE certificados SET estado='devuelto' WHERE id=?", (cid,))
    _registrar_firma(c, ente['id'], 'certificado', cid, 'devuelto', u, motivo)
    core.audit(c, ente['id'], 'certificado', cid, 'devuelto', u['id'], {'numero': cert['numero'], 'motivo': motivo})
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def autorizar_certificado(c, ctx, cid):
    """La autoridad con rol habilitante autoriza: desde esa fecha corre el vencimiento y ya no se puede anular."""
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'autorizar_certificado')
    if cert['estado'] != 'aprobado':
        raise ApiError(409, f'el certificado está {cert["estado"]}: sólo se autoriza uno aprobado por el jefe de certificaciones')
    if u['id'] in (cert['emitido_por'], cert['aprobado_por']):
        raise ApiError(403, 'quien emite o aprueba un certificado no puede autorizarlo')
    fecha_txt = ctx['body'].get('fecha_autorizacion')
    fecha = parse_fecha(fecha_txt, 'fecha_autorizacion') if fecha_txt else hoy()
    if fecha > hoy():
        raise ApiError(400, 'la fecha de autorización no puede ser futura')
    if fecha < parse_fecha(cert['emitido_at'][:10], 'emitido_at'):
        raise ApiError(400, 'la fecha de autorización no puede ser anterior a la emisión del certificado')
    vence = fecha + timedelta(days=obra['plazo_venc_cert_dias'])
    c.execute("""UPDATE certificados SET estado='autorizado', autorizado_por=?, autorizado_at=?, fecha_autorizacion=?, vence_at=? WHERE id=?""",
              (u['id'], core.iso(core.now()), fecha.strftime('%Y-%m-%d'), vence.strftime('%Y-%m-%d'), cid))
    core.audit(c, ente['id'], 'certificado', cid, 'autorizado', u['id'], {'fecha': fecha.strftime('%Y-%m-%d'), 'vence': vence.strftime('%Y-%m-%d')})
    _registrar_firma(c, ente['id'], 'certificado', cid, 'autorizado', u)
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def anular_certificado(c, ctx, cid):
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'anular_certificado')
    if cert['estado'] == 'autorizado':
        raise ApiError(409, 'un certificado autorizado ya no se puede anular')
    if cert['estado'] not in ('pendiente', 'aprobado', 'devuelto'):
        raise ApiError(409, f'el certificado ya está {cert["estado"]}')
    ultimo = c.execute("SELECT MAX(numero) FROM certificados WHERE obra_id=? AND estado!='anulado'", (obra['id'],)).fetchone()[0]
    if cert['numero'] != ultimo:
        raise ApiError(409, 'Sólo se puede anular el último certificado generado. Anulá primero el posterior.')
    motivo = core.texto(ctx['body'].get('motivo'), 5, 500, 'motivo')
    c.execute("UPDATE certificados SET estado='anulado', anulado_por=?, anulado_at=?, motivo_anulacion=? WHERE id=?",
              (u['id'], core.iso(core.now()), motivo, cid))
    core.audit(c, ente['id'], 'certificado', cid, 'anulado', u['id'], {'motivo': motivo})
    _registrar_firma(c, ente['id'], 'certificado', cid, 'anulado', u, motivo)
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def asignar_expediente(c, ctx, cid):
    """Asigna el número de expediente. No puede repetirse entre certificados, salvo certificados de $ 0."""
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'asignar_expediente')
    if cert['estado'] == 'anulado':
        raise ApiError(409, 'no se asigna expediente a un certificado anulado')
    exp = str(ctx['body'].get('expediente') or '').strip()
    if not EXPEDIENTE_RE.match(exp):
        raise ApiError(400, 'el expediente debe tener el formato 0000-000000/0000 (opcional /A000 para un anexo)')
    for otro in c.execute("SELECT id, monto_cent FROM certificados WHERE obra_id=? AND expediente_externo=? AND estado!='anulado' AND id!=?",
                          (obra['id'], exp, cid)):
        if cert['monto_cent'] != 0 or otro['monto_cent'] != 0:
            raise ApiError(409, 'ese expediente ya está asignado a otro certificado (sólo pueden compartirlo los certificados de $ 0)')
    c.execute('UPDATE certificados SET expediente_externo=? WHERE id=?', (exp, cid))
    core.audit(c, ente['id'], 'certificado', cid, 'expediente', u['id'], {'expediente': exp})
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


# ---------- modificaciones de la obra (manual pp. 51-60): curva ideal (MCI) y ampliación de plazo (AP) ----------

def _plazo_info(c, obra):
    amp = c.execute("SELECT COALESCE(SUM(dias),0) FROM curvas_ideal WHERE obra_id=? AND tipo='AP'", (obra['id'],)).fetchone()[0]
    return {'original_dias': obra['plazo_dias'] - amp, 'ampliaciones_dias': amp, 'total_dias': obra['plazo_dias']}


def _mod_dict(c, cv, vigente_id):
    d = core.fila(cv)
    d['creada_por_nombre'] = _nombre(c, d['creada_por'])
    d['autorizada_por_nombre'] = _nombre(c, d['autorizada_por'])
    d['vigente'] = d['id'] == vigente_id
    return d


def listar_modificaciones(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    _exigir_interno(u)
    v = _curva_vigente(c, oid)
    mods = [_mod_dict(c, r, v['id'] if v else None) for r in c.execute(
        "SELECT * FROM curvas_ideal WHERE obra_id=? AND tipo!='principal' ORDER BY id DESC", (oid,))]
    return 200, {'modificaciones': mods, 'plazo': _plazo_info(c, obra), 'vigente': v['nombre'] if v else None,
                 'hay_pendiente': any(m['estado'] == 'pendiente' for m in mods)}


def curva_ideal_vigente(c, ctx, oid):
    """La curva en vigor, ítem por ítem y mes por mes: es la planilla de partida para cargar una modificación."""
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    _exigir_interno(u)
    v = _curva_vigente(c, oid)
    cant = {}
    for r in c.execute('SELECT item_id, mes, cantidad FROM avance_ideal WHERE curva_id=?', (v['id'],)):
        cant.setdefault(r['item_id'], {})[r['mes']] = r['cantidad']
    return 200, {'curva': v['nombre'], 'meses': sorted({m for d in cant.values() for m in d}),
                 'items': [{'nro': i['nro'], 'descripcion': i['descripcion'], 'unidad': i['unidad'], 'cantidad_oferta': i['cantidad_oferta'],
                            'ideal': cant.get(i['id'], {})} for i in _items(c, oid) if not i['es_titulo']]}


def cargar_modificacion(c, ctx, oid):
    """Una modificación de curva ideal (queda pendiente hasta que la autoriza la autoridad) o una ampliación de plazo
    (suma días de inmediato). Ambas traen una curva nueva: lo que no se informa se copia de la curva vigente."""
    u, ente, obra = _obra_para(c, ctx, 'cargar_modificacion', oid)
    b = ctx['body']
    tipo = b.get('tipo')
    if tipo not in ('MCI', 'AP'):
        raise ApiError(400, 'tipo debe ser MCI (modificación de curva ideal) o AP (ampliación de plazo)')
    tramite = core.texto(b.get('tramite'), 3, 40, 'número de trámite')
    instrumento = core.texto(b.get('instrumento'), 3, 80, 'instrumento legal')
    if tipo == 'MCI' and not instrumento.lower().startswith('acta acuerdo'):
        raise ApiError(400, 'el instrumento legal de una modificación de curva ideal es un Acta Acuerdo')
    if c.execute("SELECT 1 FROM curvas_ideal WHERE obra_id=? AND estado='pendiente'", (oid,)).fetchone():
        raise ApiError(409, 'ya hay una modificación pendiente de autorización: se autoriza antes de cargar otra')
    dias = None
    if tipo == 'AP':
        dias = core.numero_opcional(b.get('dias'), 1, 1000, 'días')
        if dias is None or int(dias) != dias:
            raise ApiError(400, 'una ampliación de plazo necesita los días (número entero entre 1 y 1000)')
        dias = int(dias)
    items = {i['nro']: i for i in _items(c, oid) if not i['es_titulo']}
    vigente = _curva_vigente(c, oid)
    qty = {}
    for r in c.execute('SELECT item_id, mes, cantidad FROM avance_ideal WHERE curva_id=?', (vigente['id'],)):
        qty.setdefault(r['item_id'], {})[r['mes']] = r['cantidad']
    for nro, meses in (b.get('ideal') or {}).items():
        item = items.get(int(nro)) if str(nro).lstrip('-').isdigit() else None
        if item is None:
            raise ApiError(400, f'el ítem {nro} no existe en la obra o es un rubro')
        nuevo = {}
        for mes, cantidad in (meses or {}).items():
            if not re.match(r'^\d{4}-(0[1-9]|1[0-2])$', str(mes)):
                raise ApiError(400, 'los meses de la curva deben ser AAAA-MM')
            cantidad = core.numero_opcional(cantidad, 0, 1e9, f'cantidad del ítem {nro}')
            if cantidad:
                nuevo[mes] = q3(cantidad)
        if sum(_d(v) for v in nuevo.values()) > _d(item['cantidad_oferta']) + Decimal('0.0005'):
            raise ApiError(400, f'el avance ideal del ítem {nro} suma más que la cantidad contratada ({num_txt(item["cantidad_oferta"])})')
        qty[item['id']] = nuevo
    n = c.execute('SELECT COUNT(*) FROM curvas_ideal WHERE obra_id=? AND tipo=?', (oid, tipo)).fetchone()[0] + 1
    nombre = f'MCI-{n}' if tipo == 'MCI' else f'AmpP-{n}'
    estado = 'pendiente' if tipo == 'MCI' else 'autorizada'
    ahora = core.iso(core.now())
    cur = c.execute('''INSERT INTO curvas_ideal(obra_id,nombre,tipo,numero,estado,tramite,instrumento,dias,observaciones,creada_por,creada_at,
                       autorizada_por,autorizada_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (oid, nombre, tipo, n, estado, tramite, instrumento, dias, (b.get('observaciones') or '').strip()[:500] or None, u['id'], ahora,
                     u['id'] if estado == 'autorizada' else None, ahora if estado == 'autorizada' else None))
    for item_id, meses in qty.items():
        for mes, cantidad in meses.items():
            c.execute('INSERT INTO avance_ideal(curva_id,item_id,mes,cantidad) VALUES(?,?,?,?)', (cur.lastrowid, item_id, mes, cantidad))
    if tipo == 'AP':
        c.execute('UPDATE obras SET plazo_dias=plazo_dias+? WHERE id=?', (dias, oid))
    core.audit(c, ente['id'], 'obra', oid, 'modificacion_cargada', u['id'], {'curva': nombre, 'tramite': tramite, 'dias': dias})
    return 201, _mod_dict(c, c.execute('SELECT * FROM curvas_ideal WHERE id=?', (cur.lastrowid,)).fetchone(), (_curva_vigente(c, oid) or {}).get('id'))


def num_txt(x):
    return ('%g' % x).replace('.', ',')


def autorizar_modificacion(c, ctx, cid):
    """La autoridad autoriza una modificación de curva ideal; hasta entonces no está vigente."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'autorizar_modificacion')
    cv = c.execute('SELECT * FROM curvas_ideal WHERE id=?', (cid,)).fetchone()
    if cv is None or cv['tipo'] == 'principal':
        raise ApiError(404, 'modificación inexistente')
    obra = _obra(c, ente, cv['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'modificación inexistente')
    if cv['estado'] != 'pendiente':
        raise ApiError(409, 'la modificación ya está autorizada')
    if cv['creada_por'] == u['id']:
        raise ApiError(403, 'quien carga una modificación no puede autorizarla')
    c.execute("UPDATE curvas_ideal SET estado='autorizada', autorizada_por=?, autorizada_at=? WHERE id=?", (u['id'], core.iso(core.now()), cid))
    core.audit(c, ente['id'], 'obra', obra['id'], 'modificacion_autorizada', u['id'], {'curva': cv['nombre']})
    return 200, _mod_dict(c, c.execute('SELECT * FROM curvas_ideal WHERE id=?', (cid,)).fetchone(), _curva_vigente(c, obra['id'])['id'])


# ---------- libro de obra (SIGUE): órdenes de servicio ----------

def _os_dict(c, r):
    d = core.fila(r)
    for campo in ('emitida_por', 'respondida_por', 'cerrada_por'):
        d[campo + '_nombre'] = _nombre(c, d[campo])
    return d


def _os_para(c, ctx, osid, permiso):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    r = c.execute('SELECT * FROM ordenes_servicio WHERE id=?', (osid,)).fetchone()
    if r is None:
        raise ApiError(404, 'orden de servicio inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'orden de servicio inexistente')
    return u, ente, obra, core.fila(r)


def listar_os(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    return 200, [_os_dict(c, r) for r in c.execute('SELECT * FROM ordenes_servicio WHERE obra_id=? ORDER BY numero', (oid,))]


def emitir_os(c, ctx, oid):
    """El inspector asignado emite una orden de servicio. Se numeran sin saltos y no se editan ni se borran."""
    u, ente, obra = _obra_para(c, ctx, 'emitir_os', oid)
    b = ctx['body']
    asunto = core.texto(b.get('asunto'), 3, 120, 'asunto')
    cuerpo = core.texto(b.get('texto'), 10, 2000, 'texto')
    numero = c.execute('SELECT COALESCE(MAX(numero),0)+1 FROM ordenes_servicio WHERE obra_id=?', (oid,)).fetchone()[0]
    cur = c.execute('''INSERT INTO ordenes_servicio(obra_id,ente_id,numero,fecha,asunto,texto,emitida_por,emitida_at,estado)
                       VALUES(?,?,?,?,?,?,?,?,?)''',
                    (oid, ente['id'], numero, hoy().strftime('%Y-%m-%d'), asunto, cuerpo, u['id'], core.iso(core.now()), 'emitida'))
    core.audit(c, ente['id'], 'orden_servicio', cur.lastrowid, 'emitida', u['id'], {'numero': numero, 'obra': oid})
    _registrar_firma(c, ente['id'], 'orden_servicio', cur.lastrowid, 'emitida', u)
    return 201, _os_dict(c, c.execute('SELECT * FROM ordenes_servicio WHERE id=?', (cur.lastrowid,)).fetchone())


def responder_os(c, ctx, osid):
    """El contratista responde (descargo) una sola vez."""
    u, ente, obra, o = _os_para(c, ctx, osid, 'responder_os')
    if o['estado'] != 'emitida':
        raise ApiError(409, 'la orden de servicio ya fue respondida')
    resp = core.texto(ctx['body'].get('texto'), 5, 2000, 'respuesta')
    c.execute("UPDATE ordenes_servicio SET estado='respondida', respuesta=?, respondida_por=?, respondida_at=? WHERE id=?",
              (resp, u['id'], core.iso(core.now()), osid))
    core.audit(c, ente['id'], 'orden_servicio', osid, 'respondida', u['id'], {'numero': o['numero']})
    _registrar_firma(c, ente['id'], 'orden_servicio', osid, 'respondida', u)
    return 200, _os_dict(c, c.execute('SELECT * FROM ordenes_servicio WHERE id=?', (osid,)).fetchone())


def cerrar_os(c, ctx, osid):
    u, ente, obra, o = _os_para(c, ctx, osid, 'cerrar_os')
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'orden de servicio inexistente')
    if o['estado'] != 'respondida':
        raise ApiError(409, 'sólo se cierra una orden de servicio que ya fue respondida')
    obs = (ctx['body'].get('observacion') or '').strip()[:1000] or None
    c.execute("UPDATE ordenes_servicio SET estado='cerrada', cerrada_por=?, cerrada_at=?, cierre_obs=? WHERE id=?",
              (u['id'], core.iso(core.now()), obs, osid))
    core.audit(c, ente['id'], 'orden_servicio', osid, 'cerrada', u['id'], {'numero': o['numero']})
    _registrar_firma(c, ente['id'], 'orden_servicio', osid, 'cerrada', u, obs)
    return 200, _os_dict(c, c.execute('SELECT * FROM ordenes_servicio WHERE id=?', (osid,)).fetchone())


# ---------- notas de pedido (la contratista pide; la inspección responde) ----------
# Numeradas sin saltos por obra, inmutables y respondidas una sola vez (ver schema.sql). La orden de servicio va en el sentido inverso.

TIPOS_NOTA = {'ampliacion_plazo': 'Ampliación de plazo', 'modificacion_obra': 'Modificación de obra', 'otra': 'Otra'}


def _exigir_interno(u):
    """Lo que la contratista no ve: cómputos, certificados, curvas, modificaciones y legajo. Ve su obra, sus notas y sus órdenes de servicio."""
    if u['rol'] == 'contratista':
        raise ApiError(404, 'recurso inexistente')


def _registrar_firma(c, ente_id, entidad, eid, accion, u, motivo=None):
    """Registro de firmas: quién, con qué rol, cuándo y motivo. Sólo se agrega."""
    c.execute('INSERT INTO firmas_registro(ente_id,entidad,entidad_id,accion,usuario_id,rol,fecha,motivo) VALUES(?,?,?,?,?,?,?,?)',
              (ente_id, entidad, eid, accion, u['id'], u['rol'], core.iso(core.now()), motivo))


def _firmas(c, entidad, eid):
    return [{'accion': r['accion'], 'rol': r['rol'], 'nombre': _nombre(c, r['usuario_id']), 'fecha': r['fecha'], 'motivo': r['motivo']}
            for r in c.execute('SELECT * FROM firmas_registro WHERE entidad=? AND entidad_id=? ORDER BY id', (entidad, eid))]


def _nota_dict(c, r):
    d = core.fila(r)
    d['tipo_nombre'] = TIPOS_NOTA[d['tipo']]
    d['solicitada_por_nombre'] = _nombre(c, d['solicitada_por'])
    d['respondida_por_nombre'] = _nombre(c, d['respondida_por'])
    return d


def _nota_para(c, ctx, nid, permiso):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    r = c.execute('SELECT * FROM notas_pedido WHERE id=?', (nid,)).fetchone()
    if r is None:
        raise ApiError(404, 'nota de pedido inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'nota de pedido inexistente')
    return u, ente, obra, core.fila(r)


def listar_notas(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    return 200, [_nota_dict(c, r) for r in c.execute('SELECT * FROM notas_pedido WHERE obra_id=? ORDER BY numero', (oid,))]


def solicitar_nota(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, 'solicitar_nota', oid)
    b = ctx['body']
    tipo = b.get('tipo')
    if tipo not in TIPOS_NOTA:
        raise ApiError(400, 'tipo debe ser uno de: ' + ', '.join(TIPOS_NOTA))
    motivo = core.texto(b.get('motivo'), 10, 2000, 'motivo')
    numero = c.execute('SELECT COALESCE(MAX(numero),0)+1 FROM notas_pedido WHERE obra_id=?', (oid,)).fetchone()[0]
    cur = c.execute('''INSERT INTO notas_pedido(obra_id,ente_id,numero,tipo,motivo,solicitada_por,solicitada_at,estado)
                       VALUES(?,?,?,?,?,?,?,?)''', (oid, ente['id'], numero, tipo, motivo, u['id'], core.iso(core.now()), 'pendiente'))
    _registrar_firma(c, ente['id'], 'nota_pedido', cur.lastrowid, 'solicitada', u)
    core.audit(c, ente['id'], 'nota_pedido', cur.lastrowid, 'solicitada', u['id'], {'numero': numero, 'tipo': tipo})
    return 201, _nota_dict(c, c.execute('SELECT * FROM notas_pedido WHERE id=?', (cur.lastrowid,)).fetchone())


def responder_nota(c, ctx, nid):
    u, ente, obra, n = _nota_para(c, ctx, nid, 'responder_nota')
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'nota de pedido inexistente')
    if n['estado'] != 'pendiente':
        raise ApiError(409, 'la nota de pedido ya fue respondida')
    resp = core.texto(ctx['body'].get('respuesta'), 5, 2000, 'respuesta')
    c.execute("UPDATE notas_pedido SET estado='respondida', respuesta=?, respondida_por=?, respondida_at=? WHERE id=?",
              (resp, u['id'], core.iso(core.now()), nid))
    _registrar_firma(c, ente['id'], 'nota_pedido', nid, 'respondida', u)
    core.audit(c, ente['id'], 'nota_pedido', nid, 'respondida', u['id'], {'numero': n['numero']})
    return 200, _nota_dict(c, c.execute('SELECT * FROM notas_pedido WHERE id=?', (nid,)).fetchone())


def _resumen_contratista(c, obra):
    """Lo único que ve la contratista de los plazos y pendientes: sin montos, certificados ni curvas."""
    fin, h = fecha_fin(obra), hoy()
    firmas_pend = sum(1 for k in c.execute("SELECT id FROM computos WHERE obra_id=? AND estado='presentado'", (obra['id'],))
                      if _firma_de(c, 'computo', k['id'], 'contratista') is None)
    firmas_pend += sum(1 for ce in c.execute("SELECT id FROM certificados WHERE obra_id=? AND estado!='anulado'", (obra['id'],))
                       if _firma_de(c, 'certificado', ce['id'], 'contratista') is None)
    return {'plazo': _plazo_info(c, obra), 'fecha_fin': fin.strftime('%Y-%m-%d'), 'dias_restantes': (fin - h).days, 'vencida': h > fin,
            'firmas_pendientes': firmas_pend,
            'os_sin_responder': c.execute("SELECT COUNT(*) FROM ordenes_servicio WHERE obra_id=? AND estado='emitida'", (obra['id'],)).fetchone()[0],
            'notas_sin_responder': c.execute("SELECT COUNT(*) FROM notas_pedido WHERE obra_id=? AND estado='pendiente'", (obra['id'],)).fetchone()[0]}


def _ficha_contratista(c, obra):
    d = _datos_obra(c, obra)
    d.update(items=[], computos=[], certificados=[], resumen=_resumen_contratista(c, obra),
             curva={'puntos': [], 'curvas': [], 'real': [], 'desvio': {'valor': None, 'n': 0, 'nota': ''}})
    return d

# ---------- documentación técnica (SIGUE): legajo con versiones, etiquetas y observaciones ----------

TIPOS_DOC = ('plano', 'material', 'dosificacion', 'ensayo', 'calibracion', 'planilla', 'otro')
ARCHIVO_RE = re.compile(r'^data:(application/pdf|image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=\s]+)$')
EXT_ARCHIVO = {'application/pdf': 'pdf', 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}


def guardar_archivo(dataurl):
    """Como guardar_foto, pero acepta también PDF. Se guarda por hash: el mismo contenido es el mismo archivo."""
    if not isinstance(dataurl, str):
        raise ApiError(400, 'falta el archivo')
    m = ARCHIVO_RE.match(dataurl)
    if not m:
        raise ApiError(400, 'el archivo debe ser PDF, JPEG, PNG o WebP codificado en base64')
    try:
        data = core.base64.b64decode(m.group(2))
    except core.binascii.Error:
        raise ApiError(400, 'base64 inválido')
    if len(data) > core.MAX_FOTO_BYTES:
        raise ApiError(413, 'el archivo supera 3 MB')
    if m.group(1) == 'application/pdf' and not data.startswith(b'%PDF-'):
        raise ApiError(400, 'el archivo no es un PDF válido')
    sha = core.hashlib.sha256(data).hexdigest()
    ruta = f'{sha}.{EXT_ARCHIVO[m.group(1)]}'
    core.os.makedirs(core.FOTOS_DIR, exist_ok=True)
    camino = core.os.path.join(core.FOTOS_DIR, ruta)
    if not core.os.path.exists(camino):
        with open(camino, 'wb') as f:
            f.write(data)
    return sha, ruta, m.group(1)


def _fecha_opcional(valor, campo):
    return parse_fecha(valor, campo).strftime('%Y-%m-%d') if valor else None


def _docs_vigentes(c, oid, tipo=None):
    """Las versiones vigentes: las que ninguna versión posterior reemplazó."""
    sql = '''SELECT * FROM documentos_obra d WHERE obra_id=? AND NOT EXISTS (SELECT 1 FROM documentos_obra x WHERE x.reemplaza_a=d.id)'''
    return [core.fila(r) for r in c.execute(sql + (' AND tipo=?' if tipo else '') + ' ORDER BY id', (oid, tipo) if tipo else (oid,))]


def _doc_dict(c, d, ente):
    alerta = cfg_obra(ente)['alerta_vencimiento_dias']
    h = hoy()
    out = dict(d)
    out['etiquetas'] = json.loads(d['etiquetas'] or '[]')
    out['datos'] = json.loads(d['datos_json'] or '{}')
    out.pop('datos_json', None)
    out['url'] = f'/api/fotos/{d["ruta"]}'
    venc = parse_fecha(d['fecha_vencimiento'], 'fecha_vencimiento') if d['fecha_vencimiento'] else None
    out['vencido'] = bool(venc and venc < h)
    out['por_vencer'] = bool(venc and h <= venc <= h + timedelta(days=alerta))
    out['dias_para_vencer'] = (venc - h).days if venc else None
    out['vigente'] = c.execute('SELECT 1 FROM documentos_obra WHERE reemplaza_a=?', (d['id'],)).fetchone() is None
    out['subido_por_nombre'] = _nombre(c, d['subido_por'])
    out['revisado_por_nombre'] = _nombre(c, d['revisado_por'])
    obs = [core.fila(r) for r in c.execute('SELECT * FROM observaciones_documento WHERE documento_id=? ORDER BY id', (d['id'],))]
    for o in obs:
        o['autor_nombre'] = _nombre(c, o['autor_id'])
        o['resuelta'] = bool(o['resuelta'])
    out['observaciones'] = obs
    out['observaciones_abiertas'] = sum(1 for o in obs if not o['resuelta'])
    return out


def _doc_para(c, ctx, did, permiso='ver_obras'):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    r = c.execute('SELECT * FROM documentos_obra WHERE id=?', (did,)).fetchone()
    if r is None:
        raise ApiError(404, 'documento inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'documento inexistente')
    return u, ente, obra, core.fila(r)


def listar_documentos(c, ctx, oid):
    """Filtros (query): tipo, etiqueta, estado (pendiente, observado, aprobado, vencido) y todas=1 para incluir versiones anteriores."""
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    q = ctx['q']
    filas = [core.fila(r) for r in c.execute('SELECT * FROM documentos_obra WHERE obra_id=? ORDER BY id', (oid,))]
    docs = [_doc_dict(c, d, ente) for d in filas]
    if q.get('todas') != '1':
        docs = [d for d in docs if d['vigente']]
    if q.get('tipo'):
        docs = [d for d in docs if d['tipo'] == q['tipo']]
    if q.get('etiqueta'):
        docs = [d for d in docs if q['etiqueta'].lower() in [e.lower() for e in d['etiquetas']]]
    if q.get('estado') == 'vencido':
        docs = [d for d in docs if d['vencido']]
    elif q.get('estado'):
        docs = [d for d in docs if d['revision_estado'] == q['estado']]
    return 200, docs


def subir_documento(c, ctx, oid):
    """El contratista o el inspector suben un documento; una versión nueva reemplaza (sin borrar) a la anterior."""
    u, ente, obra = _obra_para(c, ctx, 'subir_documento', oid)
    cfg, b = cfg_obra(ente), ctx['body']
    tipo = b.get('tipo')
    if tipo not in TIPOS_DOC:
        raise ApiError(400, 'tipo de documento no válido: ' + ', '.join(TIPOS_DOC))
    titulo = core.texto(b.get('titulo'), 3, 120, 'título')
    etiquetas = []
    for e in b.get('etiquetas') or []:
        e = core.texto(e, 1, 30, 'etiqueta')
        if e.lower() not in [x.lower() for x in etiquetas]:
            etiquetas.append(e)
    if len(etiquetas) > 10:
        raise ApiError(400, 'hasta 10 etiquetas por documento')
    emision, venc = _fecha_opcional(b.get('fecha_emision'), 'fecha_emision'), _fecha_opcional(b.get('fecha_vencimiento'), 'fecha_vencimiento')
    if emision and venc and venc < emision:
        raise ApiError(400, 'el vencimiento no puede ser anterior a la emisión')
    clave = (b.get('clave') or '').strip()[:40] or None
    datos = {}
    if tipo == 'dosificacion':
        if clave not in cfg['hormigon']['tipos']:
            raise ApiError(400, 'la dosificación necesita el tipo de hormigón: ' + ', '.join(cfg['hormigon']['tipos']))
        asent = core.numero_opcional((b.get('datos') or {}).get('asentamiento_cm'), 0, 30, 'asentamiento de diseño (cm)')
        if asent is None:
            raise ApiError(400, 'la dosificación necesita el asentamiento de diseño en cm')
        tol = core.numero_opcional((b.get('datos') or {}).get('tolerancia_cm'), 0, 15, 'tolerancia (cm)')
        datos = {'asentamiento_cm': asent, 'tolerancia_cm': cfg['hormigon']['asentamiento_tolerancia_cm'] if tol is None else tol}
    version, reemplaza = 1, b.get('reemplaza_a')
    if reemplaza is not None:
        ant = c.execute('SELECT * FROM documentos_obra WHERE id=? AND obra_id=? AND tipo=?', (reemplaza, oid, tipo)).fetchone()
        if ant is None:
            raise ApiError(400, 'reemplaza_a debe ser un documento de esta obra del mismo tipo')
        if c.execute('SELECT 1 FROM documentos_obra WHERE reemplaza_a=?', (reemplaza,)).fetchone():
            raise ApiError(409, 'ese documento ya tiene una versión más nueva')
        version = ant['version'] + 1
    sha, ruta, mime = guardar_archivo(b.get('archivo'))
    cur = c.execute(
        '''INSERT INTO documentos_obra(obra_id,ente_id,tipo,titulo,clave,datos_json,etiquetas,sha256,ruta,mime,fecha_emision,
           fecha_vencimiento,version,reemplaza_a,subido_por,subido_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (oid, ente['id'], tipo, titulo, clave, json.dumps(datos), json.dumps(etiquetas, ensure_ascii=False), sha, ruta, mime, emision, venc,
         version, reemplaza, u['id'], core.iso(core.now())))
    core.audit(c, ente['id'], 'documento', cur.lastrowid, 'subido', u['id'], {'tipo': tipo, 'version': version})
    return 201, _doc_dict(c, core.fila(c.execute('SELECT * FROM documentos_obra WHERE id=?', (cur.lastrowid,)).fetchone()), ente)


def _revisor_de(c, u, obra, doc):
    """Sólo el inspector asignado revisa, y nunca lo que cargó él mismo."""
    if obra['inspector_id'] != u['id']:
        raise ApiError(404, 'documento inexistente')
    if doc['subido_por'] == u['id']:
        raise ApiError(403, 'quien carga un documento no puede revisarlo')


def agregar_observacion(c, ctx, did):
    u, ente, obra, d = _doc_para(c, ctx, did, 'revisar_documento')
    _revisor_de(c, u, obra, d)
    if not _doc_dict(c, d, ente)['vigente']:
        raise ApiError(409, 'ese documento ya tiene una versión más nueva: se observa la vigente')
    texto = core.texto(ctx['body'].get('texto'), 5, 1000, 'observación')
    cur = c.execute('INSERT INTO observaciones_documento(documento_id,texto,autor_id,creada_at) VALUES(?,?,?,?)',
                    (did, texto, u['id'], core.iso(core.now())))
    c.execute("UPDATE documentos_obra SET revision_estado='observado', revisado_por=?, revisado_at=? WHERE id=?",
              (u['id'], core.iso(core.now()), did))
    core.audit(c, ente['id'], 'documento', did, 'observado', u['id'], {'observacion': texto, 'id': cur.lastrowid})
    return 201, _doc_dict(c, core.fila(c.execute('SELECT * FROM documentos_obra WHERE id=?', (did,)).fetchone()), ente)


def resolver_observacion(c, ctx, obsid):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'revisar_documento')
    o = c.execute('SELECT * FROM observaciones_documento WHERE id=?', (obsid,)).fetchone()
    if o is None:
        raise ApiError(404, 'observación inexistente')
    ctx2 = dict(ctx)
    u, ente, obra, d = _doc_para(c, ctx2, o['documento_id'], 'revisar_documento')
    _revisor_de(c, u, obra, d)
    if o['resuelta']:
        raise ApiError(409, 'la observación ya está resuelta')
    c.execute('UPDATE observaciones_documento SET resuelta=1, resuelta_por=?, resuelta_at=? WHERE id=?', (u['id'], core.iso(core.now()), obsid))
    core.audit(c, ente['id'], 'documento', d['id'], 'observacion_resuelta', u['id'], {'id': obsid})
    return 200, _doc_dict(c, core.fila(c.execute('SELECT * FROM documentos_obra WHERE id=?', (d['id'],)).fetchone()), ente)


def aprobar_documento(c, ctx, did):
    u, ente, obra, d = _doc_para(c, ctx, did, 'revisar_documento')
    _revisor_de(c, u, obra, d)
    vista = _doc_dict(c, d, ente)
    if not vista['vigente']:
        raise ApiError(409, 'ese documento ya tiene una versión más nueva: se aprueba la vigente')
    if d['revision_estado'] == 'aprobado':
        raise ApiError(409, 'el documento ya está aprobado')
    if vista['observaciones_abiertas']:
        raise ApiError(409, f'No se puede aprobar: quedan {vista["observaciones_abiertas"]} observación(es) sin resolver.')
    c.execute("UPDATE documentos_obra SET revision_estado='aprobado', revisado_por=?, revisado_at=? WHERE id=?",
              (u['id'], core.iso(core.now()), did))
    core.audit(c, ente['id'], 'documento', did, 'aprobado', u['id'], {})
    return 200, _doc_dict(c, core.fila(c.execute('SELECT * FROM documentos_obra WHERE id=?', (did,)).fetchone()), ente)


# ---------- control de calidad: planilla de recepción de hormigón ----------

def banderas_recepcion(c, obra, ente, r):
    """Avisos de consistencia. No bloquean ni corrigen: el inspector decide. Umbrales de EJEMPLO (config del ente)."""
    h = cfg_obra(ente)['hormigon']
    out = []
    dosif = [d for d in _docs_vigentes(c, obra['id'], 'dosificacion') if d['clave'] == r['tipo'] and d['revision_estado'] == 'aprobado']
    if not dosif:
        out.append('sin_dosificacion_aprobada')
    elif r['asentamiento_cm'] is not None:
        datos = json.loads(dosif[-1]['datos_json'] or '{}')
        if abs(r['asentamiento_cm'] - datos.get('asentamiento_cm', r['asentamiento_cm'])) > datos.get('tolerancia_cm', h['asentamiento_tolerancia_cm']):
            out.append('asentamiento_fuera_de_diseno')
    if r['asentamiento_cm'] is None:
        out.append('sin_asentamiento')
    if r['temp_hormigon'] is not None:
        if r['temp_hormigon'] > h['temp_hormigon_max_c']:
            out.append('temperatura_hormigon_alta')
        elif r['temp_hormigon'] < h['temp_hormigon_min_c']:
            out.append('temperatura_hormigon_baja')
    if c.execute('SELECT 1 FROM recepciones_hormigon WHERE obra_id=? AND remito=?', (obra['id'], r['remito'])).fetchone():
        out.append('remito_repetido')
    for tipo, bandera in (('material', 'documento_de_material_vencido'), ('calibracion', 'calibracion_vencida')):
        if any(d['fecha_vencimiento'] and d['fecha_vencimiento'] < r['fecha'] for d in _docs_vigentes(c, obra['id'], tipo)):
            out.append(bandera)
    return out


def _rec_dict(c, r):
    d = core.fila(r)
    d['banderas'] = json.loads(d['banderas'] or '[]')
    d['cargada_por_nombre'] = _nombre(c, d['cargada_por'])
    d['revisada_por_nombre'] = _nombre(c, d['revisada_por'])
    return d


def listar_recepciones(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    return 200, [_rec_dict(c, r) for r in c.execute('SELECT * FROM recepciones_hormigon WHERE obra_id=? ORDER BY fecha DESC, hora DESC, id DESC', (oid,))]


def cargar_recepcion(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, 'cargar_recepcion', oid)
    cfg, b = cfg_obra(ente), ctx['body']
    fecha = parse_fecha(b.get('fecha'), 'fecha')
    if fecha > hoy():
        raise ApiError(400, 'la fecha de recepción no puede ser futura')
    hora = str(b.get('hora') or '')
    if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', hora):
        raise ApiError(400, 'la hora debe tener formato HH:MM')
    tipo = b.get('tipo')
    if tipo not in cfg['hormigon']['tipos']:
        raise ApiError(400, 'tipo de hormigón no válido: ' + ', '.join(cfg['hormigon']['tipos']))
    vol = core.numero_opcional(b.get('volumen_m3'), 0, 100, 'volumen (m3)')
    if not vol:
        raise ApiError(400, 'el volumen debe ser mayor que cero')
    r = {'fecha': fecha.strftime('%Y-%m-%d'), 'hora': hora, 'tipo': tipo, 'volumen_m3': q3(vol),
         'elemento': core.texto(b.get('elemento'), 3, 80, 'elemento estructural'),
         'probeta': (b.get('probeta') or '').strip()[:30] or None, 'remito': core.texto(b.get('remito'), 1, 30, 'remito'),
         'temp_ambiente': core.numero_opcional(b.get('temp_ambiente'), -20, 60, 'temperatura ambiente'),
         'temp_hormigon': core.numero_opcional(b.get('temp_hormigon'), -5, 60, 'temperatura del hormigón'),
         'asentamiento_cm': core.numero_opcional(b.get('asentamiento_cm'), 0, 30, 'asentamiento (cm)')}
    banderas = banderas_recepcion(c, obra, ente, r)
    cur = c.execute(
        '''INSERT INTO recepciones_hormigon(obra_id,ente_id,fecha,hora,tipo,volumen_m3,elemento,probeta,remito,temp_ambiente,temp_hormigon,
           asentamiento_cm,cargada_por,cargada_at,banderas) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (oid, ente['id'], r['fecha'], hora, tipo, r['volumen_m3'], r['elemento'], r['probeta'], r['remito'], r['temp_ambiente'],
         r['temp_hormigon'], r['asentamiento_cm'], u['id'], core.iso(core.now()), json.dumps(banderas)))
    core.audit(c, ente['id'], 'recepcion_hormigon', cur.lastrowid, 'cargada', u['id'], {'tipo': tipo, 'banderas': banderas})
    return 201, _rec_dict(c, c.execute('SELECT * FROM recepciones_hormigon WHERE id=?', (cur.lastrowid,)).fetchone())


def revisar_recepcion(c, ctx, rid):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'revisar_recepcion')
    r = c.execute('SELECT * FROM recepciones_hormigon WHERE id=?', (rid,)).fetchone()
    if r is None:
        raise ApiError(404, 'recepción inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra) or obra['inspector_id'] != u['id']:
        raise ApiError(404, 'recepción inexistente')
    if r['cargada_por'] == u['id']:
        raise ApiError(403, 'quien carga una planilla no puede revisarla')
    if r['revision_estado'] != 'pendiente':
        raise ApiError(409, 'la recepción ya fue revisada')
    accion = ctx['body'].get('accion')
    if accion not in ('validar', 'observar'):
        raise ApiError(400, 'accion debe ser validar u observar')
    obs = (ctx['body'].get('observacion') or '').strip()[:1000] or None
    if accion == 'observar' and (obs is None or len(obs) < 5):
        raise ApiError(400, 'para observar hace falta explicar el motivo (al menos 5 letras)')
    nuevo = 'validada' if accion == 'validar' else 'observada'
    c.execute('UPDATE recepciones_hormigon SET revision_estado=?, revisada_por=?, revisada_at=?, observacion=? WHERE id=?',
              (nuevo, u['id'], core.iso(core.now()), obs, rid))
    core.audit(c, ente['id'], 'recepcion_hormigon', rid, nuevo, u['id'], {'observacion': obs})
    return 200, _rec_dict(c, c.execute('SELECT * FROM recepciones_hormigon WHERE id=?', (rid,)).fetchone())


# ---------- equipo de la obra y firmas del representante técnico ----------

def asignar_equipo(c, ctx, oid):
    """El administrativo asigna a la obra al jefe de inspección, analista, jefe de certificaciones o director del ente."""
    u, ente, obra = _obra_para(c, ctx, 'asignar_obra', oid)
    uid = ctx['body'].get('usuario_id')
    r = c.execute('SELECT id, rol FROM usuarios WHERE id=? AND ente_id=? AND activo=1', (uid, ente['id'])).fetchone()
    if r is None or r['rol'] not in FUNCIONES_OBRA:
        raise ApiError(400, 'usuario_id debe ser un jefe de inspección, analista, jefe de certificaciones o director de este ente')
    c.execute('INSERT OR REPLACE INTO obra_asignados(obra_id,usuario_id,funcion) VALUES(?,?,?)', (oid, uid, r['rol']))
    core.audit(c, ente['id'], 'obra', oid, 'equipo_asignado', u['id'], {'usuario': uid, 'funcion': r['rol']})
    return 200, {'obra_id': oid, 'usuario_id': uid, 'funcion': r['rol']}


def _firma_de(c, entidad, eid, rol):
    r = c.execute('SELECT accion FROM firmas_registro WHERE entidad=? AND entidad_id=? AND rol=? ORDER BY id DESC LIMIT 1', (entidad, eid, rol)).fetchone()
    return r['accion'] if r else None


def _firmable(c, ctx, entidad, eid, permiso):
    """Devuelve (usuario, ente, obra, fila) del cómputo o certificado si la persona puede verlo."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    tabla = 'computos' if entidad == 'computo' else 'certificados'
    r = c.execute(f'SELECT * FROM {tabla} WHERE id=?', (eid,)).fetchone()
    if r is None:
        raise ApiError(404, 'inexistente')
    obra = _obra(c, ente, r['obra_id'])
    if not _visible(c, u, obra):
        raise ApiError(404, 'inexistente')
    return u, ente, obra, core.fila(r)


def firmar_contratista(c, ctx, entidad, eid):
    """El representante técnico firma (conforme) o rechaza (con motivo) una medición presentada o un certificado vigente. Una sola vez."""
    u, ente, obra, fila = _firmable(c, ctx, entidad, eid, 'firmar_contratista')
    if entidad == 'computo' and fila['estado'] != 'presentado':
        raise ApiError(409, 'sólo se firma una medición presentada')
    if entidad == 'certificado' and fila['estado'] == 'anulado':
        raise ApiError(409, 'no se firma un certificado anulado')
    accion = ctx['body'].get('accion')
    if accion not in ('firma', 'rechazo'):
        raise ApiError(400, 'accion debe ser firma o rechazo')
    motivo = None
    if accion == 'rechazo':
        motivo = core.texto(ctx['body'].get('motivo'), 10, 500, 'motivo del rechazo')
    if _firma_de(c, entidad, eid, 'contratista'):
        raise ApiError(409, 'ya firmaste o rechazaste esta ' + ('medición' if entidad == 'computo' else 'certificado'))
    _registrar_firma(c, ente['id'], entidad, eid, 'firmado' if accion == 'firma' else 'rechazado', u, motivo)
    core.audit(c, ente['id'], entidad, eid, 'firma_contratista', u['id'], {'accion': accion})
    return 201, {'entidad': entidad, 'id': eid, 'accion': accion, 'motivo': motivo}


def firmar_medicion_contratista(c, ctx, kid):
    return firmar_contratista(c, ctx, 'computo', kid)


def firmar_certificado_contratista(c, ctx, cid):
    return firmar_contratista(c, ctx, 'certificado', cid)


def firmas_contratista(c, ctx, oid):
    """Lo que la contratista tiene que firmar en la obra: mediciones presentadas y certificados vigentes, con lo que ya firmó o rechazó."""
    u, ente, obra = _obra_para(c, ctx, 'firmar_contratista', oid)
    out = []
    for k in c.execute("SELECT * FROM computos WHERE obra_id=? AND estado='presentado' ORDER BY mes", (oid,)):
        out.append({'entidad': 'computo', 'id': k['id'], 'titulo': f'Medición de {mes_txt(k["mes"])}', 'firmado': _firma_de(c, 'computo', k['id'], 'contratista')})
    for ce in c.execute("SELECT * FROM certificados WHERE obra_id=? AND estado!='anulado' ORDER BY numero", (oid,)):
        out.append({'entidad': 'certificado', 'id': ce['id'], 'titulo': f'Certificado N° {ce["numero"]}', 'firmado': _firma_de(c, 'certificado', ce['id'], 'contratista')})
    return 200, out


def validar_medicion(c, ctx, kid):
    """El jefe de inspección valida una medición presentada. Queda en el registro; no bloquea el certificado (ver DECISIONES.md)."""
    u, ente, obra, k = _firmable(c, ctx, 'computo', kid, 'validar_medicion')
    if k['estado'] != 'presentado':
        raise ApiError(409, 'sólo se valida una medición presentada')
    if _firma_de(c, 'computo', kid, 'jefe_inspeccion'):
        raise ApiError(409, 'esta medición ya fue validada')
    _registrar_firma(c, ente['id'], 'computo', kid, 'validada', u)
    core.audit(c, ente['id'], 'computo', kid, 'validada', u['id'], {'mes': k['mes']})
    return 200, {'id': kid, 'validada_por': u['nombre']}


def mes_txt(m):
    return MESES_TXT[int(m[5:7]) - 1] + ' ' + m[:4]


MESES_TXT = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']


# ---------- legajo técnico de la obra ----------
# Lo que se publica con una licitación de obra: acto administrativo con sus anexos, pliegos, planos, cómputo y presupuesto, y las
# circulares (consultas y respuestas, que se numeran en orden). Lo arma el administrativo del servicio antes de adjudicar.

TIPOS_LEGAJO = {
    'acto_administrativo': 'Acto administrativo y anexos',
    'pliego_condiciones': 'Pliego de Condiciones Generales y Particulares',
    'pliego_tecnico': 'Pliego de Especificaciones Técnicas',
    'plano': 'Planos',
    'computo_presupuesto': 'Cómputo y presupuesto',
    'circular': 'Circulares: consultas y respuestas',
    'otro': 'Otros documentos',
}


def _legajo_dict(c, d, reemplazados):
    out = dict(d)
    out['tipo_nombre'] = TIPOS_LEGAJO[d['tipo']]
    out['url'] = f'/api/fotos/{d["ruta"]}'
    out['vigente'] = d['id'] not in reemplazados
    out['subido_por_nombre'] = _nombre(c, d['subido_por'])
    out['fecha_publicacion'] = d['subido_at'][:10]
    out['posterior_publicacion'] = bool(d['posterior_publicacion'])
    return out


def ver_legajo(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    _exigir_interno(u)
    filas = [core.fila(r) for r in c.execute('SELECT * FROM legajo_obra WHERE obra_id=? ORDER BY id', (oid,))]
    reemplazados = {d['reemplaza_a'] for d in filas if d['reemplaza_a']}
    docs = [_legajo_dict(c, d, reemplazados) for d in filas]
    vigentes = [d for d in docs if d['vigente']]
    requeridos = [{'tipo': t, 'nombre': TIPOS_LEGAJO[t], 'cumplido': any(d['tipo'] == t for d in vigentes)} for t in cfg_obra(ente)['legajo_requeridos']]
    return 200, {'obra_id': oid, 'codigo': obra['codigo'], 'estado': obra['legajo_estado'], 'publicado_at': obra['legajo_publicado_at'],
                 'publicado_por_nombre': _nombre(c, obra['legajo_publicado_por']), 'requeridos': requeridos,
                 'completo': all(r['cumplido'] for r in requeridos), 'tipos': TIPOS_LEGAJO, 'documentos': docs}


def subir_legajo(c, ctx, oid):
    """Sube un documento del legajo. Una versión nueva (con su motivo) reemplaza a la anterior sin borrarla; las circulares se numeran solas."""
    u, ente, obra = _obra_para(c, ctx, 'subir_legajo', oid)
    b = ctx['body']
    tipo = b.get('tipo')
    if tipo not in TIPOS_LEGAJO:
        raise ApiError(400, 'tipo de documento no válido: ' + ', '.join(TIPOS_LEGAJO))
    titulo = core.texto(b.get('titulo'), 3, 120, 'título')
    nombre = ((b.get('nombre_archivo') or '').strip()[:120]) or None
    version, reemplaza, motivo = 1, b.get('reemplaza_a'), None
    numero = None
    if reemplaza is not None:
        ant = c.execute('SELECT * FROM legajo_obra WHERE id=? AND obra_id=? AND tipo=?', (reemplaza, oid, tipo)).fetchone()
        if ant is None:
            raise ApiError(400, 'reemplaza_a debe ser un documento del legajo de esta obra y del mismo tipo')
        if c.execute('SELECT 1 FROM legajo_obra WHERE reemplaza_a=?', (reemplaza,)).fetchone():
            raise ApiError(409, 'ese documento ya tiene una versión más nueva')
        motivo = core.texto(b.get('motivo_version'), 5, 300, 'motivo de la versión nueva')
        version, numero = ant['version'] + 1, ant['numero']
    elif tipo == 'circular':
        numero = (c.execute("SELECT MAX(numero) FROM legajo_obra WHERE obra_id=? AND tipo='circular'", (oid,)).fetchone()[0] or 0) + 1
    sha, ruta, mime = guardar_archivo(b.get('archivo'))
    if c.execute('SELECT 1 FROM legajo_obra WHERE obra_id=? AND sha256=?', (oid, sha)).fetchone():
        raise ApiError(409, 'ese archivo ya está en el legajo de la obra')
    cur = c.execute(
        '''INSERT INTO legajo_obra(obra_id,ente_id,tipo,titulo,numero,nombre_archivo,sha256,ruta,mime,version,reemplaza_a,motivo_version,
           posterior_publicacion,subido_por,subido_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (oid, ente['id'], tipo, titulo, numero, nombre, sha, ruta, mime, version, reemplaza, motivo,
         1 if obra['legajo_estado'] == 'publicado' else 0, u['id'], core.iso(core.now())))
    core.audit(c, ente['id'], 'legajo', cur.lastrowid, 'subido', u['id'], {'obra': obra['codigo'], 'tipo': tipo, 'version': version})
    r = core.fila(c.execute('SELECT * FROM legajo_obra WHERE id=?', (cur.lastrowid,)).fetchone())
    return 201, _legajo_dict(c, r, set())


def publicar_legajo(c, ctx, oid):
    """Publica el legajo: con eso se puede adjudicar. Hacen falta los documentos requeridos; después sólo se agregan circulares o versiones."""
    u, ente, obra = _obra_para(c, ctx, 'publicar_legajo', oid)
    if obra['legajo_estado'] == 'publicado':
        raise ApiError(409, 'el legajo ya está publicado')
    faltan = [TIPOS_LEGAJO[t] for t in cfg_obra(ente)['legajo_requeridos']
              if c.execute('SELECT 1 FROM legajo_obra WHERE obra_id=? AND tipo=?', (oid, t)).fetchone() is None]
    if faltan:
        raise ApiError(409, 'falta cargar: ' + '; '.join(faltan))
    c.execute("UPDATE obras SET legajo_estado='publicado', legajo_publicado_por=?, legajo_publicado_at=? WHERE id=?",
              (u['id'], core.iso(core.now()), oid))
    core.audit(c, ente['id'], 'obra', oid, 'legajo_publicado', u['id'], {})
    return ver_legajo(c, ctx, oid)


RUTAS = [
    ('POST', r'^/certificados/(\d+)/aprobar$', aprobar_certificado),
    ('POST', r'^/certificados/(\d+)/devolver$', devolver_certificado),
    ('POST', r'^/obras/(\d+)/equipo$', asignar_equipo),
    ('GET', r'^/obras/(\d+)/firmas-contratista$', firmas_contratista),
    ('POST', r'^/computos/(\d+)/firma-contratista$', firmar_medicion_contratista),
    ('POST', r'^/certificados/(\d+)/firma-contratista$', firmar_certificado_contratista),
    ('POST', r'^/computos/(\d+)/validar$', validar_medicion),
    ('GET', r'^/obras/(\d+)/notas$', listar_notas),
    ('POST', r'^/obras/(\d+)/notas$', solicitar_nota),
    ('POST', r'^/notas/(\d+)/respuesta$', responder_nota),
    ('POST', r'^/obras$', alta_obra),
    ('POST', r'^/obras/(\d+)/asignacion$', asignar_obra),
    ('POST', r'^/obras/(\d+)/estado$', cambiar_estado_obra),
    ('GET', r'^/obras/(\d+)/legajo$', ver_legajo),
    ('POST', r'^/obras/(\d+)/legajo$', subir_legajo),
    ('POST', r'^/obras/(\d+)/legajo/publicar$', publicar_legajo),
    ('GET', r'^/obras/(\d+)/modificaciones$', listar_modificaciones),
    ('POST', r'^/obras/(\d+)/modificaciones$', cargar_modificacion),
    ('GET', r'^/obras/(\d+)/curva-ideal$', curva_ideal_vigente),
    ('POST', r'^/modificaciones/(\d+)/autorizar$', autorizar_modificacion),
    ('GET', r'^/obras/(\d+)/ordenes-servicio$', listar_os),
    ('POST', r'^/obras/(\d+)/ordenes-servicio$', emitir_os),
    ('POST', r'^/ordenes-servicio/(\d+)/respuesta$', responder_os),
    ('POST', r'^/ordenes-servicio/(\d+)/cerrar$', cerrar_os),
    ('GET', r'^/obras/(\d+)/documentos$', listar_documentos),
    ('POST', r'^/obras/(\d+)/documentos$', subir_documento),
    ('POST', r'^/documentos/(\d+)/observaciones$', agregar_observacion),
    ('POST', r'^/documentos/(\d+)/aprobar$', aprobar_documento),
    ('POST', r'^/observaciones/(\d+)/resolver$', resolver_observacion),
    ('GET', r'^/obras/(\d+)/recepciones$', listar_recepciones),
    ('POST', r'^/obras/(\d+)/recepciones$', cargar_recepcion),
    ('POST', r'^/recepciones/(\d+)/revision$', revisar_recepcion),
    ('POST', r'^/obras/(\d+)/certificados$', emitir_certificado),
    ('GET', r'^/obras/(\d+)/certificados$', listar_certificados),
    ('GET', r'^/certificados/(\d+)$', ficha_certificado),
    ('POST', r'^/certificados/(\d+)/autorizar$', autorizar_certificado),
    ('POST', r'^/certificados/(\d+)/anular$', anular_certificado),
    ('POST', r'^/certificados/(\d+)/expediente$', asignar_expediente),
    ('GET', r'^/obras$', listar_obras),
    ('GET', r'^/obras/(\d+)$', ficha_obra),
    ('GET', r'^/obras/(\d+)/curva$', curva_obra),
    ('POST', r'^/obras/(\d+)/computos$', nuevo_computo),
    ('POST', r'^/obras/(\d+)/plazo-vencida$', registrar_plazo_vencida),
    ('GET', r'^/computos/(\d+)$', ver_computo),
    ('POST', r'^/computos/(\d+)/guardar$', guardar_computo),
    ('POST', r'^/computos/(\d+)/presentar$', presentar_computo),
    ('POST', r'^/computos/(\d+)/eliminar$', eliminar_computo),
]


