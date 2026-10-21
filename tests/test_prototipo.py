"""Pruebas del prototipo SIGPLAT. Ejecutar desde 01_PROTOTIPO:  python -m unittest discover -s tests -v

Usan una base temporal y fotos temporales: no tocan data/. Los datos son ficticios.
"""
import base64
import json
import os
import secrets
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'app'))
import core  # noqa: E402
import obra  # noqa: E402
import server  # noqa: E402

def png(rgb):
    """PNG 4x4 de color sólido, válido, con hash distinto según el color."""
    def bloque(tipo, datos):
        return struct.pack('>I', len(datos)) + tipo + datos + struct.pack('>I', zlib.crc32(tipo + datos) & 0xffffffff)
    raw = b''.join(b'\x00' + bytes(rgb) * 4 for _ in range(4))
    data = b'\x89PNG\r\n\x1a\n' + bloque(b'IHDR', struct.pack('>IIBBBBB', 4, 4, 8, 2, 0, 0, 0)) + \
        bloque(b'IDAT', zlib.compress(raw)) + bloque(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def ahora_iso(delta_min=0):
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_min)).strftime('%Y-%m-%dT%H:%M:%SZ')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._fotos = core.FOTOS_DIR
        core.FOTOS_DIR = os.path.join(self.tmp, 'fotos')
        self._iter = core.PBKDF2_ITER
        core.PBKDF2_ITER = 1000  # sólo para que las pruebas sean rápidas
        core.FALLOS_LOGIN.clear()
        self.c = core.conectar(os.path.join(self.tmp, 'test.db'))
        core.cargar_configs(self.c)
        self.c.commit()
        self.cba = self.ente('CBA-PILOTO')
        self.ente_b = self.ente('ENTE-B')
        self.insp = self.usuario(self.cba, 'Inspector T', 'inspector')
        self.resp = self.usuario(self.cba, 'Responsable T', 'responsable')
        self.resp2 = self.usuario(self.cba, 'Responsable T2', 'responsable')
        self.auth = self.usuario(self.cba, 'Autoridad T', 'autoridad')
        self.resp_b = self.usuario(self.ente_b, 'Responsable B', 'responsable')
        self.auth_b = self.usuario(self.ente_b, 'Autoridad B', 'autoridad')

    def tearDown(self):
        self.c.close()
        core.FOTOS_DIR = self._fotos
        core.PBKDF2_ITER = self._iter

    def ente(self, codigo):
        return dict(self.c.execute('SELECT * FROM entes WHERE codigo=?', (codigo,)).fetchone())

    def usuario(self, ente, nombre, rol):
        uid = core.crear_usuario(self.c, ente['id'], nombre, f'{rol}.{secrets.token_hex(4)}', 'clave-de-prueba', rol)
        self.c.commit()
        return uid

    def llamar(self, fn, uid=None, body=None, q=None, *args):
        ctx = {'uid': str(uid) if uid else None, 'body': body or {}, 'q': q or {}}
        out = fn(self.c, ctx, *args)
        self.c.commit()
        return out

    def reporte(self, lat=-31.42, lon=-64.19, categoria='Bache', foto=None, extra=None):
        cuerpo = {'ente': 'CBA-PILOTO', 'categoria': categoria, 'descripcion': 'Bache de prueba en calzada',
                  'lat': lat, 'lon': lon, 'precision_m': 5, 'canal': 'web', 'capturada_at': ahora_iso(-5)}
        if foto is not None:
            cuerpo['foto'] = foto
        cuerpo.update(extra or {})
        _, r = self.llamar(core.reportar, None, cuerpo)
        return r

    def confirmar(self, codigo, gravedad=2):
        inc = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (codigo,)).fetchone()
        self.llamar(core.inspeccionar, self.insp, {'resultado': 'confirmada', 'gravedad': gravedad,
                                                    'observaciones': 'Verificado'}, None, inc['id'])
        return inc['id']

    def intervencion(self, incs, actividades=('Bacheo',), tipo='orden_trabajo'):
        _, d = self.llamar(core.crear_intervencion, self.auth, {
            'tipo': tipo, 'titulo': 'Intervención de prueba', 'responsable_id': self.resp,
            'incidencia_ids': incs, 'fecha_planificada_fin': (datetime.now() + timedelta(days=10)).strftime('%Y-%m-%d'),
            'actividades': list(actividades)})
        return d


class ReporteCiudadano(Base):
    def test_reporte_publico_genera_codigo_y_estado_recibida(self):
        r = self.reporte()
        self.assertTrue(r['codigo'].startswith('INC-'))
        self.assertEqual(r['estado'], 'recibida')

    def test_categoria_de_otro_ente_se_rechaza(self):
        with self.assertRaises(core.ApiError) as e:
            self.reporte(categoria='Pozo o hundimiento')
        self.assertEqual(e.exception.status, 400)

    def test_posible_duplicado_cercano_no_se_fusiona_automaticamente(self):
        primero = self.reporte()
        segundo = self.reporte(lat=-31.42 + 0.0002)  # unos 22 m
        self.assertEqual(segundo['posibles_duplicados'][0]['codigo'], primero['codigo'])
        filas = self.c.execute('SELECT COUNT(*) FROM incidencias').fetchone()[0]
        self.assertEqual(filas, 2)  # se conservan ambos

    def test_seguimiento_publico_no_expone_datos_internos(self):
        r = self.reporte()
        _, seg = self.llamar(core.seguimiento_publico, None, None, None, r['codigo'])
        self.assertNotIn('observaciones', json.dumps(seg))
        self.assertEqual(seg['estado'], 'Recibida, en revisión')

    def test_seguimiento_tolera_como_se_escribe_el_codigo(self):
        r = self.reporte()
        for escrito in (r['codigo'].lower(), ' ' + r['codigo'] + ' ', r['codigo'][4:].lower(), r['codigo'].replace('-', ' ')):
            _, seg = self.llamar(core.seguimiento_publico, None, None, None, escrito)
            self.assertEqual(seg['codigo'], r['codigo'])

    def test_seguimiento_distingue_formato_invalido_de_inexistente(self):
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.seguimiento_publico, None, None, None, 'INC-12')
        self.assertEqual(e.exception.status, 400)
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.seguimiento_publico, None, None, None, 'INC-ABCDEF')
        self.assertEqual(e.exception.status, 404)
        self.assertIn('INC-ABCDEF', e.exception.msg)

    def test_ubicacion_fuera_de_rango_se_rechaza(self):
        with self.assertRaises(core.ApiError):
            self.reporte(lat=200)


class InspeccionYRoles(Base):
    def test_responsable_no_puede_inspeccionar(self):
        r = self.reporte()
        inc = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (r['codigo'],)).fetchone()
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.inspeccionar, self.resp, {'resultado': 'confirmada', 'gravedad': 1}, None, inc['id'])
        self.assertEqual(e.exception.status, 403)

    def test_confirmada_exige_gravedad(self):
        r = self.reporte()
        inc = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (r['codigo'],)).fetchone()
        with self.assertRaises(core.ApiError):
            self.llamar(core.inspeccionar, self.insp, {'resultado': 'confirmada'}, None, inc['id'])

    def test_duplicada_suma_reportes_a_la_principal(self):
        p = self.reporte()
        s = self.reporte(lat=-31.42 + 0.0002)
        pid = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (p['codigo'],)).fetchone()['id']
        sid = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (s['codigo'],)).fetchone()['id']
        self.llamar(core.inspeccionar, self.insp, {'resultado': 'duplicada', 'duplicada_de': pid}, None, sid)
        cnt = self.c.execute('SELECT reportes_count FROM incidencias WHERE id=?', (pid,)).fetchone()[0]
        self.assertEqual(cnt, 2)

    def test_sin_identificacion_no_hay_acceso_interno(self):
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.listar_incidencias, None)
        self.assertEqual(e.exception.status, 401)


