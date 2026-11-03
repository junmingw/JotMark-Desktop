@echo off
setlocal EnableExtensions
chcp 65001 >nul

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
if not defined JOTMARK_ENV_DIR set "JOTMARK_ENV_DIR=%LOCALAPPDATA%\JotMark\manual-venv"
set "PYTHON=%JOTMARK_ENV_DIR%\Scripts\python.exe"

call :ensure_environment
if errorlevel 1 (
    echo.
    echo Automatic environment setup did not finish. Check the message above,
    echo then double-click Start-JotMark-Manual-Windows.bat again.
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

:ensure_environment
if not exist "%PYTHON%" goto :repair_environment

pushd "%ROOT%"
set "JOTMARK_MANUAL_ONLY=1"
"%PYTHON%" -c "import sys, cv2, numpy; from PIL import Image; from PySide6.QtWidgets import QApplication; import main; assert sys.version_info >= (3, 10); assert main.MANUAL_ONLY" >nul 2>nul
set "HEALTH_RESULT=%ERRORLEVEL%"
popd
if "%HEALTH_RESULT%"=="0" exit /b 0

:repair_environment
echo.
echo The JotMark manual-annotation environment is missing or incomplete.
echo Starting automatic setup now. Please keep this window open...
echo.
call "%ROOT%\scripts\install_manual_annotation_windows.bat"
if errorlevel 1 exit /b 1

if not exist "%PYTHON%" exit /b 1
pushd "%ROOT%"
set "JOTMARK_MANUAL_ONLY=1"
"%PYTHON%" -c "import sys, cv2, numpy; from PIL import Image; from PySide6.QtWidgets import QApplication; import main; assert sys.version_info >= (3, 10); assert main.MANUAL_ONLY" >nul 2>nul
set "HEALTH_RESULT=%ERRORLEVEL%"
popd
if not "%HEALTH_RESULT%"=="0" exit /b 1
exit /b 0
