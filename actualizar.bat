@echo off
REM Doble clic: trae los correos nuevos de Gmail, saca los movimientos y genera los reportes.
REM La ruta del JSON de credenciales de Google se toma de la variable de
REM entorno CREDENCIALES. Configurala una sola vez en una terminal:
REM     setx CREDENCIALES "C:\ruta\a\tu\client_secret.json"
REM Si no existe, se busca client_secret.json junto a este archivo.
REM Opcional: para copiar los reportes a Obsidian, configura tambien
REM     setx OBSIDIAN_REPORTES "C:\ruta\a\boveda\Control de gastos\Reportes"
if not defined CREDENCIALES set CREDENCIALES=%~dp0client_secret.json

cd /d "%~dp0"
call .venv\Scripts\activate.bat
cd scripts

python sincronizar_gmail.py --credenciales "%CREDENCIALES%"
if errorlevel 1 goto error
python procesar_correos.py
if errorlevel 1 goto error
if defined OBSIDIAN_REPORTES (
    python reporte.py --todos --obsidian "%OBSIDIAN_REPORTES%"
) else (
    python reporte.py --todos
)
if errorlevel 1 goto error

echo.
echo Listo. Resultados en la carpeta procesar_correos_* mas reciente dentro de datos\
pause
exit /b 0

:error
echo.
echo Algo fallo. Revisa el .log de la carpeta mas reciente dentro de datos\
pause
exit /b 1
