"""SIGPLAT · servidor HTTP local (biblioteca estándar, sin costo).

Uso:  python app/server.py [--puerto 8000]
Variables: SIGPLAT_DB para cambiar la base (por defecto data/sigplat.db).

Autenticación: login con usuario y contraseña. La sesión viaja en una cookie HttpOnly
con SameSite=Strict. Los POST exigen Content-Type application/json (un formulario de otro
sitio no puede enviarlo sin un preflight, que este servidor no permite).
"""
import argparse
import json
import mimetypes
import os
import re
import sys
import threading
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
DB_PATH = os.environ.get('SIGPLAT_DB', os.path.join(core.BASE_DIR, 'data', 'sigplat.db'))
MAX_BODY = 4 * 1024 * 1024
COOKIE = 'sigplat_sesion'
FOTO_NOMBRE = re.compile(r'^[a-f0-9]{64}\.(jpg|png|webp)$')
LOCK = threading.Lock()
CONEXION = {}


def crear_conexion(ruta=DB_PATH):
    c = core.conectar(ruta)
    core.cargar_configs(c)
    c.commit()
    return c


class Manejador(BaseHTTPRequestHandler):
    server_version = 'SIGPLAT/0.2'

    def log_message(self, fmt, *args):  # silencia el log por defecto
        pass

    def do_GET(self):
        self._procesar('GET')

    def do_POST(self):
        self._procesar('POST')

    def _token(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get('Cookie') or '')
        except cookies.CookieError:
            return None
        return jar[COOKIE].value if COOKIE in jar else None

    def _procesar(self, metodo):
        url = urlparse(self.path)
        ruta = url.path
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if not ruta.startswith('/api/'):
            if metodo == 'GET':
                return self._estatico(ruta)
            return self._json(405, {'error': 'método no permitido'})
        token = self._token()
        if ruta.startswith('/api/fotos/'):
            return self._foto(ruta[len('/api/fotos/'):], token)
        try:
            if metodo == 'POST' and not (self.headers.get('Content-Type') or '').startswith('application/json'):
                raise core.ApiError(415, 'los POST requieren Content-Type application/json')
            body = self._leer_cuerpo() if metodo == 'POST' else {}
            with LOCK:
                c = CONEXION['c']
                uid = core.uid_de_sesion(c, token)
                estado, payload = core.despachar(c, metodo, ruta[len('/api'):], q, body, uid, token)
                c.commit()
            cookie_nueva = None
            if isinstance(payload, dict) and '_token' in payload:
                cookie_nueva = self._cookie(payload.pop('_token'), 8 * 3600)
            if ruta == '/api/logout':
                cookie_nueva = self._cookie('', 0)
            return self._json(estado, payload, cookie_nueva)
        except core.ApiError as e:
            with LOCK:
                CONEXION['c'].rollback()
            return self._json(e.status, {'error': e.msg})
        except Exception as e:  # noqa: BLE001 - el prototipo no filtra trazas al cliente
            with LOCK:
                CONEXION['c'].rollback()
            sys.stderr.write(f'error interno: {e!r}\n')
            return self._json(500, {'error': 'error interno del prototipo'})

    def _cookie(self, valor, max_age):
        return f'{COOKIE}={valor}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}'

    def _leer_cuerpo(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > MAX_BODY:
            raise core.ApiError(413, 'cuerpo demasiado grande')
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise core.ApiError(400, 'JSON inválido')

    def _json(self, estado, payload, cookie=None):
        datos = json.dumps(payload, ensure_ascii=False, default=str).encode('utf-8')
        self.send_response(estado)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(datos)))
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(datos)

    def _foto(self, nombre, token):
        with LOCK:
            uid = core.uid_de_sesion(CONEXION['c'], token)
        if not uid:
            return self._json(401, {'error': 'se requiere iniciar sesión'})
        if not FOTO_NOMBRE.match(nombre):
            return self._json(404, {'error': 'foto inexistente'})
        camino = os.path.join(core.FOTOS_DIR, nombre)
        if not os.path.isfile(camino):
            return self._json(404, {'error': 'foto inexistente'})
        with open(camino, 'rb') as f:
            datos = f.read()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(nombre)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(datos)))
        self.send_header('Cache-Control', 'private, max-age=3600')
        self.end_headers()
        self.wfile.write(datos)

    def _estatico(self, ruta):
        rel = 'index.html' if ruta in ('/', '') else ruta.lstrip('/')
        camino = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not camino.startswith(STATIC_DIR) or not os.path.isfile(camino):
            self.send_response(404)
            self.end_headers()
            return
        tipo = mimetypes.guess_type(camino)[0] or 'application/octet-stream'
        with open(camino, 'rb') as f:
            datos = f.read()
        self.send_response(200)
        self.send_header('Content-Type', tipo + ('; charset=utf-8' if tipo.startswith('text/') or tipo.endswith('javascript') else ''))
        self.send_header('Content-Length', str(len(datos)))
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(datos)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--puerto', type=int, default=8000)
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    CONEXION['c'] = crear_conexion()
    servidor = ThreadingHTTPServer((args.host, args.puerto), Manejador)
    print(f'SIGPLAT prototipo en http://{args.host}:{args.puerto}  (base: {DB_PATH})')
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
