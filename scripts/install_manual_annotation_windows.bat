@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem Install the lightweight Windows edition.  It deliberately does not install
rem PyTorch, SAM, YOLO, CUDA, model weights, or the training platform.
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
rem Keep third-party packages out of the project directory.  PySide6 contains
rem deeply nested Qt files and can exceed the legacy Windows 260-character
rem path limit when a ZIP was extracted into a long folder name.
if not defined JOTMARK_ENV_DIR set "JOTMARK_ENV_DIR=%LOCALAPPDATA%\JotMark\manual-venv"
set "ENV_DIR=%JOTMARK_ENV_DIR%"
set "PYTHON=%ENV_DIR%\Scripts\python.exe"

echo.
echo ===============================================
echo  JotMark Manual Annotation - Windows Installer
echo ===============================================
echo Python environment: %ENV_DIR%
echo.

if not exist "%PYTHON%" (
    echo Creating the isolated Python environment...
    call :create_venv
    if errorlevel 1 goto :python_error
)

"%PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto :python_version_error

call :select_fastest_pypi_index
echo Selected package source: %PIP_INDEX_NAME%
echo %PIP_INDEX_URL%

echo Installing required packages. Please keep this window open...
call :pip_with_fallback --upgrade pip setuptools wheel
if errorlevel 1 goto :install_error
call :pip_with_fallback -r "%ROOT%\requirements-manual.txt"
if errorlevel 1 goto :install_error

pushd "%ROOT%"
set "JOTMARK_MANUAL_ONLY=1"
"%PYTHON%" -c "import cv2, numpy; from PIL import Image; from PySide6.QtWidgets import QApplication; import main; from jotmark.data.exporter import Exporter; from jotmark.data.attribute_schema import ClassAttributeStore; from jotmark.data.category_conversion import convert_attribute_to_category; from ui.annotation_tree_widget import AnnotationTreeWidget; from ui.attribute_widgets import AttributeScopeDialog, BooleanValuePicker; from ui.category_conversion_dialog import CategoryConversionDialog; assert main.MANUAL_ONLY; assert hasattr(Exporter, 'save_curvelanes'); assert hasattr(AnnotationTreeWidget, 'show_class_context_menu_for_item'); assert hasattr(AnnotationTreeWidget, 'show_annotation_context_menu_for_item'); assert BooleanValuePicker is not None; assert hasattr(ClassAttributeStore, 'replace_schema'); assert CategoryConversionDialog is not None and callable(convert_attribute_to_category); assert hasattr(AttributeScopeDialog, '_current_visibility_signature'); print('Environment check passed: manual annotation edition, CurveLanes export, attribute import and safe category conversion are ready.')"
set "CHECK_RESULT=%ERRORLEVEL%"
popd
if not "%CHECK_RESULT%"=="0" goto :install_error

echo.
echo Installation complete.
echo Next time, double-click: Start-JotMark-Manual-Windows.bat
echo.
pause
exit /b 0

:create_venv
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        python -m venv "%ENV_DIR%"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

echo Python was not found. Installing Python 3.12 with Windows Package Manager...
where winget >nul 2>nul
if errorlevel 1 exit /b 1

winget install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 exit /b 1

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

if exist "%ProgramFiles(x86)%\Python312\python.exe" (
    "%ProgramFiles(x86)%\Python312\python.exe" -m venv "%ENV_DIR%"
    if errorlevel 1 exit /b 1
    exit /b 0
)

for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul ^| find "REG_SZ"') do (
    if exist "%%Bpython.exe" (
        "%%Bpython.exe" -m venv "%ENV_DIR%"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul ^| find "REG_SZ"') do (
    if exist "%%Bpython.exe" (
        "%%Bpython.exe" -m venv "%ENV_DIR%"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)

exit /b 1

:select_fastest_pypi_index
if defined JOTMARK_PIP_INDEX_URL (
    set "PIP_INDEX_URL=%JOTMARK_PIP_INDEX_URL%"
    set "PIP_INDEX_NAME=custom source"
    exit /b 0
)

set "PIP_INDEX_URL=https://pypi.org/simple"
set "PIP_INDEX_NAME=Official PyPI (fallback)"
echo Checking package-source response time. This takes up to about 15 seconds...

for /f "tokens=1,2 delims=|" %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $candidates = @(@{ Name = 'Tsinghua TUNA'; Url = 'https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/' }, @{ Name = 'USTC'; Url = 'https://mirrors.ustc.edu.cn/pypi/simple/' }, @{ Name = 'Aliyun'; Url = 'https://mirrors.aliyun.com/pypi/simple/' }, @{ Name = 'Official PyPI'; Url = 'https://pypi.org/simple/' }); $best = $null; foreach ($candidate in $candidates) { try { $request = [System.Net.HttpWebRequest]::Create($candidate.Url); $request.Method = 'HEAD'; $request.Timeout = 3500; $request.ReadWriteTimeout = 3500; $watch = [System.Diagnostics.Stopwatch]::StartNew(); $response = $request.GetResponse(); $watch.Stop(); $response.Close(); if (($null -eq $best) -or ($watch.ElapsedMilliseconds -lt $best.Milliseconds)) { $best = [PSCustomObject]@{ Url = $candidate.Url; Name = $candidate.Name; Milliseconds = $watch.ElapsedMilliseconds } } } catch {} }; if ($null -ne $best) { Write-Output ($best.Url + [char]124 + $best.Name) }"') do (
    set "PIP_INDEX_URL=%%A"
    set "PIP_INDEX_NAME=%%B"
)
exit /b 0

:pip_with_fallback
"%PYTHON%" -m pip install %* --index-url "%PIP_INDEX_URL%" --timeout 30 --retries 2
if not errorlevel 1 exit /b 0

if /I "%PIP_INDEX_URL%"=="https://pypi.org/simple/" exit /b 1

echo The selected source failed. Retrying once with Official PyPI...
"%PYTHON%" -m pip install %* --index-url "https://pypi.org/simple/" --timeout 30 --retries 2
if errorlevel 1 exit /b 1
exit /b 0

:python_error
echo.
echo Python 3 was not found.
echo If Python was just installed, close this window and run this file once
echo more so Windows can refresh the Python launcher path.
echo If winget is unavailable, install or update App Installer from Microsoft
echo Store, or install 64-bit Python 3.10 or later manually and run again.
echo.
pause
exit /b 1

:python_version_error
echo.
echo Python 3.10 or later is required. Install a current 64-bit Python release,
echo then run this file again.
echo.
pause
exit /b 1

:install_error
echo.
echo Installation did not finish. Check the error above, confirm the computer
echo is connected to the Internet, then run this file again.
echo.
pause
exit /b 1