class CircuitoIntervencion(Base):
    def test_orden_de_trabajo_requiere_actividades(self):
        inc = self.confirmar(self.reporte()['codigo'])
        with self.assertRaises(core.ApiError):
            self.intervencion([inc], actividades=(), tipo='orden_trabajo')

    def test_solo_incidencias_confirmadas_pasan_a_intervencion(self):
        r = self.reporte()
        inc = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (r['codigo'],)).fetchone()['id']
        with self.assertRaises(core.ApiError):
            self.intervencion([inc])

    def test_responsable_debe_ser_del_mismo_ente(self):
        inc = self.confirmar(self.reporte()['codigo'])
        with self.assertRaises(core.ApiError):
            self.llamar(core.crear_intervencion, self.auth, {
                'tipo': 'mantenimiento', 'titulo': 'Mal responsable', 'responsable_id': self.resp_b,
                'incidencia_ids': [inc], 'fecha_planificada_fin': '2030-01-01'})

    def test_flujo_completo_con_separacion_de_funciones(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc], actividades=('Bacheo',))
        iid = d['id']
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, iid)
        act = d['actividades'][0]['id']
        # un responsable no asignado no puede declarar
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.declarar_avance, self.resp2, {'actividad_id': act, 'pct': 50}, None, iid)
        self.assertEqual(e.exception.status, 403)
        self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 100}, None, iid)
        av = self.c.execute('SELECT id FROM avances WHERE actividad_id=?', (act,)).fetchone()['id']
        # quien declara no puede validar su propio avance
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.revisar_avance, self.resp, {'accion': 'validar'}, None, av)
        self.assertEqual(e.exception.status, 403)
        # sin foto de cierre no se puede declarar la finalización
        with self.assertRaises(core.ApiError):
            self.llamar(core.transicion_intervencion, self.resp, {'accion': 'declarar_finalizacion'}, None, iid)
        self.llamar(core.subir_evidencia, self.resp, {'tipo': 'cierre', 'foto': png(10), 'lat': d['lat'], 'lon': d['lon'],
                                                      'precision_m': 4, 'capturada_at': ahora_iso(-3)}, None, iid)
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'declarar_finalizacion'}, None, iid)
        # sin validación no se puede validar el cierre
        with self.assertRaises(core.ApiError):
            self.llamar(core.transicion_intervencion, self.insp, {'accion': 'validar_cierre'}, None, iid)
        self.llamar(core.revisar_avance, self.insp, {'accion': 'validar'}, None, av)
        ev = self.c.execute("SELECT id FROM evidencias WHERE intervencion_id=? AND tipo='cierre'", (iid,)).fetchone()['id']
        self.llamar(core.revisar_evidencia, self.insp, {'accion': 'validada'}, None, ev)
        self.llamar(core.transicion_intervencion, self.insp, {'accion': 'validar_cierre'}, None, iid)
        self.llamar(core.transicion_intervencion, self.auth, {'accion': 'cerrar'}, None, iid)
        estado_inc = self.c.execute('SELECT estado FROM incidencias WHERE id=?', (inc,)).fetchone()['estado']
        self.assertEqual(estado_inc, 'resuelta')
        hist = self.c.execute("SELECT COUNT(*) FROM historial WHERE entidad='intervencion' AND entidad_id=?", (iid,)).fetchone()[0]
        self.assertGreaterEqual(hist, 5)

    def test_avance_no_puede_bajar_del_validado(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc], actividades=('Bacheo',))
        iid, act = d['id'], d['actividades'][0]['id']
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, iid)
        self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 60}, None, iid)
        av = self.c.execute('SELECT id FROM avances WHERE actividad_id=?', (act,)).fetchone()['id']
        self.llamar(core.revisar_avance, self.insp, {'accion': 'validar'}, None, av)
        with self.assertRaises(core.ApiError):
            self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 40}, None, iid)

    def test_diferencia_declarado_validado_solo_con_validacion(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc], actividades=('Bacheo',))
        iid, act = d['id'], d['actividades'][0]['id']
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, iid)
        self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 80}, None, iid)
        _, ficha = self.llamar(core.ficha_intervencion, self.insp, None, None, iid)
        self.assertIsNone(ficha['actividades'][0]['diferencia_declarado_validado'])
        av = self.c.execute('SELECT id FROM avances WHERE actividad_id=?', (act,)).fetchone()['id']
        self.llamar(core.revisar_avance, self.insp, {'accion': 'validar', 'pct_validado': 50}, None, av)
        _, ficha = self.llamar(core.ficha_intervencion, self.insp, None, None, iid)
        self.assertEqual(ficha['actividades'][0]['diferencia_declarado_validado'], 30)


class Evidencias(Base):
    def test_sin_fecha_captura_y_lejos_quedan_marcadas(self):
        r = self.reporte(foto=png(20))
        inc = self.c.execute('SELECT id FROM incidencias WHERE codigo=?', (r['codigo'],)).fetchone()['id']
        ev = self.c.execute('SELECT banderas FROM evidencias WHERE incidencia_id=?', (inc,)).fetchone()
        self.assertEqual(ev['banderas'], '[]')  # con captura y precisión: sin banderas
        inc2 = self.confirmar(self.reporte(lat=-31.40, lon=-64.10)['codigo'])
        d = self.intervencion([inc2])
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, d['id'])
        self.llamar(core.subir_evidencia, self.resp, {'tipo': 'avance', 'foto': png(30), 'lat': -31.5, 'lon': -64.3,
                                                     'precision_m': 300}, None, d['id'])
        ultima = self.c.execute('SELECT banderas FROM evidencias ORDER BY id DESC LIMIT 1').fetchone()['banderas']
        self.assertIn('sin_fecha_de_captura', ultima)
        self.assertIn('lejos_de_la_ubicacion_de_referencia', ultima)
        self.assertIn('precision_geografica_baja_o_desconocida', ultima)

    def test_foto_identica_se_detecta(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc])
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, d['id'])
        f = png(77)
        self.llamar(core.subir_evidencia, self.resp, {'tipo': 'avance', 'foto': f, 'lat': d['lat'], 'lon': d['lon'],
                                                     'precision_m': 5, 'capturada_at': ahora_iso(-2)}, None, d['id'])
        self.llamar(core.subir_evidencia, self.resp, {'tipo': 'avance', 'foto': f, 'lat': d['lat'], 'lon': d['lon'],
                                                     'precision_m': 5, 'capturada_at': ahora_iso(-2)}, None, d['id'])
        ultima = self.c.execute('SELECT banderas FROM evidencias ORDER BY id DESC LIMIT 1').fetchone()['banderas']
        self.assertIn('foto_identica_a_evidencia_', ultima)

    def test_reemplazo_conserva_la_anterior(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc])
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, d['id'])
        base = {'tipo': 'avance', 'lat': d['lat'], 'lon': d['lon'], 'precision_m': 5, 'capturada_at': ahora_iso(-2)}
        _, a = self.llamar(core.subir_evidencia, self.resp, dict(base, foto=png(1)), None, d['id'])
        _, b = self.llamar(core.subir_evidencia, self.resp, dict(base, foto=png(2), reemplaza_a=a['id']), None, d['id'])
        self.assertEqual(b['reemplaza_a'], a['id'])
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM evidencias WHERE id=?', (a['id'],)).fetchone()[0], 1)

    def test_evidencias_no_se_borran_ni_historial_se_edita(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc])
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, d['id'])
        self.llamar(core.subir_evidencia, self.resp, {'tipo': 'avance', 'foto': png(5), 'lat': d['lat'], 'lon': d['lon'],
                                                     'precision_m': 5, 'capturada_at': ahora_iso(-2)}, None, d['id'])
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM evidencias')
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('UPDATE historial SET accion="x"')
        self.c.rollback()

    def test_foto_invalida_se_rechaza(self):
        with self.assertRaises(core.ApiError):
            self.reporte(foto='data:text/plain;base64,AAAA')

    def test_foto_mayor_a_3mb_se_rechaza(self):
        grande = 'data:image/png;base64,' + base64.b64encode(b'\x00' * (3 * 1024 * 1024 + 1)).decode()
        with self.assertRaises(core.ApiError) as e:
            self.reporte(foto=grande)
        self.assertEqual(e.exception.status, 413)


class Aislamiento(Base):
    def test_usuario_de_otro_ente_no_ve_la_intervencion(self):
        inc = self.confirmar(self.reporte()['codigo'])
        d = self.intervencion([inc])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.ficha_intervencion, self.auth_b, None, None, d['id'])
        self.assertEqual(e.exception.status, 404)

    def test_categoria_de_ente_b_no_existe_en_cba(self):
        with self.assertRaises(core.ApiError):
            self.llamar(core.reportar, None, {'ente': 'CBA-PILOTO', 'categoria': 'Veredas rotas', 'descripcion': 'Vereda rota ficticia',
                                              'lat': -31.4, 'lon': -64.2})

    def test_entes_de_otra_configuracion_mantienen_sus_propias_categorias(self):
        _, ents = self.llamar(core.listar_entes, None)
        cats = {e['codigo']: e['config']['categorias'] for e in ents}
        self.assertNotEqual(cats['CBA-PILOTO'], cats['ENTE-B'])


class Indicadores(Base):
    def test_sin_datos_no_se_inventan_porcentajes(self):
        self.reporte()
        _, d = self.llamar(core.indicadores, self.auth)
        self.assertIsNone(d['cumplimiento_plazos_pct']['valor'])
        self.assertEqual(d['cumplimiento_plazos_pct']['nota'], 'Sin datos suficientes')
        self.assertEqual(d['incidencias_pendientes']['valor'], 1)


