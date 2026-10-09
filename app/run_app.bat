@echo off
setlocal EnableDelayedExpansion
set "PROJECT_DIR=%~dp0"
REM Entorno de Python en ruta corta y fija (limite de ~260 caracteres de Windows).
set "VENV_DIR=%USERPROFILE%\.tp-gve-app-venv"
cd /d "%PROJECT_DIR%"

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Preparando la app por primera vez, puede tardar unos minutos...
    set "PYCMD="
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"
    if not defined PYCMD (
        python --version >nul 2>&1
        if not errorlevel 1 set "PYCMD=python"
    )
    if not defined PYCMD (
        echo No se encontro Python instalado en esta PC.
        echo Instalalo desde https://www.python.org/downloads/ tildando "Add python.exe to PATH" y volve a ejecutar.
        pause
        exit /b 1
    )
    !PYCMD! -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo No se pudo crear el entorno de Python.
        pause
        exit /b 1
    )
)

call "%VENV_DIR%\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

REM Solo localhost: la app no tiene login propio.
streamlit run app.py --server.address=localhost --server.port=8502
pause
