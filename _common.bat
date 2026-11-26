@echo off
rem ===================================================================
rem  XRD Studio - shared setup, sourced (call) by start.bat /
rem  start-dev.bat / stop.bat.
rem
rem  Usage:
rem    call _common.bat full      -> paths + venv check + toolchain + mode
rem    call _common.bat minimal   -> paths only (stop.bat; must work even
rem                                  when Node/Rust/venv are missing)
rem
rem  Always sets (in the caller's environment):
rem    ROOT APPDIR TAURI_DIR ENGINE_DIR PYTHON_EXE XRD_DISTNAME
rem    XRD_DATA_ROOT XRD_LOG_DIR
rem    XRD_MODE  = run | dev   (full mode only)
rem    NPM_EXE CARGO_EXE       (full mode only)
rem
rem  Encoding note: this file is saved as UTF-8 *without* BOM and stays
rem  pure ASCII, so cmd.exe parses it identically under any codepage.
rem  Chinese messages live in _launcher.ps1 (see the note there).
rem ===================================================================

set "XRD_DISTNAME=XRD Studio.exe"
set "XRD_LOCALAPPDATA=%LOCALAPPDATA%"
if not defined XRD_LOCALAPPDATA set "XRD_LOCALAPPDATA=%USERPROFILE%\AppData\Local"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined XRD_DATA_ROOT (
    set "XRD_DATA_ROOT=%ROOT%\XRDStudio\library"
    set "XRD_LEGACY_DATA_ROOT=%XRD_LOCALAPPDATA%\XRDStudio"
)
if not defined XRD_LOG_DIR set "XRD_LOG_DIR=%ROOT%\XRDStudio\logs"
set "ROOT_WIN=%ROOT:\=/%"
set "APPDIR=%ROOT%\app"
set "TAURI_DIR=%APPDIR%\src-tauri"
set "ENGINE_DIR=%ROOT%\engine"
set "PYTHON_EXE=%ENGINE_DIR%\.venv\Scripts\python.exe"

if /i "%~1"=="minimal" exit /b 0

rem ---------------------------------------------------------------
rem  full mode: environment sanity checks
rem ---------------------------------------------------------------
if not exist "%TAURI_DIR%\tauri.conf.json" (
    echo [X] Scripts must sit in the repository root next to app\ and engine\.
    echo     Expected: %TAURI_DIR%\tauri.conf.json
    echo     Move start.bat / stop.bat back to the project root.
    exit /b 1
)
if not exist "%PYTHON_EXE%" (
    echo [X] Python engine venv not found:
    echo     %PYTHON_EXE%
    echo     Create it once with:  py -3 -m venv engine\.venv
    echo                          engine\.venv\Scripts\python.exe -m pip install -e engine
    exit /b 1
)

set "XRD_MODE=dev"
call :resolve_tools
if errorlevel 1 exit /b 1

rem Fastest available mode: launch the packaged build when present.
if exist "%TAURI_DIR%\target\release\%XRD_DISTNAME%" set "XRD_MODE=run"
if exist "%APPDIR%\dist\index.html" if exist "%TAURI_DIR%\target\release\app.exe" set "XRD_MODE=run"
exit /b 0

rem ---------------------------------------------------------------
rem  resolve_tools: locate npm and cargo as absolute paths
rem  (necessary because _launcher.ps1 runs with -NoProfile, and the
rem  8.3 short path of this Chinese-named project would otherwise
rem  break the embedded path references in the Tauri build).
rem ---------------------------------------------------------------
:resolve_tools
call :find_tool NPM_EXE "%ProgramFiles%\nodejs\npm.cmd" "%ProgramFiles(x86)%\nodejs\npm.cmd"
if not defined NPM_EXE (
    for /f "delims=" %%I in ('where npm.cmd 2^>nul') do if not defined NPM_EXE set "NPM_EXE=%%~fI"
)
if not defined NPM_EXE (
    echo [X] npm.cmd not found. Install Node.js LTS from https://nodejs.org
    exit /b 1
)

call :find_tool CARGO_EXE "%USERPROFILE%\.cargo\bin\cargo.exe"
if not defined CARGO_EXE (
    for /f "delims=" %%I in ('where cargo.exe 2^>nul') do if not defined CARGO_EXE set "CARGO_EXE=%%~fI"
)
if not defined CARGO_EXE (
    echo [X] cargo.exe not found. Install Rust from https://rustup.rs
    exit /b 1
)
exit /b 0

rem ---------------------------------------------------------------
rem  find_tool <var> <candidate1> [candidate2 ...]
rem    Resolves the first existing candidate to its absolute path.
rem ---------------------------------------------------------------
:find_tool
set "__VAR=%~1"
shift
:find_tool_loop
if "%~1"=="" set "__VAR=" & exit /b 1
if exist "%~1" (
    for %%I in ("%~1") do set "%__VAR%=%%~fI"
    set "__VAR="
    exit /b 0
)
shift
goto :find_tool_loop