class ServidorHTTP(Base):
    def setUp(self):
        super().setUp()
        server.CONEXION['c'] = self.c
        self.srv = ThreadingHTTPServer(('127.0.0.1', 0), server.Manejador)
        self.puerto = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def pedir(self, metodo, ruta, cuerpo=None, cookie=None, cabeceras=None):
        """Llama a la API. Guarda las cabeceras de respuesta en self.ultimas_cabeceras."""
        cab = {'Content-Type': 'application/json', **(cabeceras or {})}
        if cookie:
            cab['Cookie'] = f'{server.COOKIE}={cookie}'
        data = json.dumps(cuerpo if cuerpo is not None else {}).encode() if metodo == 'POST' else None
        req = urllib.request.Request(f'http://127.0.0.1:{self.puerto}{ruta}', data=data, method=metodo, headers=cab)
        try:
            with urllib.request.urlopen(req) as r:
                self.ultimas_cabeceras = r.headers
                return r.status, json.loads(r.read() or b'null')
        except urllib.error.HTTPError as e:
            self.ultimas_cabeceras = e.headers
            return e.code, json.loads(e.read() or b'null')

    def sesion(self, login, clave):
        st, cuerpo = self.pedir('POST', '/api/login', {'login': login, 'password': clave})
        token = None
        if st == 200:
            jar = self.ultimas_cabeceras.get('Set-Cookie', '')
            token = jar.split(';')[0].split('=', 1)[1]
        return st, cuerpo, token

    def test_api_y_estaticos(self):
        st, ents = self.pedir('GET', '/api/entes')
        self.assertEqual(st, 200)
        self.assertEqual({e['codigo'] for e in ents}, {'CBA-PILOTO', 'ENTE-B'})
        st, _ = self.pedir('GET', '/api/indicadores')
        self.assertEqual(st, 401)
        st, rep = self.pedir('POST', '/api/incidencias', {'ente': 'CBA-PILOTO', 'categoria': 'Bache',
                                                         'descripcion': 'Bache por HTTP', 'lat': -31.4, 'lon': -64.2})
        self.assertEqual(st, 201)
        st, seg = self.pedir('GET', '/api/seguimiento/' + rep['codigo'])
        self.assertEqual(st, 200)
        st, seg = self.pedir('GET', '/api/seguimiento/%20' + rep['codigo'].lower().replace('-', '%20') + '%20')
        self.assertEqual(st, 200)  # el código llega codificado en la URL, con espacios y en minúsculas
        self.assertEqual(seg['codigo'], rep['codigo'])
        st, _ = self.pedir('GET', '/api/seguimiento/INC-12')
        self.assertEqual(st, 400)  # formato inválido: se explica, no se responde "ruta inexistente"
        st, _ = self.pedir('GET', '/api/no-existe')
        self.assertEqual(st, 404)
        st, _ = self.pedir('POST', '/api/incidencias', {'ente': 'CBA-PILOTO', 'categoria': 'Bache', 'descripcion': 'corto'})
        self.assertEqual(st, 400)
        req = urllib.request.urlopen(f'http://127.0.0.1:{self.puerto}/')
        self.assertIn(b'SIGPLAT', req.read())

    def test_foto_requiere_identificacion(self):
        st, _ = self.pedir('GET', '/api/fotos/' + '0' * 64 + '.png')
        self.assertEqual(st, 401)


class ReferenciaExterna(Base):
    def test_referencia_a_sigo_se_guarda_y_sistema_invalido_se_rechaza(self):
        inc = self.confirmar(self.reporte()['codigo'])
        _, d = self.llamar(core.crear_intervencion, self.auth, {
            'tipo': 'mantenimiento', 'titulo': 'Con expediente', 'responsable_id': self.resp, 'incidencia_ids': [inc],
            'fecha_planificada_fin': '2030-01-01', 'sistema_externo': 'SIGO', 'expediente_externo': 'FICT-EXP-001'})
        self.assertEqual((d['sistema_externo'], d['expediente_externo']), ('SIGO', 'FICT-EXP-001'))
        inc2 = self.confirmar(self.reporte(lat=-31.3)['codigo'])
        with self.assertRaises(core.ApiError):
            self.llamar(core.crear_intervencion, self.auth, {
                'tipo': 'mantenimiento', 'titulo': 'Sistema inventado', 'responsable_id': self.resp, 'incidencia_ids': [inc2],
                'fecha_planificada_fin': '2030-01-01', 'sistema_externo': 'OTRO'})


class AutorizacionPorRol(Base):
    def test_responsable_solo_ve_sus_intervenciones(self):
        inc = self.confirmar(self.reporte()['codigo'])
        propia = self.intervencion([inc])  # asignada a self.resp
        _, lista = self.llamar(core.listar_intervenciones, self.resp)
        self.assertEqual([i['id'] for i in lista], [propia['id']])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.ficha_intervencion, self.resp2, None, None, propia['id'])
        self.assertEqual(e.exception.status, 404)

    def test_responsable_no_accede_a_tablero_ni_bandeja(self):
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.indicadores, self.resp)
        self.assertEqual(e.exception.status, 403)
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.listar_incidencias, self.resp)
        self.assertEqual(e.exception.status, 403)

    def test_inspector_ve_tablero_pero_no_crea_intervenciones(self):
        self.llamar(core.indicadores, self.insp)  # no lanza
        inc = self.confirmar(self.reporte()['codigo'])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.crear_intervencion, self.insp, {'tipo': 'mantenimiento', 'titulo': 'No debería crearse',
                                                             'responsable_id': self.resp, 'incidencia_ids': [inc],
                                                             'fecha_planificada_fin': '2030-01-01'})
        self.assertEqual(e.exception.status, 403)


class TrabajoDeInspeccion(Base):
    def en_ejecucion(self):
        inc = self.confirmar(self.reporte(lat=-31.30)['codigo'])
        d = self.intervencion([inc], actividades=('Bacheo',))
        self.llamar(core.transicion_intervencion, self.resp, {'accion': 'iniciar'}, None, d['id'])
        return d

    def foto_de(self, quien, iid, d, color=20):
        self.llamar(core.subir_evidencia, quien, {'tipo': 'avance', 'foto': png(color), 'lat': d['lat'], 'lon': d['lon'],
                                                  'precision_m': 4, 'capturada_at': ahora_iso(-3)}, None, iid)

    def test_seguimiento_publico_incluye_el_estado_como_codigo(self):
        r = self.reporte()
        _, seg = self.llamar(core.seguimiento_publico, None, None, None, r['codigo'])
        self.assertEqual(seg['estado_codigo'], 'recibida')

    def test_mi_trabajo_lista_lo_pendiente_y_excluye_lo_propio(self):
        pendiente = self.reporte(lat=-31.5)
        d = self.en_ejecucion()
        act = d['actividades'][0]['id']
        self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 70}, None, d['id'])
        self.foto_de(self.resp, d['id'], d)
        _, t = self.llamar(core.mi_trabajo, self.insp)
        self.assertEqual([i['codigo'] for i in t['para_inspeccionar']], [pendiente['codigo']])
        self.assertEqual([(a['actividad'], a['pct']) for a in t['avances_pendientes']], [('Bacheo', 70.0)])
        self.assertEqual(len(t['evidencias_pendientes']), 1)
        self.assertTrue(t['evidencias_pendientes'][0]['url'].startswith('/api/fotos/'))
        self.assertEqual(t['contadores'], {'para_inspeccionar': 1, 'avances': 1, 'evidencias': 1, 'cierres': 0})
        # una foto que sube la misma persona de inspección no aparece para que la revise ella, pero sí para otra
        self.foto_de(self.insp, d['id'], d, color=30)
        _, propia = self.llamar(core.mi_trabajo, self.insp)
        _, ajena = self.llamar(core.mi_trabajo, self.auth)
        self.assertEqual(len(propia['evidencias_pendientes']), 1)
        self.assertEqual(len(ajena['evidencias_pendientes']), 2)

    def test_mi_trabajo_solo_para_inspeccion(self):
        for quien in (self.resp, None):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(core.mi_trabajo, quien)
            self.assertIn(e.exception.status, (401, 403))

    def test_foto_de_inspeccion_sobre_una_incidencia(self):
        r = self.reporte()
        inc = self.c.execute('SELECT id, lat, lon FROM incidencias WHERE codigo=?', (r['codigo'],)).fetchone()
        cuerpo = {'foto': png(40), 'lat': inc['lat'], 'lon': inc['lon'], 'precision_m': 5, 'capturada_at': ahora_iso(-2)}
        st, ev = self.llamar(core.subir_evidencia_incidencia, self.insp, cuerpo, None, inc['id'])
        self.assertEqual((st, ev['tipo']), (201, 'inspeccion'))
        _, ficha = self.llamar(core.ficha_incidencia, self.insp, None, None, inc['id'])
        self.assertIn('inspeccion', [e['tipo'] for e in ficha['evidencias']])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.subir_evidencia_incidencia, self.resp, cuerpo, None, inc['id'])
        self.assertEqual(e.exception.status, 403)
        self.llamar(core.inspeccionar, self.insp, {'resultado': 'descartada', 'observaciones': 'No corresponde'}, None, inc['id'])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(core.subir_evidencia_incidencia, self.insp, cuerpo, None, inc['id'])
        self.assertEqual(e.exception.status, 409)

    def test_historial_de_la_intervencion_incluye_avances_y_evidencias(self):
        d = self.en_ejecucion()
        act = d['actividades'][0]['id']
        self.llamar(core.declarar_avance, self.resp, {'actividad_id': act, 'pct': 60}, None, d['id'])
        self.foto_de(self.resp, d['id'], d)
        av = self.c.execute('SELECT id FROM avances WHERE actividad_id=?', (act,)).fetchone()['id']
        self.llamar(core.revisar_avance, self.insp, {'accion': 'validar', 'pct_validado': 50}, None, av)
        _, ficha = self.llamar(core.ficha_intervencion, self.insp, None, None, d['id'])
        hechos = {(h['entidad'], h['accion']) for h in ficha['historial']}
        self.assertIn(('avance', 'declarado'), hechos)
        self.assertIn(('avance', 'validado'), hechos)
        self.assertIn(('evidencia', 'recibida'), hechos)
        self.assertIn(('intervencion', 'creada'), hechos)


