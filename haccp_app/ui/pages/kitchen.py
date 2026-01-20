"""
Kitchen HACCP page.
Temperature logging, goods receipt, open products, cleaning tasks.
"""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from db import (
    HACCPDatabase,
    KitchenTemperature,
    GoodsReceipt,
    OpenProduct,
    KitchenCleaning,
    KitchenTemperatureRepo,
    GoodsReceiptRepo,
    OpenProductRepo,
    KitchenCleaningRepo,
)
from services import HACCPReportGenerator
from ui.components import render_date_range_picker


def render_kitchen_page(db: HACCPDatabase, cleaning_schedule: dict):
    """Render the complete kitchen HACCP page."""
    # Temperature control section
    _render_temperature_section(db)

    st.divider()

    # Goods receipt section
    _render_goods_receipt_section(db)

    st.divider()

    # Open products section
    _render_open_products_section(db)

    st.divider()

    # Cleaning section
    _render_cleaning_section(db, cleaning_schedule)

    st.divider()

    # Reports section
    _render_reports_section(db)


def _render_temperature_section(db: HACCPDatabase):
    """Temperature logging for fridge and freezer."""
    st.subheader("Temperatur-Kontrolle")

    col1, col2 = st.columns(2)

    with col1:
        st.write("**Kühlschrank**")
        fridge_temp = st.number_input(
            "Temperatur (°C)",
            min_value=-30.0,
            max_value=30.0,
            step=0.1,
            key="fridge_temp",
        )
        fridge_employee = st.text_input("Mitarbeiter*in", key="fridge_employee")

        if st.button("Logge Kühlschrank-Temperatur"):
            repo = KitchenTemperatureRepo(db)
            entry = KitchenTemperature(
                location="fridge",
                temperature=fridge_temp,
                employee=fridge_employee or "unbekannt",
            )
            repo.insert(entry)
            st.success("Kühlschrank-Eintrag gespeichert.")

    with col2:
        st.write("**Gefrierschrank**")
        freezer_temp = st.number_input(
            "Temperatur (°C)",
            min_value=-30.0,
            max_value=0.0,
            step=0.1,
            key="freezer_temp",
        )
        freezer_employee = st.text_input("Mitarbeiter*in", key="freezer_employee")

        if st.button("Logge Gefrierschrank-Temperatur"):
            repo = KitchenTemperatureRepo(db)
            entry = KitchenTemperature(
                location="freezer",
                temperature=freezer_temp,
                employee=freezer_employee or "unbekannt",
            )
            repo.insert(entry)
            st.success("Gefrierschrank-Eintrag gespeichert.")

    # Display temperature history
    st.subheader("Temperatur-Verlauf")
    repo = KitchenTemperatureRepo(db)
    df = repo.get_dataframe(
        "SELECT timestamp, temperature, employee, location FROM kitchen_temperature ORDER BY timestamp DESC LIMIT 50"
    )

    if not df.empty:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["location"] = df["location"].map({"fridge": "Kühlschrank", "freezer": "Gefrierschrank"})
        st.dataframe(df, width="stretch")
    else:
        st.info("Noch keine Temperatur-Einträge vorhanden.")


def _render_goods_receipt_section(db: HACCPDatabase):
    """Goods receipt logging."""
    st.subheader("Warenannahme")

    with st.expander("Eintrag hinzufügen"):
        prod = st.text_input("Produktname", key="receipt_product")
        qty = st.text_input("Menge / Einheit", key="receipt_amount")
        receipt_date = st.date_input("Entgegennahmedatum", key="receipt_date")
        receipt_employee = st.text_input("Entgegengenommen von", key="receipt_employee")

        if st.button("Speichern (Warenannahme)"):
            if prod and qty:
                repo = GoodsReceiptRepo(db)
                entry = GoodsReceipt(
                    product=prod,
                    amount=qty,
                    receipt_date=receipt_date.isoformat(),
                    employee=receipt_employee or "unbekannt",
                )
                repo.insert(entry)
                st.success(f"{prod} wurde gespeichert.")
            else:
                st.error("Bitte Produkt und Menge angeben.")

    # Display history
    st.subheader("Warenannahme-Historie")
    repo = GoodsReceiptRepo(db)
    df = repo.get_dataframe(
        "SELECT product, amount, receipt_date, employee FROM kitchen_goods_receipt ORDER BY receipt_date DESC LIMIT 50"
    )

    if not df.empty:
        df["receipt_date"] = pd.to_datetime(df["receipt_date"]).dt.date
        st.table(df)
    else:
        st.info("Noch keine Warenannahme-Einträge.")


