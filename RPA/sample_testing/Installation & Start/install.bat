@echo off
REM ===================================================
REM Installations-Skript für Stichprobentest
REM ===================================================

echo.
echo ====================================================
echo Installation - Finanzdaten Stichprobentest
echo ====================================================
echo.
echo Dieses Skript richtet die Python-Umgebung ein
echo und installiert alle benötigten Komponenten.
echo.
echo ====================================================
echo.
pause

REM Python-Version prüfen
echo.
echo Prüfe Python-Installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo FEHLER: Python wurde nicht gefunden!
    echo.
    echo Bitte installieren Sie Python 3.8 oder höher von:
    echo https://www.python.org/downloads/
    echo.
    echo Achten Sie darauf, "Add Python to PATH" anzukreuzen!
    echo.
    pause
    exit /b 1
)

python --version
echo.

REM Virtuelle Umgebung erstellen
echo Erstelle virtuelle Python-Umgebung...
python -m venv venv

if errorlevel 1 (
    echo.
    echo FEHLER: Virtuelle Umgebung konnte nicht erstellt werden!
    echo.
    pause
    exit /b 1
)

echo Virtuelle Umgebung erfolgreich erstellt!
echo.

REM Virtuelle Umgebung aktivieren
echo Aktiviere virtuelle Umgebung...
call venv\Scripts\activate.bat

if errorlevel 1 (
    echo.
    echo FEHLER: Virtuelle Umgebung konnte nicht aktiviert werden!
    echo.
    pause
    exit /b 1
)

REM Pip aktualisieren
echo.
echo Aktualisiere pip...
python -m pip install --upgrade pip

REM Pakete installieren
echo.
echo Installiere benötigte Pakete...
echo Dies kann einige Minuten dauern...
echo.
pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo FEHLER: Pakete konnten nicht installiert werden!
    echo.
    pause
    exit /b 1
)

REM Installation erfolgreich
echo.
echo ====================================================
echo.
echo INSTALLATION ERFOLGREICH ABGESCHLOSSEN!
echo.
echo Sie können nun die Anwendungen starten:
echo - start_standard.bat    (für die Desktop-Version)
echo - start_erweitert.bat   (für die Web-Version)
echo.
echo ====================================================
echo.

REM Virtuelle Umgebung deaktivieren
call deactivate

pause
