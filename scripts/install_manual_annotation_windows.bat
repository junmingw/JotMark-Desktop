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
set "PYTHON_BOOTSTRAP_VERSION=3.12.10"
set "PYTHON_BOOTSTRAP_OFFICIAL_URL=https://www.python.org/ftp/python/%PYTHON_BOOTSTRAP_VERSION%/python-%PYTHON_BOOTSTRAP_VERSION%-amd64.exe"
set "PYTHON_BOOTSTRAP_URL=%PYTHON_BOOTSTRAP_OFFICIAL_URL%"
set "PYTHON_BOOTSTRAP_SOURCE=Official Python (fallback)"
set "PYTHON_BOOTSTRAP_EXE=%TEMP%\jotmark-python-%PYTHON_BOOTSTRAP_VERSION%-amd64.exe"

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
echo The start script checks the environment and automatically repairs it if needed.
echo.
pause
exit /b 0

:create_venv
call :create_venv_with_existing_python
if not errorlevel 1 exit /b 0

echo Python was not found. Trying Windows Package Manager first...
where winget.exe >nul 2>nul
if not errorlevel 1 (
    winget.exe install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements
    if not errorlevel 1 (
        call :create_venv_with_existing_python
        if not errorlevel 1 exit /b 0
    )
    echo Windows Package Manager could not install Python. Falling back to the official Python installer...
) else (
    echo Windows Package Manager is unavailable. Falling back to the official Python installer...
)

call :install_python_from_official
if errorlevel 1 exit /b 1
call :create_venv_with_existing_python
exit /b %ERRORLEVEL%

:create_venv_with_existing_python
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%ENV_DIR%"
        if not errorlevel 1 exit /b 0
    )
)

where python >nul 2>nul
if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 (
        python -m venv "%ENV_DIR%"
        if not errorlevel 1 exit /b 0
    )
)

if exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    "%LocalAppData%\Programs\Python\Python312\python.exe" -m venv "%ENV_DIR%"
    if not errorlevel 1 exit /b 0
)

if exist "%ProgramFiles%\Python312\python.exe" (
    "%ProgramFiles%\Python312\python.exe" -m venv "%ENV_DIR%"
    if not errorlevel 1 exit /b 0
)

if exist "%ProgramFiles(x86)%\Python312\python.exe" (
    "%ProgramFiles(x86)%\Python312\python.exe" -m venv "%ENV_DIR%"
    if not errorlevel 1 exit /b 0
)

for /f "tokens=2,*" %%A in ('reg query "HKCU\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul ^| find "REG_SZ"') do (
    if exist "%%Bpython.exe" (
        "%%Bpython.exe" -m venv "%ENV_DIR%"
        if not errorlevel 1 exit /b 0
    )
)

for /f "tokens=2,*" %%A in ('reg query "HKLM\Software\Python\PythonCore\3.12\InstallPath" /ve 2^>nul ^| find "REG_SZ"') do (
    if exist "%%Bpython.exe" (
        "%%Bpython.exe" -m venv "%ENV_DIR%"
        if not errorlevel 1 exit /b 0
    )
)

exit /b 1

:install_python_from_official
call :select_fastest_python_bootstrap_source
echo Selected Python installer source: %PYTHON_BOOTSTRAP_SOURCE%
echo %PYTHON_BOOTSTRAP_URL%

call :download_python_bootstrap
if errorlevel 1 (
    if /I "%PYTHON_BOOTSTRAP_URL%"=="%PYTHON_BOOTSTRAP_OFFICIAL_URL%" exit /b 1
    echo The selected Python source failed. Retrying once with Official Python...
    set "PYTHON_BOOTSTRAP_URL=%PYTHON_BOOTSTRAP_OFFICIAL_URL%"
    set "PYTHON_BOOTSTRAP_SOURCE=Official Python (fallback)"
    call :download_python_bootstrap
    if errorlevel 1 exit /b 1
)
goto :verify_python_bootstrap

:download_python_bootstrap
if exist "%PYTHON_BOOTSTRAP_EXE%" del /q "%PYTHON_BOOTSTRAP_EXE%" >nul 2>nul

