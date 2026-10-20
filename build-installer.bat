@echo off
rem ===================================================================
rem  XRD Studio - BUILD WINDOWS INSTALLER (NSIS)
rem
rem  Produces:
rem    app\src-tauri\target\release\bundle\nsis\XRD Studio_*.exe
rem
rem  Steps:
rem    1. PyInstaller onedir sidecar  ->  app\src-tauri\sidecar\
rem    2. npm run build (frontend, also run by tauri)
rem    3. cargo release + NSIS bundle
rem
rem  Requires: Node.js, Rust/MSVC, engine\.venv with [packaging] extras.
rem  Pure ASCII (same rule as other root .bat scripts).
rem ===================================================================

setlocal EnableExtensions
setlocal EnableDelayedExpansion

set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "APPDIR=%ROOT%\app"
set "TAURI_DIR=%APPDIR%\src-tauri"
set "ENGINE_DIR=%ROOT%\engine"
set "PYTHON_EXE=%ENGINE_DIR%\.venv\Scripts\python.exe"
set "NPX_TAURI=%APPDIR%\node_modules\.bin\tauri.cmd"

if not exist "%TAURI_DIR%\tauri.conf.json" (
    echo [X] Run this from the repository root ^(next to app\ and engine\^).
    exit /b 1
)
if not exist "%PYTHON_EXE%" (
    echo [X] Engine venv missing:
    echo     %PYTHON_EXE%
    echo     Create: py -3 -m venv engine\.venv
    echo             engine\.venv\Scripts\python.exe -m pip install -e "engine[dev,packaging]"
    exit /b 1
)
if not exist "%NPX_TAURI%" (
    echo [X] Tauri CLI not found. Run: cd app ^&^& npm install
    exit /b 1
)

echo [1/3] Python sidecar ^(PyInstaller onedir^) ...
"%PYTHON_EXE%" "%ENGINE_DIR%\scripts\build_sidecar.py"
if errorlevel 1 exit /b 1

echo [2/3] Frontend + cargo release + NSIS ...
pushd "%APPDIR%"
call "%NPX_TAURI%" build --bundles nsis
set "CODE=%ERRORLEVEL%"
popd
if not "%CODE%"=="0" (
    echo [X] tauri build failed with exit code %CODE%.
    exit /b %CODE%
)

echo.
echo [OK] Installer:
dir /b "%TAURI_DIR%\target\release\bundle\nsis\*.exe"
echo.
echo Install that .exe on a machine without the repo, then launch
echo "XRD Studio" from the Start Menu ^(or the install folder^).
exit /b 0
