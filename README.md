# SIGPLAT · prototipo (genérico multi-ente, piloto Córdoba)

Prototipo demostrable del ciclo **reporte → inspección → intervención → avance declarado → validación → cierre → historial**, con datos **ficticios**.
Un mismo despliegue sirve a varios entes; cada uno se define con un archivo de configuración en `config/entes/`.

> Qué NO es: no reemplaza a SIGO (sistema provincial de obras de Córdoba, `sigo.cba.gov.ar`), no es un sistema de seguridad de producción y no tiene datos reales.

## Requisitos (costo cero)

- Python 3.11 o superior. Sólo biblioteca estándar: sin `pip install`.
- Navegador moderno e internet. La interfaz carga Leaflet 1.9.4 desde unpkg y las teselas de OpenStreetMap. Sin internet, la interfaz no se inicializa.

## Puesta en marcha

Desde la carpeta `01_PROTOTIPO`:

```bash
python scripts/seed.py
python app/server.py --puerto 8000
```

Abrir <http://127.0.0.1:8000>. El `seed` recrea `data/sigplat.db` con datos ficticios reproducibles (semilla fija).

## Pruebas

```bash
python -m unittest discover -s tests -v
```

46 pruebas: reglas de estado, separación de funciones, evidencias append-only, banderas de consistencia, aislamiento entre entes, permisos por rol, autenticación (contraseña, sesión, bloqueo, revocación), indicadores sin datos inventados, y pruebas HTTP. Usan base y fotos temporales.

## Acceso

- **Personal** (inspector, responsable, autoridad, administrador): usuario y contraseña en el encabezado. Contraseñas con PBKDF2-SHA256 (biblioteca estándar, 310 000 iteraciones). La sesión dura 8 h, se guarda sólo como hash y viaja en una cookie `HttpOnly; SameSite=Strict`. Tras 5 intentos fallidos el usuario queda bloqueado 5 minutos. Cada intento fallido queda en el historial.
- **Ciudadano**: sin cuenta. Reporta y consulta con el código de seguimiento. Es una decisión deliberada: no pedimos datos personales para reportar. La identidad ciudadana se resolverá después con CiDi.
- **Datos de prueba**: `python scripts/seed.py` genera `data/credenciales_prueba.txt` con los usuarios y la contraseña común de prueba (definida en `scripts/seed.py`). Son de uso local. No usar en ningún despliegue real.

Cada rol ve sólo lo que le corresponde:

| Rol | Puede |
|---|---|
| Ciudadano (sin cuenta) | Reportar, consultar por código |
| Responsable | Ver **sus** intervenciones asignadas, iniciar, declarar avances y finalización, cargar evidencias |
| Inspector | Bandeja de incidencias, inspeccionar, validar o observar avances, revisar evidencias, mapa y tablero |
| Autoridad | Todo lo anterior, más crear intervenciones y cerrarlas |
| Administrador | Mapa, tablero y bandeja de consulta (sin operar intervenciones) |

## Cómo se agrega un ente

1. Copiar `config/entes/cba_piloto.json` a `config/entes/<codigo>.json`.
2. Cambiar `codigo`, `nombre`, `centro`, `categorias`, `tipos_intervencion`, `duplicados` y `radio_evidencia_m`.
3. Reiniciar el servidor (carga las configuraciones). No hay cambios de código.

Lo que es configurable: categorías de reporte, tipos de intervención y si requieren actividades, radios de duplicados y de evidencia, centro del mapa.
Lo que **no** es configurable todavía: las transiciones de estado y los permisos por rol (son reglas del núcleo, en `app/core.py`).

## Estructura

| Ruta | Contenido |
|---|---|
| `app/schema.sql` | Esquema SQLite. Todas las tablas llevan `ente_id`. Triggers que impiden borrar evidencias y editar el historial |
| `app/core.py` | Reglas de negocio: estados, permisos, banderas, indicadores |
| `app/server.py` | Servidor HTTP con biblioteca estándar |
| `app/static/index.html` | Interfaz por roles: ciudadano, mapa y tablero, bandeja, intervenciones |
| `config/entes/*.json` | Configuración por ente (CBA piloto y un ente ficticio B) |
| `scripts/seed.py` | Datos ficticios |
| `tests/test_prototipo.py` | Pruebas |

## Reglas principales

