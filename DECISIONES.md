# Decisiones de diseño

Registro de decisiones tomadas y de las que quedan a confirmar. Las confirmadas por el responsable del proyecto van con fecha.

## Confirmadas

- **Simulación, no sistema institucional.** Datos ficticios, sin validez legal, sin conexión con SIGO, SIGUE ni otros sistemas. (2026-10)
- **Sólo biblioteca estándar en el backend** (Python 3.11, sqlite3), para que la demo en GitHub Pages corra con Pyodide. Sin dependencias pagas ni frameworks nuevos.
- **Dinero en centavos enteros**, redondeo comercial (mitad hacia arriba).
- **Nada se borra ni se edita en los registros críticos**: se anula con estado, o se reemplaza por una versión nueva con motivo. Lo implementan triggers en SQLite.
- **Separación de emisión y autorización.** Quien emite un certificado no puede autorizarlo (`autorizar_certificado`). Hoy emite el inspector y autoriza la autoridad.
- **Código de obra** con dígito verificador (módulo 11) y secuencia que sólo sube: un código no se reutiliza.
- **"Mi día" como pantalla de entrada** para los roles de obra. Los nombres SIGO y SIGUE no son categorías de navegación (sólo se mencionan como referencia técnica).
- **Contratista acotada** a sus notas de pedido y órdenes de servicio, más su obra en lo básico. Cómputos, certificados, curvas, modificaciones y legajo quedan fuera de su vista.

## Etapa 2 (implementada, a confirmar)

- **Contratista (representante técnico)**: firma conforme o rechaza, con motivo, una medición presentada y un certificado vigente. Una sola vez. Para firmarlos los ve: sólo el cómputo presentado y el certificado que firma (no la lista de certificados, ni curvas, ni legajo, ni modificaciones). Esto cambia lo que decidí en la etapa 1, por eso lo marco. Sigue sin ver montos de la obra en general.
- **La firma o rechazo del contratista no bloquea el certificado.** Queda en el registro. Si el rechazo debe frenar el circuito, hay que definirlo con la normativa.
- **Jefe de inspección**: valida mediciones presentadas (una vez). La validación queda en el registro y no bloquea el certificado.
- **Rol por obra**: jefe de inspección, analista, jefe de certificaciones y director ven sólo las obras donde el administrativo los asignó (`obra_asignados`). La autoridad, el administrativo, el administrador y el auditor ven todas las del ente. El auditor sólo lee.
- **Adjudicar es de la autoridad** (antes lo hacía el administrativo). Las demás transiciones de estado siguen siendo del administrativo.
- **Administrador de sistema**: da de alta usuarios por API (`POST /usuarios`). No firma actos. Los parámetros llegan en la etapa 3.
- **Base existente**: los roles nuevos cambian el esquema. Para una base ya creada hay que regenerarla (`python scripts/seed.py`) o migrarla.

## A confirmar por el responsable (etapa 2, no implementado)

- **Quién emite el certificado.** El pedido dice que el analista de certificaciones emite. Antes definiste que el inspector certifica y otra persona autoriza. Hoy sigue emitiendo el inspector y el analista no tiene permiso de emisión. Hace falta decidir cuál de las dos reglas vale.
- **Aprobación del jefe de certificaciones** (aprueba o devuelve). Requiere un estado nuevo en el circuito; no lo agregué. Propongo: emite el analista, aprueba el jefe, autoriza la autoridad. Hay que confirmarlo.
- **Certificación final del director.** No hay un tipo de certificado para eso más allá del "final" del cómputo. Falta definir qué cambia.
- **Propuesta de multas y reclamos sobre inspectores auxiliares** (jefe de inspección). No existen esas entidades en el sistema y no quiero inventar reglas. Hace falta definirlas con normativa.
- **Redeterminaciones y recepciones** (autoridad). No existen en el sistema.
- **Una persona con dos roles.** Hoy cada usuario tiene un solo rol. Tenerlo en dos exige una tabla nueva de roles por usuario.

## A confirmar por el responsable

1. **Contratista: documentos y planillas de hormigón.** Hoy sube documentos técnicos y carga planillas de recepción de hormigón (`subir_documento`, `cargar_recepcion`). El pedido original pide que vea sólo notas de pedido y órdenes de servicio. Hay tres opciones:
   - a) Mantener la carga de documentos y planillas (es lo que hace hoy la obra real: el contratista presenta la documentación de calidad).
   - b) Quitarla: la carga pasa al inspector, que lo hace en campo.
   - c) Mantenerla sólo en Calidad, sin el resto del campo.
   Mientras no se confirme, se mantiene (a).
2. **Notas de pedido: tipos y efectos.** Hoy la nota sólo registra el pedido y la respuesta. No cambia plazos ni montos. Falta definir si una ampliación aceptada debe pasar a ser una modificación de curva ideal (MCI/AP) y quién lo decide. Requiere validación institucional.
3. **Quién firma cada acto y con qué rango.** El pedido propone roles nuevos (representante técnico, jefe de inspección, analista y jefe de certificaciones, director, auditor) y un campo de rango por usuario. Eso es la etapa 2 y 3, y los nombres de cargos, los rangos y la cadena de aprobadores deben validarse con la normativa de Córdoba y con personal real. No se inventan aquí.
4. **Parámetros por ente** (plazo de firma del certificado, protesta, denuncia de demoras, pago, fondo de reparo). Hoy hay valores de ejemplo. Se pasan a `config/entes/*.json` en la etapa 3.
