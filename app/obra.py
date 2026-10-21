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


def _visible(u, obra):
    """El inspector ve sus obras, el contratista la suya; administrativo, autoridad y administrador, todas las del ente."""
    if u['rol'] == 'inspector':
        return obra['inspector_id'] == u['id']
    if u['rol'] == 'contratista':
        return obra['contratista_id'] == u['id']
    return u['rol'] in ('autoridad', 'administrativo', 'administrador')


def _obra_para(c, ctx, permiso='ver_obras', oid=None):
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, permiso)
    obra = _obra(c, ente, oid)
    if not _visible(u, obra):
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


# ---------- alta de una obra (seed y pruebas; en la simulación no hay pantalla de alta) ----------

def crear_obra(c, ente_id, denominacion, contratista, fecha_replanteo, plazo_dias, items, ideal, contratista_id=None,
               inspector_id=None, anticipo_pct=0, fondo_reparo_pct=5, fondo_inspeccion_pct=0, plazo_venc_cert_dias=30,
               lat=None, lon=None, estado='en_ejecucion', usuario_id=None):
    """items: [{nro, descripcion, unidad, cantidad, precio_cent, titulo}]; ideal: {nro: {'AAAA-MM': cantidad}}."""
    parse_fecha(fecha_replanteo, 'fecha_replanteo')
    nros = [i['nro'] for i in items]
    if len(set(nros)) != len(nros):
        raise ApiError(400, 'los números de ítem no pueden repetirse')
    cur = c.execute(
        '''INSERT INTO obras(ente_id,denominacion,estado,contratista,contratista_id,inspector_id,fecha_replanteo,plazo_dias,
           anticipo_pct,fondo_reparo_pct,fondo_inspeccion_pct,plazo_venc_cert_dias,lat,lon,curva_cerrada)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',
        (ente_id, denominacion, estado, contratista, contratista_id, inspector_id, fecha_replanteo, plazo_dias,
         anticipo_pct, fondo_reparo_pct, fondo_inspeccion_pct, plazo_venc_cert_dias, lat, lon))
    oid = cur.lastrowid
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
            c.execute('INSERT INTO avance_ideal(item_id,mes,cantidad) VALUES(?,?,?)', (item_id, mes, q3(cant)))
    core.audit(c, ente_id, 'obra', oid, 'creada', usuario_id, {'denominacion': denominacion})
    return oid


# ---------- curva de avance ideal contra real ----------

def _ideal_por_mes(c, oid):
    out = {}
    for r in c.execute('''SELECT a.mes, a.cantidad, i.precio_oferta_cent FROM avance_ideal a
                          JOIN items_obra i ON i.id=a.item_id WHERE i.obra_id=?''', (oid,)):
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
    ideal, real = _ideal_por_mes(c, obra['id']), _real_por_mes(c, obra['id'], precios)
    m0 = mes_de(obra['fecha_replanteo'])
    ultimo = max([mes_de(fecha_fin(obra))] + list(ideal) + list(real))
    puntos, ia, ra = [], 0, 0
    ultimo_real = max(real) if real else None
    for mes in meses_entre(min([m0] + list(ideal) + list(real)), ultimo):
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
    return {'monto_contrato_cent': monto, 'puntos': puntos,
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
            'editable': k['estado'] in ('borrador', 'presentado') and cert is None}


def _computo_para(c, ctx, kid, escribir=False):
    """Devuelve (usuario, ente, obra, cómputo). Para escribir hace falta ser el inspector asignado."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'medir' if escribir else 'ver_obras')
    k = c.execute('SELECT * FROM computos WHERE id=?', (kid,)).fetchone()
    if k is None:
        raise ApiError(404, 'cómputo inexistente')
    obra = _obra(c, ente, k['obra_id'])
    if not _visible(u, obra):
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
    return {'monto_contrato_cent': monto, 'computos_sin_certificado': sin_certificado, 'certificados_pendientes': cert_pendientes,
            'fecha_fin': fin.strftime('%Y-%m-%d'), 'dias_restantes': (fin - h).days, 'vencida': h > fin,
            'plazo_cert_vencida_activo': _plazo_vencida_activo(c, obra['id']),
            'avance_ideal_pct': ideal_hoy[-1]['ideal_acum_pct'] if ideal_hoy else 0.0,
            'avance_certificado_pct': real[-1]['real_acum_pct'] if real else None,
            'avance_medido_pct': pct(medido, monto), 'desvio': cv['desvio'], 'mes_a_medir': mes_a_medir,
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
        if _visible(u, obra):
            out.append(dict(_datos_obra(c, obra), resumen=_resumen(c, obra)))
    return 200, out


def ficha_obra(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
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
    if not _visible(u, obra):
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
    """El administrativo emite el certificado de un cómputo presentado. Nace PENDIENTE (se imprime 'NO AUTORIZADO')."""
    u, ente = core.usuario_actual(c, ctx)
    core.exigir(u, 'emitir_certificado')
    obra = _obra(c, ente, oid)
    if not _visible(u, obra):
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
    return 201, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cur.lastrowid,)).fetchone()))


def _ficha_certificado(c, obra, cert):
    k = core.fila(c.execute('SELECT * FROM computos WHERE id=?', (cert['computo_id'],)).fetchone())
    d = _resumen_certificado(c, cert)
    d.update(
        obra={'id': obra['id'], 'denominacion': obra['denominacion'], 'contratista': obra['contratista'], 'fecha_replanteo': obra['fecha_replanteo'],
              'plazo_dias': obra['plazo_dias']},
        liquidacion=json.loads(cert['liquidacion_json'] or '{}'), observaciones=cert['observaciones'],
        leyenda={'pendiente': 'NO AUTORIZADO', 'anulado': 'ANULADO'}.get(cert['estado']), emitido_at=cert['emitido_at'],
        motivo_anulacion=cert['motivo_anulacion'],
        firmas=[{'cargo': 'Inspector de obra (mide)', 'nombre': _nombre(c, k['presentado_por']), 'fecha': k['presentado_at']},
                {'cargo': 'Administrativo (emite)', 'nombre': _nombre(c, cert['emitido_por']), 'fecha': cert['emitido_at']},
                {'cargo': 'Autoridad (autoriza)', 'nombre': _nombre(c, cert['autorizado_por']), 'fecha': cert['autorizado_at']}],
        nota_firmas=f'Firmas simuladas: el certificado real lleva {FIRMAS_REALES}.')
    return d


def ficha_certificado(c, ctx, cid):
    u, ente, obra, cert = _cert_para(c, ctx, cid)
    return 200, _ficha_certificado(c, obra, cert)


def listar_certificados(c, ctx, oid):
    u, ente, obra = _obra_para(c, ctx, oid=oid)
    return 200, [_resumen_certificado(c, core.fila(r)) for r in c.execute('SELECT * FROM certificados WHERE obra_id=? ORDER BY numero, id', (oid,))]


def autorizar_certificado(c, ctx, cid):
    """La autoridad con rol habilitante autoriza: desde esa fecha corre el vencimiento y ya no se puede anular."""
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'autorizar_certificado')
    if cert['estado'] != 'pendiente':
        raise ApiError(409, f'el certificado está {cert["estado"]}: sólo se autoriza uno pendiente')
    if cert['emitido_por'] == u['id']:
        raise ApiError(403, 'quien emite un certificado no puede autorizarlo')
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
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def anular_certificado(c, ctx, cid):
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'gestionar_certificado')
    if cert['estado'] == 'autorizado':
        raise ApiError(409, 'un certificado autorizado ya no se puede anular')
    if cert['estado'] != 'pendiente':
        raise ApiError(409, f'el certificado ya está {cert["estado"]}')
    ultimo = c.execute("SELECT MAX(numero) FROM certificados WHERE obra_id=? AND estado!='anulado'", (obra['id'],)).fetchone()[0]
    if cert['numero'] != ultimo:
        raise ApiError(409, 'Sólo se puede anular el último certificado generado. Anulá primero el posterior.')
    motivo = core.texto(ctx['body'].get('motivo'), 5, 500, 'motivo')
    c.execute("UPDATE certificados SET estado='anulado', anulado_por=?, anulado_at=?, motivo_anulacion=? WHERE id=?",
              (u['id'], core.iso(core.now()), motivo, cid))
    core.audit(c, ente['id'], 'certificado', cid, 'anulado', u['id'], {'motivo': motivo})
    return 200, _ficha_certificado(c, obra, core.fila(c.execute('SELECT * FROM certificados WHERE id=?', (cid,)).fetchone()))


def asignar_expediente(c, ctx, cid):
    """Asigna el número de expediente. No puede repetirse entre certificados, salvo certificados de $ 0."""
    u, ente, obra, cert = _cert_para(c, ctx, cid, 'gestionar_certificado')
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


RUTAS = [
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
