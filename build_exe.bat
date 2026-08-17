@echo off
setlocal

echo ============================================
echo   ORD Viewer - EXE Builder
echo ============================================
echo.

REM Controleer of Python beschikbaar is
python --version >nul 2>&1
if errorlevel 1 (
    echo [FOUT] Python niet gevonden in PATH.
    echo Zorg dat Python geinstalleerd is en beschikbaar in de PATH.
    pause
    exit /b 1
)

REM Installeer of update PyInstaller
echo [1/3] PyInstaller installeren / updaten...
pip install --upgrade pyinstaller
if errorlevel 1 (
    echo [FOUT] Kon PyInstaller niet installeren.
    pause
    exit /b 1
)

echo.
echo [2/3] EXE bouwen...

REM Ga naar de map waar dit script staat
cd /d "%~dp0"

pyinstaller ^
    --onefile ^
    --windowed ^
    --name "ORD Viewer" ^
    ord_viewer.py

if errorlevel 1 (
    echo.
    echo [FOUT] Build mislukt. Bekijk de foutmeldingen hierboven.
    pause
    exit /b 1
)

echo.
echo [3/3] Klaar!
echo.
echo De EXE staat in:  %~dp0dist\ORD Viewer.exe
echo.
echo Druk op een toets om de dist-map te openen...
pause >nul
explorer "%~dp0dist"
