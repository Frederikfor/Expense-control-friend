
# Proyecto: rastreador personal de gastos (Costa Rica)

Estoy construyendo un sistema personal en Python que registra mis gastos automáticamente a partir de los correos de mis bancos, sin digitarlos a mano. Corre en mi laptop con Windows, solo cuando yo lo ejecuto. Por privacidad, ningún dato financiero sale de mi computadora.

## Bancos y fuentes

- **BNCR (Banco Nacional), bncr.fi.cr:** "Voucher Digital" por cada compra con tarjeta (fuente principal), avisos de Sobres (son movimientos internos, no gastos) y estados de cuenta en PDF.
- **BAC, baccredomatic.cr y notificacionesbaccr.com:** "Notificación de transacción" por compra y "Notificación de Transferencia" (SINPE).
- **Davibank (antes Scotiabank), davibank.cr:** solo avisa de débitos y créditos grandes de cuenta, sin comercio. No hay alertas por compra con tarjeta, así que para este banco **la fuente principal es el estado de cuenta en PDF**.

## Lo que ya existe (carpeta scripts/)

- `leer_mbox.py`: importa el .mbox de Google Takeout a `datos/correos.sqlite` (tabla `correos`) y extrae los PDF/XML adjuntos a `datos/adjuntos/`. Incluye `carpeta_de_corrida()` e `iniciar_log()`: cada corrida escribe su log y sus resultados en una carpeta con fecha.
- `sincronizar_gmail.py`: Gmail API con permiso solo lectura (refresh token en el Administrador de credenciales de Windows vía keyring). Es incremental, deduplica por Message-ID y guarda por lotes.
- `config/bancos.toml` + `configuracion.py`: los datos (bancos, dominios oficiales y nombres viejos, asuntos ignorados, meses, monedas, palabra→tipo) viven en el archivo, no en el código. Se validan al cargar. Principio: **funciones genéricas + archivos de datos**, nada específico de un banco escrito en las funciones.
- `extractores.py`: funciones puras con regex por banco, que devuelven un `Movimiento` (banco, tipo, direccion = salida/entrada/interno/info, fecha, monto, moneda, comercio, ciudad, pais, marca_tarjeta, ultimos4, autorizacion, referencia, confianza, detalle). Solo acepta los dominios oficiales; las fechas ambiguas dd/mm se resuelven con la fecha del correo.
- `procesar_correos.py`: aplica los extractores y rehace las tablas `movimientos` y `clasificacion`. Genera `movimientos.xlsx` con hojas de no reconocidos y sin verificar (DKIM/SPF).
- `muestras_anonimas.py`: saca ejemplos de correos con dígitos→9 y nombres tapados.
- `tests/test_extractores.py`: 19 pruebas con pytest, todas pasan.

## Decisiones tomadas (no cambiarlas sin preguntarme)

- **Reglas primero, IA después.** El monto, la fecha y el comercio salen de reglas deterministas y probadas. La IA (Ollama local, que ya tengo instalado) se usa solo para: categorizar comercios, leer PDF de formato raro, formatos de correo nuevos, y el agente de análisis/reporte. Los cálculos los hace Python; la IA interpreta y redacta. La IA en la nube queda apagada.
- **Stack:** Python, SQLite, pandas, pytest, y un panel web local en Streamlit (solo en 127.0.0.1).
- Código y comentarios en español, el mismo estilo que los scripts actuales.

## Siguientes tareas, en orden

1. **Estados de cuenta en PDF** (prioridad: Davibank; luego BNCR y BAC). Primero, un script tipo `muestras_anonimas.py` que saque la estructura del texto y las tablas de cada PDF con los datos tapados, para diseñar el lector. Luego el lector con pdfplumber (pikepdf si tiene contraseña, que se guarda en keyring y nunca en el código). Debe validar saldo inicial + movimientos = saldo final, y dejar una tabla `movimientos_estado_cuenta`.
2. **Conciliación:** cruzar el estado de cuenta con las alertas de correo (monto + fecha ±3 días) para encontrar gastos sin alerta.
3. **Categorizador:** tabla de reglas comercio→categoría. Si no hay regla, Ollama sugiere la categoría con salida JSON validada. Las correcciones que yo haga se guardan como reglas nuevas.
4. **Panel en Streamlit** y el **agente de análisis** (gastos hormiga, suscripciones, presupuesto, preguntas en lenguaje natural con SQL de solo lectura).

## Reglas de privacidad para ti (importante)

- **NO abras ni leas:** `datos/correos.sqlite`, el .mbox, `datos/adjuntos/` (PDF), los .xlsx de resultados, ni el JSON de credenciales de Google. Lo que leas se envía fuera de mi laptop.
- Para diseñar lectores, pídeme que corra un script que genere muestras anonimizadas y trabaja sobre esas.
- Cualquier cambio en los extractores debe venir con su prueba en `tests/` y con `python -m pytest tests` pasando.
- Antes de cada tarea, explícame brevemente el plan (qué entra, qué sale, qué puede fallar), porque quiero aprender arquitectura.

Empieza por la tarea 1.