- **Declarado vs validado.** El responsable declara el avance por actividad. Otro usuario (inspector o autoridad) lo valida o lo observa. El tablero sólo calcula la diferencia cuando hay validación.
- **Separación de funciones.** Quien declara no puede validar su propio avance. Quien carga una evidencia no puede revisarla.
- **Cierre.** Para declarar la finalización hace falta una foto de cierre. Para validar el cierre, todas las actividades deben estar validadas al 100% y la foto de cierre debe estar validada. Recién entonces la autoridad puede cerrar, y la incidencia pasa a "resuelta".
- **Evidencias.** Nunca se borran ni se sobrescriben. Un reemplazo referencia a la anterior (`reemplaza_a`). Cada foto guarda hash SHA-256, hora de recepción del servidor, hora de captura declarada, precisión y ubicación.
- **Banderas, no correcciones.** Sin fecha de captura, captura posterior a la recepción, captura de más de 48 h, precisión mayor a 100 m o desconocida, lejos de la ubicación de referencia, o foto idéntica a otra evidencia. El sistema las marca y no decide por el usuario.
- **Duplicados.** Un reporte cercano y del mismo tipo se muestra como posible duplicado. No se fusiona automáticamente: lo decide el inspector.
- **Indicadores.** Cada indicador muestra `n` y una nota. Si no hay datos suficientes, el valor es "—", no cero. El cumplimiento de plazos requiere al menos 5 intervenciones finalizadas.

## Referencia a SIGO / SIGUE

Cada intervención puede llevar `sistema_externo` (SIGO o SIGUE) y `expediente_externo` (texto libre). Son **sólo referencias**: el prototipo no lee ni escribe en esos sistemas. El flujo observado en el video y las decisiones de alineación están en `../02_REFERENCIA_SIGO_SIGUE/FLUJO_OBSERVADO.md`. No se generan certificados: el certificado oficial sigue siendo de SIGO.

## Demo en el navegador (GitHub Pages)

La carpeta `docs/` es una versión estática que corre **sin servidor**: el mismo `core.py` y el mismo seed se ejecutan en el navegador con [Pyodide](https://pyodide.org) (Python en WebAssembly). Cada visitante tiene su propia base con datos ficticios, guardada en su navegador (IndexedDB): los reportes y cambios sobreviven a recargar la página, no se comparten con nadie y el enlace "Reiniciar demo" vuelve al estado inicial. Otro navegador o un modo privado empiezan de cero. La primera carga descarga unos 10 MB.

Para publicarla: Settings → Pages → *Deploy from a branch* → rama `main`, carpeta `/docs`.

`docs/` se genera, no se edita a mano. Después de cambiar `app/` o `scripts/seed.py`:

```bash
python scripts/build_demo.py
```

Para probarla en local: `python -m http.server 8001` dentro de `docs/` y abrir <http://127.0.0.1:8001>. En la demo el hash de contraseñas usa 1000 iteraciones y una implementación de PBKDF2 en Python puro (Pyodide no trae `hashlib.pbkdf2_hmac`) para que arranque rápido. La app local usa 310 000 iteraciones.

## Limitaciones conocidas

- **Autenticación de prototipo.** Hay contraseñas y sesiones reales, pero sin recuperación de contraseña, sin doble factor, sin HTTPS (en local) y con bloqueo en memoria que se reinicia con el servidor. Para producción hace falta HTTPS, administración de usuarios y auditoría de accesos revisada.
- **Base anterior.** Una base creada antes de este cambio no tiene contraseñas: volver a ejecutar `python scripts/seed.py`.
- **Fotos.** Se guardan en disco local, sin compresión del lado del servidor ni antivirus. Las fotos se sirven sólo a usuarios identificados. La hora de captura y la ubicación vienen del dispositivo y no están verificadas.
- **Sin modo offline.** No hay cola de sincronización. Requiere conexión.
- **Sin integración** con SIGO, CiDi ni otros sistemas.
- **Sin validez legal.** No hay firma digital ni certificación formal.
- **Concurrencia.** Un solo proceso y una conexión SQLite protegida por un lock. Suficiente para demostración, no para carga municipal.
- **Pruebas de navegador.** Se verificó a mano el ciclo completo en el navegador: reporte ciudadano, avance y evidencia del responsable, validación del inspector y cierre por la autoridad.
