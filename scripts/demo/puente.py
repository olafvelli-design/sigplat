"""SIGPLAT · puente entre el navegador (Pyodide) y el núcleo de reglas.

Hace lo mismo que app/server.py, pero sin HTTP: la página llama a `llamar()` en lugar de hacer fetch.
Todo corre en el navegador del visitante; la base vive en memoria y se pierde al recargar.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

RAIZ = '/sigplat'
sys.path.insert(0, RAIZ + '/app')
sys.path.insert(0, RAIZ + '/scripts')
import core  # noqa: E402


def _pbkdf2_hmac(nombre, password, sal, iteraciones, dklen=None):
    """PBKDF2 (RFC 8018) con hmac, para entornos sin hashlib.pbkdf2_hmac (Pyodide)."""
    base_hmac = hmac.new(password, digestmod=nombre)
    tam = base_hmac.digest_size
    dklen = dklen or tam
    salida = b''
    for bloque in range(1, -(-dklen // tam) + 1):
        h = base_hmac.copy()
        h.update(sal + struct.pack('>I', bloque))
        u = h.digest()
        acumulado = int.from_bytes(u, 'big')
        for _ in range(iteraciones - 1):
            h = base_hmac.copy()
            h.update(u)
            u = h.digest()
            acumulado ^= int.from_bytes(u, 'big')
        salida += acumulado.to_bytes(tam, 'big')
    return salida[:dklen]


if not hasattr(hashlib, 'pbkdf2_hmac'):
    hashlib.pbkdf2_hmac = _pbkdf2_hmac

core.PBKDF2_ITER = 1000  # demo: arranque rápido en el navegador; la app local usa 310000
import seed  # noqa: E402

FOTO_NOMBRE = re.compile(r'^[a-f0-9]{64}\.(jpg|png|webp)$')
MIME = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp'}
EST = {}


def iniciar(duracion_s):
    """Carga la historia ficticia terminando el día anterior a hoy y abre la conexión de trabajo."""
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    inicio = (ahora - timedelta(seconds=duracion_s) - timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    ruta = RAIZ + '/data/sigplat.db'
    seed.construir(ruta, inicio)
    c = core.conectar(ruta)
    core.cargar_configs(c)
    c.commit()
    EST['c'] = c


def _salida(estado, payload, **extra):
    return json.dumps({'estado': estado, 'payload': payload, **extra}, ensure_ascii=False, default=str)


def llamar(metodo, url, cuerpo, token):
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    c = EST['c']
    try:
        body = json.loads(cuerpo) if (metodo == 'POST' and cuerpo) else {}
        uid = core.uid_de_sesion(c, token)
        estado, payload = core.despachar(c, metodo, u.path[len('/api'):], q, body, uid, token)
        c.commit()
    except core.ApiError as e:
        c.rollback()
        return _salida(e.status, {'error': e.msg})
    except Exception as e:  # noqa: BLE001 - igual que el servidor: no se filtran trazas
        c.rollback()
        sys.stderr.write(f'error interno: {e!r}\n')
        return _salida(500, {'error': 'error interno del prototipo'})
    nuevo = payload.pop('_token') if isinstance(payload, dict) and '_token' in payload else None
    return _salida(estado, payload, token_nuevo=nuevo, cerrar_sesion=(u.path == '/api/logout'))


def foto(nombre, token):
    if not core.uid_de_sesion(EST['c'], token):
        return _salida(401, {'error': 'se requiere iniciar sesión'})
    camino = os.path.join(core.FOTOS_DIR, nombre)
    if not FOTO_NOMBRE.match(nombre) or not os.path.isfile(camino):
        return _salida(404, {'error': 'foto inexistente'})
    with open(camino, 'rb') as f:
        datos = base64.b64encode(f.read()).decode('ascii')
    return _salida(200, None, mime=MIME[nombre.rsplit('.', 1)[1]], b64=datos)
