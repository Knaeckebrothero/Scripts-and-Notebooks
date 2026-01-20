"""
Hotel general page.
Guest information and arrival control.
"""
import pandas as pd
import streamlit as st

from db import (
    HACCPDatabase,
    HotelGuest,
    ArrivalControl,
    HotelGuestRepo,
    ArrivalControlRepo,
)


def render_hotel_page(db: HACCPDatabase):
    """Render the complete hotel general page."""
    # Guest information form
    _render_guest_form(db)

    st.divider()

    # Arrival control
    _render_arrival_control(db)

    st.divider()

    # Display guest list and arrivals
    _render_guest_history(db)


def _render_guest_form(db: HACCPDatabase):
    """Guest information entry form."""
    st.subheader("Gäste-Informationen")

    with st.form("guest_form"):
        anreise = st.date_input("Anreisedatum", key="guest_arrival")
        abreise = st.date_input("Abreisedatum", key="guest_departure")
        name = st.text_input("Gastname", key="guest_name")
        hund = st.checkbox("Gast kommt mit Hund", key="guest_dog")
        besondere_wünsche = st.text_area(
            "Besondere Wünsche / Hinweise", key="guest_notes"
        )

        saved = st.form_submit_button("Eintrag speichern")

        if saved:
            if name:
                repo = HotelGuestRepo(db)
                entry = HotelGuest(
                    name=name,
                    anreise=anreise.isoformat(),
                    abreise=abreise.isoformat(),
                    hund_mit=hund,
                    notizen=besondere_wünsche or None,
                )
                repo.insert(entry)
                st.success("Gästedaten gespeichert.")
            else:
                st.error("Bitte Gastname angeben.")


def _render_arrival_control(db: HACCPDatabase):
    """Arrival check-in control form."""
    st.subheader("Anreise-Kontrolle")

    with st.form("arrival_control"):
        arrival_date = st.date_input("Datum", key="arrival_date")
        room = st.text_input("Zimmernummer", key="arrival_room")
        employee = st.text_input("Mitarbeiter*in (Aufnahme)", key="arrival_employee")

        submitted = st.form_submit_button("Kontrolle speichern")

        if submitted:
            if room:
                repo = ArrivalControlRepo(db)
                entry = ArrivalControl(
                    datum=arrival_date.isoformat(),
                    zimmer=room,
                    employee=employee or "unbekannt",
                )
                repo.insert(entry)
                st.success("Anreise-Kontrolle gespeichert.")
            else:
                st.error("Bitte Zimmernummer angeben.")


def _render_guest_history(db: HACCPDatabase):
    """Display guest list and arrival controls."""
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Gästeliste")

        repo = HotelGuestRepo(db)
        df = repo.get_dataframe(
            "SELECT name, anreise, abreise, hund_mit, notizen FROM hotel_guests ORDER BY anreise DESC LIMIT 50"
        )

        if not df.empty:
            df["anreise"] = pd.to_datetime(df["anreise"]).dt.date
            df["abreise"] = pd.to_datetime(df["abreise"]).dt.date
            df["hund_mit"] = df["hund_mit"].apply(lambda x: "Ja" if x else "Nein")
            df.columns = ["Name", "Anreise", "Abreise", "Hund", "Notizen"]
            st.dataframe(df, width="stretch")
        else:
            st.info("Keine Gästedaten vorhanden.")

    with col2:
        st.subheader("Anreise-Kontrolle-Übersicht")

        arrival_repo = ArrivalControlRepo(db)
        df_arrival = arrival_repo.get_dataframe(
            "SELECT datum, zimmer, employee FROM hotel_arrival_control ORDER BY datum DESC LIMIT 20"
        )

        if not df_arrival.empty:
            df_arrival["datum"] = pd.to_datetime(df_arrival["datum"]).dt.date
            df_arrival.columns = ["Datum", "Zimmer", "Mitarbeiter"]
            st.table(df_arrival)
        else:
            st.info("Noch keine Anreise-Kontrolle-Einträge.")

    # Current guests summary
    st.subheader("Aktuelle Gäste")

    current_guests = repo.get_current_guests()
    if current_guests:
        for guest in current_guests:
            dog_icon = " 🐕" if guest.hund_mit else ""
            st.write(f"• **{guest.name}**{dog_icon} — Abreise: {guest.abreise}")
            if guest.notizen:
                st.caption(f"  Hinweis: {guest.notizen}")
    else:
        st.info("Keine aktuellen Gäste.")

    # Today's arrivals
    st.subheader("Heutige Anreisen")

    arriving_today = repo.get_arriving_today()
    if arriving_today:
        for guest in arriving_today:
            dog_icon = " 🐕" if guest.hund_mit else ""
            st.write(f"• **{guest.name}**{dog_icon}")
    else:
        st.info("Keine Anreisen heute.")