class ObraBase(Base):
    """Obra ficticia de prueba: un rubro y dos ítems, con replanteo hace 75 días (tres meses para medir)."""
    def setUp(self):
        super().setUp()
        self.contr = self.usuario(self.cba, 'Contratista T', 'contratista')
        self.admin = self.usuario(self.cba, 'Administrativo T', 'administrativo')
        self.hoy = datetime.now(timezone.utc).date()
        self.replanteo = self.hoy - timedelta(days=75)
        self.m0 = obra.mes_de(self.replanteo)
        self.m1, self.m2 = obra.sumar_meses(self.m0, 1), obra.sumar_meses(self.m0, 2)
        self.oid = self.nueva_obra()
        self.ids = {r['nro']: r['id'] for r in self.c.execute('SELECT id, nro FROM items_obra WHERE obra_id=?', (self.oid,))}

    def nueva_obra(self, replanteo=None, plazo=360, inspector='por_defecto', **kw):
        items = [{'nro': 1, 'descripcion': 'Movimiento de suelos', 'titulo': True},
                 {'nro': 2, 'descripcion': 'Excavación', 'unidad': 'm3', 'cantidad': 100, 'precio_cent': 2500000},
                 {'nro': 3, 'descripcion': 'Hormigón H21', 'unidad': 'm3', 'cantidad': 50, 'precio_cent': 16000000}]
        ideal = {2: {self.m0: 60, self.m1: 40}, 3: {self.m1: 25, self.m2: 25}}
        oid = obra.crear_obra(self.c, self.cba['id'], 'Obra ficticia de prueba', 'Contratista ficticia',
                              (replanteo or self.replanteo).strftime('%Y-%m-%d'), plazo, items, ideal, contratista_id=self.contr,
                              inspector_id=self.insp if inspector == 'por_defecto' else inspector, **kw)
        self.c.commit()
        return oid

    def computo(self, oid=None, uid=None):
        _, k = self.llamar(obra.nuevo_computo, uid or self.insp, None, None, oid or self.oid)
        return k

    def guardar(self, kid, c2=0, c3=0, personal=40, **extra):
        cuerpo = {'items': [{'item_id': self.ids[2], 'cantidad': c2}, {'item_id': self.ids[3], 'cantidad': c3}],
                  'personal_afectado': personal, **extra}
        return self.llamar(obra.guardar_computo, self.insp, cuerpo, None, kid)[1]

    def presentar(self, kid):
        return self.llamar(obra.presentar_computo, self.insp, None, None, kid)[1]

    def certificado_directo(self, kid, oid=None):
        """Emite el certificado de un cómputo presentado, como lo haría el administrativo."""
        return self.llamar(obra.emitir_certificado, self.admin, {'computo_id': kid, 'observaciones': 'Certificación de prueba'},
                           None, oid or self.oid)[1]

    def computo_certificado(self, c2=0, c3=0, **extra):
        k = self.computo()
        self.guardar(k['id'], c2, c3, **extra)
        self.presentar(k['id'])
        self.certificado_directo(k['id'])
        return k['id']

    def error(self, fn, uid, body=None, *args):
        with self.assertRaises(core.ApiError) as e:
            self.llamar(fn, uid, body, None, *args)
        return e.exception


class ObraComputo(ObraBase):
    def test_dinero_en_centavos_con_redondeo_comercial(self):
        self.assertEqual([obra.centavos(0.5), obra.centavos(1.5), obra.centavos(2.5)], [1, 2, 3])  # round() de Python daría 0, 2, 2
        self.assertEqual(obra.importe(33.333, 1999), 66633)
        self.assertEqual(obra.importe(100, 2500000), 250000000)

    def test_el_primer_computo_es_el_mes_del_replanteo_y_queda_en_borrador(self):
        k = self.computo()
        self.assertEqual((k['mes'], k['estado'], k['editable']), (self.m0, 'borrador', True))
        self.assertEqual([f['nro'] for f in k['items']], [1, 2, 3])
        self.assertTrue(k['items'][0]['es_titulo'])

    def test_sin_inspector_asignado_no_se_carga_el_primer_computo(self):
        oid = self.nueva_obra(inspector=None)
        e = self.error(obra.nuevo_computo, self.insp, None, oid)
        self.assertEqual(e.status, 409)
        self.assertIn('inspector', e.msg)

    def test_solo_el_inspector_asignado_mide(self):
        otro = self.usuario(self.cba, 'Otro inspector', 'inspector')
        self.assertEqual(self.error(obra.nuevo_computo, otro, None, self.oid).status, 404)
        for quien in (self.contr, self.auth, self.admin):
            self.assertEqual(self.error(obra.nuevo_computo, quien, None, self.oid).status, 403)

    def test_se_carga_el_avance_del_mes_y_se_acumula_sin_float(self):
        k0 = self.computo_certificado(c2=50)
        k1 = self.computo()
        v = self.guardar(k1['id'], c2=30, c3=10.5)
        f = {x['nro']: x for x in v['items'] if not x['es_titulo']}
        self.assertEqual((f[2]['anterior'], f[2]['presente'], f[2]['acumulada']), (50.0, 30.0, 80.0))
        self.assertEqual((f[2]['pct_anterior'], f[2]['pct_presente'], f[2]['pct_acumulado']), (50.0, 30.0, 80.0))
        self.assertEqual(f[3]['pct_acumulado'], 21.0)
        self.assertEqual(f[2]['importe_presente_cent'], 75000000)
        self.assertEqual(f[3]['importe_presente_cent'], 168000000)
        self.assertEqual(v['totales']['importe_presente_cent'], 243000000)
        self.assertEqual(v['mes'], self.m1)

    def test_para_presentar_hace_falta_el_personal_afectado(self):
        k = self.computo()
        self.guardar(k['id'], c2=10, personal=None)
        e = self.error(obra.presentar_computo, self.insp, None, k['id'])
        self.assertEqual(e.status, 400)
        self.assertIn('personal afectado', e.msg)
        self.guardar(k['id'], c2=10, personal=35)
        self.assertEqual(self.presentar(k['id'])['estado'], 'presentado')

    def test_no_se_abre_un_computo_sin_el_certificado_del_mes_anterior(self):
        k = self.computo()
        self.guardar(k['id'], c2=10)
        e = self.error(obra.nuevo_computo, self.insp, None, self.oid)  # el anterior sigue en borrador
        self.assertEqual(e.status, 409)
        self.presentar(k['id'])
        e = self.error(obra.nuevo_computo, self.insp, None, self.oid)  # presentado, pero sin certificado
        self.assertIn('certificado del mes anterior', e.msg)
        self.certificado_directo(k['id'])
        self.assertEqual(self.computo()['mes'], self.m1)

    def test_un_computo_solo_se_modifica_o_elimina_sin_certificado(self):
        k = self.computo()
        self.guardar(k['id'], c2=10)
        self.presentar(k['id'])
        self.guardar(k['id'], c2=20)  # presentado y sin certificado: se puede corregir
        v = self.llamar(obra.ver_computo, self.insp, None, None, k['id'])[1]
        self.assertEqual(v['items'][1]['presente'], 20.0)
        self.certificado_directo(k['id'])
        self.assertEqual(self.error(obra.guardar_computo, self.insp, {'personal_afectado': 3}, k['id']).status, 409)
        self.assertEqual(self.error(obra.eliminar_computo, self.insp, None, k['id']).status, 409)

    def test_eliminar_libera_el_mes_pero_queda_el_registro(self):
        k = self.computo()
        self.llamar(obra.eliminar_computo, self.insp, None, None, k['id'])
        self.assertEqual(self.c.execute('SELECT estado FROM computos WHERE id=?', (k['id'],)).fetchone()['estado'], 'eliminado')
        self.assertEqual(self.computo()['mes'], self.m0)  # el mes vuelve a estar disponible
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM computos')

    def test_el_computo_final_cierra_la_serie(self):
        k = self.computo_certificado(c2=100, c3=50, es_final=True)
        e = self.error(obra.nuevo_computo, self.insp, None, self.oid)
        self.assertIn('cómputo final', e.msg)

    def test_lo_medido_por_encima_de_lo_contratado_avisa_y_exige_observacion(self):
        k = self.computo()
        v = self.guardar(k['id'], c2=120)
        self.assertEqual(v['items_que_superan_oferta'], [2])
        e = self.error(obra.presentar_computo, self.insp, None, k['id'])
        self.assertEqual(e.status, 400)
        self.assertIn('superan la cantidad contratada', e.msg)
        self.guardar(k['id'], c2=120, observaciones='Se midió demasía autorizada por la dirección')
        self.assertEqual(self.presentar(k['id'])['estado'], 'presentado')  # avisa, no bloquea

    def test_con_la_obra_vencida_hace_falta_el_plazo_de_certificacion(self):
        oid = self.nueva_obra(plazo=30)  # replanteo hace 75 días: vencida
        e = self.error(obra.nuevo_computo, self.insp, None, oid)
        self.assertEqual((e.status, e.msg), (409, 'Para dar de alta un plazo cert. obra vencida haga click aquí'))
        self.assertEqual(self.error(obra.registrar_plazo_vencida, self.insp, {'dias': 20}, oid).status, 403)
        self.llamar(obra.registrar_plazo_vencida, self.admin, {'dias': 20}, None, oid)
        self.assertEqual(self.computo(oid)['mes'], self.m0)
        self.assertEqual(self.error(obra.registrar_plazo_vencida, self.admin, {'dias': 20}, self.oid).status, 409)  # esta no venció

    def test_no_se_mide_un_mes_que_todavia_no_empezo(self):
        oid = self.nueva_obra(replanteo=self.hoy + timedelta(days=40))
        self.assertEqual(self.error(obra.nuevo_computo, self.insp, None, oid).status, 409)

    def test_curva_ideal_contra_real_con_n_y_nota(self):
        ficha = self.llamar(obra.ficha_obra, self.insp, None, None, self.oid)[1]
        self.assertIsNone(ficha['curva']['desvio']['valor'])  # sin certificados no se inventa un desvío
        self.assertEqual(ficha['curva']['desvio']['nota'], 'Sin datos suficientes')
        self.computo_certificado(c2=50)
        cv = self.llamar(obra.curva_obra, self.insp, None, None, self.oid)[1]
        p0 = cv['puntos'][0]
        self.assertEqual((p0['mes'], p0['ideal_acum_cent'], p0['real_acum_cent'], p0['desvio_cent']),
                         (self.m0, 150000000, 125000000, -25000000))
        self.assertEqual(cv['monto_contrato_cent'], 1050000000)
        self.assertEqual((p0['ideal_acum_pct'], p0['real_acum_pct'], p0['desvio_pp']), (14.29, 11.9, -2.38))
        self.assertEqual(cv['desvio']['n'], 1)
        self.assertIsNone(cv['puntos'][1]['real_acum_cent'])  # los meses sin certificado no tienen valor real

    def test_cada_rol_ve_solo_sus_obras(self):
        otro = self.usuario(self.cba, 'Otro inspector', 'inspector')
        otro_c = self.usuario(self.cba, 'Otro contratista', 'contratista')
        ve = lambda quien: [o['id'] for o in self.llamar(obra.listar_obras, quien)[1]]  # noqa: E731
        self.assertEqual(ve(self.insp), [self.oid])
        self.assertEqual(ve(self.contr), [self.oid])
        for quien in (self.auth, self.admin):
            self.assertEqual(ve(quien), [self.oid])
        self.assertEqual((ve(otro), ve(otro_c)), ([], []))
        self.assertEqual(self.error(obra.ficha_obra, otro, None, self.oid).status, 404)
        self.assertEqual(self.error(obra.ficha_obra, otro_c, None, self.oid).status, 404)
        self.assertEqual(self.error(obra.ficha_obra, self.resp, None, self.oid).status, 403)  # el responsable de intervenciones no ve obras

    def test_otro_ente_no_ve_la_obra(self):
        self.assertEqual(self.error(obra.ficha_obra, self.auth_b, None, self.oid).status, 404)


