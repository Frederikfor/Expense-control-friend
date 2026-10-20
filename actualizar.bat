@echo off
REM Doble clic: trae los correos nuevos de Gmail y saca los movimientos.
REM La ruta del JSON de credenciales de Google se toma de la variable de
REM entorno CREDENCIALES. Configurala una sola vez en una terminal:
REM     setx CREDENCIALES "C:\ruta\a\tu\client_secret.json"
REM Si no existe, se busca client_secret.json junto a este archivo.
if not defined CREDENCIALES set CREDENCIALES=%~dp0client_secret.json

cd /d "%~dp0"
call .venv\Scripts\activate.bat
cd scripts

python sincronizar_gmail.py --credenciales "%CREDENCIALES%"
if errorlevel 1 goto error
python procesar_correos.py
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