def _render_open_products_section(db: HACCPDatabase):
    """Open products with expiry tracking."""
    st.subheader("Offene Lebensmittel & Ablaufdaten")

    with st.expander("Eintrag hinzufügen"):
        name = st.text_input("Produktname", key="open_name")
        menge = st.text_input("Menge / Einheit", key="open_amount")
        ablauf = st.date_input("Ablaufdatum", key="open_expiry")

        if st.button("Speichern (offenes Produkt)"):
            if name and menge:
                repo = OpenProductRepo(db)
                entry = OpenProduct(
                    product=name,
                    amount=menge,
                    expiry_date=ablauf.isoformat(),
                )
                repo.insert(entry)
                st.success(f"{name} gespeichert.")
            else:
                st.error("Produktname und Menge erforderlich.")

    # Display with expiry highlighting
    repo = OpenProductRepo(db)
    df = repo.get_dataframe(
        "SELECT id, product, amount, expiry_date FROM kitchen_open_products ORDER BY expiry_date ASC"
    )

    if not df.empty:
        df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date
        today = date.today()

        # Color code based on expiry
        def highlight_expiry(row):
            exp = row["expiry_date"]
            if exp < today:
                return ["background-color: #ffcccc"] * len(row)  # Red - expired
            elif exp <= today + timedelta(days=1):
                return ["background-color: #ffeecc"] * len(row)  # Orange - expiring soon
            elif exp <= today + timedelta(days=3):
                return ["background-color: #ffffcc"] * len(row)  # Yellow - warning
            return [""] * len(row)

        styled_df = df[["product", "amount", "expiry_date"]].style.apply(highlight_expiry, axis=1)
        st.dataframe(styled_df, width="stretch")

        # Delete option
        with st.expander("Eintrag löschen"):
            product_options = {f"{row['product']} (Ablauf: {row['expiry_date']})": row["id"] for _, row in df.iterrows()}
            selected = st.selectbox("Produkt auswählen", options=list(product_options.keys()))
            if st.button("Löschen"):
                repo.delete(product_options[selected])
                st.success("Eintrag gelöscht.")
                st.rerun()
    else:
        st.info("Keine offenen Lebensmittel vorhanden.")


def _render_cleaning_section(db: HACCPDatabase, cleaning_schedule: dict):
    """Kitchen cleaning task logging."""
    st.subheader("Reinigungslisten")

    repo = KitchenCleaningRepo(db)

    for station_key, config in cleaning_schedule.items():
        label = config.get("label", station_key.title())
        tasks_options = config.get("tasks", [
            "Oberfläche abwischen",
            "Entkalken",
            "Filter prüfen/wechseln",
            "Tägliche Reinigung",
            "Wöchentliche Tiefenreinigung",
        ])

        with st.expander(f"{label} reinigen"):
            tasks = st.multiselect(
                f"Aufgaben für {label}",
                options=tasks_options,
                key=f"{station_key}_tasks",
            )

            if st.button(f"Speichern {label}", key=f"save_{station_key}"):
                entry = KitchenCleaning(station=station_key, tasks=tasks)
                repo.insert(entry)
                st.success(f"Aufgaben für {label} gespeichert.")

    # Display cleaning overview
    st.subheader("Reinigungs-Übersicht")
    df = repo.get_dataframe(
        "SELECT station, tasks, completed_at FROM kitchen_cleaning ORDER BY completed_at DESC LIMIT 20"
    )

    if not df.empty:
        import json
        df["tasks"] = df["tasks"].apply(lambda x: ", ".join(json.loads(x)) if x else "-")
        df["completed_at"] = pd.to_datetime(df["completed_at"]).dt.strftime("%d.%m.%Y %H:%M")
        df["station"] = df["station"].apply(lambda s: cleaning_schedule.get(s, {}).get("label", s.title()))
        st.dataframe(df, width="stretch")
    else:
        st.info("Keine Reinigungs-Einträge vorhanden.")


def _render_reports_section(db: HACCPDatabase):
    """PDF report generation section."""
    st.subheader("HACCP Berichte")

    start_date, end_date = render_date_range_picker("kitchen_report")

    col1, col2, col3, col4 = st.columns(4)

    report_gen = HACCPReportGenerator(db)

    with col1:
        if st.button("Temperatur-Bericht"):
            pdf = report_gen.generate_temperature_report(start_date, end_date)
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name=f"haccp_temperatur_{start_date}_{end_date}.pdf",
                mime="application/pdf",
            )

    with col2:
        if st.button("Reinigungs-Bericht"):
            pdf = report_gen.generate_cleaning_report(start_date, end_date)
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name=f"haccp_reinigung_{start_date}_{end_date}.pdf",
                mime="application/pdf",
            )

    with col3:
        if st.button("Wareneingang-Bericht"):
            pdf = report_gen.generate_goods_receipt_report(start_date, end_date)
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name=f"haccp_wareneingang_{start_date}_{end_date}.pdf",
                mime="application/pdf",
            )

    with col4:
        if st.button("Gesamtbericht"):
            pdf = report_gen.generate_full_report(start_date, end_date)
            st.download_button(
                "Download PDF",
                data=pdf,
                file_name=f"haccp_gesamt_{start_date}_{end_date}.pdf",
                mime="application/pdf",
            )
