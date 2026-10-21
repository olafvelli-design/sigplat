// SIGPLAT · modo demo en el navegador.
// Reemplaza fetch('/api/...') por llamadas al núcleo de reglas corriendo en Pyodide (Python en WebAssembly).
// No hay servidor: la base es en memoria y se reinicia al recargar la página.
(function () {
  const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/';
  const fetchReal = window.fetch.bind(window);
  let token = null;
  let puente = null;

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

  const listo = (async () => {
    await cargarScript(PYODIDE + 'pyodide.js');
    const py = await loadPyodide({ indexURL: PYODIDE });
    await py.loadPackage('sqlite3');
    const man = await (await fetchReal('py/manifiesto.json')).json();
    for (const ruta of man.archivos) {
      const destino = '/sigplat/' + ruta;
      py.FS.mkdirTree(destino.slice(0, destino.lastIndexOf('/')));
      py.FS.writeFile(destino, await (await fetchReal('py/' + ruta)).text());
    }
    py.runPython("import sys; sys.path.insert(0, '/sigplat')");
    puente = py.pyimport('puente');
    puente.iniciar(man.duracion_segundos);
  })().then(() => aviso.remove(), err => {
    aviso.textContent = 'No se pudo iniciar la demo: ' + err.message;
    throw err;
  });

  const respuesta = (salida, cuerpo) => new Response(cuerpo, { status: salida.estado });

  window.fetch = async function (entrada, opts = {}) {
    const url = typeof entrada === 'string' ? entrada : (entrada && entrada.url) || String(entrada);
    if (!url.startsWith('/api/')) return fetchReal(entrada, opts);
    await listo;
    if (url.startsWith('/api/fotos/')) {
      const s = JSON.parse(puente.foto(url.slice('/api/fotos/'.length), token));
      if (s.estado !== 200) return respuesta(s, JSON.stringify(s.payload));
      const bytes = Uint8Array.from(atob(s.b64), ch => ch.charCodeAt(0));
      return new Response(new Blob([bytes], { type: s.mime }), { status: 200 });
    }
    const s = JSON.parse(puente.llamar((opts.method || 'GET').toUpperCase(), url, opts.body || '', token));
    if (s.token_nuevo) token = s.token_nuevo;
    if (s.cerrar_sesion) token = null;
    return new Response(JSON.stringify(s.payload), { status: s.estado, headers: { 'Content-Type': 'application/json' } });
  };
})();
