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
