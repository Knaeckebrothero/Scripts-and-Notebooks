"""
Housekeeping HACCP page.
Room cleaning documentation and deep cleaning records.
"""
import json

import pandas as pd
import streamlit as st

from db import (
    HACCPDatabase,
    Housekeeping,
    BasicCleaning,
    HousekeepingRepo,
    BasicCleaningRepo,
)


def render_housekeeping_page(db: HACCPDatabase):
    """Render the complete housekeeping HACCP page."""
    # Standard housekeeping form
    _render_housekeeping_form(db)

    st.divider()

    # Deep cleaning section
    _render_basic_cleaning_section(db)

    st.divider()

    # Display all records
    _render_housekeeping_history(db)


def _render_housekeeping_form(db: HACCPDatabase):
    """Standard housekeeping documentation form."""
    st.subheader("Reinigungs-Dokumentationsliste")

    with st.form("hk_form"):
        datum = st.date_input("Datum", key="hk_date")
        raum = st.text_input("Raumnummer / Name", key="hk_room")
        erledigt = st.multiselect(
            "Durchgeführte Reinigungsaufgaben",
            options=[
                "Bettwäsche gewechselt",
                "Boden gesaugt",
                "Badezimmer desinfiziert",
                "Möbel abgewischt",
                "Mülleimer geleert",
            ],
            key="hk_tasks",
        )

        submitted = st.form_submit_button("Eintrag speichern")

        if submitted:
            if raum:
                repo = HousekeepingRepo(db)
                entry = Housekeeping(
                    datum=datum.isoformat(),
                    raum=raum,
                    aufgaben=erledigt,
                )
                repo.insert(entry)
                st.success("Housekeeping-Eintrag gespeichert.")
            else:
                st.error("Bitte Raumnummer angeben.")


def _render_basic_cleaning_section(db: HACCPDatabase):
    """Deep cleaning / basic cleaning form."""
    st.subheader("Grundreinigung")

    with st.expander("Zusätzliche Angaben"):
        grund_datum = st.date_input("Datum der Grundreinigung", key="basic_date")
        abreise = st.text_input("Abreise (falls Gäste)", key="basic_abreise")
        bleibe = st.text_input("Bleibe (Anzahl Nächte)", key="basic_bleibe")
        notes = st.text_area("Bemerkungen", key="basic_notes")

        if st.button("Speichern Grundreinigung"):
            repo = BasicCleaningRepo(db)
            entry = BasicCleaning(
                datum=grund_datum.isoformat(),
                abreise=abreise or None,
                bleibe=bleibe or None,
                notes=notes or None,
            )
            repo.insert(entry)
            st.success("Grundreinigungs-Eintrag gespeichert.")


def _render_housekeeping_history(db: HACCPDatabase):
    """Display all housekeeping records."""
    st.subheader("Alle Housekeeping-Einträge")

    repo = HousekeepingRepo(db)
    df = repo.get_dataframe(
        "SELECT datum, raum, aufgaben FROM housekeeping ORDER BY datum DESC LIMIT 50"
    )

    if not df.empty:
        df["datum"] = pd.to_datetime(df["datum"]).dt.date
        df["aufgaben"] = df["aufgaben"].apply(
            lambda x: ", ".join(json.loads(x)) if x else "-"
        )
        st.dataframe(df, width="stretch")
    else:
        st.info("Keine Housekeeping-Einträge vorhanden.")

    # Basic cleaning overview
    st.subheader("Grundreinigung-Übersicht")

    basic_repo = BasicCleaningRepo(db)
    df_basic = basic_repo.get_dataframe(
        "SELECT datum, abreise, bleibe, notes FROM housekeeping_basic_cleaning ORDER BY datum DESC LIMIT 20"
    )

    if not df_basic.empty:
        df_basic["datum"] = pd.to_datetime(df_basic["datum"]).dt.date
        st.table(df_basic)
    else:
        st.info("Noch keine Grundreinigung-Einträge.")