where curl.exe >nul 2>nul
if not errorlevel 1 (
    echo Downloading 64-bit Python %PYTHON_BOOTSTRAP_VERSION% installer with curl...
    curl.exe --fail --location --retry 2 --connect-timeout 20 --progress-bar --output "%PYTHON_BOOTSTRAP_EXE%" "%PYTHON_BOOTSTRAP_URL%"
    if not errorlevel 1 (
        call :python_bootstrap_is_complete
        if not errorlevel 1 exit /b 0
    )
    echo curl download failed or was incomplete. Trying Windows Background Intelligent Transfer Service...
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; Start-BitsTransfer -Source '%PYTHON_BOOTSTRAP_URL%' -Destination '%PYTHON_BOOTSTRAP_EXE%'"
if errorlevel 1 exit /b 1
call :python_bootstrap_is_complete
exit /b %ERRORLEVEL%

:verify_python_bootstrap
call :python_bootstrap_is_complete
if not errorlevel 1 goto :run_python_bootstrap
echo The Python installer download is incomplete. Please check the network connection and retry.
exit /b 1

:python_bootstrap_is_complete
if not exist "%PYTHON_BOOTSTRAP_EXE%" exit /b 1
for %%F in ("%PYTHON_BOOTSTRAP_EXE%") do if %%~zF GEQ 20000000 exit /b 0
exit /b 1

:run_python_bootstrap
echo Installing Python %PYTHON_BOOTSTRAP_VERSION% for the current user...
"%PYTHON_BOOTSTRAP_EXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
if errorlevel 1 exit /b 1
exit /b 0

:select_fastest_python_bootstrap_source
if defined JOTMARK_PYTHON_BOOTSTRAP_URL (
    set "PYTHON_BOOTSTRAP_URL=%JOTMARK_PYTHON_BOOTSTRAP_URL%"
    set "PYTHON_BOOTSTRAP_SOURCE=custom source"
    exit /b 0
)

set "PYTHON_BOOTSTRAP_URL=%PYTHON_BOOTSTRAP_OFFICIAL_URL%"
set "PYTHON_BOOTSTRAP_SOURCE=Official Python (fallback)"
echo Checking Python installer source response time. This takes up to about 15 seconds...

for /f "tokens=1,2 delims=|" %%A in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $version = '%PYTHON_BOOTSTRAP_VERSION%'; $file = 'python-' + $version + '-amd64.exe'; $candidates = @(@{ Name = 'Tsinghua TUNA'; Url = 'https://mirrors.tuna.tsinghua.edu.cn/python/' + $version + '/' + $file }, @{ Name = 'USTC'; Url = 'https://mirrors.ustc.edu.cn/python/' + $version + '/' + $file }, @{ Name = 'Aliyun'; Url = 'https://mirrors.aliyun.com/python-release/windows/' + $file }, @{ Name = 'Official Python'; Url = 'https://www.python.org/ftp/python/' + $version + '/' + $file }); $best = $null; foreach ($candidate in $candidates) { try { $request = [System.Net.HttpWebRequest]::Create($candidate.Url); $request.Method = 'HEAD'; $request.Timeout = 3500; $request.ReadWriteTimeout = 3500; $request.UserAgent = 'JotMark installer'; $watch = [System.Diagnostics.Stopwatch]::StartNew(); $response = $request.GetResponse(); $watch.Stop(); $length = $response.ContentLength; $response.Close(); if (($length -ge 20000000) -and (($null -eq $best) -or ($watch.ElapsedMilliseconds -lt $best.Milliseconds))) { $best = [PSCustomObject]@{ Url = $candidate.Url; Name = $candidate.Name; Milliseconds = $watch.ElapsedMilliseconds } } } catch {} }; if ($null -ne $best) { Write-Output ($best.Url + [char]124 + $best.Name) }"') do (
    set "PYTHON_BOOTSTRAP_URL=%%A"
    set "PYTHON_BOOTSTRAP_SOURCE=%%B"
)
exit /b 0

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
echo JotMark tried Windows Package Manager and then the official Python installer.
echo Check the network connection, proxy or security software, then run this file again.
echo If the problem persists, install 64-bit Python 3.10 or later manually and retry.
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
