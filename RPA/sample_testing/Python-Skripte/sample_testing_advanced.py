"""
Finanzdaten Stichprobentest - Erweiterte Version mit Streamlit
==============================================================
Diese moderne Webanwendung ermöglicht es Wirtschaftsprüfern, zufällige
Stichproben aus Finanzdaten zu ziehen. Die Anwendung nutzt Streamlit für
eine intuitive Benutzeroberfläche und Pandas für effiziente Datenverarbeitung.

Features:
- Unterstützung für CSV und Excel-Dateien
- Erweiterte Filteroptionen
- Datenvisualisierung
- Export in verschiedene Formate
"""
import io  # Input/Output-Operationen für Datei-Export
import streamlit as st  # Web-Framework für die Benutzeroberfläche
import pandas as pd  # Datenverarbeitung und -analyse
from datetime import datetime  # Datum- und Zeitfunktionen


# ========== SEITEN-KONFIGURATION ==========
# Grundlegende Einstellungen für die Streamlit-Seite
st.set_page_config(
    page_title="Finanzdaten Stichprobentest",  # Browser-Tab Titel
    page_icon="📊",  # Icon im Browser-Tab
    layout="wide"  # Breites Layout für bessere Platznutzung
)

# ========== HAUPTTITEL ==========
st.title("🏦 Finanzdaten Stichprobentest - Erweiterte Version")
st.markdown("---")  # Horizontale Linie zur visuellen Trennung

# ========== SESSION STATE INITIALISIERUNG ==========
# Session State speichert Daten zwischen Interaktionen
# (Streamlit lädt die Seite bei jeder Interaktion neu)
if 'data' not in st.session_state:
    st.session_state.data = None  # Originaldaten
if 'filtered_data' not in st.session_state:
    st.session_state.filtered_data = None  # Gefilterte Daten
if 'sample' not in st.session_state:
    st.session_state.sample = None  # Generierte Stichprobe

# ========== SEITENLEISTE FÜR DATEI-UPLOAD ==========
with st.sidebar:
    st.header("📁 Datenimport")

    # Datei-Upload Widget
    uploaded_file = st.file_uploader(
        "Datei auswählen",
        type=['csv', 'xlsx', 'xls'],  # Erlaubte Dateitypen
        help="Laden Sie eine CSV- oder Excel-Datei mit Finanzdaten hoch"
    )

    # Dateiverarbeitung wenn eine Datei hochgeladen wurde
    if uploaded_file is not None:
        try:
            # Je nach Dateityp unterschiedliche Verarbeitung
            if uploaded_file.name.endswith('.csv'):
                # CSV-Datei mit europäischem Format lesen
                st.session_state.data = pd.read_csv(
                    uploaded_file,
                    sep=';',  # Semikolon als Trennzeichen
                    decimal=',',  # Komma als Dezimaltrennzeichen
                    parse_dates=['date'],  # Datumsspalte automatisch parsen
                    dayfirst=True  # Tag-Monat-Jahr Format
                )
            else:
                # Excel-Datei lesen
                st.session_state.data = pd.read_excel(
                    uploaded_file,
                    parse_dates=['date']  # Datumsspalte automatisch parsen
                )
                # Werte in numerisches Format konvertieren falls nötig
                if 'value' in st.session_state.data.columns:
                    # Komma durch Punkt ersetzen und in Zahl konvertieren
                    st.session_state.data['value'] = pd.to_numeric(
                        st.session_state.data['value'].astype(str).str.replace(',', '.'),
                        errors='coerce'  # Fehlerhafte Werte werden zu NaN
                    )

            # Erfolgsmeldung
            st.success(f"✅ {len(st.session_state.data)} Datensätze geladen")
            # Anfangs sind alle Daten auch die gefilterten Daten
            st.session_state.filtered_data = st.session_state.data.copy()

        except Exception as e:
            # Fehlermeldung bei Problemen
            st.error(f"❌ Fehler beim Laden der Datei: {str(e)}")

    # ========== DATENÜBERSICHT IN DER SEITENLEISTE ==========
    if st.session_state.data is not None:
        st.markdown("---")
        st.subheader("📊 Datenübersicht")

        # Grundlegende Statistiken anzeigen
        st.write(f"**Gesamtanzahl Datensätze:** {len(st.session_state.data)}")

        # Datumsbereich
        date_min = st.session_state.data['date'].min().strftime('%d-%m-%Y')
        date_max = st.session_state.data['date'].max().strftime('%d-%m-%Y')
        st.write(f"**Zeitraum:** {date_min} bis {date_max}")

        # Soll- und Haben-Summen berechnen
        # DT = Debit (Soll), CT = Credit (Haben)
        soll_daten = st.session_state.data[st.session_state.data['key figure'].str.endswith('DT')]
        haben_daten = st.session_state.data[st.session_state.data['key figure'].str.endswith('CT')]

        gesamt_soll = soll_daten['value'].sum()
        gesamt_haben = abs(haben_daten['value'].sum())  # Haben ist normalerweise negativ

        st.write(f"**Gesamt Soll:** €{gesamt_soll:,.2f}")
        st.write(f"**Gesamt Haben:** €{gesamt_haben:,.2f}")

        # Bilanz anzeigen (sollte idealerweise 0 sein)
        bilanz = gesamt_soll - gesamt_haben
        if abs(bilanz) < 0.01:  # Kleine Rundungsdifferenzen erlauben
            st.write(f"**Bilanz:** ✅ €{bilanz:,.2f}")
        else:
            st.write(f"**Bilanz:** ⚠️ €{bilanz:,.2f}")

