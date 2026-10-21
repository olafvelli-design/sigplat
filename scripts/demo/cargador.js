// SIGPLAT · modo demo en el navegador.
// Reemplaza fetch('/api/...') por llamadas al núcleo de reglas corriendo en Pyodide (Python en WebAssembly).
// No hay servidor: la base vive en este navegador (IndexedDB) y cada visitante tiene la suya.
window.SIGPLAT_DEMO = true;
(function () {
  const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
  const DB = '/sigplat/data/sigplat.db';
  const FOTOS = '/sigplat/data/fotos';
  const fetchReal = window.fetch.bind(window);
  let token = null;
  let py = null;
  let puente = null;
  let version = null;

  const aviso = document.createElement('div');
  aviso.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;' +
    'background:rgba(20,30,42,.92);color:#fff;font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px';
  aviso.textContent = 'Preparando la demo… la primera vez descarga unos 10 MB.';
  document.addEventListener('DOMContentLoaded', () => document.body.appendChild(aviso));

  const cargarScript = src => new Promise((ok, mal) => {
    const s = document.createElement('script');
    s.src = src; s.onload = ok; s.onerror = () => mal(new Error('no se pudo cargar ' + src));
    document.head.appendChild(s);
  });

  // ---- Almacenamiento local: si el navegador no lo permite, la demo funciona igual pero no recuerda ----
  const almacen = modo => new Promise((ok, mal) => {
    try {
      const r = indexedDB.open('sigplat-demo', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('estado');
      r.onsuccess = () => ok(r.result.transaction('estado', modo).objectStore('estado'));
      r.onerror = () => mal(r.error);
    } catch (e) { mal(e); }
  });
  const pedir = req => new Promise((ok, mal) => { req.onsuccess = () => ok(req.result); req.onerror = () => mal(req.error); });
  const leerGuardado = async () => pedir((await almacen('readonly')).get('demo'));
  const escribirGuardado = async valor => pedir((await almacen('readwrite')).put(valor, 'demo'));
  const borrarGuardado = async () => pedir((await almacen('readwrite')).delete('demo'));

  let temporizador = null;
  const programarGuardado = () => { clearTimeout(temporizador); temporizador = setTimeout(guardar, 400); };
  async function guardar() {
    try {
      const fotos = py.FS.readdir(FOTOS).filter(n => n !== '.' && n !== '..')
        .map(n => [n, py.FS.readFile(FOTOS + '/' + n)]);
      await escribirGuardado({ version, db: py.FS.readFile(DB), fotos });
    } catch (_) { /* sin almacenamiento: no se guarda, pero la demo sigue andando */ }
  }
  async function restaurar() {
    try {
      const g = await leerGuardado();
      if (!g || g.version !== version) return false;  // otra versión de la demo: se parte de cero
      py.FS.mkdirTree(FOTOS);
      py.FS.writeFile(DB, g.db);
      for (const [nombre, datos] of g.fotos) py.FS.writeFile(FOTOS + '/' + nombre, datos);
      return true;
    } catch (_) { return false; }
  }
  window.reiniciarDemo = async () => {
    if (!confirm('Se borran los reportes y cambios que hiciste en esta demo y se vuelve al estado inicial. ¿Seguir?')) return;
    try { await borrarGuardado(); } catch (_) { /* nada que borrar */ }
    location.reload();
  };

  const listo = (async () => {
    await cargarScript(PYODIDE + 'pyodide.js');
    py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage('sqlite3');
    const man = await (await fetchReal('py/manifiesto.json')).json();
    version = man.version;
    for (const ruta of man.archivos) {
      const destino = '/sigplat/' + ruta;
      py.FS.mkdirTree(destino.slice(0, destino.lastIndexOf('/')));
      py.FS.writeFile(destino, await (await fetchReal('py/' + ruta)).text());
    }
    py.runPython("import sys; sys.path.insert(0, '/sigplat')");
    puente = py.pyimport('puente');
    let abierta = false;
    if (await restaurar()) {
      try { puente.abrir(); abierta = true; } catch (_) { try { py.FS.unlink(DB); } catch (__) { /* ya no está */ } }
    }
    if (!abierta) { puente.iniciar(man.duracion_segundos); programarGuardado(); }
  })().then(() => aviso.remove(), err => {
    aviso.textContent = 'No se pudo iniciar la demo: ' + err.message;
    throw err;
  });

  window.fetch = async function (entrada, opts = {}) {
    const url = typeof entrada === 'string' ? entrada : (entrada && entrada.url) || String(entrada);
    if (!url.startsWith('/api/')) return fetchReal(entrada, opts);
    await listo;
    if (url.startsWith('/api/fotos/')) {
      const s = JSON.parse(puente.foto(url.slice('/api/fotos/'.length), token));
      if (s.estado !== 200) return new Response(JSON.stringify(s.payload), { status: s.estado });
      const bytes = Uint8Array.from(atob(s.b64), ch => ch.charCodeAt(0));
      return new Response(new Blob([bytes], { type: s.mime }), { status: 200 });
    }
    const metodo = (opts.method || 'GET').toUpperCase();
    const s = JSON.parse(puente.llamar(metodo, url, opts.body || '', token));
    if (s.token_nuevo) token = s.token_nuevo;
    if (s.cerrar_sesion) token = null;
    if (metodo === 'POST' && s.estado < 400) programarGuardado();
    return new Response(JSON.stringify(s.payload), { status: s.estado, headers: { 'Content-Type': 'application/json' } });
  };
})();
