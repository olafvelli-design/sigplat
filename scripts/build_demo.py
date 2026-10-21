"""Genera docs/ para publicar una demo en GitHub Pages (sitio estático, sin servidor).

Uso:  python scripts/build_demo.py

Copia el código real (core.py, schema, configuración, seed) y arma una página que lo ejecuta en el
navegador con Pyodide. Así la demo no se desfasa de la app: se regenera con este script.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(RAIZ, 'app'))
sys.path.insert(0, HERE)
import core  # noqa: E402
import seed  # noqa: E402

DOCS = os.path.join(RAIZ, 'docs')
BANNER_APP = ('<div class="banner">Prototipo de demostración con <b>datos ficticios</b> y contraseñas locales de prueba. '
              'No es un sistema de producción.</div>')
BANNER_DEMO = ('<div class="banner"><b>Demo en el navegador</b>: corre en tu equipo, sin servidor, y guarda lo que hacés sólo en este '
               'navegador. Datos <b>ficticios</b>. Reportar y consultar no requiere cuenta. Para entrar: usuario y contraseña '
               '<code>vecino</code> (cuenta ciudadana, ve sus reportes) o <code>inspector</code> (inspección y tablero). '
               'También <code>cba-piloto.responsable1</code> y <code>cba-piloto.autoridad1</code> con contraseña '
               f'<code>{seed.PASSWORD_PRUEBA}</code>. '
               '<a href="#" onclick="reiniciarDemo();return false">Reiniciar demo</a></div>')
ARCHIVOS = [
    ('app/core.py', os.path.join(RAIZ, 'app', 'core.py')),
    ('app/obra.py', os.path.join(RAIZ, 'app', 'obra.py')),
    ('app/schema.sql', os.path.join(RAIZ, 'app', 'schema.sql')),
    ('scripts/seed.py', os.path.join(RAIZ, 'scripts', 'seed.py')),
    ('puente.py', os.path.join(HERE, 'demo', 'puente.py')),
]


def medir_duracion():
    """Duración simulada de la historia ficticia (es determinista: la semilla es fija)."""
    core.PBKDF2_ITER = 1000
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    inicio = ahora - timedelta(days=200)
    carpeta = tempfile.mkdtemp()
    try:
        fin = seed.construir(os.path.join(carpeta, 'medicion.db'), inicio)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    return int((fin - inicio).total_seconds())


def main():
    archivos = list(ARCHIVOS)
    carpeta_cfg = os.path.join(RAIZ, 'config', 'entes')
    for nombre in sorted(os.listdir(carpeta_cfg)):
        if nombre.endswith('.json'):
            archivos.append((f'config/entes/{nombre}', os.path.join(carpeta_cfg, nombre)))

    if os.path.isdir(DOCS):  # se vacía el contenido, no la carpeta (en Windows puede estar abierta por un servidor)
        for nombre in os.listdir(DOCS):
            ruta = os.path.join(DOCS, nombre)
            shutil.rmtree(ruta) if os.path.isdir(ruta) else os.remove(ruta)
    os.makedirs(os.path.join(DOCS, 'py'))
    for destino, origen in archivos:
        ruta = os.path.join(DOCS, 'py', destino)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        shutil.copyfile(origen, ruta)
    with open(os.path.join(DOCS, 'py', 'manifiesto.json'), 'w', encoding='utf-8') as f:
        json.dump({'archivos': [d for d, _ in archivos], 'duracion_segundos': medir_duracion(),
                   'version': datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}, f, indent=2)

    with open(os.path.join(RAIZ, 'app', 'static', 'index.html'), encoding='utf-8') as f:
        html = f.read()
    with open(os.path.join(HERE, 'demo', 'cargador.js'), encoding='utf-8') as f:
        cargador = f.read()
    marca = '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    for esperado in (BANNER_APP, marca, '<title>SIGPLAT · prototipo</title>'):
        if esperado not in html:
            raise SystemExit(f'index.html cambió: no se encontró {esperado[:40]!r}; actualizar build_demo.py')
    html = (html.replace(BANNER_APP, BANNER_DEMO)
                .replace('<title>SIGPLAT · prototipo</title>', '<title>SIGPLAT · demo</title>')
                .replace(marca, f'<script>\n{cargador}</script>\n{marca}'))
    with open(os.path.join(DOCS, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html)
    open(os.path.join(DOCS, '.nojekyll'), 'w').close()
    print('Demo generada en', DOCS)


if __name__ == '__main__':
    main()
