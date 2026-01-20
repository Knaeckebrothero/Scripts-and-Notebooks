"""
Alert service for HACCP compliance warnings.
Detects expiring products and overdue cleaning tasks.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Dict, Any
from enum import Enum

from db import HACCPDatabase, OpenProductRepo, KitchenCleaningRepo


class AlertPriority(Enum):
    """Alert priority levels for display."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Alert:
    """A single alert/warning."""

    message: str
    priority: AlertPriority
    category: str  # "food_expiry", "cleaning_overdue"
    created_at: datetime
    entity_id: int = None
    entity_type: str = None


class AlertService:
    """
    Service to check for conditions requiring alerts.
    Called on each page load - no background scheduler needed.
    """

    def __init__(self, db: HACCPDatabase):
        self.db = db
        self.open_product_repo = OpenProductRepo(db)
        self.cleaning_repo = KitchenCleaningRepo(db)

    def check_expiring_products(self, warn_days: int = 3) -> List[Alert]:
        """Check for products expiring within N days."""
        alerts = []
        products = self.open_product_repo.get_expiring_soon(warn_days)

        for product in products:
            try:
                expiry = date.fromisoformat(product.expiry_date)
            except ValueError:
                continue

            days_until = (expiry - date.today()).days

            if days_until < 0:
                priority = AlertPriority.CRITICAL
                msg = f"ABGELAUFEN: {product.product} ({abs(days_until)} Tage)"
            elif days_until == 0:
                priority = AlertPriority.HIGH
                msg = f"Läuft HEUTE ab: {product.product}"
            elif days_until == 1:
                priority = AlertPriority.HIGH
                msg = f"Läuft MORGEN ab: {product.product}"
            else:
                priority = AlertPriority.MEDIUM
                msg = f"Läuft in {days_until} Tagen ab: {product.product}"

            alerts.append(
                Alert(
                    message=msg,
                    priority=priority,
                    category="food_expiry",
                    created_at=datetime.now(),
                    entity_id=product.id,
                    entity_type="open_product",
                )
            )

        return alerts

    def check_overdue_cleaning(self, schedule: Dict[str, Dict[str, Any]]) -> List[Alert]:
        """
        Check if scheduled cleaning tasks are overdue.

        Args:
            schedule: Dict mapping station name to config with 'frequency_days'.
                      Example: {"kaffeemaschine": {"frequency_days": 1},
                               "buffet": {"frequency_days": 7}}
        """
        alerts = []

        for station, config in schedule.items():
            frequency = config.get("frequency_days", 1)
            last_cleaning = self.cleaning_repo.get_last_for_station(station)

            if last_cleaning:
                try:
                    last_date = datetime.fromisoformat(
                        last_cleaning.completed_at
                    ).date()
                except ValueError:
                    continue

                days_since = (date.today() - last_date).days

                if days_since >= frequency:
                    overdue_by = days_since - frequency + 1
                    if overdue_by > frequency:
                        priority = AlertPriority.HIGH
                    else:
                        priority = AlertPriority.MEDIUM

                    station_label = config.get("label", station.title())
                    alerts.append(
                        Alert(
                            message=f"Reinigung überfällig: {station_label} (letzte: vor {days_since} Tagen)",
                            priority=priority,
                            category="cleaning_overdue",
                            created_at=datetime.now(),
                        )
                    )
            else:
                # No cleaning record at all
                station_label = config.get("label", station.title())
                alerts.append(
                    Alert(
                        message=f"Keine Reinigung dokumentiert: {station_label}",
                        priority=AlertPriority.MEDIUM,
                        category="cleaning_overdue",
                        created_at=datetime.now(),
                    )
                )

        return alerts

    def get_all_alerts(self, cleaning_schedule: Dict[str, Dict[str, Any]]) -> List[Alert]:
        """
        Aggregate all alerts, sorted by priority.

        Args:
            cleaning_schedule: Schedule dict for cleaning checks.
        """
        alerts = []
        alerts.extend(self.check_expiring_products())
        alerts.extend(self.check_overdue_cleaning(cleaning_schedule))

        # Sort by priority (CRITICAL first)
        priority_order = {
            AlertPriority.CRITICAL: 0,
            AlertPriority.HIGH: 1,
            AlertPriority.MEDIUM: 2,
            AlertPriority.LOW: 3,
        }
        alerts.sort(key=lambda a: priority_order[a.priority])

        return alerts
