# Bitácora

## Etapa 0 · Lectura y línea base
- Revisados README.md, MODO_OBRA.md, app/core.py (PERMISOS, ROLES, TRANSICIONES_INT), app/obra.py (_visible y rutas), scripts/seed.py (USUARIOS_SIMPLES) y tests/.
- No existían CLAUDE.md, CEREBRO_*, MEMORIA_*, ESTADO_ACTUAL, DECISIONES ni BITACORA: se crearon los tres últimos en esta etapa.
- Tests antes de cambiar nada: **144 OK**.
- Hallazgo: el certificado ya cumple el doble control (1c): quien emite (inspector) no puede autorizar, y la autorización es de la autoridad.

## Etapa 1 · Contratista acotada, notas de pedido y registro de firmas
**Cambió**
- `schema.sql`: tablas `notas_pedido` (numeración por obra, inmutable, respuesta única) y `firmas_registro` (sólo se agrega). Triggers que impiden editar, borrar o responder dos veces.
- `core.py`: permisos `solicitar_nota` (contratista) y `responder_nota` (inspector).
- `obra.py`:
  - `_exigir_interno`: la contratista recibe 404 en cómputos, certificados, curva, modificaciones y legajo.
  - `ficha_obra` y `listar_obras` devuelven a la contratista sólo plazos y pendientes (sin montos ni certificados).
  - Notas de pedido: listar, solicitar, responder.
  - Registro de firmas en emisión, autorización y anulación de certificados, y en órdenes de servicio y notas.
  - El resumen de la obra cuenta las notas pendientes (para Mi día).
- `seed.py`: dos notas de ejemplo (una respondida, otra pendiente).
- `index.html`: la contratista ve sólo las pestañas de campo; el libro de obra muestra las notas y permite solicitarlas o responderlas; el certificado muestra el registro de firmas; Mi día incluye las notas.
- `tests/test_prototipo.py`: 2 pruebas ajustadas (la contratista ya no ve certificados ni legajo) y 8 nuevas en `NotasYContratista`.

**Resultado**: **152 OK** (144 + 8 nuevas). Probado en el navegador como contratista (tres pestañas, notas, sin botones de cómputo) y como inspector (Mi día: "Responder 1 nota(s) de pedido").

**Pendiente de decisión** (ver DECISIONES.md, punto 1): si la contratista sigue subiendo documentos y planillas de hormigón. Se mantuvo.

## Etapa 2 · Roles nuevos
**Confirmada la etapa 1** por el responsable: se mantiene la carga de documentos y planillas de la contratista.

**Cambió**
- `schema.sql`: roles nuevos en `usuarios.rol`; tabla `obra_asignados`; `firmas_registro` admite `computo`.
- `core.py`: permisos `validar_medicion`, `firmar_contratista`, `adjudicar_obra`, `gestionar_usuarios`; roles en `ver_obras`; `alta_usuario` (`POST /usuarios`).
- `obra.py`: `_visible` mira la asignación de la obra para los roles de la cadena; `asignar_equipo`; `firmar_contratista` (firma o rechazo, una vez, con motivo); `validar_medicion`; `firmas_contratista`; adjudicar pasa a la autoridad; el resumen cuenta mediciones sin validar y firmas pendientes.
- Visibilidad de la contratista: ve el cómputo presentado y el certificado vigente que firma (cambio respecto de la etapa 1, marcado en DECISIONES.md).
- `seed.py`: cuentas nuevas con usuario = contraseña (`jefe_inspeccion`, `analista_certificaciones`, `jefe_certificaciones`, `director`, `auditor`, `admin_sistema`); equipo asignado a la obra de ejemplo.
- `index.html`: Mi día y pestañas por rol; la contratista firma o rechaza en el libro de obra; el jefe valida en "Gestionar mes".
- Tests: 8 nuevas en `RolesDeObra`; 3 ajustadas (la contratista ve el certificado que firma; adjudica la autoridad).

**Resultado**: **160 OK**. Probado en el navegador: la contratista ve sus firmas pendientes; el jefe ve "Validar 4 medición(es)" y valida una medición.

**Sin implementar** (ver DECISIONES.md): emisión por el analista, aprobación del jefe de certificaciones, certificación del director, multas y reclamos, redeterminaciones y recepciones, y dos roles por persona.

## Etapa 2b · Circuito del certificado (jefe de certificaciones)
**Decisión confirmada**: emite el inspector; aprueba o devuelve el jefe de certificaciones; autoriza la autoridad.

**Cambió**
- `schema.sql`: estados `aprobado` y `devuelto` en certificados; columnas `aprobado_por` y `aprobado_at`.
- `core.py`: permiso `aprobar_certificado` (sólo jefe de certificaciones).
- `obra.py`: `aprobar_certificado` y `devolver_certificado` (motivo de 10 letras o más); `autorizar_certificado` exige estado aprobado y que no lo hayan emitido ni aprobado; la anulación admite pendiente, aprobado y devuelto; contadores de aprobados y devueltos en el resumen; el certificado muestra al jefe entre las firmas.
- `seed.py`: el jefe aprueba los certificados antes de que la autoridad los autorice.
- `index.html`: botones Aprobar y Devolver en el certificado; la autoridad ve los aprobados; Mi día para el jefe y para el inspector con certificados devueltos.
- Tests: 5 nuevas en `CircuitoCertificado`. Las pruebas de autorización pasan por la aprobación (`autorizar_cert`); la obra de prueba queda asignada al jefe.

**Resultado**: **165 OK**. Probado en el navegador como jefe de certificaciones: "Aprobar o devolver 1 certificado(s)", con los botones en el certificado.

**Sin implementar**: la certificación final del director, las multas y reclamos del jefe de inspección, las redeterminaciones y recepciones, y dos roles por persona.

## Etapa 3 · Rangos, topes, plazos y parámetros
**Cambió**
- `schema.sql`: `usuarios.rango` (1 a 5); `obras.firmante_contrato_id`; `curvas_ideal.excede_tope` y `delta_monto_cent`; firmas admiten `curva`.
- `core.py`: el alta de usuarios acepta `rango`.
- `obra.py`: parámetros por defecto en `CONFIG_OBRA` (plazos, topes, cadena, fondo de reparo); al cargar una MCI se calculan los topes y el cambio de monto; autorizar exige el acuerdo de la contratista si se supera el tope, y el rango del firmante si cambia el monto; la contratista firma o rechaza una modificación que supera el tope; los plazos de firma y de protesta se controlan; `GET /parametros`.
- `config/entes/*.json`: sección `obra` con plazos, topes, cadena y fondo de reparo (EJEMPLO).
- `seed.py`: rangos y firmante del contrato en la obra de ejemplo.
- `index.html`: aviso de tope y de cambio de monto en cada modificación; la contratista firma las modificaciones que superan el tope desde el libro de obra.
- Tests: 7 nuevas en `TopesYJerarquia`.

**Resultado**: **172 OK**. Probado en el navegador: la contratista lee los parámetros del ente.

**Limitaciones**: la cadena de aprobadores es informativa; la denuncia de demoras y el pago no tienen pantalla; los plazos de firma se miden desde el acto y en la demo algunos actos antiguos ya están vencidos.

