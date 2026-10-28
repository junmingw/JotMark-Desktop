@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
if not defined JOTMARK_ENV_DIR set "JOTMARK_ENV_DIR=%LOCALAPPDATA%\JotMark\manual-venv"
set "PYTHON=%JOTMARK_ENV_DIR%\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo.
    echo JotMark is not installed yet.
    echo Double-click Install-JotMark-Manual-Windows.bat first.
    echo.
    pause
    exit /b 1
)

cd /d "%ROOT%"
set "JOTMARK_MANUAL_ONLY=1"
"%PYTHON%" main.py
set "RESULT=%ERRORLEVEL%"

if not "%RESULT%"=="0" (
    echo.
    echo JotMark closed with an error. Please take a screenshot of this window.
    pause
)
exit /b %RESULT%
