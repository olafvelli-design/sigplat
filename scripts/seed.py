"""Carga datos FICTICIOS de demostración en la base del prototipo.

Uso:  python scripts/seed.py            (recrea data/sigplat.db desde cero)
Todos los nombres, ubicaciones y fotos son sintéticos. No contiene datos reales de personas.
"""
import base64
import os
import random
import struct
import sys
import zlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'app'))
import core  # noqa: E402
import obra  # noqa: E402

DB = os.environ.get('SIGPLAT_DB', os.path.join(core.BASE_DIR, 'data', 'sigplat.db'))
ALEATORIO = random.Random(7)  # semilla fija: el seed es reproducible
# Contraseña ÚNICA de los usuarios de prueba. Sólo para desarrollo local: cambiarla o no crear usuarios así en producción.
PASSWORD_PRUEBA = 'SIGPLAT-prueba-2026'
CREDENCIALES = []  # (ente, login, nombre, rol); se escribe en data/credenciales_prueba.txt
# Cuentas fáciles de recordar para probar a mano (usuario = contraseña). Sólo para esta demo con datos ficticios.
USUARIOS_SIMPLES = [('vecino', 'Vecino de prueba', 'vecino'), ('inspector', 'Inspector de prueba', 'inspector'),
                    ('contratista', 'Representante de la contratista (ficticio)', 'contratista'),
                    ('administrativo', 'Administrativo de prueba', 'administrativo'),
                    ('autoridad', 'Autoridad de prueba', 'autoridad')]


