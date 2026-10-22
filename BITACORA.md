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
Pendiente. Esperando la confirmación de la etapa 1.
