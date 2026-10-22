# Estado actual de SIGPLAT

> Simulación con datos ficticios. Sin validez legal. No está conectada a SIGO, SIGUE ni a ningún sistema oficial.
> En una versión real, los permisos se validan en el servidor, no sólo en la interfaz.

## Qué funciona

- **Reportes ciudadanos**: reporte con ubicación y foto, detección de duplicados, inspección, intervención y cierre con evidencias.
- **Obras (Modo obra)**: alta con código propio (`OP-<ente>-<año>-<número>-<dígito>`), datos oficiales de la contratación, legajo técnico con versiones, cómputo métrico mensual, certificados (emisión por el inspector, autorización por otra persona), modificaciones de curva ideal y ampliaciones de plazo, libro de obra con órdenes de servicio, documentación técnica y control del hormigón.
- **Notas de pedido** (nuevo, etapa 1): la contratista las solicita (ampliación de plazo, modificación de obra u otra), numeradas sin saltos por obra. La inspección las responde una sola vez. No se editan ni se borran.
- **Registro de firmas** (nuevo, etapa 1): cada emisión, autorización, anulación, nota y orden de servicio queda con quién, con qué rol, cuándo y el motivo cuando corresponde.
- **Contratista acotada** (nuevo, etapa 1): ve su obra, sus notas de pedido y sus órdenes de servicio. No ve cómputos, certificados, curvas, modificaciones ni legajo.
- **Mi día** por rol: lista lo que le toca a cada persona, con un botón que lleva a la pantalla donde se resuelve.

## Roles actuales

Inspector, responsable de cuadrilla, autoridad, administrador, vecino, contratista (representante técnico), administrativo, jefe de inspección, analista de certificaciones, jefe de certificaciones, director y auditor (sólo lectura). El administrador de sistema es el rol `administrador` con alta de usuarios (`POST /usuarios`); no firma actos. Las cuentas de demo están en `scripts/seed.py` (`USUARIOS_SIMPLES`) y en `data/credenciales_prueba.txt`, que no se versiona.

## Tests

- Suite: `python -m unittest discover -s tests`.
- Antes de la etapa 1: 144 pruebas, OK.
- Después de la etapa 1: 152 pruebas, OK.
- Después de la etapa 2: 160 pruebas, OK (8 nuevas en `RolesDeObra`).

## Pendiente

- **Etapa 2, no implementado** (decisiones en DECISIONES.md): que el analista emita certificados (hoy emite el inspector); la aprobación del jefe de certificaciones (aprueba o devuelve); la certificación final del director; propuesta de multas y reclamos del jefe de inspección; redeterminaciones y recepciones de la autoridad; una persona con dos roles.
- Etapa 3: rango por usuario, topes de modificación (20 % por ítem, 30 % del total), y parámetros por ente.
- Pantalla de alta de usuarios y de parámetros para el administrador de sistema.
- Vista previa del certificado dentro de "Gestionar mes".
- Pruebas de usabilidad con usuarios reales: no hechas.
