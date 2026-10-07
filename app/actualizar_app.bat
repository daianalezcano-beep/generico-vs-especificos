@echo off
REM Doble click para traer los cambios nuevos del repo (git pull).
REM El .git vive en la carpeta padre de app\ (raiz del repo).
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if not exist ".git" (
    echo Esta carpeta no es un clon de git ^(no tiene carpeta .git^).
    echo Clonala una sola vez con: git clone ^<url-del-repo^>
    pause
    exit /b 1
)

set "GIT_EXE="
git --version >nul 2>&1
if not errorlevel 1 set "GIT_EXE=git"
if not defined GIT_EXE (
    if exist "C:\Users\daiana.lezcano\PortableGit\cmd\git.exe" set "GIT_EXE=C:\Users\daiana.lezcano\PortableGit\cmd\git.exe"
)
if not defined GIT_EXE (
    echo No se encontro git. Instalalo desde https://git-scm.com/download/win
    pause
    exit /b 1
)

"%GIT_EXE%" config --global --get-all safe.directory | findstr /L /C:"%CD%" >nul
if errorlevel 1 "%GIT_EXE%" config --global --add safe.directory "%CD%"

"%GIT_EXE%" pull
pause
