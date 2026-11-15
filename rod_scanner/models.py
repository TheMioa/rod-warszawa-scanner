"""Shared data models for the Warsaw ROD scanner."""

from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Listing:
    id: str                  # e.g. "olx:12345"
    source: str              # e.g. "olx", "otodom", "gratka", "sprzedajemy", "pzd_mazowiecki", "rod_garden"
    title: str
    url: str
    price_pln: Optional[float] = None
    price_raw: Optional[str] = None
    area_m2: Optional[float] = None
    location_raw: str = ""
    description_snippet: str = ""
    published_at: Optional[str] = None
    district_match: str = "unknown"  # "mokotow", "ochota", "warszawa_unassigned", or "excluded"
    matched_garden: Optional[str] = None
    match_reason: str = ""

    @property
    def price_per_m2(self) -> Optional[float]:
        if self.price_pln is not None and self.area_m2 and self.area_m2 > 0:
            return round(self.price_pln / self.area_m2, 2)
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Listing":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
