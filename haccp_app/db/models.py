"""
Data models for HACCP application.
Using dataclasses for type safety and easy dict conversion.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List
import json


def _now_iso() -> str:
    return datetime.now().isoformat()


@dataclass
class KitchenTemperature:
    """Temperature log entry for fridge or freezer."""

    location: str  # "fridge" or "freezer"
    temperature: float
    employee: str
    timestamp: str = field(default_factory=_now_iso)
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        d.pop("created_at", None)  # Let DB set this
        return d


@dataclass
class GoodsReceipt:
    """Incoming goods record."""

    product: str
    amount: str
    receipt_date: str
    employee: str
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        d.pop("created_at", None)
        return d


@dataclass
class OpenProduct:
    """Open food product with expiry tracking."""

    product: str
    amount: str
    expiry_date: str
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        d.pop("created_at", None)
        return d


@dataclass
class KitchenCleaning:
    """Kitchen cleaning task record."""

    station: str
    tasks: List[str]
    completed_at: str = field(default_factory=_now_iso)
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "station": self.station,
            "tasks": json.dumps(self.tasks),
            "completed_at": self.completed_at,
            "created_by": self.created_by,
        }

    @classmethod
    def from_row(cls, row: dict) -> "KitchenCleaning":
        return cls(
            id=row["id"],
            station=row["station"],
            tasks=json.loads(row["tasks"]),
            completed_at=row["completed_at"],
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
        )


@dataclass
class Housekeeping:
    """Housekeeping room cleaning record."""

    datum: str
    raum: str
    aufgaben: List[str]
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "datum": self.datum,
            "raum": self.raum,
            "aufgaben": json.dumps(self.aufgaben),
            "created_by": self.created_by,
        }

    @classmethod
    def from_row(cls, row: dict) -> "Housekeeping":
        return cls(
            id=row["id"],
            datum=row["datum"],
            raum=row["raum"],
            aufgaben=json.loads(row["aufgaben"]),
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
        )


@dataclass
class BasicCleaning:
    """Deep cleaning / basic cleaning record."""

    datum: str
    abreise: Optional[str] = None
    bleibe: Optional[str] = None
    notes: Optional[str] = None
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        d.pop("created_at", None)
        return d


@dataclass
class HotelGuest:
    """Hotel guest record."""

    name: str
    anreise: str
    abreise: str
    hund_mit: bool
    notizen: Optional[str] = None
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "anreise": self.anreise,
            "abreise": self.abreise,
            "hund_mit": 1 if self.hund_mit else 0,
            "notizen": self.notizen,
            "created_by": self.created_by,
        }

    @classmethod
    def from_row(cls, row: dict) -> "HotelGuest":
        return cls(
            id=row["id"],
            name=row["name"],
            anreise=row["anreise"],
            abreise=row["abreise"],
            hund_mit=bool(row["hund_mit"]),
            notizen=row["notizen"],
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
        )


@dataclass
class ArrivalControl:
    """Arrival check-in control record."""

    datum: str
    zimmer: str
    employee: str
    id: Optional[int] = None
    created_by: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("id", None)
        d.pop("created_at", None)
        return d
