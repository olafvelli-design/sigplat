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
  // Cada operación abre y cierra su conexión, y termina recién cuando la transacción se confirmó en disco.
  const conAlmacen = (modo, operacion) => new Promise((ok, mal) => {
    try {
      const r = indexedDB.open('sigplat-demo', 1);
      r.onupgradeneeded = () => r.result.createObjectStore('estado');
      r.onerror = () => mal(r.error);
      r.onsuccess = () => {
        const db = r.result;
        const t = db.transaction('estado', modo);
        const req = operacion(t.objectStore('estado'));
        t.oncomplete = () => { db.close(); ok(req.result); };
        t.onerror = t.onabort = () => { db.close(); mal(t.error); };
      };
    } catch (e) { mal(e); }
  });
  const leerGuardado = () => conAlmacen('readonly', s => s.get('demo'));
  const escribirGuardado = valor => conAlmacen('readwrite', s => s.put(valor, 'demo'));
  const borrarGuardado = () => conAlmacen('readwrite', s => s.delete('demo'));

  let temporizador = null;
  const programarGuardado = () => { clearTimeout(temporizador); temporizador = setTimeout(guardar, 150); };
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
    // Siempre revalidados: GitHub Pages cachea 10 minutos y no debe mezclarse un archivo viejo con uno nuevo.
    const fresco = ruta => fetchReal('py/' + ruta, { cache: 'no-cache' });
    const man = await (await fresco('manifiesto.json')).json();
    version = man.version;
    for (const ruta of man.archivos) {
      const destino = '/sigplat/' + ruta;
      py.FS.mkdirTree(destino.slice(0, destino.lastIndexOf('/')));
      py.FS.writeFile(destino, await (await fresco(ruta)).text());
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