class Certificados(ObraBase):
    def setUp(self):
        super().setUp()
        self.oid = self.nueva_obra(anticipo_pct=10, fondo_reparo_pct=5, fondo_inspeccion_pct=1)  # con anticipo y fondos
        self.ids = {r['nro']: r['id'] for r in self.c.execute('SELECT id, nro FROM items_obra WHERE obra_id=?', (self.oid,))}

    def presentado(self, c2=50, c3=0, **extra):
        k = self.computo(self.oid)
        self.guardar(k['id'], c2, c3, **extra)
        self.presentar(k['id'])
        return k['id']

    def emitir(self, kid, quien=None, **cuerpo):
        return self.llamar(obra.emitir_certificado, quien or self.admin, dict({'computo_id': kid}, **cuerpo), None, self.oid)[1]

    def test_liquidacion_con_valores_calculados_a_mano(self):
        liq = obra.liquidar(100000000, 10, 5, 1)  # $ 1.000.000,00
        self.assertEqual((liq['descuento_anticipo_cent'], liq['fondo_reparo_cent'], liq['fondo_inspeccion_cent']),
                         (10000000, 5000000, 1000000))
        self.assertEqual((liq['neto_cent'], liq['iva_cent'], liq['a_abonar_cent']), (84000000, 17640000, 101640000))
        redondeo = obra.liquidar(1000, 10, 5, 1)
        self.assertEqual((redondeo['neto_cent'], redondeo['iva_cent'], redondeo['a_abonar_cent']), (840, 176, 1016))
        self.assertEqual(obra.liquidar(25, 10, 0, 0)['descuento_anticipo_cent'], 3)  # 2,5 centavos sube: mitad hacia arriba

    def test_el_certificado_nace_pendiente_con_la_liquidacion_del_computo(self):
        cert = self.emitir(self.presentado(c2=50))
        self.assertEqual((cert['numero'], cert['tipo'], cert['estado'], cert['leyenda']), (1, 'parcial', 'pendiente', 'NO AUTORIZADO'))
        self.assertEqual(cert['monto_cent'], 125000000)  # 50 m3 x $ 25.000
        liq = cert['liquidacion']
        self.assertEqual((liq['importe_mes_cent'], liq['importe_acumulado_cent'], liq['importe_anterior_cent']), (125000000, 125000000, 0))
        self.assertEqual(liq['a_abonar_cent'], liq['neto_cent'] + liq['iva_cent'])
        self.assertEqual(cert['firmas'][1]['cargo'], 'Administrativo (emite)')
        self.assertIsNone(cert['firmas'][2]['nombre'])  # la autorización todavía no se firmó

    def test_el_segundo_certificado_resta_lo_ya_certificado(self):
        self.emitir(self.presentado(c2=50))
        cert2 = self.emitir(self.presentado(c2=30, c3=10))
        liq = cert2['liquidacion']
        self.assertEqual(cert2['numero'], 2)
        self.assertEqual((liq['importe_anterior_cent'], liq['importe_mes_cent']), (125000000, 75000000 + 160000000))
        self.assertEqual(liq['importe_acumulado_cent'], 125000000 + 75000000 + 160000000)

    def test_solo_el_administrativo_emite(self):
        kid = self.presentado()
        for quien in (self.insp, self.auth, self.contr):
            with self.assertRaises(core.ApiError) as e:
                self.emitir(kid, quien)
            self.assertEqual(e.exception.status, 403)

    def test_un_monto_cero_exige_observaciones(self):
        kid = self.presentado(c2=0, c3=0)
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.emitir_certificado, self.admin, {'computo_id': kid}, None, self.oid)
        self.assertEqual(e.exception.status, 400)
        self.assertIn('$ 0', e.exception.msg)
        cert = self.emitir(kid, observaciones='Mes sin avance: obra demorada por lluvias')
        self.assertEqual(cert['monto_cent'], 0)

    def test_no_se_emite_dos_veces_ni_de_un_borrador(self):
        k = self.computo(self.oid)
        with self.assertRaises(core.ApiError) as e:
            self.emitir(k['id'])
        self.assertEqual(e.exception.status, 409)
        self.guardar(k['id'], 10)
        self.presentar(k['id'])
        self.emitir(k['id'])
        with self.assertRaises(core.ApiError) as e:
            self.emitir(k['id'])
        self.assertIn('ya tiene su certificado', e.exception.msg)

    def test_la_autoridad_autoriza_y_corre_el_vencimiento(self):
        cert = self.emitir(self.presentado())
        aut = self.llamar(obra.autorizar_certificado, self.auth, {}, None, cert['id'])[1]
        self.assertEqual((aut['estado'], aut['leyenda']), ('autorizado', None))
        self.assertEqual(aut['fecha_autorizacion'], self.hoy.strftime('%Y-%m-%d'))
        self.assertEqual(aut['vence_at'], (self.hoy + timedelta(days=30)).strftime('%Y-%m-%d'))
        self.assertEqual(aut['firmas'][2]['nombre'], 'Autoridad T')
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.autorizar_certificado, self.auth, {}, None, cert['id'])
        self.assertEqual(e.exception.status, 409)

    def test_quien_emitio_no_puede_autorizar_y_solo_la_autoridad_autoriza(self):
        cert = self.emitir(self.presentado())
        for quien in (self.admin, self.insp, self.contr):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(obra.autorizar_certificado, quien, {}, None, cert['id'])
            self.assertEqual(e.exception.status, 403)
        self.c.execute('UPDATE certificados SET emitido_por=? WHERE id=?', (self.auth, cert['id']))  # la misma persona emite y autoriza
        self.c.commit()
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.autorizar_certificado, self.auth, {}, None, cert['id'])
        self.assertEqual(e.exception.status, 403)
        self.assertIn('no puede autorizarlo', e.exception.msg)

    def test_la_fecha_de_autorizacion_no_puede_ser_futura_ni_anterior_a_la_emision(self):
        cert = self.emitir(self.presentado())
        for fecha in ((self.hoy + timedelta(days=2)).strftime('%Y-%m-%d'), (self.hoy - timedelta(days=3)).strftime('%Y-%m-%d'), '2026-13-40'):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(obra.autorizar_certificado, self.auth, {'fecha_autorizacion': fecha}, None, cert['id'])
            self.assertEqual(e.exception.status, 400, fecha)

    def test_solo_se_anula_el_ultimo_certificado_si_esta_pendiente(self):
        c1 = self.emitir(self.presentado(c2=10))
        c2 = self.emitir(self.presentado(c2=20))
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Error de carga'}, None, c1['id'])
        self.assertIn('último certificado', e.exception.msg)
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.anular_certificado, self.admin, {'motivo': 'x'}, None, c2['id'])  # motivo muy corto
        self.assertEqual(e.exception.status, 400)
        anulado = self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Error de carga'}, None, c2['id'])[1]
        self.assertEqual((anulado['estado'], anulado['leyenda']), ('anulado', 'ANULADO'))
        self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Se anula también el anterior'}, None, c1['id'])

    def test_un_certificado_autorizado_ya_no_se_anula(self):
        cert = self.emitir(self.presentado())
        self.llamar(obra.autorizar_certificado, self.auth, {}, None, cert['id'])
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Quiero anularlo'}, None, cert['id'])
        self.assertEqual(e.exception.status, 409)
        self.assertIn('autorizado', e.exception.msg)

    def test_anular_libera_el_computo_y_el_numero(self):
        kid = self.presentado(c2=10)
        cert = self.emitir(kid)
        with self.assertRaises(core.ApiError):  # con certificado el cómputo no se toca
            self.llamar(obra.guardar_computo, self.insp, {'personal_afectado': 3}, None, kid)
        self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Se corrige el cómputo'}, None, cert['id'])
        self.guardar(kid, 12)  # vuelve a ser editable
        self.assertEqual(self.emitir(kid)['numero'], 1)  # el número se reutiliza porque el anulado no cuenta
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM certificados WHERE estado='anulado'").fetchone()[0], 1)  # pero queda el registro
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM certificados')

    def test_el_certificado_anulado_no_forma_parte_de_la_curva_real(self):
        cert = self.emitir(self.presentado(c2=50))
        self.assertEqual(self.llamar(obra.curva_obra, self.insp, None, None, self.oid)[1]['puntos'][0]['real_acum_cent'], 125000000)
        self.llamar(obra.anular_certificado, self.admin, {'motivo': 'Error de medición'}, None, cert['id'])
        self.assertIsNone(self.llamar(obra.curva_obra, self.insp, None, None, self.oid)[1]['puntos'][0]['real_acum_cent'])

    def test_expediente_con_formato_y_sin_repetirse_salvo_certificados_de_cero(self):
        c1 = self.emitir(self.presentado(c2=0), observaciones='Mes sin avance')
        c2 = self.emitir(self.presentado(c2=0), observaciones='Otro mes sin avance')
        c3 = self.emitir(self.presentado(c2=10))
        asignar = lambda cid, exp: self.llamar(obra.asignar_expediente, self.admin, {'expediente': exp}, None, cid)[1]  # noqa: E731
        with self.assertRaises(core.ApiError) as e:
            asignar(c1['id'], '12345')
        self.assertEqual(e.exception.status, 400)
        self.assertEqual(asignar(c1['id'], '0045-012345/2026')['expediente_externo'], '0045-012345/2026')
        asignar(c2['id'], '0045-012345/2026')  # dos certificados de $ 0 pueden compartir el expediente
        with self.assertRaises(core.ApiError) as e:
            asignar(c3['id'], '0045-012345/2026')  # uno con monto no
        self.assertEqual(e.exception.status, 409)
        asignar(c3['id'], '0045-012345/2026/A001')  # un anexo es otro expediente

    def test_con_la_obra_vencida_el_certificado_tambien_exige_el_plazo(self):
        oid = self.nueva_obra(plazo=30)
        self.llamar(obra.registrar_plazo_vencida, self.admin, {'dias': 20}, None, oid)
        k = self.computo(oid)
        self.llamar(obra.guardar_computo, self.insp, {'items': [{'item_id': self.ids_de(oid)[2], 'cantidad': 5}], 'personal_afectado': 9}, None, k['id'])
        self.llamar(obra.presentar_computo, self.insp, None, None, k['id'])
        self.c.execute('DELETE FROM plazos_cert_vencida')  # el plazo de certificación se agotó
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.emitir_certificado, self.admin, {'computo_id': k['id']}, None, oid)
        self.assertEqual(e.exception.msg, obra.MSG_PLAZO_VENCIDO)

    def ids_de(self, oid):
        return {r['nro']: r['id'] for r in self.c.execute('SELECT id, nro FROM items_obra WHERE obra_id=?', (oid,))}

    def test_la_ficha_de_la_obra_trae_certificados_y_pendientes(self):
        k1 = self.presentado(c2=10)
        ficha = self.llamar(obra.ficha_obra, self.auth, None, None, self.oid)[1]
        self.assertEqual((ficha['resumen']['computos_sin_certificado'], ficha['resumen']['certificados_pendientes']), (1, 0))
        self.emitir(k1)
        ficha = self.llamar(obra.ficha_obra, self.auth, None, None, self.oid)[1]
        self.assertEqual((ficha['resumen']['computos_sin_certificado'], ficha['resumen']['certificados_pendientes']), (0, 1))
        self.assertEqual([c['numero'] for c in ficha['certificados']], [1])
        self.assertEqual(ficha['resumen']['avance_certificado_pct'], 2.38)  # 10 m3 x $ 25.000 sobre un contrato de $ 10.500.000

    def test_quien_no_ve_la_obra_no_ve_sus_certificados(self):
        cert = self.emitir(self.presentado())
        otro = self.usuario(self.cba, 'Otro inspector', 'inspector')
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.ficha_certificado, otro, None, None, cert['id'])
        self.assertEqual(e.exception.status, 404)
        self.llamar(obra.ficha_certificado, self.contr, None, None, cert['id'])  # el contratista de la obra sí


