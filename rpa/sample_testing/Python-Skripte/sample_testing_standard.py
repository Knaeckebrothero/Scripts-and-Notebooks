"""
Finanzdaten Stichprobentest - Standard Bibliothek Version
=========================================================
Diese Anwendung ermöglicht es Wirtschaftsprüfern, zufällige Stichproben
aus Finanzdaten zu ziehen. Es werden nur Python Standard-Bibliotheken verwendet,
was die Anwendung ideal für Umgebungen mit hohen Sicherheitsanforderungen macht.
"""
import csv
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime
import os


class StichprobentestApp:
    """
    Hauptklasse für die Stichprobentest-Anwendung.

    Diese Klasse erstellt und verwaltet die gesamte Benutzeroberfläche
    sowie die Datenverarbeitung für die Stichprobenauswahl.
    """

    def __init__(self, root):
        """
        Initialisiert die Anwendung.

        Args:
            root: Das Tkinter Hauptfenster
        """
        self.root = root
        self.root.title("Finanzdaten Stichprobentest - Standard Bibliothek Version")
        self.root.geometry("1000x700")

        # Datencontainer initialisieren
        self.data = []  # Alle geladenen Daten
        self.filtered_data = []  # Gefilterte Daten nach Anwendung der Filter

        # Benutzeroberfläche erstellen
        self.create_widgets()

    def create_widgets(self):
        """
        Erstellt alle UI-Elemente der Anwendung.

        Die Oberfläche ist in mehrere Bereiche unterteilt:
        1. Dateiauswahl
        2. Filteroptionen
        3. Stichprobenauswahl
        4. Ergebnisanzeige
        """

        # ========== DATEIAUSWAHL BEREICH ==========
        file_frame = ttk.Frame(self.root, padding="10")
        file_frame.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E))

        ttk.Label(file_frame, text="CSV-Datei:").grid(row=0, column=0, sticky=tk.W)
        self.file_label = ttk.Label(file_frame, text="Keine Datei ausgewählt", relief=tk.SUNKEN)
        self.file_label.grid(row=0, column=1, padx=5, sticky=(tk.W, tk.E))
        ttk.Button(file_frame, text="Durchsuchen", command=self.load_file).grid(row=0, column=2)

        # ========== FILTER BEREICH ==========
        filter_frame = ttk.LabelFrame(self.root, text="Filteroptionen", padding="10")
        filter_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Datumsfilter
        ttk.Label(filter_frame, text="Datum vor:").grid(row=0, column=0, sticky=tk.W)
        self.date_filter = ttk.Entry(filter_frame, width=15)
        self.date_filter.grid(row=0, column=1, padx=5)
        self.date_filter.insert(0, "31-12-2024")  # Standardwert
        ttk.Label(filter_frame, text="(TT-MM-JJJJ)", font=("Arial", 8)).grid(row=0, column=2, sticky=tk.W)

        # Wertfilter - Minimum
        ttk.Label(filter_frame, text="Mindestwert (€):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.min_value = ttk.Entry(filter_frame, width=15)
        self.min_value.grid(row=1, column=1, padx=5)
        ttk.Label(filter_frame, text="(Optional)", font=("Arial", 8)).grid(row=1, column=2, sticky=tk.W)

        # Wertfilter - Maximum
        ttk.Label(filter_frame, text="Höchstwert (€):").grid(row=2, column=0, sticky=tk.W)
        self.max_value = ttk.Entry(filter_frame, width=15)
        self.max_value.grid(row=2, column=1, padx=5)
        ttk.Label(filter_frame, text="(Optional)", font=("Arial", 8)).grid(row=2, column=2, sticky=tk.W)

        # Filter anwenden Button
        ttk.Button(
            filter_frame,
            text="Filter anwenden",
            command=self.apply_filters
        ).grid(row=3, column=0, columnspan=2, pady=10)

        # ========== STICHPROBENAUSWAHL BEREICH ==========
        sample_frame = ttk.LabelFrame(self.root, text="Stichprobenauswahl", padding="10")
        sample_frame.grid(row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Anzahl der Stichproben
        ttk.Label(sample_frame, text="Anzahl Stichproben:").grid(row=0, column=0, sticky=tk.W)
        self.sample_count = ttk.Spinbox(sample_frame, from_=1, to=100, width=10)
        self.sample_count.set(5)  # Standardwert: 5 Stichproben
        self.sample_count.grid(row=0, column=1, padx=5)

        # Verfügbare Datensätze anzeigen
        ttk.Label(sample_frame, text="Verfügbare Datensätze:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.available_label = ttk.Label(sample_frame, text="0", font=("Arial", 10, "bold"))
        self.available_label.grid(row=1, column=1, sticky=tk.W, padx=5)

        # Stichprobe generieren Button
        ttk.Button(
            sample_frame,
            text="Stichprobe generieren",
            command=self.generate_sample,
            style="Accent.TButton"  # Hervorgehobener Button
        ).grid(row=2, column=0, columnspan=2, pady=10)

        # Hinweis für Benutzer
        info_label = ttk.Label(
            sample_frame,
            text="Hinweis: Es werden zufällige\nDatensätze ausgewählt.",
            font=("Arial", 8),
            foreground="gray"
        )
        info_label.grid(row=3, column=0, columnspan=2, pady=5)

        # ========== ERGEBNIS BEREICH ==========
        results_frame = ttk.LabelFrame(self.root, text="Stichprobenergebnisse", padding="10")
        results_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=5)

        # Tabelle (Treeview) für die Ergebnisse erstellen
        columns = ('ID', 'Kennzahl', 'Betrag', 'Datum')
        self.tree = ttk.Treeview(results_frame, columns=columns, show='headings', height=12)

        # Spaltenüberschriften und -breiten definieren
        column_widths = {'ID': 100, 'Kennzahl': 200, 'Betrag': 150, 'Datum': 120}
        for col in columns:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=column_widths.get(col, 150))

        # Scrollbar hinzufügen
        scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        # Elemente im Grid platzieren
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))

        # ========== LAYOUT KONFIGURATION ==========
        # Spalten und Zeilen so konfigurieren, dass sie sich mit dem Fenster skalieren
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)
        results_frame.columnconfigure(0, weight=1)

    def load_file(self):
        """
        Lädt eine CSV-Datei und parst die Daten.

        Die Funktion:
        1. Öffnet einen Dateidialog zur CSV-Auswahl
        2. Liest die Datei mit dem korrekten Format (Semikolon-getrennt)
        3. Konvertiert europäische Zahlen- und Datumsformate
        4. Speichert die Daten in self.data
        """
        # Dateidialog öffnen
        filename = filedialog.askopenfilename(
            title="CSV-Datei auswählen",
            filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")]
        )

        if filename:
            try:
                # Liste für die geladenen Daten leeren
                self.data = []

                # CSV-Datei öffnen und lesen
                with open(filename, 'r', encoding='utf-8') as file:
                    # CSV-Reader mit Semikolon als Trennzeichen erstellen
                    reader = csv.DictReader(file, delimiter=';')

                    # Jede Zeile verarbeiten
                    for row in reader:
                        # Daten parsen und in einheitliches Format bringen
                        parsed_row = {
                            'id': row['id'].strip(),  # Leerzeichen entfernen
                            'key figure': row['key figure'].strip(),
                            'value': self.parse_value(row['value']),  # Europäisches Format konvertieren
                            'date': self.parse_date(row['date'])  # Deutsches Datumsformat konvertieren
                        }
                        self.data.append(parsed_row)

                # UI aktualisieren
                self.file_label.config(text=os.path.basename(filename))
                self.filtered_data = self.data.copy()  # Anfangs sind alle Daten "gefiltert"
                self.update_available_count()

                # Erfolgsmeldung
                messagebox.showinfo(
                    "Erfolg",
                    f"{len(self.data)} Datensätze erfolgreich geladen!"
                )

            except Exception as e:
                # Fehlermeldung bei Problemen
                messagebox.showerror(
                    "Fehler",
                    f"Datei konnte nicht geladen werden:\n{str(e)}"
                )

    def parse_value(self, value_str):
        """
        Konvertiert europäisches Zahlenformat in Python float.

        Args:
            value_str: String im Format "123.456,78" oder "-123.456,78"

        Returns:
            float: Die konvertierte Zahl

        Beispiel:
            "600.000,00" -> 600000.0
            "-45.750,25" -> -45750.25
        """
        try:
            # Tausendertrennzeichen (Punkt) entfernen
            value_str = value_str.replace('.', '')
            # Dezimalkomma durch Punkt ersetzen
            value_str = value_str.replace(',', '.')
            return float(value_str)
        except:
            # Bei Fehler 0.0 zurückgeben
            return 0.0

    def parse_date(self, date_str):
        """
        Konvertiert deutsches Datumsformat in datetime-Objekt.

        Args:
            date_str: String im Format "TT-MM-JJJJ"

        Returns:
            datetime: Das konvertierte Datum oder None bei Fehler

        Beispiel:
            "31-12-2024" -> datetime(2024, 12, 31)
        """
        try:
            return datetime.strptime(date_str.strip(), '%d-%m-%Y')
        except:
            return None

    def apply_filters(self):
        """
        Wendet die eingestellten Filter auf die Daten an.

        Filter:
        1. Datumsfilter: Nur Einträge vor einem bestimmten Datum
        2. Wertfilter: Nur Einträge innerhalb eines Wertebereichs

        Die gefilterten Daten werden in self.filtered_data gespeichert.
        """
        # Prüfen ob Daten geladen wurden
        if not self.data:
            messagebox.showwarning("Warnung", "Bitte laden Sie zuerst eine Datei!")
            return

        # Mit allen Daten beginnen
        self.filtered_data = self.data.copy()

        # ========== DATUMSFILTER ANWENDEN ==========
        date_filter_str = self.date_filter.get().strip()
        if date_filter_str:
            try:
                # Datum parsen
                date_limit = datetime.strptime(date_filter_str, '%d-%m-%Y')

                # Nur Einträge behalten, die vor oder am Datum liegen
                self.filtered_data = [
                    row for row in self.filtered_data
                    if row['date'] and row['date'] <= date_limit
                ]
            except ValueError:
                messagebox.showerror(
                    "Fehler",
                    "Ungültiges Datumsformat!\nBitte verwenden Sie TT-MM-JJJJ\nBeispiel: 31-12-2024"
                )
                return

        # ========== WERTFILTER ANWENDEN ==========
        try:
            # Mindestwert-Filter
            if self.min_value.get().strip():
                min_val = float(self.min_value.get().replace(',', '.'))
                self.filtered_data = [
                    row for row in self.filtered_data
                    if row['value'] >= min_val
                ]

            # Höchstwert-Filter
            if self.max_value.get().strip():
                max_val = float(self.max_value.get().replace(',', '.'))
                self.filtered_data = [
                    row for row in self.filtered_data
                    if row['value'] <= max_val
                ]
        except ValueError:
            messagebox.showerror(
                "Fehler",
                "Ungültiges Zahlenformat!\nBitte verwenden Sie Zahlen wie: 1000 oder 1000,50"
            )
            return

        # UI aktualisieren und Erfolgsmeldung
        self.update_available_count()
        messagebox.showinfo(
            "Erfolg",
            f"Filter angewendet!\n{len(self.filtered_data)} Datensätze entsprechen den Kriterien."
        )

    def update_available_count(self):
        """
        Aktualisiert die Anzeige der verfügbaren Datensätze.

        Diese Funktion wird nach dem Laden der Daten und nach
        jeder Filteranwendung aufgerufen.
        """
        count = len(self.filtered_data)
        self.available_label.config(text=str(count))

        # Farbe ändern basierend auf der Anzahl
        if count == 0:
            self.available_label.config(foreground="red")
        elif count < 10:
            self.available_label.config(foreground="orange")
        else:
            self.available_label.config(foreground="green")

    def generate_sample(self):
        """
        Generiert eine zufällige Stichprobe aus den gefilterten Daten.

        Die Funktion:
        1. Prüft ob Daten verfügbar sind
        2. Ermittelt die gewünschte Stichprobengröße
        3. Wählt zufällig Datensätze aus
        4. Zeigt die Ergebnisse in der Tabelle an
        """
        # Prüfen ob gefilterte Daten vorhanden sind
        if not self.filtered_data:
            messagebox.showwarning(
                "Warnung",
                "Keine Daten für die Stichprobe verfügbar!\nBitte laden Sie eine Datei oder ändern Sie die Filter."
            )
            return

        try:
            # Gewünschte Stichprobengröße ermitteln
            sample_size = int(self.sample_count.get())

            # Prüfen ob genügend Daten vorhanden sind
            if sample_size > len(self.filtered_data):
                response = messagebox.askyesno(
                    "Achtung",
                    f"Die gewünschte Stichprobengröße ({sample_size}) ist größer als die "
                    f"verfügbaren Datensätze ({len(self.filtered_data)}).\n\n"
                    f"Möchten Sie alle verfügbaren Datensätze verwenden?"
                )
                if response:
                    sample_size = len(self.filtered_data)
                else:
                    return

            # ========== ZUFÄLLIGE STICHPROBE GENERIEREN ==========
            # random.sample() wählt zufällig ohne Wiederholung
            sample = random.sample(self.filtered_data, sample_size)

            # ========== ERGEBNISSE IN TABELLE ANZEIGEN ==========
            # Alte Ergebnisse löschen
            for item in self.tree.get_children():
                self.tree.delete(item)

            # Neue Ergebnisse einfügen
            for i, row in enumerate(sample):
                # Betrag im europäischen Format formatieren
                formatted_value = f"{row['value']:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')

                # Datum formatieren
                date_str = row['date'].strftime('%d-%m-%Y') if row['date'] else ''

                # Zeile in Tabelle einfügen
                # Tags für die Formatierung (gerade/ungerade Zeilen)
                tag = 'even' if i % 2 == 0 else 'odd'
                self.tree.insert('', tk.END, values=(
                    row['id'],
                    row['key figure'],
                    f"€ {formatted_value}",
                    date_str
                ), tags=(tag,))

            # Zeilen-Formatierung für bessere Lesbarkeit
            self.tree.tag_configure('even', background='#f0f0f0')
            self.tree.tag_configure('odd', background='white')

            # Erfolgsmeldung
            messagebox.showinfo(
                "Erfolg",
                f"{sample_size} zufällige Stichproben wurden generiert!"
            )

        except ValueError:
            messagebox.showerror(
                "Fehler",
                "Ungültige Stichprobengröße!\nBitte geben Sie eine ganze Zahl ein."
            )


def main():
    """
    Hauptfunktion zum Starten der Anwendung.

    Diese Funktion:
    1. Erstellt das Hauptfenster
    2. Initialisiert die Anwendung
    3. Startet die Tkinter-Ereignisschleife
    """
    # Tkinter Hauptfenster erstellen
    root = tk.Tk()

    # Stil konfigurieren für besseres Aussehen
    style = ttk.Style()
    style.theme_use('clam')  # Modernes Theme verwenden

    # Anwendung initialisieren
    app = StichprobentestApp(root)

    # Ereignisschleife starten (wartet auf Benutzerinteraktionen)
    root.mainloop()


if __name__ == "__main__":
    # Programm nur ausführen, wenn es direkt gestartet wird
    # (nicht wenn es als Modul importiert wird)
    main()
