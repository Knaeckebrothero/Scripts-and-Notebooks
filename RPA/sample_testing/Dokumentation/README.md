# Finanzdaten Stichprobentest - Installationsanleitung

## 📋 Übersicht

Dieses Projekt enthält zwei Python-Anwendungen für die zufällige Stichprobenauswahl aus Finanzdaten:

1. **Standard Version** (`sample_testing_standard.py`) - Verwendet nur Python-Standardbibliotheken
2. **Erweiterte Version** (`sample_testing_advanced.py`) - Moderne Web-Oberfläche mit erweiterten Features

## 🚀 Schnellstart

1. **Entpacken** Sie die ZIP-Datei
2. **Doppelklick** auf `install.bat` (einmalig)
3. **Starten** Sie die gewünschte Version:
    - `start_standard.bat` → Desktop-Anwendung
    - `start_erweitert.bat` → Web-Anwendung

Das war's! Die Beispieldaten können Sie direkt in den Anwendungen laden.

## 🔧 Voraussetzungen

- **Python 3.8 oder höher** muss auf Ihrem Windows-Computer installiert sein
- Sie können dies prüfen, indem Sie die Eingabeaufforderung öffnen und `python --version` eingeben

Falls Python noch nicht installiert ist, laden Sie es von [python.org](https://www.python.org/downloads/) herunter.

## 📦 Installation

### 🚀 Schnellinstallation (Empfohlen)

1. Entpacken Sie die ZIP-Datei in einen Ordner (z.B. `C:\Stichprobentest\`)
2. Doppelklicken Sie auf `install.bat`
3. Folgen Sie den Anweisungen auf dem Bildschirm

Die Installation ist abgeschlossen, wenn "INSTALLATION ERFOLGREICH ABGESCHLOSSEN!" erscheint.

### Manuelle Installation (Alternative)

Falls die automatische Installation nicht funktioniert, folgen Sie diesen Schritten:

#### Schritt 1: ZIP-Datei entpacken

Entpacken Sie die ZIP-Datei in einen Ordner Ihrer Wahl, z.B. `C:\Stichprobentest\`

#### Schritt 2: Eingabeaufforderung öffnen

Entpacken Sie die ZIP-Datei in einen Ordner Ihrer Wahl, z.B.:
```
C:\Stichprobentest\
```

Der Ordner sollte folgende Dateien enthalten:
```
Stichprobentest/
├── sample_testing_standard.py      # Standard-Version (Tkinter)
├── sample_testing_advanced.py      # Erweiterte Version (Streamlit)
├── requirements.txt                # Python-Paketliste
├── sample_financial_data.csv       # Beispieldaten CSV
├── sample_financial_data.xlsx      # Beispieldaten Excel
├── install.bat                     # Installations-Skript
├── start_standard.bat              # Starter für Standard-Version
├── start_erweitert.bat             # Starter für erweiterte Version
└── README.md                       # Diese Anleitung
```

#### Schritt 2: Eingabeaufforderung öffnen

1. Drücken Sie `Windows + R`
2. Geben Sie `cmd` ein und drücken Sie Enter
3. Navigieren Sie zum entpackten Ordner:
```bash
cd C:\Stichprobentest
```

#### Schritt 3: Virtuelle Umgebung erstellen

Eine virtuelle Umgebung isoliert die Python-Pakete für dieses Projekt:

```bash
python -m venv venv
```

Dies erstellt einen neuen Ordner namens `venv` in Ihrem Projektverzeichnis.

#### Schritt 4: Virtuelle Umgebung aktivieren

```bash
venv\Scripts\activate
```

Nach erfolgreicher Aktivierung sehen Sie `(venv)` am Anfang Ihrer Eingabezeile:
```
(venv) C:\Stichprobentest>
```

#### Schritt 5: Abhängigkeiten installieren

Installieren Sie die benötigten Pakete für die erweiterte Version:

```bash
pip install -r requirements.txt
```

Dies installiert:
- `streamlit` - Web-Framework für die Benutzeroberfläche
- `pandas` - Datenverarbeitung
- `openpyxl` - Excel-Datei Unterstützung

## 🚀 Anwendungen starten

### 🎯 Einfacher Start (Empfohlen)

Nach erfolgreicher Installation können Sie die Anwendungen einfach per Doppelklick starten:

- **Standard-Version**: Doppelklick auf `start_standard.bat`
- **Erweiterte Version**: Doppelklick auf `start_erweitert.bat`

### Manueller Start (Alternative)

Falls Sie die Anwendungen manuell starten möchten:

#### Option 1: Standard Version (Tkinter)

Diese Version benötigt **keine zusätzlichen Pakete** und kann direkt gestartet werden:

```bash
python sample_testing_standard.py
```

**Features:**
- Desktop-Anwendung mit klassischer Benutzeroberfläche
- Unterstützt CSV-Dateien
- Ideal für Umgebungen mit Sicherheitsbeschränkungen

#### Option 2: Erweiterte Version (Streamlit)

Starten Sie die moderne Web-Anwendung:

```bash
streamlit run sample_testing_advanced.py
```

Nach dem Start:
1. Es öffnet sich automatisch Ihr Standard-Webbrowser
2. Falls nicht, öffnen Sie manuell: http://localhost:8501
3. Die Anwendung läuft lokal auf Ihrem Computer

**Features:**
- Moderne Web-Oberfläche
- Unterstützt CSV und Excel-Dateien
- Erweiterte Filter und Analysen
- Export in verschiedene Formate

## 📊 Verwendung der Beispieldaten

Im Ordner finden Sie zwei Beispieldateien:
- `sample_financial_data.csv` - Beispieldaten im CSV-Format
- `sample_financial_data.xlsx` - Gleiche Daten im Excel-Format

Diese können Sie direkt in den Anwendungen laden, um die Funktionalität zu testen.

## 🔄 Tägliche Nutzung

### Einfache Nutzung mit Batch-Dateien

Doppelklicken Sie einfach auf:
- `start_standard.bat` für die Desktop-Version
- `start_erweitert.bat` für die Web-Version

### Manuelle Nutzung über Eingabeaufforderung

Für die zukünftige Nutzung:

1. Öffnen Sie die Eingabeaufforderung
2. Navigieren Sie zum Projektordner: `cd C:\Stichprobentest`
3. Aktivieren Sie die virtuelle Umgebung: `venv\Scripts\activate`
4. Starten Sie die gewünschte Anwendung (siehe oben)

## ❓ Häufige Probleme

### Batch-Dateien funktionieren nicht
- Rechtklick → "Als Administrator ausführen"
- Prüfen Sie, ob Sie im richtigen Ordner sind
- Führen Sie zuerst `install.bat` aus

### "python" wird nicht erkannt
- Stellen Sie sicher, dass Python installiert und im PATH ist
- Alternativ versuchen Sie `py` statt `python`

### Streamlit startet nicht
- Prüfen Sie, ob die virtuelle Umgebung aktiviert ist (sehen Sie `(venv)` in der Eingabezeile?)
- Installieren Sie die Pakete erneut: `pip install -r requirements.txt`

### Firewall-Warnung bei Streamlit
- Dies ist normal, da Streamlit einen lokalen Webserver startet
- Erlauben Sie den Zugriff für private Netzwerke

## 🛑 Anwendung beenden

- **Tkinter Version**: Schließen Sie einfach das Fenster
- **Streamlit Version**: Drücken Sie `Strg + C` in der Eingabeaufforderung

## 📝 Virtuelle Umgebung deaktivieren

Nach der Nutzung können Sie die virtuelle Umgebung verlassen:

```bash
deactivate
```

## 💡 Tipps

- **Desktop-Verknüpfungen erstellen**:
    - Rechtsklick auf `start_standard.bat` oder `start_erweitert.bat`
    - "Senden an" → "Desktop (Verknüpfung erstellen)"
    - Benennen Sie die Verknüpfungen um (z.B. "Stichprobentest Standard")
- Kopieren Sie Ihre eigenen Finanzdaten in den Projektordner
- Die virtuelle Umgebung muss nur einmal erstellt werden
- Updates der Pakete: `pip install --upgrade -r requirements.txt`

## 📞 Support

Bei Fragen wenden Sie sich an die IT-Abteilung oder die Datenmanagement-Abteilung.

## 📁 Übersicht der Dateien

| Datei | Beschreibung |
|-------|--------------|
| `install.bat` | Einmalige Installation durchführen |
| `start_standard.bat` | Desktop-Version starten |
| `start_erweitert.bat` | Web-Version starten |
| `requirements.txt` | Liste der benötigten Python-Pakete |
| `*.py` | Die eigentlichen Programmdateien |
| `*.csv / *.xlsx` | Beispieldaten zum Testen |

---

**Viel Erfolg mit der Stichprobenauswahl!** 🎯
