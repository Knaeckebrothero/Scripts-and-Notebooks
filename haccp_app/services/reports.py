"""
PDF report generation for HACCP compliance documentation.
Uses reportlab for pure Python PDF generation.
"""
import io
from datetime import date, datetime
from typing import List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageBreak,
)

from db import (
    HACCPDatabase,
    KitchenTemperatureRepo,
    GoodsReceiptRepo,
    OpenProductRepo,
    KitchenCleaningRepo,
)


class HACCPReportGenerator:
    """Generate HACCP inspection reports as PDF."""

    def __init__(self, db: HACCPDatabase):
        self.db = db
        self.styles = getSampleStyleSheet()
        self._setup_styles()

    def _setup_styles(self):
        """Add custom styles for reports."""
        self.styles.add(
            ParagraphStyle(
                name="ReportTitle",
                parent=self.styles["Heading1"],
                fontSize=18,
                spaceAfter=20,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionHeader",
                parent=self.styles["Heading2"],
                fontSize=14,
                spaceBefore=15,
                spaceAfter=10,
            )
        )

    def _create_table(self, data: List[List[str]], col_widths: List[float] = None) -> Table:
        """Create a styled table."""
        table = Table(data, colWidths=col_widths)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4a4a4a")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                    ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f5f5f5")),
                    ("FONTSIZE", (0, 1), (-1, -1), 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f0f0f0")]),
                ]
            )
        )
        return table

    def _add_header(self, elements: List, title: str, start_date: date, end_date: date):
        """Add report header with title and date range."""
        elements.append(Paragraph(title, self.styles["ReportTitle"]))
        elements.append(
            Paragraph(
                f"Zeitraum: {start_date.strftime('%d.%m.%Y')} bis {end_date.strftime('%d.%m.%Y')}",
                self.styles["Normal"],
            )
        )
        elements.append(
            Paragraph(
                f"Erstellt am: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                self.styles["Normal"],
            )
        )
        elements.append(Spacer(1, 20))

    def _add_signature_section(self, elements: List):
        """Add signature lines at the end of report."""
        elements.append(Spacer(1, 40))
        elements.append(Paragraph("_" * 50, self.styles["Normal"]))
        elements.append(Paragraph("Unterschrift Verantwortlicher", self.styles["Normal"]))
        elements.append(Spacer(1, 20))
        elements.append(Paragraph("_" * 50, self.styles["Normal"]))
        elements.append(Paragraph("Datum", self.styles["Normal"]))

    def generate_temperature_report(
        self,
        start_date: date,
        end_date: date,
        location: Optional[str] = None,
    ) -> bytes:
        """
        Generate temperature log report for HACCP inspection.

        Args:
            start_date: Start of reporting period
            end_date: End of reporting period
            location: Optional filter for "fridge" or "freezer"

        Returns:
            PDF as bytes
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        elements = []

        # Header
        title = "HACCP Temperaturprotokoll"
        if location:
            title += f" - {location.title()}"
        self._add_header(elements, title, start_date, end_date)

        # Fetch data
        repo = KitchenTemperatureRepo(self.db)
        temps = repo.get_by_date_range(start_date, end_date, location)

        if temps:
            # Build table
            data = [["Datum/Zeit", "Ort", "Temperatur (°C)", "Mitarbeiter"]]
            for t in temps:
                try:
                    ts = datetime.fromisoformat(t.timestamp)
                    formatted_ts = ts.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    formatted_ts = t.timestamp

                loc_label = "Kühlschrank" if t.location == "fridge" else "Gefrierschrank"
                data.append([formatted_ts, loc_label, f"{t.temperature:.1f}", t.employee])

            table = self._create_table(data, col_widths=[4.5 * cm, 3.5 * cm, 3 * cm, 4 * cm])
            elements.append(table)

            # Summary
            elements.append(Spacer(1, 20))
            elements.append(
                Paragraph(f"Anzahl Messungen: {len(temps)}", self.styles["Normal"])
            )
        else:
            elements.append(
                Paragraph(
                    "Keine Temperaturmessungen im angegebenen Zeitraum.",
                    self.styles["Normal"],
                )
            )

        self._add_signature_section(elements)
        doc.build(elements)
        return buffer.getvalue()

    def generate_cleaning_report(self, start_date: date, end_date: date) -> bytes:
        """Generate cleaning documentation report."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        elements = []

        self._add_header(elements, "HACCP Reinigungsprotokoll", start_date, end_date)

        repo = KitchenCleaningRepo(self.db)
        df = repo.get_dataframe(
            """SELECT * FROM kitchen_cleaning
               WHERE completed_at >= %s AND completed_at < %s
               ORDER BY completed_at DESC""",
            (start_date.isoformat(), (end_date + __import__('datetime').timedelta(days=1)).isoformat()),
        )

        if not df.empty:
            import json
            data = [["Datum/Zeit", "Station", "Durchgeführte Aufgaben"]]
            for _, row in df.iterrows():
                try:
                    ts = datetime.fromisoformat(row["completed_at"])
                    formatted_ts = ts.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    formatted_ts = row["completed_at"]

                tasks = json.loads(row["tasks"]) if isinstance(row["tasks"], str) else row["tasks"]
                tasks_str = ", ".join(tasks) if tasks else "-"
                data.append([formatted_ts, row["station"].title(), tasks_str])

            table = self._create_table(data, col_widths=[4 * cm, 3.5 * cm, 8.5 * cm])
            elements.append(table)
        else:
            elements.append(
                Paragraph(
                    "Keine Reinigungsdokumentation im angegebenen Zeitraum.",
                    self.styles["Normal"],
                )
            )

        self._add_signature_section(elements)
        doc.build(elements)
        return buffer.getvalue()

    def generate_goods_receipt_report(self, start_date: date, end_date: date) -> bytes:
        """Generate goods receipt report."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        elements = []

        self._add_header(elements, "HACCP Wareneingangsprotokoll", start_date, end_date)

        repo = GoodsReceiptRepo(self.db)
        receipts = repo.get_by_date_range(start_date, end_date)

        if receipts:
            data = [["Datum", "Produkt", "Menge", "Angenommen von"]]
            for r in receipts:
                try:
                    dt = date.fromisoformat(r.receipt_date)
                    formatted_date = dt.strftime("%d.%m.%Y")
                except ValueError:
                    formatted_date = r.receipt_date

                data.append([formatted_date, r.product, r.amount, r.employee])

            table = self._create_table(data, col_widths=[3 * cm, 5 * cm, 4 * cm, 4 * cm])
            elements.append(table)
        else:
            elements.append(
                Paragraph(
                    "Keine Wareneingänge im angegebenen Zeitraum.",
                    self.styles["Normal"],
                )
            )

        self._add_signature_section(elements)
        doc.build(elements)
        return buffer.getvalue()

    def generate_full_report(self, start_date: date, end_date: date) -> bytes:
        """
        Generate complete HACCP report with all sections.
        Suitable for inspector audits.
        """
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )
        elements = []

        # Main header
        self._add_header(elements, "HACCP Gesamtbericht", start_date, end_date)

        # Temperature section
        elements.append(Paragraph("1. Temperaturkontrollen", self.styles["SectionHeader"]))
        temp_repo = KitchenTemperatureRepo(self.db)
        temps = temp_repo.get_by_date_range(start_date, end_date)

        if temps:
            data = [["Datum/Zeit", "Ort", "°C", "Mitarbeiter"]]
            for t in temps[:50]:  # Limit for readability
                try:
                    ts = datetime.fromisoformat(t.timestamp)
                    formatted_ts = ts.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    formatted_ts = t.timestamp
                loc_label = "Kühlschrank" if t.location == "fridge" else "Gefrierschrank"
                data.append([formatted_ts, loc_label, f"{t.temperature:.1f}", t.employee])

            table = self._create_table(data, col_widths=[4 * cm, 3.5 * cm, 2 * cm, 4 * cm])
            elements.append(table)
            if len(temps) > 50:
                elements.append(
                    Paragraph(f"... und {len(temps) - 50} weitere Einträge", self.styles["Normal"])
                )
        else:
            elements.append(Paragraph("Keine Einträge.", self.styles["Normal"]))

        elements.append(PageBreak())

        # Goods receipt section
        elements.append(Paragraph("2. Wareneingänge", self.styles["SectionHeader"]))
        goods_repo = GoodsReceiptRepo(self.db)
        receipts = goods_repo.get_by_date_range(start_date, end_date)

        if receipts:
            data = [["Datum", "Produkt", "Menge", "Angenommen von"]]
            for r in receipts:
                try:
                    dt = date.fromisoformat(r.receipt_date)
                    formatted_date = dt.strftime("%d.%m.%Y")
                except ValueError:
                    formatted_date = r.receipt_date
                data.append([formatted_date, r.product, r.amount, r.employee])

            table = self._create_table(data, col_widths=[3 * cm, 5 * cm, 3.5 * cm, 4 * cm])
            elements.append(table)
        else:
            elements.append(Paragraph("Keine Einträge.", self.styles["Normal"]))

        elements.append(Spacer(1, 20))

        # Open products section
        elements.append(Paragraph("3. Offene Lebensmittel", self.styles["SectionHeader"]))
        open_repo = OpenProductRepo(self.db)
        open_products = open_repo.get_all(order_by="expiry_date ASC")

        if open_products:
            data = [["Produkt", "Menge", "Ablaufdatum"]]
            for p in open_products:
                try:
                    dt = date.fromisoformat(p.expiry_date)
                    formatted_date = dt.strftime("%d.%m.%Y")
                except ValueError:
                    formatted_date = p.expiry_date
                data.append([p.product, p.amount, formatted_date])

            table = self._create_table(data, col_widths=[6 * cm, 4 * cm, 4 * cm])
            elements.append(table)
        else:
            elements.append(Paragraph("Keine Einträge.", self.styles["Normal"]))

        elements.append(Spacer(1, 20))

        # Cleaning section
        elements.append(Paragraph("4. Reinigungsdokumentation", self.styles["SectionHeader"]))
        clean_repo = KitchenCleaningRepo(self.db)
        df = clean_repo.get_dataframe(
            """SELECT * FROM kitchen_cleaning
               WHERE completed_at >= %s AND completed_at < %s
               ORDER BY completed_at DESC""",
            (start_date.isoformat(), (end_date + __import__('datetime').timedelta(days=1)).isoformat()),
        )

        if not df.empty:
            import json
            data = [["Datum/Zeit", "Station", "Aufgaben"]]
            for _, row in df.iterrows():
                try:
                    ts = datetime.fromisoformat(row["completed_at"])
                    formatted_ts = ts.strftime("%d.%m.%Y %H:%M")
                except ValueError:
                    formatted_ts = row["completed_at"]
                tasks = json.loads(row["tasks"]) if isinstance(row["tasks"], str) else row["tasks"]
                tasks_str = ", ".join(tasks) if tasks else "-"
                data.append([formatted_ts, row["station"].title(), tasks_str])

            table = self._create_table(data, col_widths=[4 * cm, 3 * cm, 9 * cm])
            elements.append(table)
        else:
            elements.append(Paragraph("Keine Einträge.", self.styles["Normal"]))

        self._add_signature_section(elements)
        doc.build(elements)
        return buffer.getvalue()
