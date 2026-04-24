@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    where python3 >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python3"
)

if not defined PYTHON_CMD (
    echo ERROR: Python was not found in PATH.
    echo Please install Python 3.10+ first and enable "Add python.exe to PATH".
    goto :error
)

echo Using Python launcher: %PYTHON_CMD%
%PYTHON_CMD% --version
if errorlevel 1 goto :error

echo.
echo [1/5] Checking bundled FFmpeg files...
set "NEED_FFMPEG="
if not exist "ffmpeg.exe" set "NEED_FFMPEG=1"
if not exist "ffprobe.exe" set "NEED_FFMPEG=1"

if defined NEED_FFMPEG (
    echo ffmpeg.exe or ffprobe.exe is missing. Downloading automatically...
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\download_ffmpeg.ps1" -Destination "%CD%"
    if errorlevel 1 (
        echo ERROR: Failed to download FFmpeg.
        goto :error
    )
)

if not exist "ffmpeg.exe" (
    echo ERROR: ffmpeg.exe is still missing after download.
    goto :error
)
if not exist "ffprobe.exe" (
    echo ERROR: ffprobe.exe is still missing after download.
    goto :error
)
echo OK: ffmpeg.exe and ffprobe.exe will be bundled into the final EXE.

echo.
echo [2/5] Checking pip...
%PYTHON_CMD% -m pip --version >nul 2>nul
if errorlevel 1 (
    echo pip not found. Bootstrapping with ensurepip...
    %PYTHON_CMD% -m ensurepip --upgrade
    if errorlevel 1 (
        echo ERROR: Failed to initialize pip.
        goto :error
    )
)

echo.
echo [3/5] Checking build dependencies...
%PYTHON_CMD% -c "import PyInstaller, PySide6, pysubs2" >nul 2>nul
if errorlevel 1 (
    echo Missing build dependencies. Installing from requirements.txt...
    %PYTHON_CMD% -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install build dependencies.
        goto :error
    )
) else (
    echo OK: PyInstaller, PySide6, and pysubs2 are already available.
)

echo.
echo [4/5] Cleaning old output...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"

echo.
echo [5/5] Building with spec file...
%PYTHON_CMD% -m PyInstaller VideoClipper.spec --clean --noconfirm
if errorlevel 1 (
    echo ERROR: PyInstaller build failed.
    goto :error
)

echo.
if exist "dist\VideoClipper.exe" (
    echo SUCCESS! Output: dist\VideoClipper.exe
    echo This EXE already contains ffmpeg.exe and ffprobe.exe.
    pause
    exit /b 0
)

echo ERROR: dist\VideoClipper.exe was not generated.
goto :error

:error
echo.
echo BUILD FAILED. Please check the messages above.
pause
exit /b 1
