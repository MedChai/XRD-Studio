@echo off
rem ===================================================================
rem  XRD Studio - ONE-CLICK START  (double-click this file)
rem
rem  Picks the fastest available mode automatically:
rem    * packaged build found  ->  launches it directly (instant)
rem    * otherwise             ->  `npm run tauri dev` (first run
rem                                compiles, may take minutes)
rem
rem  Closing the app window shuts the engine down with it.
rem  To close everything by hand, double-click stop.bat.
rem ===================================================================

setlocal EnableExtensions EnableDelayedExpansion

rem UTF-8 console so the launcher's Chinese messages render correctly.
rem Guarded: on an ancient console this simply fails and we continue in
rem the default codepage (English-only output, still fully functional).
chcp 65001 >nul 2>nul

call "%~dp0_common.bat" full
if errorlevel 1 goto :fail

rem _common.bat left XRD_MODE = run (packaged build found) or dev.
set "XRD_MODE_LBL=%XRD_MODE%"
if /i "%XRD_MODE%"=="run" (
    set "XRD_DIST_EXE=%TAURI_DIR%\target\release\%XRD_DISTNAME%"
    if not exist "!XRD_DIST_EXE!" set "XRD_DIST_EXE=%TAURI_DIR%\target\release\app.exe"
) else (
    set "XRD_DIST_EXE="
)
set "XRD_MODE=start"

set "XRD_ROOT=%ROOT%"
set "XRD_PYTHON=%PYTHON_EXE%"
set "XRD_NPM=%NPM_EXE%"
set "XRD_CARGO=%CARGO_EXE%"
set "XRD_TAURI_DIR=%TAURI_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\_launcher.ps1"
set "CODE=%ERRORLEVEL%"

rem The launcher's stdout (PID / exit code) is informational - keep the
rem window open only long enough to read a failure.
if not "%CODE%"=="0" (
    echo.
    echo [i] Exit code %CODE%.
    echo [i] Tauri stdout: %XRD_LOG_DIR%\tauri.stdout.log
    echo [i] Tauri stderr: %XRD_LOG_DIR%\tauri.stderr.log
    echo [i] Engine log:   %XRD_LOG_DIR%\engine.log
    pause
)
endlocal & exit /b %CODE%

:fail
echo.
pause
endlocal & exit /b 1