def pdf_de_prueba(texto='Documento de prueba'):
    """PDF mínimo y válido para las pruebas."""
    cuerpo = f'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n' \
             f'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n% {texto}\ntrailer<</Root 1 0 R>>\n%%EOF'
    return 'data:application/pdf;base64,' + base64.b64encode(cuerpo.encode('latin-1')).decode()


class LibroDeObra(ObraBase):
    def os(self, asunto='Presentar el plan de trabajos', texto='Se solicita presentar el plan de trabajos actualizado.', uid=None):
        return self.llamar(obra.emitir_os, uid or self.insp, {'asunto': asunto, 'texto': texto}, None, self.oid)[1]

    def test_las_ordenes_se_numeran_sin_saltos(self):
        self.assertEqual([self.os(f'Asunto número {i}')['numero'] for i in range(1, 5)], [1, 2, 3, 4])
        otra = self.nueva_obra()  # la numeración es por obra
        self.assertEqual(self.llamar(obra.emitir_os, self.insp, {'asunto': 'Primera de la otra obra', 'texto': 'Texto suficientemente largo'},
                                     None, otra)[1]['numero'], 1)

    def test_solo_el_inspector_asignado_emite(self):
        for quien in (self.contr, self.admin, self.auth):
            with self.assertRaises(core.ApiError) as e:
                self.os(uid=quien)
            self.assertEqual(e.exception.status, 403)
        otro = self.usuario(self.cba, 'Otro inspector', 'inspector')
        with self.assertRaises(core.ApiError) as e:
            self.os(uid=otro)
        self.assertEqual(e.exception.status, 404)

    def test_el_contratista_responde_una_sola_vez_y_el_inspector_cierra(self):
        o = self.os()
        self.assertEqual(o['estado'], 'emitida')
        with self.assertRaises(core.ApiError) as e:  # no se cierra sin respuesta
            self.llamar(obra.cerrar_os, self.insp, {}, None, o['id'])
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(core.ApiError) as e:  # el inspector no responde por el contratista
            self.llamar(obra.responder_os, self.insp, {'texto': 'Respuesta del inspector'}, None, o['id'])
        self.assertEqual(e.exception.status, 403)
        r = self.llamar(obra.responder_os, self.contr, {'texto': 'Se presenta el plan actualizado en la fecha pedida.'}, None, o['id'])[1]
        self.assertEqual((r['estado'], r['respondida_por_nombre']), ('respondida', 'Contratista T'))
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.responder_os, self.contr, {'texto': 'Segunda respuesta'}, None, o['id'])
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.llamar(obra.cerrar_os, self.insp, {'observacion': 'Conforme'}, None, o['id'])[1]['estado'], 'cerrada')

    def test_las_ordenes_no_se_editan_ni_se_borran(self):
        o = self.os()
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('UPDATE ordenes_servicio SET texto=? WHERE id=?', ('Otro texto', o['id']))
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('UPDATE ordenes_servicio SET numero=99 WHERE id=?', (o['id'],))
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM ordenes_servicio')

    def test_validaciones_de_la_orden(self):
        for cuerpo in ({'asunto': 'ok', 'texto': 'Texto suficientemente largo'}, {'asunto': 'Asunto válido', 'texto': 'corto'}):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(obra.emitir_os, self.insp, cuerpo, None, self.oid)
            self.assertEqual(e.exception.status, 400)

    def test_el_contratista_solo_ve_las_ordenes_de_su_obra(self):
        self.os()
        otro_c = self.usuario(self.cba, 'Otro contratista', 'contratista')
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.listar_os, otro_c, None, None, self.oid)
        self.assertEqual(e.exception.status, 404)
        self.assertEqual(len(self.llamar(obra.listar_os, self.contr, None, None, self.oid)[1]), 1)


