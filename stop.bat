@echo off
rem ===================================================================
rem  XRD Studio - ONE-CLICK STOP  (double-click this file)
rem
rem  Ends the app, the Vite dev server and the Python analysis engine.
rem  Safe to run when nothing is running.
rem ===================================================================

setlocal EnableExtensions EnableDelayedExpansion

chcp 65001 >nul 2>nul

call "%~dp0_common.bat" minimal
if errorlevel 1 goto :fail

rem stop.bat deliberately does NOT resolve the toolchain: you must be
rem able to shut the app down even after Node/Rust/venv moved or broke.
set "XRD_MODE=stop"
set "XRD_ROOT=%ROOT%"
set "XRD_TAURI_DIR=%TAURI_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\_launcher.ps1"
set "CODE=%ERRORLEVEL%"

endlocal & exit /b %CODE%

:fail
echo.
pause
endlocal & exit /b 1
