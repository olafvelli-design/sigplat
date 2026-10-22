# Modo obra: simulación de SIGO y SIGUE para el inspector

> **Simulación con datos ficticios. Sin validez legal. No está conectada a SIGO ni a SIGUE.**
> Sirve para diseñar y probar el flujo de trabajo de un inspector de obra pública con gente real.

## Qué simula

| Sistema | Qué guarda en esta simulación |
|---|---|
| **SIGO** (contrato y dinero) | Detalle de obra (medidores y curvas), cómputo métrico, certificados, modificaciones de la obra (curva ideal y ampliación de plazo) |
| **SIGUE** (campo) | Libro de obra y órdenes de servicio, documentación técnica, control de calidad |

El reparto SIGO/SIGUE es una hipótesis tomada del video y de lo indicado por el usuario. El manual de SIGO (v20) ubica también el legajo digital en SIGO. Hay que confirmarlo con un inspector real.

## Pantallas por tarea (rediseño en curso)

- **Mi día**: pantalla de entrada para inspector, autoridad, administrativo y contratista. Lista lo que le toca a cada persona en cada obra, con un botón que lleva directo a la pestaña donde se resuelve.
- **Gestionar mes** (dentro de la obra): medir, presentar, certificar y seguimiento de la autorización, en una sola pantalla con pasos.
- **Modificar cronograma** es una sola pantalla: el gráfico con la curva original, la vigente y el avance real; la propuesta con su vista previa (cómo queda cada mes frente a la vigente) antes de pedir la autorización; y el historial. Una propuesta pendiente no cambia el cronograma vigente.
- **Legajo técnico** y **Resumen de la obra** agrupan lo que antes eran pestañas sueltas. Las categorías SIGO y SIGUE ya no aparecen en la navegación.

## Quién hace qué

| Rol | Hace |
|---|---|
| Inspector | Mide el avance del mes, presenta el cómputo y **emite el certificado** (nace pendiente) desde la misma pantalla del cómputo; emite y cierra órdenes de servicio; revisa documentación y planillas |
| Contratista | Ve sólo su obra, sus notas de pedido y sus órdenes de servicio. Solicita notas de pedido, responde órdenes de servicio, sube documentación y carga planillas de hormigón (pendiente de decisión: ver DECISIONES.md) |
| Administrativo | **Da de alta la obra** (se genera el código), arma y publica el **legajo técnico**, asigna inspector y contratista y avanza el estado; asigna el expediente de los certificados; anula el último pendiente; carga modificaciones de curva ideal y ampliaciones de plazo; registra el plazo de certificación de obra vencida |
| Autoridad | Autoriza el certificado y las modificaciones de curva ideal (quien emite o carga no autoriza) |
| Administrador | Consulta sin operar. Da de alta usuarios (administrador de sistema); no firma actos |
| Jefe de inspección | Valida mediciones presentadas. Sólo en las obras que le asignan |
| Analista y jefe de certificaciones | Pendiente de definir el circuito (ver DECISIONES.md). Sólo en las obras asignadas |
| Director | Pendiente de definir la certificación final. Sólo en las obras asignadas |
| Auditor | Lee todo el ente; no escribe ni firma |

Visibilidad: el inspector ve sólo las obras donde está asignado; el contratista sólo la suya; administrativo y autoridad, todas las del ente.

## Decisiones de diseño

