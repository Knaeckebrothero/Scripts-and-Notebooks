@echo off
REM ===================================================
REM Starter für Stichprobentest - Standard Version
REM ===================================================

echo.
echo ========================================
echo Stichprobentest - Standard Version
echo ========================================
echo.

REM Virtuelle Umgebung aktivieren
echo Aktiviere Python-Umgebung...
call venv\Scripts\activate.bat

REM Prüfen ob Aktivierung erfolgreich war
if errorlevel 1 (
    echo.
    echo FEHLER: Virtuelle Umgebung konnte nicht aktiviert werden!
    echo.
    echo Bitte stellen Sie sicher, dass Sie:
    echo 1. Die Installation gemäß README.md durchgeführt haben
    echo 2. Sich im richtigen Ordner befinden
    echo.
    pause
    exit /b 1
)

REM Standard Version starten
echo.
echo Starte Anwendung...
echo.
python sample_testing_standard.py

REM Bei Fehler
if errorlevel 1 (
    echo.
    echo FEHLER: Anwendung konnte nicht gestartet werden!
    echo.
    pause
)

REM Virtuelle Umgebung deaktivieren
call deactivate