# ========== HAUPTBEREICH ==========
if st.session_state.data is not None:
    # Tabs für verschiedene Funktionsbereiche erstellen
    tab1, tab2, tab3 = st.tabs(["🔍 Filter & Stichprobe", "📋 Ergebnisse", "📊 Analyse"])

    # ========== TAB 1: FILTER UND STICHPROBENAUSWAHL ==========
    with tab1:
        col1, col2 = st.columns(2)  # Zwei Spalten für bessere Platznutzung

        # ========== LINKE SPALTE: FILTEROPTIONEN ==========
        with col1:
            st.subheader("🔍 Filteroptionen")

            # ========== DATUMSFILTER ==========
            st.write("**Datumsfilter**")
            date_filter_option = st.radio(
                "Nach Datum filtern?",
                ["Kein Filter", "Vor bestimmtem Datum", "Datumsbereich"],
                horizontal=True  # Optionen horizontal anordnen
            )

            # Je nach Auswahl unterschiedliche Eingabefelder anzeigen
            if date_filter_option == "Vor bestimmtem Datum":
                max_date = st.date_input(
                    "Maximales Datum auswählen",
                    value=st.session_state.data['date'].max(),
                    max_value=st.session_state.data['date'].max(),
                    min_value=st.session_state.data['date'].min(),
                    format="DD/MM/YYYY"  # Deutsches Datumsformat
                )
            elif date_filter_option == "Datumsbereich":
                date_range = st.date_input(
                    "Datumsbereich auswählen",
                    value=(st.session_state.data['date'].min(),
                           st.session_state.data['date'].max()),
                    max_value=st.session_state.data['date'].max(),
                    min_value=st.session_state.data['date'].min(),
                    format="DD/MM/YYYY"
                )

            # ========== WERTFILTER ==========
            st.write("**Wertfilter**")
            value_filter = st.checkbox("Nach Wertbereich filtern")
            if value_filter:
                # Slider für Min/Max Werte
                min_val, max_val = st.slider(
                    "Wertbereich auswählen",
                    min_value=float(st.session_state.data['value'].min()),
                    max_value=float(st.session_state.data['value'].max()),
                    value=(float(st.session_state.data['value'].min()),
                           float(st.session_state.data['value'].max())),
                    format="€%.2f",  # Euro-Format
                    help="Ziehen Sie die Regler, um den Wertebereich einzugrenzen"
                )

            # ========== KONTOFILTER ==========
            st.write("**Kontofilter**")
            account_filter = st.checkbox("Nach Kontotyp filtern")
            if account_filter:
                account_types = st.multiselect(
                    "Kontotypen auswählen",
                    ["Soll (DT)", "Haben (CT)"],
                    default=["Soll (DT)", "Haben (CT)"],  # Standardmäßig beide ausgewählt
                    help="DT = Debit/Soll, CT = Credit/Haben"
                )

            # ========== FILTER ANWENDEN BUTTON ==========
            if st.button("🔄 Filter anwenden", type="primary", use_container_width=True):
                # Mit Kopie der Originaldaten beginnen
                filtered = st.session_state.data.copy()

                # Datumsfilter anwenden
                if date_filter_option == "Vor bestimmtem Datum":
                    filtered = filtered[filtered['date'] <= pd.Timestamp(max_date)]
                elif date_filter_option == "Datumsbereich":
                    filtered = filtered[
                        (filtered['date'] >= pd.Timestamp(date_range[0])) &
                        (filtered['date'] <= pd.Timestamp(date_range[1]))
                        ]

                # Wertfilter anwenden
                if value_filter:
                    filtered = filtered[
                        (filtered['value'] >= min_val) &
                        (filtered['value'] <= max_val)
                        ]

                # Kontofilter anwenden
                if account_filter:
                    # Liste für die Bedingungen erstellen
                    account_conditions = []
                    if "Soll (DT)" in account_types:
                        account_conditions.append(filtered['key figure'].str.endswith('DT'))
                    if "Haben (CT)" in account_types:
                        account_conditions.append(filtered['key figure'].str.endswith('CT'))

                    # Bedingungen mit ODER verknüpfen
                    if account_conditions:
                        filtered = filtered[pd.concat(account_conditions, axis=1).any(axis=1)]

                # Gefilterte Daten speichern
                st.session_state.filtered_data = filtered
                st.success(f"✅ Filter angewendet! {len(filtered)} Datensätze entsprechen den Kriterien")

        # ========== RECHTE SPALTE: STICHPROBENAUSWAHL ==========
        with col2:
            st.subheader("🎲 Stichprobenauswahl")

            if st.session_state.filtered_data is not None:
                # Anzahl verfügbarer Datensätze anzeigen
                st.info(f"📊 Verfügbare Datensätze für Stichprobe: **{len(st.session_state.filtered_data)}**")

                # ========== STICHPROBENGRÖSSE FESTLEGEN ==========
                sample_method = st.radio(
                    "Stichprobengröße festlegen",
                    ["Feste Anzahl", "Prozentsatz"],
                    horizontal=True
                )

                if sample_method == "Feste Anzahl":
                    # Direkte Eingabe der Anzahl
                    sample_size = st.number_input(
                        "Anzahl der Stichproben",
                        min_value=1,
                        max_value=len(st.session_state.filtered_data),
                        value=min(5, len(st.session_state.filtered_data)),  # Standard: 5 oder Maximum
                        step=1,
                        help="Geben Sie die gewünschte Anzahl an Stichproben ein"
                    )
                else:
                    # Prozentsatz-basierte Auswahl
                    sample_percent = st.slider(
                        "Prozentsatz der Stichproben",
                        min_value=1,
                        max_value=100,
                        value=10,  # Standard: 10%
                        step=1,
                        format="%d%%"
                    )
                    # Berechnung der tatsächlichen Anzahl
                    sample_size = max(1, int(len(st.session_state.filtered_data) * sample_percent / 100))
                    st.write(f"Dies entspricht **{sample_size}** Datensätzen")

                # ========== REPRODUZIERBARKEIT ==========
                st.write("**Reproduzierbarkeit**")
                use_seed = st.checkbox(
                    "Festen Zufallswert verwenden",
                    help="Aktivieren Sie diese Option, um bei jedem Durchlauf die gleichen Stichproben zu erhalten"
                )
                if use_seed:
                    seed = st.number_input(
                        "Zufallswert (Seed)",
                        value=42,
                        step=1,
                        help="Gleicher Wert = gleiche Stichproben"
                    )
                else:
                    seed = None

                # ========== STICHPROBE GENERIEREN ==========
                st.markdown("---")
                if st.button("🎯 Stichprobe generieren", type="primary", use_container_width=True):
                    # Stichprobe mit oder ohne festen Seed generieren
                    if seed is not None:
                        sample = st.session_state.filtered_data.sample(n=sample_size, random_state=seed)
                    else:
                        sample = st.session_state.filtered_data.sample(n=sample_size)

                    # Stichprobe speichern
                    st.session_state.sample = sample
                    st.success(f"✅ {len(sample)} zufällige Stichproben generiert!")
                    st.balloons()  # Visuelle Bestätigung

    # ========== TAB 2: ERGEBNISSE ANZEIGEN ==========
    with tab2:
        st.subheader("📋 Stichprobenergebnisse")

        if 'sample' in st.session_state and st.session_state.sample is not None:
            # ========== ANZEIGEOPTIONEN ==========
            col1, col2, col3 = st.columns(3)
            with col1:
                show_index = st.checkbox("Index anzeigen", value=False)
            with col2:
                highlight_negatives = st.checkbox("Negative Werte hervorheben", value=True)
            with col3:
                export_format = st.selectbox("Exportformat", ["CSV", "Excel", "JSON"])

            # ========== DATEN FÜR ANZEIGE FORMATIEREN ==========
            display_df = st.session_state.sample.copy()
            # Währungsformat mit Euro-Symbol
            display_df['value'] = display_df['value'].apply(lambda x: f"€{x:,.2f}")
            # Datumsformat deutsch
            display_df['date'] = display_df['date'].dt.strftime('%d.%m.%Y')

            # Spaltennamen auf Deutsch übersetzen für die Anzeige
            display_df = display_df.rename(columns={
                'id': 'ID',
                'key figure': 'Kennzahl',
                'value': 'Betrag',
                'date': 'Datum'
            })

            # ========== FORMATIERUNG FÜR NEGATIVE WERTE ==========
            def highlight_negative(val):
                """Färbt negative Werte rot ein"""
                if isinstance(val, str) and val.startswith('€-'):
                    return 'color: red; font-weight: bold'
                return ''

            # Tabelle mit oder ohne Formatierung anzeigen
            if highlight_negatives:
                styled_df = display_df.style.applymap(
                    highlight_negative,
                    subset=['Betrag']
                ).set_properties(**{
                    'text-align': 'right'
                }, subset=['Betrag'])
                st.dataframe(styled_df, use_container_width=True, hide_index=not show_index)
            else:
                st.dataframe(display_df, use_container_width=True, hide_index=not show_index)

            # ========== EXPORT-FUNKTIONALITÄT ==========
            st.markdown("---")
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write("**Stichprobenergebnisse exportieren**")
            with col2:
                # Zeitstempel für Dateinamen
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

                if export_format == "CSV":
                    # CSV mit deutschem Format erstellen
                    csv = st.session_state.sample.to_csv(
                        index=False,
                        sep=';',  # Semikolon als Trennzeichen
                        decimal=','  # Komma als Dezimaltrennzeichen
                    )
                    st.download_button(
                        label="📥 CSV herunterladen",
                        data=csv,
                        file_name=f"stichprobe_{timestamp}.csv",
                        mime="text/csv"
                    )

                elif export_format == "Excel":
                    # Excel-Datei im Speicher erstellen
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                        # Daten in Excel schreiben
                        st.session_state.sample.to_excel(
                            writer,
                            index=False,
                            sheet_name='Stichprobe'
                        )
                        # Formatierung hinzufügen
                        worksheet = writer.sheets['Stichprobe']
                        # Spaltenbreiten anpassen
                        worksheet.column_dimensions['A'].width = 15  # ID
                        worksheet.column_dimensions['B'].width = 25  # Kennzahl
                        worksheet.column_dimensions['C'].width = 20  # Betrag
                        worksheet.column_dimensions['D'].width = 15  # Datum

                    buffer.seek(0)
                    st.download_button(
                        label="📥 Excel herunterladen",
                        data=buffer,
                        file_name=f"stichprobe_{timestamp}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                elif export_format == "JSON":
                    # JSON mit schöner Formatierung
                    json_str = st.session_state.sample.to_json(
                        orient='records',  # Jeden Datensatz als Objekt
                        date_format='iso',  # ISO-Datumsformat
                        indent=2  # Einrückung für Lesbarkeit
                    )
                    st.download_button(
                        label="📥 JSON herunterladen",
                        data=json_str,
                        file_name=f"stichprobe_{timestamp}.json",
                        mime="application/json"
                    )
        else:
            # Hinweis wenn noch keine Stichprobe generiert wurde
            st.info("👆 Bitte generieren Sie zuerst eine Stichprobe im Tab 'Filter & Stichprobe'")

    # ========== TAB 3: STATISTISCHE ANALYSE ==========
    with tab3:
        st.subheader("📊 Stichprobenanalyse")

        if 'sample' in st.session_state and st.session_state.sample is not None:
            # ========== GRUNDLEGENDE STATISTIKEN ==========
            col1, col2 = st.columns(2)

            with col1:
                st.write("**Statistische Kennzahlen**")

                # Stichprobengröße und statistische Werte
                st.metric("Stichprobengröße", len(st.session_state.sample))
                st.metric("Durchschnittswert", f"€{st.session_state.sample['value'].mean():,.2f}")
                st.metric("Median", f"€{st.session_state.sample['value'].median():,.2f}")
                st.metric("Standardabweichung", f"€{st.session_state.sample['value'].std():,.2f}")

                # Min/Max Werte
                st.write("**Extremwerte**")
                st.write(f"Minimum: €{st.session_state.sample['value'].min():,.2f}")
                st.write(f"Maximum: €{st.session_state.sample['value'].max():,.2f}")

            with col2:
                st.write("**Kontoarten-Verteilung**")

                # Soll/Haben Aufteilung berechnen
                dt_count = len(st.session_state.sample[
                                   st.session_state.sample['key figure'].str.endswith('DT')
                               ])
                ct_count = len(st.session_state.sample[
                                   st.session_state.sample['key figure'].str.endswith('CT')
                               ])

                # Kreisdiagramm-Daten
                pie_data = pd.DataFrame({
                    'Typ': ['Soll (DT)', 'Haben (CT)'],
                    'Anzahl': [dt_count, ct_count]
                })

                # Metriken anzeigen
                col2_1, col2_2 = st.columns(2)
                with col2_1:
                    st.metric("Soll-Buchungen", dt_count)
                with col2_2:
                    st.metric("Haben-Buchungen", ct_count)

                # Balkendiagramm
                st.bar_chart(pie_data.set_index('Typ'))

            # ========== WERTVERTEILUNG ==========
            st.write("**Wertverteilung der Stichprobe**")

            # Histogramm erstellen
            # Werte in Bereiche einteilen
            bins = pd.cut(st.session_state.sample['value'], bins=5)
            bin_counts = bins.value_counts().sort_index()

            # Daten für Diagramm vorbereiten
            chart_data = pd.DataFrame({
                'Wertebereich': [f"€{interval.left:,.0f} - €{interval.right:,.0f}"
                                 for interval in bin_counts.index],
                'Anzahl': bin_counts.values
            })

            # Balkendiagramm anzeigen
            st.bar_chart(chart_data.set_index('Wertebereich'))

            # ========== ZEITLICHE VERTEILUNG ==========
            st.write("**Zeitliche Verteilung**")

            # Nach Monat gruppieren
            monthly = st.session_state.sample.groupby(
                st.session_state.sample['date'].dt.to_period('M')
            ).size()

            # Daten für Diagramm vorbereiten
            monthly_df = pd.DataFrame({
                'Monat': [f"{period.month:02d}/{period.year}" for period in monthly.index],
                'Anzahl': monthly.values
            })

            # Balkendiagramm anzeigen
            st.bar_chart(monthly_df.set_index('Monat'))

            # ========== ZUSAMMENFASSUNG ==========
            with st.expander("📝 Zusammenfassung der Analyse"):
                st.write(f"""
                **Stichprobenübersicht:**
                - Gesamtanzahl: {len(st.session_state.sample)} Datensätze
                - Zeitraum: {st.session_state.sample['date'].min().strftime('%d.%m.%Y')} 
                  bis {st.session_state.sample['date'].max().strftime('%d.%m.%Y')}
                - Durchschnittlicher Betrag: €{st.session_state.sample['value'].mean():,.2f}
                - Gesamtsumme: €{st.session_state.sample['value'].sum():,.2f}
                
                **Verteilung:**
                - {dt_count} Soll-Buchungen ({dt_count/len(st.session_state.sample)*100:.1f}%)
                - {ct_count} Haben-Buchungen ({ct_count/len(st.session_state.sample)*100:.1f}%)
                
                Diese Stichprobe wurde am {datetime.now().strftime('%d.%m.%Y um %H:%M:%S')} generiert.
                """)

        else:
            # Hinweis wenn noch keine Stichprobe vorhanden
            st.info("👆 Bitte generieren Sie zuerst eine Stichprobe im Tab 'Filter & Stichprobe'")

# ========== STARTSEITE (WENN KEINE DATEN GELADEN) ==========
else:
    # Willkommensnachricht
    st.info("👈 Bitte laden Sie eine CSV- oder Excel-Datei über die Seitenleiste hoch")

    # ========== ANLEITUNG ==========
    with st.expander("📖 Anleitung zur Verwendung"):
        st.markdown("""
        ### So verwenden Sie diese Anwendung:
        
        1. **Datei hochladen** 
           - Klicken Sie in der Seitenleiste auf "Datei auswählen"
           - Wählen Sie eine CSV- oder Excel-Datei mit Ihren Finanzdaten
        
        2. **Filter anwenden** 
           - Nutzen Sie die Filteroptionen um die Daten einzugrenzen
           - Sie können nach Datum, Betrag und Kontotyp filtern
        
        3. **Stichprobe generieren** 
           - Legen Sie die Anzahl oder den Prozentsatz fest
           - Klicken Sie auf "Stichprobe generieren"
        
        4. **Ergebnisse exportieren** 
           - Wählen Sie das gewünschte Format (CSV, Excel, JSON)
           - Laden Sie die Stichprobe herunter
        
        5. **Analyse durchführen** 
           - Nutzen Sie die eingebauten Statistiken und Visualisierungen
           - Exportieren Sie die Zusammenfassung für Ihre Dokumentation
        
        ### Dateiformatanforderungen:
        
        **CSV-Dateien:**
        - Trennzeichen: Semikolon (;)
        - Dezimaltrennzeichen: Komma (,)
        - Datumsformat: TT-MM-JJJJ
        - Pflichtfelder: id, key figure, value, date
        
        **Excel-Dateien:**
        - Erste Zeile muss Spaltenüberschriften enthalten
        - Gleiche Pflichtfelder wie bei CSV
        
        ### Tipps:
        - Verwenden Sie den "Festen Zufallswert" für reproduzierbare Ergebnisse
        - Exportieren Sie die Stichprobe für Ihre Arbeitspapiere
        - Die Analyse-Tab bietet zusätzliche Einblicke in Ihre Stichprobe
        """)

    # ========== BEISPIELDATEN ANZEIGEN ==========
    st.subheader("📝 Erwartetes Datenformat")
    st.write("Ihre Datei sollte folgende Struktur haben:")

    # Beispieldaten erstellen
    sample_data = pd.DataFrame({
        'id': ['436436', '436437', '436438', '436439', '436440'],
        'key figure': ['100.515.100.00.DT', '100.515.100.00.CT', '200.120.050.00.DT',
                       '200.120.050.00.CT', '300.225.075.00.DT'],
        'value': ['600.000,00', '-450.000,00', '125.750,50', '-125.750,50', '89.999,99'],
        'date': ['31-12-2024', '31-12-2024', '31-12-2024', '31-12-2024', '30-11-2024']
    })

    # Tabelle anzeigen
    st.dataframe(sample_data, use_container_width=True, hide_index=True)

    # ========== ZUSÄTZLICHE INFORMATIONEN ==========
    with st.expander("ℹ️ Über diese Anwendung"):
        st.markdown("""
        Diese Anwendung wurde speziell für die Anforderungen der Wirtschaftsprüfung entwickelt.
        Sie ermöglicht eine effiziente und nachvollziehbare Stichprobenauswahl aus Finanzdaten.
        
        **Vorteile gegenüber manueller Auswahl:**
        - 🎯 Zufällige, unvoreingenommene Auswahl
        - ⚡ Schnelle Verarbeitung großer Datenmengen
        - 📊 Integrierte Analyse und Visualisierung
        - 📁 Verschiedene Exportformate
        - 🔄 Reproduzierbare Ergebnisse
        
        **Sicherheit:**
        - Alle Daten werden nur lokal in Ihrem Browser verarbeitet
        - Keine Daten werden an externe Server gesendet
        - Die Anwendung läuft vollständig auf Ihrem Computer
        
        **Version:** 2.0 | **Erstellt für:** Abteilung Datenmanagement
        """)

# ========== FOOTER ==========
st.markdown("---")
st.markdown(
    "<div style='text-align: center; color: gray; font-size: 12px;'>"
    "Finanzdaten Stichprobentest | Entwickelt für die Wirtschaftsprüfung | "
    f"© {datetime.now().year}"
    "</div>",
    unsafe_allow_html=True
)
