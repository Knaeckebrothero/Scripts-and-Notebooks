@echo off
REM ===================================================
REM Starter für Stichprobentest - Erweiterte Version
REM ===================================================

echo.
echo ========================================
echo Stichprobentest - Erweiterte Version
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

REM Erweiterte Version starten
echo.
echo Starte Web-Anwendung...
echo.
echo Die Anwendung öffnet sich automatisch in Ihrem Browser.
echo Falls nicht, öffnen Sie bitte: http://localhost:8501
echo.
echo Zum Beenden drücken Sie Strg+C in diesem Fenster.
echo.
echo ========================================
echo.

streamlit run sample_testing_advanced.py

REM Bei Fehler
if errorlevel 1 (
    echo.
    echo FEHLER: Anwendung konnte nicht gestartet werden!
    echo.
    echo Mögliche Ursachen:
    echo - Die benötigten Pakete sind nicht installiert
    echo - Führen Sie 'pip install -r requirements.txt' aus
    echo.
    pause
)

REM Virtuelle Umgebung deaktivieren
call deactivate
