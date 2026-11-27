@echo off
rem ===================================================================
rem  XRD Studio - BUILD WINDOWS INSTALLER (NSIS)
rem
rem  Produces:
rem    app\src-tauri\target\release\bundle\nsis\XRD Studio_*.exe
rem
rem  Tauri builds the frontend and PyInstaller sidecar before bundling.
rem
rem  Requires: Node.js, Rust/MSVC, engine\.venv with [packaging] extras.
rem  Pure ASCII (same rule as other root .bat scripts).
rem ===================================================================

setlocal EnableExtensions

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

set "TEMP=%ENGINE_DIR%\build\release-temp"
set "TMP=%TEMP%"
set "PYINSTALLER_CONFIG_DIR=%ENGINE_DIR%\build\pyinstaller-cache"
set "npm_config_cache=%ENGINE_DIR%\build\npm-cache"
if not exist "%TEMP%\." mkdir "%TEMP%"
if not exist "%TEMP%\." exit /b 1
set "XRD_PACK_TOOLCHAIN=%ENGINE_DIR%\build\windows-toolchain\use.ps1"

echo Building frontend, Python sidecar, and NSIS installer ...
pushd "%APPDIR%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Test-Path -LiteralPath $env:XRD_PACK_TOOLCHAIN -PathType Leaf) { . $env:XRD_PACK_TOOLCHAIN }; & $env:NPX_TAURI build --bundles nsis; exit $LASTEXITCODE"
set "CODE=%ERRORLEVEL%"
popd
if not "%CODE%"=="0" (
    echo [X] tauri build failed with exit code %CODE%.
    exit /b %CODE%
)

echo.
echo [OK] See the path reported by Tauri above for this build.
echo.
echo Install that .exe on a machine without the repo, then launch
echo "XRD Studio" from the Start Menu ^(or the install folder^).
exit /b 0