class Documentacion(ObraBase):
    def subir(self, titulo='Plano de la sala de bombas', uid=None, tipo='plano', **extra):
        cuerpo = dict({'tipo': tipo, 'titulo': titulo, 'archivo': pdf_de_prueba(titulo)}, **extra)
        return self.llamar(obra.subir_documento, uid or self.contr, cuerpo, None, self.oid)[1]

    def dosificacion(self, clave='H21', aprobar=True, asentamiento=10):
        d = self.subir(f'Dosificación {clave}', tipo='dosificacion', clave=clave, datos={'asentamiento_cm': asentamiento})
        if aprobar:
            self.llamar(obra.aprobar_documento, self.insp, None, None, d['id'])
        return d

    def test_se_sube_un_pdf_con_etiquetas_y_se_guarda_por_hash(self):
        d = self.subir(etiquetas=['Arquitectura', 'sala de bombas', 'arquitectura'], fecha_emision='2026-09-01')
        self.assertEqual((d['version'], d['revision_estado'], d['mime']), (1, 'pendiente', 'application/pdf'))
        self.assertEqual(d['etiquetas'], ['Arquitectura', 'sala de bombas'])  # sin repetir
        self.assertEqual(len(d['sha256']), 64)
        self.assertTrue(d['url'].endswith('.pdf'))
        self.assertTrue(core.os.path.isfile(core.os.path.join(core.FOTOS_DIR, d['ruta'])))

    def test_solo_se_aceptan_pdf_o_imagenes_validos(self):
        for malo in ('data:text/html;base64,PGh0bWw+', 'no es un archivo', 'data:application/pdf;base64,' + base64.b64encode(b'esto no es un pdf').decode()):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(obra.subir_documento, self.contr, {'tipo': 'plano', 'titulo': 'Plano malo', 'archivo': malo}, None, self.oid)
            self.assertEqual(e.exception.status, 400)

    def test_una_version_nueva_reemplaza_sin_borrar(self):
        v1 = self.subir()
        v2 = self.llamar(obra.subir_documento, self.contr, {'tipo': 'plano', 'titulo': 'Plano corregido', 'reemplaza_a': v1['id'],
                                                           'archivo': pdf_de_prueba('v2')}, None, self.oid)[1]
        self.assertEqual((v2['version'], v2['reemplaza_a']), (2, v1['id']))
        vigentes = self.llamar(obra.listar_documentos, self.insp, None, {}, self.oid)[1]
        self.assertEqual([d['id'] for d in vigentes], [v2['id']])
        todas = self.llamar(obra.listar_documentos, self.insp, None, {'todas': '1'}, self.oid)[1]
        self.assertEqual(len(todas), 2)
        with self.assertRaises(core.ApiError) as e:  # la v1 ya tiene reemplazo
            self.llamar(obra.subir_documento, self.contr, {'tipo': 'plano', 'titulo': 'Otra v2', 'reemplaza_a': v1['id'],
                                                           'archivo': pdf_de_prueba('otra')}, None, self.oid)
        self.assertEqual(e.exception.status, 409)
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM documentos_obra')

    def test_quien_carga_no_revisa_y_solo_revisa_el_inspector_asignado(self):
        d = self.subir(uid=self.insp)  # lo subió el propio inspector
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.aprobar_documento, self.insp, None, None, d['id'])
        self.assertEqual(e.exception.status, 403)
        d2 = self.subir('Plano del contratista')
        for quien in (self.contr, self.auth, self.admin):
            with self.assertRaises(core.ApiError) as e:
                self.llamar(obra.aprobar_documento, quien, None, None, d2['id'])
            self.assertEqual(e.exception.status, 403)
        self.assertEqual(self.llamar(obra.aprobar_documento, self.insp, None, None, d2['id'])[1]['revision_estado'], 'aprobado')

    def test_solo_se_aprueba_con_las_observaciones_resueltas(self):
        d = self.subir()
        o = self.llamar(obra.agregar_observacion, self.insp, {'texto': 'Falta el desagüe de piso en la sala'}, None, d['id'])[1]
        self.assertEqual((o['revision_estado'], o['observaciones_abiertas']), ('observado', 1))
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.aprobar_documento, self.insp, None, None, d['id'])
        self.assertEqual(e.exception.status, 409)
        self.assertIn('sin resolver', e.exception.msg)
        r = self.llamar(obra.resolver_observacion, self.insp, None, None, o['observaciones'][0]['id'])[1]
        self.assertEqual(r['observaciones_abiertas'], 0)
        self.assertEqual(self.llamar(obra.aprobar_documento, self.insp, None, None, d['id'])[1]['revision_estado'], 'aprobado')

    def test_filtros_por_tipo_etiqueta_estado_y_vencimiento(self):
        self.subir('Plano A', etiquetas=['arquitectura'])
        self.subir('Certificado cemento', tipo='material', etiquetas=['cemento'], fecha_vencimiento=(self.hoy - timedelta(days=5)).strftime('%Y-%m-%d'))
        self.subir('Certificado aditivo', tipo='material', fecha_vencimiento=(self.hoy + timedelta(days=10)).strftime('%Y-%m-%d'))
        ver = lambda **q: sorted(d['titulo'] for d in self.llamar(obra.listar_documentos, self.insp, None, q, self.oid)[1])  # noqa: E731
        self.assertEqual(ver(tipo='material'), ['Certificado aditivo', 'Certificado cemento'])
        self.assertEqual(ver(etiqueta='Cemento'), ['Certificado cemento'])
        self.assertEqual(ver(estado='vencido'), ['Certificado cemento'])
        self.assertEqual(len(ver(estado='pendiente')), 3)
        por_vencer = {d['titulo']: d['por_vencer'] for d in self.llamar(obra.listar_documentos, self.insp, None, {}, self.oid)[1]}
        self.assertTrue(por_vencer['Certificado aditivo'])
        self.assertFalse(por_vencer['Certificado cemento'])  # ese ya venció

    def test_la_dosificacion_necesita_tipo_y_asentamiento(self):
        for extra in ({}, {'clave': 'H99', 'datos': {'asentamiento_cm': 10}}, {'clave': 'H21'}):
            with self.assertRaises(core.ApiError) as e:
                self.subir('Dosificación', tipo='dosificacion', **extra)
            self.assertEqual(e.exception.status, 400)

    def test_el_contratista_solo_sube_a_su_obra(self):
        otro_c = self.usuario(self.cba, 'Otro contratista', 'contratista')
        with self.assertRaises(core.ApiError) as e:
            self.subir(uid=otro_c)
        self.assertEqual(e.exception.status, 404)


class CalidadHormigon(ObraBase):
    def setUp(self):
        super().setUp()
        self.doc = Documentacion.subir.__get__(self)  # reutiliza el subidor de documentos

    def dosif(self, clave='H21', aprobar=True):
        d = self.llamar(obra.subir_documento, self.contr, {'tipo': 'dosificacion', 'titulo': f'Dosificación {clave}', 'clave': clave,
                                                          'datos': {'asentamiento_cm': 10, 'tolerancia_cm': 2}, 'archivo': pdf_de_prueba(clave)},
                        None, self.oid)[1]
        if aprobar:
            self.llamar(obra.aprobar_documento, self.insp, None, None, d['id'])

    def recepcion(self, uid=None, **kw):
        cuerpo = dict({'fecha': self.hoy.strftime('%Y-%m-%d'), 'hora': '09:30', 'tipo': 'H21', 'volumen_m3': 6, 'elemento': 'Zapata Z3',
                       'probeta': 'P-01', 'remito': f'R-{secrets.token_hex(2)}', 'temp_ambiente': 22, 'temp_hormigon': 24, 'asentamiento_cm': 10}, **kw)
        return self.llamar(obra.cargar_recepcion, uid or self.contr, cuerpo, None, self.oid)[1]

    def test_una_recepcion_en_orden_no_tiene_avisos(self):
        self.dosif()
        self.assertEqual(self.recepcion()['banderas'], [])

    def test_sin_dosificacion_aprobada_se_avisa(self):
        self.assertEqual(self.recepcion()['banderas'], ['sin_dosificacion_aprobada'])
        self.dosif(aprobar=False)  # cargada pero sin aprobar tampoco alcanza
        self.assertIn('sin_dosificacion_aprobada', self.recepcion()['banderas'])

    def test_asentamiento_y_temperatura_fuera_de_rango_avisan_pero_no_bloquean(self):
        self.dosif()
        r = self.recepcion(asentamiento_cm=16, temp_hormigon=34)
        self.assertEqual(sorted(r['banderas']), ['asentamiento_fuera_de_diseno', 'temperatura_hormigon_alta'])
        self.assertEqual(r['revision_estado'], 'pendiente')  # se carga igual: decide el inspector
        self.assertIn('temperatura_hormigon_baja', self.recepcion(temp_hormigon=2)['banderas'])
        self.assertEqual(self.recepcion(asentamiento_cm=12)['banderas'], [])  # 10 + 2 está dentro de la tolerancia
        self.assertIn('sin_asentamiento', self.recepcion(asentamiento_cm=None)['banderas'])

    def test_remito_repetido(self):
        self.dosif()
        self.recepcion(remito='R-0001')
        self.assertIn('remito_repetido', self.recepcion(remito='R-0001')['banderas'])

    def test_materiales_y_calibracion_vencidos_al_momento_de_la_recepcion(self):
        self.dosif()
        ayer = (self.hoy - timedelta(days=1)).strftime('%Y-%m-%d')
        for tipo in ('material', 'calibracion'):
            self.llamar(obra.subir_documento, self.contr, {'tipo': tipo, 'titulo': f'Certificado de {tipo}', 'fecha_vencimiento': ayer,
                                                          'archivo': pdf_de_prueba(tipo)}, None, self.oid)
        banderas = self.recepcion()['banderas']
        self.assertEqual(sorted(banderas), ['calibracion_vencida', 'documento_de_material_vencido'])
        anterior = (self.hoy - timedelta(days=10)).strftime('%Y-%m-%d')  # antes del vencimiento, estaba vigente
        self.assertEqual(self.recepcion(fecha=anterior)['banderas'], [])

    def test_quien_carga_la_planilla_no_la_revisa(self):
        self.dosif()
        propia = self.recepcion(uid=self.insp)
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.revisar_recepcion, self.insp, {'accion': 'validar'}, None, propia['id'])
        self.assertEqual(e.exception.status, 403)
        ajena = self.recepcion()
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.revisar_recepcion, self.insp, {'accion': 'observar'}, None, ajena['id'])  # sin motivo
        self.assertEqual(e.exception.status, 400)
        self.assertEqual(self.llamar(obra.revisar_recepcion, self.insp, {'accion': 'validar'}, None, ajena['id'])[1]['revision_estado'], 'validada')
        with self.assertRaises(core.ApiError) as e:
            self.llamar(obra.revisar_recepcion, self.insp, {'accion': 'validar'}, None, ajena['id'])
        self.assertEqual(e.exception.status, 409)

    def test_validaciones_de_la_planilla(self):
        self.dosif()
        for malo in ({'tipo': 'H99'}, {'hora': '25:00'}, {'volumen_m3': 0}, {'fecha': (self.hoy + timedelta(days=1)).strftime('%Y-%m-%d')},
                     {'remito': ''}, {'asentamiento_cm': 99}):
            with self.assertRaises(core.ApiError) as e:
                self.recepcion(**malo)
            self.assertEqual(e.exception.status, 400, malo)
        self.recepcion()  # una válida, para que haya algo que no se pueda borrar
        with self.assertRaises(sqlite3.IntegrityError):
            self.c.execute('DELETE FROM recepciones_hormigon')

    def test_los_pendientes_aparecen_en_el_resumen_de_la_obra(self):
        self.dosif(aprobar=False)
        self.recepcion()
        o = self.llamar(obra.emitir_os, self.insp, {'asunto': 'Asunto de prueba', 'texto': 'Texto suficientemente largo'}, None, self.oid)[1]
        res = self.llamar(obra.ficha_obra, self.insp, None, None, self.oid)[1]['resumen']
        self.assertEqual((res['os_sin_responder'], res['documentos_por_revisar'], res['recepciones_por_revisar']), (1, 1, 1))


