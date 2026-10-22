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

## A confirmar por el responsable

1. **Contratista: documentos y planillas de hormigón.** Hoy sube documentos técnicos y carga planillas de recepción de hormigón (`subir_documento`, `cargar_recepcion`). El pedido original pide que vea sólo notas de pedido y órdenes de servicio. Hay tres opciones:
   - a) Mantener la carga de documentos y planillas (es lo que hace hoy la obra real: el contratista presenta la documentación de calidad).
   - b) Quitarla: la carga pasa al inspector, que lo hace en campo.
   - c) Mantenerla sólo en Calidad, sin el resto del campo.
   Mientras no se confirme, se mantiene (a).
2. **Notas de pedido: tipos y efectos.** Hoy la nota sólo registra el pedido y la respuesta. No cambia plazos ni montos. Falta definir si una ampliación aceptada debe pasar a ser una modificación de curva ideal (MCI/AP) y quién lo decide. Requiere validación institucional.
3. **Quién firma cada acto y con qué rango.** El pedido propone roles nuevos (representante técnico, jefe de inspección, analista y jefe de certificaciones, director, auditor) y un campo de rango por usuario. Eso es la etapa 2 y 3, y los nombres de cargos, los rangos y la cadena de aprobadores deben validarse con la normativa de Córdoba y con personal real. No se inventan aquí.
4. **Parámetros por ente** (plazo de firma del certificado, protesta, denuncia de demoras, pago, fondo de reparo). Hoy hay valores de ejemplo. Se pasan a `config/entes/*.json` en la etapa 3.