def png_color(rgb, lado=16):
    """PNG válido de color sólido. Colores distintos producen archivos (y hashes) distintos."""
    raw = b''.join(b'\x00' + bytes(rgb) * lado for _ in range(lado))

    def bloque(tipo, datos):
        return struct.pack('>I', len(datos)) + tipo + datos + struct.pack('>I', zlib.crc32(tipo + datos) & 0xffffffff)

    cabecera = struct.pack('>IIBBBBB', lado, lado, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n' + bloque(b'IHDR', cabecera) + bloque(b'IDAT', zlib.compress(raw)) + bloque(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(png).decode('ascii')


def ctx(body=None, uid=None, q=None):
    return {'body': body or {}, 'uid': str(uid) if uid else None, 'q': q or {}}


class Sembrador:
    def __init__(self, c, inicio):
        self.c = c
        self.t = inicio
        core.CLOCK['now'] = inicio

    def avanzar(self, dias=0, horas=0):
        self.t = self.t + timedelta(days=dias, hours=horas)
        core.CLOCK['now'] = self.t

    def usuario(self, ente_id, login_nombre, nombre, rol):
        return core.crear_usuario(self.c, ente_id, nombre, login_nombre, PASSWORD_PRUEBA, rol)


def reset_base():
    if os.path.exists(DB):
        os.remove(DB)
    os.makedirs(os.path.dirname(DB), exist_ok=True)


def sembrar_ente(s, codigo, usuarios, reportes):
    c = s.c
    ente = c.execute('SELECT * FROM entes WHERE codigo=?', (codigo,)).fetchone()
    cfg = core.cfg_de(ente)
    ids = {}
    por_rol = {}
    for nombre, rol in usuarios:
        por_rol[rol] = por_rol.get(rol, 0) + 1
        login_nombre = f'{codigo.lower()}.{rol}{por_rol[rol]}'
        ids[nombre] = s.usuario(ente['id'], login_nombre, nombre, rol)
        CREDENCIALES.append((codigo, login_nombre, nombre, rol))
    c.commit()
    insp = [v for k, v in ids.items() if k.startswith('Inspector')]
    resp = [v for k, v in ids.items() if k.startswith('Responsable')]
    auth = [v for k, v in ids.items() if k.startswith('Autoridad')][0]
    centro = cfg['centro']
    confirmadas = []
    for i in range(reportes):
        s.avanzar(dias=ALEATORIO.uniform(0.2, 1.4))
        categoria = ALEATORIO.choice(cfg['categorias'])
        lat = centro[0] + ALEATORIO.uniform(-0.02, 0.02)
        lon = centro[1] + ALEATORIO.uniform(-0.02, 0.02)
        cuerpo = {'ente': codigo, 'categoria': categoria, 'lat': lat, 'lon': lon, 'precision_m': ALEATORIO.choice([5, 8, 15, 40]),
                  'descripcion': f'Reporte ficticio {i + 1}: {categoria.lower()} observado en la vía pública.',
                  'canal': ALEATORIO.choice(['app', 'cidi', 'web']),
                  'capturada_at': s.t.strftime('%Y-%m-%dT%H:%M:%SZ'),
                  'foto': png_color((60 + i * 3 % 180, 90, 120))}
        st, res = core.reportar(c, ctx(cuerpo))
        c.commit()
        # Clasificación: parte se inspecciona días después; el resto queda pendiente a propósito.
        if i < reportes - 4:
            s.avanzar(dias=ALEATORIO.uniform(1, 4))
            inc = c.execute('SELECT id FROM incidencias WHERE codigo=?', (res['codigo'],)).fetchone()
            r = ALEATORIO.random()
            if r < 0.6:
                st, _ = core.inspeccionar(c, ctx({'resultado': 'confirmada', 'gravedad': ALEATORIO.choice([1, 2, 3]),
                                                  'observaciones': 'Verificado en campo (dato ficticio).'}, insp[0]), inc['id'])
                confirmadas.append(inc['id'])
            elif r < 0.7:
                core.inspeccionar(c, ctx({'resultado': 'descartada', 'observaciones': 'No corresponde (dato ficticio).'}, insp[1 % len(insp)]), inc['id'])
            # el resto queda en 'recibida' para mostrar bandeja pendiente
        c.commit()
    s.avanzar(dias=2)
    return ids, insp, resp, auth, confirmadas, cfg


def flujo_completo(s, ids, insp, resp, auth, incidencias, titulo, tipo, actividades, dias_plazo):
    """Recorre una intervención desde su creación hasta su cierre, con evidencias de cierre validadas."""
    c = s.c
    st, inter = core.crear_intervencion(c, ctx({
        'tipo': tipo, 'titulo': titulo, 'responsable_id': resp[0], 'incidencia_ids': incidencias,
        'fecha_planificada_fin': (s.t + timedelta(days=dias_plazo)).strftime('%Y-%m-%d'), 'actividades': actividades,
        'sistema_externo': 'SIGO', 'expediente_externo': f'FICT-EXP-{s.t.strftime("%y%m%d")}-{len(titulo)}',
    }, auth))
    iid = inter['id']
    c.commit()
    s.avanzar(dias=2)
    core.transicion_intervencion(c, ctx({'accion': 'iniciar'}, resp[0]), iid)
    for act in inter['actividades']:
        s.avanzar(dias=3)
        core.declarar_avance(c, ctx({'actividad_id': act['id'], 'pct': 100, 'nota': 'Ejecutado (dato ficticio).'}, resp[0]), iid)
        av = c.execute('SELECT id FROM avances WHERE actividad_id=? ORDER BY id DESC LIMIT 1', (act['id'],)).fetchone()
        s.avanzar(dias=1)
        core.revisar_avance(c, ctx({'accion': 'validar', 'observacion': 'Coincide con la inspección (ficticio).'}, insp[0]), av['id'])
    s.avanzar(dias=1)
    core.subir_evidencia(c, ctx({'tipo': 'cierre', 'foto': png_color((30 + 50 * iid % 200, 140, 60)), 'lat': inter['lat'], 'lon': inter['lon'],
                                  'precision_m': 6, 'capturada_at': s.t.strftime('%Y-%m-%dT%H:%M:%SZ')}, resp[0]), iid)
    cierre = c.execute("SELECT id FROM evidencias WHERE intervencion_id=? AND tipo='cierre' ORDER BY id DESC LIMIT 1",
                       (iid,)).fetchone()
    core.transicion_intervencion(c, ctx({'accion': 'declarar_finalizacion'}, resp[0]), iid)
    s.avanzar(dias=1)
    core.revisar_evidencia(c, ctx({'accion': 'validada'}, insp[0]), cierre['id'])
    core.transicion_intervencion(c, ctx({'accion': 'validar_cierre'}, insp[0]), iid)
    s.avanzar(dias=1)
    core.transicion_intervencion(c, ctx({'accion': 'cerrar'}, auth), iid)
    c.commit()
    return iid


def flujo_parcial(s, ids, insp, resp, auth, incidencias, titulo, tipo, actividades, dias_plazo, con_observacion=False):
    """Intervención en ejecución con avances declarados, algunos pendientes de revisión."""
    c = s.c
    st, inter = core.crear_intervencion(c, ctx({
        'tipo': tipo, 'titulo': titulo, 'responsable_id': resp[1 % len(resp)], 'incidencia_ids': incidencias,
        'fecha_planificada_fin': (s.t + timedelta(days=dias_plazo)).strftime('%Y-%m-%d'), 'actividades': actividades,
    }, auth))
    iid = inter['id']
    c.commit()
    s.avanzar(dias=1)
    core.transicion_intervencion(c, ctx({'accion': 'iniciar'}, resp[1 % len(resp)]), iid)
    for k, act in enumerate(inter['actividades']):
        s.avanzar(dias=2)
        core.declarar_avance(c, ctx({'actividad_id': act['id'], 'pct': 60 + 10 * k, 'nota': 'Avance parcial (ficticio).'},
                                    resp[1 % len(resp)]), iid)
        av = c.execute('SELECT id FROM avances WHERE actividad_id=? ORDER BY id DESC LIMIT 1', (act['id'],)).fetchone()
        if con_observacion and k == 0:
            core.revisar_avance(c, ctx({'accion': 'observar', 'observacion': 'Las fotos no muestran la actividad declarada (ficticio).'}, insp[0]), av['id'])
        elif k == 0:
            core.revisar_avance(c, ctx({'accion': 'validar'}, insp[0]), av['id'])
    s.avanzar(dias=1)
    c.commit()
    return iid


# ---------- Modo obra: una obra FICTICIA con nombres, montos e identificadores inventados ----------
OBRA_ITEMS = [  # (nro, descripción, unidad, cantidad, precio en pesos); sin unidad = rubro (título)
    (1, 'Trabajos preliminares', None, None, None),
    (2, 'Limpieza y replanteo del terreno', 'gl', 1, 9500000),
    (3, 'Excavación para fundaciones', 'm3', 850, 21000),
    (4, 'Estructuras de hormigón', None, None, None),
    (5, 'Hormigón H13 de limpieza', 'm3', 120, 128000),
    (6, 'Hormigón H21 en fundaciones', 'm3', 640, 165000),
    (7, 'Hormigón H25 en estructura', 'm3', 410, 182000),
    (8, 'Acero para armaduras', 'kg', 78000, 2450),
    (9, 'Albañilería y terminaciones', None, None, None),
    (10, 'Mampostería', 'm2', 520, 38000),
    (11, 'Revoques', 'm2', 1040, 14500),
    (12, 'Pisos y contrapisos', 'm2', 640, 33000),
    (13, 'Instalaciones', None, None, None),
    (14, 'Instalación sanitaria del baño', 'gl', 1, 12400000),
    (15, 'Sala de bombas: equipos y montaje', 'gl', 1, 46000000),
]
OBRA_REPARTO = {  # porcentaje comprometido por el contratista en cada mes de obra (desde el replanteo)
    2: {0: 100}, 3: {0: 60, 1: 40}, 5: {1: 50, 2: 50}, 6: {1: 10, 2: 30, 3: 30, 4: 30},
    7: {3: 15, 4: 25, 5: 30, 6: 30}, 8: {1: 8, 2: 12, 3: 15, 4: 15, 5: 15, 6: 15, 7: 12, 8: 8},
    10: {5: 20, 6: 30, 7: 30, 8: 20}, 11: {7: 20, 8: 30, 9: 30, 10: 20}, 12: {8: 20, 9: 30, 10: 30, 11: 20},
    14: {9: 50, 10: 50}, 15: {8: 20, 9: 30, 10: 30, 11: 20},
}
OBRA_RENDIMIENTO = [0.95, 0.92, 0.88, 0.85]  # el contratista va un poco atrasado respecto de lo comprometido
OBRA_PERSONAL = [38, 42, 45, 44]


def ideal_de_obra(m0):
    """{nro: {mes: cantidad}} a partir del reparto; el último mes de cada ítem completa la cantidad exacta."""
    cantidades = {i[0]: i[3] for i in OBRA_ITEMS if i[2]}
    ideal = {}
    for nro, reparto in OBRA_REPARTO.items():
        meses = sorted(reparto)
        acumulado = Decimal(0)
        ideal[nro] = {}
        for k, off in enumerate(meses):
            cant = (Decimal(str(cantidades[nro])) - acumulado) if k == len(meses) - 1 else \
                (Decimal(str(cantidades[nro])) * reparto[off] / 100).quantize(Decimal('0.001'))
            acumulado += cant
            ideal[nro][obra.sumar_meses(m0, off)] = float(cant)
    return ideal


def sembrar_obra(s, simples):
    c, base = s.c, s.t
    ente = c.execute("SELECT id FROM entes WHERE codigo='CBA-PILOTO'").fetchone()['id']
    actual = obra.mes_de(base)
    m = lambda k: obra.sumar_meses(actual, k - 4)  # noqa: E731  m(0) = cuatro meses antes del actual

    def en(mes, dia=3, hora=10):
        return min(datetime(int(mes[:4]), int(mes[5:7]), dia, hora, tzinfo=timezone.utc), base - timedelta(hours=2))

    core.CLOCK['now'] = en(m(0), 5, 9)
    items = [{'nro': n, 'descripcion': d, 'unidad': u, 'cantidad': q, 'precio_cent': int(p * 100) if p else 0, 'titulo': u is None}
             for n, d, u, q, p in OBRA_ITEMS]
    oid = obra.crear_obra(
        c, ente, 'Estación transformadora ficticia Las Rosas', 'Constructora Modelo del Centro (ficticia)',
        date(int(m(0)[:4]), int(m(0)[5:7]), 10).strftime('%Y-%m-%d'), 360, items, ideal_de_obra(m(0)),
        contratista_id=simples['contratista'], inspector_id=simples['inspector'], anticipo_pct=10, fondo_reparo_pct=5,
        fondo_inspeccion_pct=1, plazo_venc_cert_dias=30, lat=-31.4412, lon=-64.2105, usuario_id=simples['autoridad'])
    c.commit()
    ids = {r['nro']: r['id'] for r in c.execute('SELECT id, nro FROM items_obra WHERE obra_id=?', (oid,))}
    ideal = ideal_de_obra(m(0))
    insp = simples['inspector']
    for k in range(1):
        core.CLOCK['now'] = en(m(k + 1), 3, 10)
        _, v = obra.nuevo_computo(c, ctx(None, insp), oid)
        medido = [{'item_id': ids[nro], 'cantidad': round(meses.get(m(k), 0) * OBRA_RENDIMIENTO[k], 3)}
                  for nro, meses in ideal.items() if meses.get(m(k), 0)]
        obra.guardar_computo(c, ctx({'items': medido, 'personal_afectado': OBRA_PERSONAL[k]}, insp), v['id'])
        obra.presentar_computo(c, ctx(None, insp), v['id'])
        c.commit()
    return oid


def construir(ruta, inicio):
    """Carga toda la historia ficticia a partir de `inicio` y devuelve la hora final simulada."""
    global ALEATORIO
    ALEATORIO = random.Random(7)  # cada pasada parte de la misma semilla
    CREDENCIALES.clear()
    c = core.conectar(ruta)
    core.cargar_configs(c)
    c.commit()
    s = Sembrador(c, inicio)

    ids_cba, insp_cba, resp_cba, auth_cba, confirmadas_cba, cfg_cba = sembrar_ente(
        s, 'CBA-PILOTO',
        [('Inspector ficticio 1', 'inspector'), ('Inspector ficticio 2', 'inspector'),
         ('Responsable cuadrilla A (ficticia)', 'responsable'), ('Responsable cuadrilla B (ficticia)', 'responsable'),
         ('Autoridad ficticia (Secretaría de Obras)', 'autoridad'), ('Administrador ficticio', 'administrador')],
        reportes=30)
    ente_cba = c.execute("SELECT id FROM entes WHERE codigo='CBA-PILOTO'").fetchone()['id']
    simples = {rol: core.crear_usuario(c, ente_cba, nombre, login_nombre, login_nombre, rol)
               for login_nombre, nombre, rol in USUARIOS_SIMPLES}
    c.commit()
    s.avanzar(dias=5)
    confirmadas_cba = confirmadas_cba[:8]
    f = confirmadas_cba
    flujo_completo(s, ids_cba, insp_cba, resp_cba, auth_cba, [f[0]], 'Bacheo calle ficticia 101', 'mantenimiento', [], 20)
    flujo_completo(s, ids_cba, insp_cba, resp_cba, auth_cba, [f[1]], 'Reparación de cordón cuneta ficticio', 'orden_trabajo',
                   ['Excavación', 'Hormigonado', 'Terminación'], 25)
    flujo_parcial(s, ids_cba, insp_cba, resp_cba, auth_cba, [f[2]], 'Obra de drenaje ficticia Barrio X', 'obra',
                  ['Limpieza de cuneta', 'Alcantarilla'], 15, con_observacion=True)
    flujo_parcial(s, ids_cba, insp_cba, resp_cba, auth_cba, [f[3]], 'Recambio de luminarias ficticio', 'orden_trabajo',
                  ['Recambio de 10 luminarias'], -5)  # plazo ya vencido: aparece como atrasada
    if len(f) > 4:
        s.avanzar(dias=1)
        core.crear_intervencion(c, ctx({'tipo': 'mantenimiento', 'titulo': 'Poda de arbolado en riesgo ficticio',
                                        'responsable_id': resp_cba[0], 'incidencia_ids': [f[4]],
                                        'fecha_planificada_fin': (s.t + timedelta(days=10)).strftime('%Y-%m-%d')}, auth_cba))
        c.commit()

    # Un reporte repetido cerca de uno existente, para mostrar la agrupación de duplicados
    base = c.execute("SELECT codigo, lat, lon, categoria FROM incidencias WHERE ente_id=(SELECT id FROM entes WHERE codigo='CBA-PILOTO') LIMIT 1").fetchone()
    s.avanzar(dias=1)
    st, res = core.reportar(c, ctx({'ente': 'CBA-PILOTO', 'categoria': base['categoria'], 'lat': base['lat'] + 0.0002,
                                    'lon': base['lon'], 'precision_m': 10, 'canal': 'app',
                                    'descripcion': 'Otro vecino reporta el mismo problema (ficticio).',
                                    'foto': png_color((60, 90, 120))}))  # misma foto que el primer reporte
    c.commit()

    # Reportes de la cuenta "vecino", con estados distintos para que "Mis reportes" no aparezca vacío
    mis_codigos = []
    for k, categoria in enumerate(cfg_cba['categorias'][:3]):
        s.avanzar(horas=3)
        _, res = core.reportar(c, ctx({
            'ente': 'CBA-PILOTO', 'categoria': categoria, 'lat': cfg_cba['centro'][0] + 0.004 * (k + 1),
            'lon': cfg_cba['centro'][1] - 0.003 * (k + 1), 'precision_m': 8, 'canal': 'web',
            'descripcion': f'Reporte ficticio {k + 1} de la cuenta vecino de prueba.',
            'capturada_at': s.t.strftime('%Y-%m-%dT%H:%M:%SZ'), 'foto': png_color((200, 60 + 50 * k, 60))}, simples['vecino']))
        mis_codigos.append(res['codigo'])
        c.commit()
    s.avanzar(dias=1)
    for codigo, resultado in zip(mis_codigos[:2], ('confirmada', 'descartada')):  # el tercero queda sin inspeccionar
        inc = c.execute('SELECT id FROM incidencias WHERE codigo=?', (codigo,)).fetchone()
        core.inspeccionar(c, ctx({'resultado': resultado, 'gravedad': 2, 'observaciones': 'Revisado (dato ficticio).'},
                                 insp_cba[0]), inc['id'])
        c.commit()

    # Ente B: flujo mínimo para demostrar que el mismo sistema sirve a otro ente con otra configuración
    ids_b, insp_b, resp_b, auth_b, confirmadas_b, cfg_b = sembrar_ente(
        s, 'ENTE-B',
        [('Inspector ficticio B', 'inspector'), ('Responsable cuadrilla B1 (ficticia)', 'responsable'),
         ('Autoridad ficticia B', 'autoridad')],
        reportes=10)
    s.avanzar(dias=3)
    if confirmadas_b:
        flujo_completo(s, ids_b, insp_b, resp_b, auth_b, [confirmadas_b[0]], 'Mantenimiento de acceso rural ficticio',
                       'mantenimiento', [], 12)

    fin = s.t
    sembrar_obra(s, simples)
    c.close()
    core.CLOCK['now'] = None
    return fin


def main():
    """Dos pasadas: la primera mide la duración de la historia; la segunda la ubica para terminar ayer.
    Así ninguna fecha ficticia queda en el futuro respecto de la hora real."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    prueba = DB + '.tmp'
    if os.path.exists(prueba):
        os.remove(prueba)
    inicio_prueba = ahora - timedelta(days=200)
    duracion = construir(prueba, inicio_prueba) - inicio_prueba
    os.remove(prueba)
    reset_base()
    inicio = (ahora - duracion - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    construir(DB, inicio)
    ruta_cred = os.path.join(core.BASE_DIR, 'data', 'credenciales_prueba.txt')
    with open(ruta_cred, 'w', encoding='utf-8') as f:
        f.write('Usuarios de PRUEBA (datos ficticios, sólo desarrollo local)\n')
        f.write(f'Contraseña común: {PASSWORD_PRUEBA}\n\n')
        for ente, login_nombre, nombre, rol in CREDENCIALES:
            f.write(f'{ente:12} {login_nombre:24} {rol:12} {nombre}\n')
        f.write('\nCuentas simples de CBA-PILOTO (la contraseña es igual al usuario):\n')
        for login_nombre, nombre, rol in USUARIOS_SIMPLES:
            f.write(f'{"CBA-PILOTO":12} {login_nombre:24} {rol:12} {nombre}\n')
    print('Base ficticia creada:', DB, '| historia desde', inicio.date(), 'hasta', (inicio + duracion).date())
    print('Credenciales de prueba en', ruta_cred)


if __name__ == '__main__':
    main()