class CuentaVecino(ServidorHTTP):
    """El vecino con cuenta reporta y ve sólo lo suyo; nada interno del ente."""
    def setUp(self):
        super().setUp()
        core.crear_usuario(self.c, self.cba['id'], 'Vecino T', 'vecino-t', 'vecino-t', 'vecino')
        core.crear_usuario(self.c, self.cba['id'], 'Otro vecino T', 'vecino-t2', 'vecino-t2', 'vecino')
        core.crear_usuario(self.c, self.cba['id'], 'Inspector T2', 'inspector-t', 'inspector-t', 'inspector')
        self.c.commit()
        self.rep = {'ente': 'CBA-PILOTO', 'categoria': 'Bache', 'descripcion': 'Bache hecho por un vecino',
                    'lat': -31.4, 'lon': -64.2}

    def test_reporte_con_sesion_queda_en_mis_reportes_y_el_anonimo_no(self):
        _, _, tok = self.sesion('vecino-t', 'vecino-t')
        st, mio = self.pedir('POST', '/api/incidencias', self.rep, cookie=tok)
        self.assertEqual(st, 201)
        st, anonimo = self.pedir('POST', '/api/incidencias', dict(self.rep, lat=-31.5))
        self.assertEqual(st, 201)
        st, lista = self.pedir('GET', '/api/mis-reportes', cookie=tok)
        self.assertEqual(st, 200)
        self.assertEqual([r['codigo'] for r in lista], [mio['codigo']])
        self.assertEqual(lista[0]['estado'], 'Recibida, en revisión')

    def test_cada_vecino_ve_solo_sus_reportes(self):
        _, _, t1 = self.sesion('vecino-t', 'vecino-t')
        _, _, t2 = self.sesion('vecino-t2', 'vecino-t2')
        self.pedir('POST', '/api/incidencias', self.rep, cookie=t1)
        st, lista = self.pedir('GET', '/api/mis-reportes', cookie=t2)
        self.assertEqual((st, lista), (200, []))

    def test_vecino_no_accede_a_nada_interno(self):
        _, _, tok = self.sesion('vecino-t', 'vecino-t')
        for ruta in ('/api/indicadores', '/api/incidencias', '/api/intervenciones', '/api/usuarios', '/api/mapa'):
            st, _ = self.pedir('GET', ruta, cookie=tok)
            self.assertEqual(st, 403, ruta)

    def test_mis_reportes_exige_cuenta_de_vecino(self):
        st, _ = self.pedir('GET', '/api/mis-reportes')
        self.assertEqual(st, 401)
        _, _, tok = self.sesion('inspector-t', 'inspector-t')
        st, _ = self.pedir('GET', '/api/mis-reportes', cookie=tok)
        self.assertEqual(st, 403)

    def test_vista_interna_no_muestra_quien_reporto(self):
        _, _, t_vecino = self.sesion('vecino-t', 'vecino-t')
        st, mio = self.pedir('POST', '/api/incidencias', self.rep, cookie=t_vecino)
        _, _, t_insp = self.sesion('inspector-t', 'inspector-t')
        st, lista = self.pedir('GET', '/api/incidencias', cookie=t_insp)
        self.assertEqual(st, 200)
        self.assertTrue(any(i['codigo'] == mio['codigo'] for i in lista))
        self.assertNotIn('reportada_por', json.dumps(lista))

    def test_vecino_no_reporta_en_otro_ente(self):
        _, _, tok = self.sesion('vecino-t', 'vecino-t')
        st, _ = self.pedir('POST', '/api/incidencias', dict(self.rep, ente='ENTE-B', categoria='Pozo o hundimiento'), cookie=tok)
        self.assertEqual(st, 403)


class Autenticacion(ServidorHTTP):
    def setUp(self):
        super().setUp()
        self.ente_cba = self.cba
        self.login_insp = 'inspector-test'
        core.crear_usuario(self.c, self.cba['id'], 'Inspector auth', self.login_insp, 'clave-correcta-1', 'inspector')
        self.c.commit()

    def test_hash_no_guarda_la_clave_y_verifica(self):
        h = core.hash_password('secreto-123')
        self.assertNotIn('secreto-123', h)
        self.assertTrue(core.verificar_password('secreto-123', h))
        self.assertFalse(core.verificar_password('otro', h))
        self.assertFalse(core.verificar_password('x', 'basura'))

    def test_login_correcto_entrega_cookie_httponly(self):
        st, cuerpo, token = self.sesion(self.login_insp, 'clave-correcta-1')
        self.assertEqual(st, 200)
        self.assertNotIn('password_hash', json.dumps(cuerpo))
        self.assertIsNotNone(token)
        cookie = self.ultimas_cabeceras.get('Set-Cookie')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_sesion_permite_consultar_y_expira(self):
        st, _, token = self.sesion(self.login_insp, 'clave-correcta-1')
        st, me = self.pedir('GET', '/api/me', cookie=token)
        self.assertEqual(st, 200)
        self.assertEqual(me['usuario']['login'], self.login_insp)
        # vencimiento forzado en la base
        self.c.execute("UPDATE sesiones SET expira_at='2000-01-01T00:00:00Z'")
        self.c.commit()
        st, _ = self.pedir('GET', '/api/me', cookie=token)
        self.assertEqual(st, 401)

    def test_logout_revoca_la_sesion(self):
        st, _, token = self.sesion(self.login_insp, 'clave-correcta-1')
        self.pedir('POST', '/api/logout', {}, cookie=token)
        st, _ = self.pedir('GET', '/api/me', cookie=token)
        self.assertEqual(st, 401)

    def test_clave_incorrecta_y_usuario_inexistente_dan_el_mismo_mensaje(self):
        st1, c1, _ = self.sesion(self.login_insp, 'mal')
        st2, c2, _ = self.sesion('no-existe', 'mal')
        self.assertEqual((st1, c1), (401, {'error': 'usuario o contraseña incorrectos'}))
        self.assertEqual((st2, c2), (401, {'error': 'usuario o contraseña incorrectos'}))

    def test_bloqueo_tras_varios_intentos(self):
        for _ in range(core.LOGIN_MAX_FALLOS):
            self.sesion(self.login_insp, 'mal')
        st, cuerpo, _ = self.sesion(self.login_insp, 'clave-correcta-1')
        self.assertEqual(st, 429)
        registros = self.c.execute("SELECT COUNT(*) FROM historial WHERE accion='login_fallido'").fetchone()[0]
        self.assertEqual(registros, core.LOGIN_MAX_FALLOS)

    def test_usuario_inactivo_no_entra(self):
        self.c.execute('UPDATE usuarios SET activo=0 WHERE login=?', (self.login_insp,))
        self.c.commit()
        st, _, _ = self.sesion(self.login_insp, 'clave-correcta-1')
        self.assertEqual(st, 401)

    def test_cabecera_X_Usuario_Id_ya_no_autentica(self):
        uid = self.c.execute('SELECT id FROM usuarios WHERE login=?', (self.login_insp,)).fetchone()[0]
        st, _ = self.pedir('GET', '/api/indicadores', cabeceras={'X-Usuario-Id': str(uid)})
        self.assertEqual(st, 401)

    def test_post_sin_json_se_rechaza(self):
        req = urllib.request.Request(f'http://127.0.0.1:{self.puerto}/api/incidencias', method='POST',
                                     data=b'ente=CBA-PILOTO', headers={'Content-Type': 'application/x-www-form-urlencoded'})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 415)

    def test_foto_con_sesion_valida(self):
        _, _, token = self.sesion(self.login_insp, 'clave-correcta-1')
        st, _ = self.pedir('GET', '/api/fotos/' + '0' * 64 + '.png', cookie=token)
        self.assertEqual(st, 404)  # autenticado, pero la foto no existe

    def test_listado_de_personal_solo_del_mismo_ente(self):
        _, _, token = self.sesion(self.login_insp, 'clave-correcta-1')
        st, personal = self.pedir('GET', '/api/usuarios', cookie=token)
        self.assertEqual(st, 200)
        self.assertNotIn('login', json.dumps(personal))
        self.assertNotIn('Responsable B', json.dumps(personal))


if __name__ == '__main__':
    unittest.main()
