@echo off
rem ===================================================================
rem  XRD Studio - ONE-CLICK DEV START  (double-click this file)
rem
rem  Always runs `npm run tauri dev` (hot reload, dev tools), even when
rem  a packaged build exists. Use this while editing code.
rem ===================================================================

setlocal EnableExtensions EnableDelayedExpansion

chcp 65001 >nul 2>nul

call "%~dp0_common.bat" full
if errorlevel 1 goto :fail

rem Force dev regardless of which mode _common.bat detected.
set "XRD_MODE=dev"
set "XRD_MODE_LBL=dev"
set "XRD_DIST_EXE="

set "XRD_ROOT=%ROOT%"
set "XRD_PYTHON=%PYTHON_EXE%"
set "XRD_NPM=%NPM_EXE%"
set "XRD_CARGO=%CARGO_EXE%"
set "XRD_TAURI_DIR=%TAURI_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\_launcher.ps1"
set "CODE=%ERRORLEVEL%"

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