- El código vive en `app/obra.py` (usa `core.py` para sesiones, permisos y auditoría). `core.despachar` suma sus rutas. Sólo biblioteca estándar, así que corre también en el navegador con Pyodide.
- Dinero en **centavos enteros**, con `Decimal` para multiplicar y redondeo "mitad hacia arriba". Nunca `float`.
- Las cantidades son del MES (no acumuladas ni en porcentaje). El sistema calcula anterior, presente, acumulado, porcentajes e importes.
- Nada se borra: un cómputo "eliminado" o un certificado "anulado" quedan con estado y auditoría. Las órdenes de servicio y los documentos no se editan ni se borran (triggers).
- Cambio respecto de la guía: los documentos de **dosificación** son un tipo propio (`dosificacion`), con `clave` (H13, H17, H21, H25) y datos de diseño (asentamiento y tolerancia), para poder cruzarlos con las recepciones de hormigón. El fondo de inspección es un porcentaje (0 = "NO contiene fondo de inspección"), como en el contrato de SIGO.
- Pantalla de obra como en el video de SIGUE: detalle con mapa, tres medidores (plazo, avance físico, avance financiero), "Plazos y montos", gráfico de áreas interactivo (Principal, MCI-n, AmpP-n y Avance real; $ o %; datos en CSV), cómputo con pestañas por mes y pasos Medir → Presentar → Certificar → Autorizar, y un panel de atajos. Paleta clara, cercana a CiDi.`n- Planillas en CSV (el Excel real se exporta a CSV). Se arma y se lee en el navegador; el servidor sólo recibe JSON.
- Los archivos se guardan por hash (SHA-256) igual que las fotos; se amplía el almacenamiento para aceptar PDF.

## Tablas

`obras` (con código y datos de contratación), `secuencias_obra`, `legajo_obra`, `items_obra`, `curvas_ideal`, `avance_ideal`, `computos`, `computo_items`, `plazos_cert_vencida`, `certificados`, `ordenes_servicio`, `documentos_obra`, `observaciones_documento`, `recepciones_hormigon`. Todas llevan `ente_id` (directo o por su obra).

## Rutas

Lectura: `GET /obras`, `/obras/{id}`, `/obras/{id}/curva`, `/obras/{id}/modificaciones`, `/obras/{id}/curva-ideal`, `/computos/{id}`, `/obras/{id}/certificados`, `/certificados/{id}`, `/obras/{id}/ordenes-servicio`, `/obras/{id}/documentos`, `/obras/{id}/recepciones`.
Legajo y alta: `POST /obras`, `/obras/{id}/asignacion|estado`, `GET|POST /obras/{id}/legajo`, `POST /obras/{id}/legajo/publicar`.
Escritura: `POST /obras/{id}/computos`, `/computos/{id}/guardar|presentar|eliminar`, `/obras/{id}/plazo-vencida`, `/obras/{id}/certificados`, `/certificados/{id}/autorizar|anular|expediente`, `/obras/{id}/modificaciones` (POST), `/modificaciones/{id}/autorizar`, `/obras/{id}/ordenes-servicio`, `/ordenes-servicio/{id}/respuesta|cerrar`, `/obras/{id}/documentos`, `/documentos/{id}/observaciones|aprobar`, `/observaciones/{id}/resolver`, `/obras/{id}/recepciones`, `/recepciones/{id}/revision`.

## Reglas (de la guía y del manual de SIGO, con sus páginas)

**Cómputo métrico (pp. 61-63)**
1. Se carga la cantidad avanzada en el mes.
2. Para presentarlo hace falta el personal afectado.
3. Sólo se modifica o elimina mientras no exista certificado de ese mes.
4. No se abre uno nuevo si no se generó el certificado del mes anterior.
5. El último se marca "cómputo final".
6. Sin inspector asignado no se puede cargar el primer cómputo.
7. Si la obra venció, hace falta un "plazo de certificación de obra vencida".
8. Si lo acumulado supera lo contratado: aviso y observación obligatoria, sin bloquear.

**Curva (pp. 46-48):** ideal por precio; real desde los cómputos con certificado no anulado; desvío en montos y puntos porcentuales, con n y nota.

**Certificados (pp. 66-78)**
9. Los emite el inspector y nacen pendientes ("NO AUTORIZADO").
10. Liquidación simplificada y calculada.
11. Observación obligatoria si el monto es $0.
12. Autoriza la autoridad, distinta de quien emitió; fija fecha y vencimiento; ya no se anula.
13. Sólo se anula el último, si está pendiente.
14. El expediente no se repite, salvo certificados de $0.

**Modificaciones:** una modificación de curva ideal (MCI) exige trámite e instrumento "Acta Acuerdo" y queda pendiente hasta que la autoridad la autoriza; una ampliación de plazo (AP) suma días de inmediato; la vigente es la última autorizada; hay una pendiente por vez.`n`n**Código de obra y legajo**
19. El código es `OP-<sigla del ente>-<año>-<número>-<dígito>` (ej. `OP-CBA-2026-0042-7`): único, correlativo por ente y año, no se reutiliza (secuencia que sólo sube) y el dígito final (módulo 11) detecta un carácter mal o dos intercambiados. Los números oficiales (N° de contratación `2026/000112` o `06/2025`, expediente `0045-012345/2026`, tipo "- O.P.", servicio administrativo) se guardan aparte y se validan; no se repiten por servicio ni expediente.
20. El legajo se arma por tipo (acto administrativo, pliegos, planos, cómputo y presupuesto, circulares). Las circulares se numeran solas; una corrección es una versión nueva con motivo; el mismo archivo no se sube dos veces; nada se borra ni se edita.
21. No se adjudica con el legajo sin publicar; después de publicarlo, lo que se agrega queda marcado como posterior. No se pasa a ejecución sin inspector.

**Libro de obra:** 15. Órdenes numeradas sin saltos, inmutables; el contratista responde una vez; el inspector cierra.

**Documentación y calidad:** 16. Versiones con hash; aprobado sólo sin observaciones sin resolver. 17. Banderas que avisan y no deciden. 18. Quien carga no revisa.

## Supuestos a validar con un inspector real

- Umbrales de calidad del hormigón (asentamiento ± tolerancia y temperatura): son **valores de ejemplo**, configurables en el ente.
- Qué hacer cuando lo medido supera lo contratado: acá es un aviso con observación obligatoria.
- Quién carga cada planilla de control (acá: contratista o inspector, revisa el inspector, nunca el mismo).
- Qué documentos componen el legajo de cada tipo de contratación (la lista de requeridos es configurable por ente) y si los planos DWG deben aceptarse (hoy sólo PDF e imágenes).
- Quién emite el certificado (acá: el inspector; lo autoriza otra persona).`n- Quién registra el plazo de certificación de obra vencida (acá: administrativo o autoridad).
- Fórmula de liquidación: simplificada; no replica los 17 conceptos del certificado real.
- Si el cómputo se carga en SIGUE o en SIGO.




