@echo off
REM ===================================================================
REM  Vaiven - genera dist\Vaiven.exe con un doble clic
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo  [1/4] Comprobando Python...
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: no se ha encontrado Python.
    echo  Instalalo desde https://www.python.org/downloads/ y marca
    echo  la casilla "Add Python to PATH" durante la instalacion.
    echo.
    pause
    exit /b 1
)

echo  [2/4] Instalando dependencias...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  ERROR: no se pudieron instalar las dependencias.
    pause
    exit /b 1
)

echo  [3/4] Limpiando la compilacion anterior...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Vaiven.spec del /q Vaiven.spec

echo  [4/4] Generando Vaiven.exe...
python -m PyInstaller --onefile --windowed --name Vaiven ^
    --icon assets\icon.ico ^
    --add-data "assets;assets" ^
    --hidden-import keyring.backends.Windows ^
    --hidden-import win32timezone ^
    src\main.py
if errorlevel 1 (
    echo.
    echo  ERROR: PyInstaller no pudo generar el ejecutable.
    pause
    exit /b 1
)

echo.
echo  ==============================================
echo   Listo: dist\Vaiven.exe
echo.
echo   Copialo donde quieras y abrelo con doble clic.
echo   La primera vez Windows puede avisar de que no
echo   reconoce el programa: pulsa "Mas informacion"
echo   y luego "Ejecutar de todas formas".
echo  ==============================================
echo.
pause
