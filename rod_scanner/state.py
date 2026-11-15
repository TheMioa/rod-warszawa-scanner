"""Persistent JSON state store for tracking seen ROD listings and price drops."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Optional, Union

from rod_scanner.models import Listing


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a timezone-aware UTC datetime."""
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class StateStore:
    """Persists seen ROD listings to JSON and computes new listings and price drops."""

    def __init__(self, path: Union[str, Path] = "data/seen_listings.json") -> None:
        self.path: Path = Path(path)
        self.seen: dict[str, dict[str, Any]] = {}
        self.load()

    @property
    def listings(self) -> dict[str, dict[str, Any]]:
        """Alias for ``self.seen``."""
        return self.seen

    def __len__(self) -> int:
        return len(self.seen)

    def __contains__(self, listing_id: object) -> bool:
        return listing_id in self.seen

    def __getitem__(self, listing_id: str) -> dict[str, Any]:
        return self.seen[listing_id]

    def load(self) -> dict[str, dict[str, Any]]:
        """Load seen listings from disk if the state file exists."""
        if not self.path.exists():
            self.seen = {}
            return self.seen

        try:
            raw_text = self.path.read_text(encoding="utf-8").strip()
            if not raw_text:
                self.seen = {}
                return self.seen
            data = json.loads(raw_text)
        except (OSError, json.JSONDecodeError):
            self.seen = {}
            return self.seen

        if isinstance(data, dict):
            # Support either a flat dict keyed by listing.id or a wrapped dict
            if (
                "seen" in data
                and isinstance(data["seen"], dict)
                and all(isinstance(v, dict) for v in data["seen"].values())
                and not any(k in data for k in ("first_seen_at", "last_seen_at", "title"))
            ):
                self.seen = dict(data["seen"])
            elif (
                "listings" in data
                and isinstance(data["listings"], dict)
                and all(isinstance(v, dict) for v in data["listings"].values())
                and not any(k in data for k in ("first_seen_at", "last_seen_at", "title"))
            ):
                self.seen = dict(data["listings"])
            else:
                self.seen = {
                    str(k): dict(v) for k, v in data.items() if isinstance(v, dict)
                }
        else:
            self.seen = {}

        return self.seen

    def diff_and_update(
        self,
        listings: list[Listing],
        now_iso: Optional[str] = None,
    ) -> tuple[list[Listing], list[tuple[Listing, float]]]:
        """Compare incoming listings against seen state and update metadata.

        Returns:
            ``(new_listings, price_changed_listings)`` where
            ``price_changed_listings`` is a list of ``(listing, previous_price_pln)``
            whenever a previously seen listing's price dropped by at least 1 PLN.
        """
        if now_iso is None:
            now_iso = datetime.now(timezone.utc).isoformat()

        new_listings: list[Listing] = []
        price_changed_listings: list[tuple[Listing, float]] = []

        for listing in listings:
            if not listing.id:
                continue

            existing = self.seen.get(listing.id)
            if existing is None:
                new_listings.append(listing)
                self.seen[listing.id] = {
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                    "price_pln": listing.price_pln,
                    "title": listing.title,
                    "url": listing.url,
                    "district_match": listing.district_match,
                    "matched_garden": listing.matched_garden,
                }
            else:
                prev_price = existing.get("price_pln")
                curr_price = listing.price_pln
                if (
                    prev_price is not None
                    and curr_price is not None
                    and (float(prev_price) - float(curr_price)) >= 1.0
                ):
                    price_changed_listings.append((listing, float(prev_price)))

                first_seen = existing.get("first_seen_at") or now_iso
                updated_price = curr_price if curr_price is not None else prev_price
                self.seen[listing.id] = {
                    "first_seen_at": first_seen,
                    "last_seen_at": now_iso,
                    "price_pln": updated_price,
                    "title": listing.title or existing.get("title", ""),
                    "url": listing.url or existing.get("url", ""),
                    "district_match": listing.district_match or existing.get("district_match", "unknown"),
                    "matched_garden": (
                        listing.matched_garden
                        if listing.matched_garden is not None
                        else existing.get("matched_garden")
                    ),
                }

        return new_listings, price_changed_listings

    def prune_old(
        self,
        max_age_days: int = 180,
        now: Optional[Union[datetime, str]] = None,
    ) -> int:
        """Remove entries not seen within ``max_age_days`` days.

        Returns the count of pruned entries.
        """
        if now is None:
            ref_dt = datetime.now(timezone.utc)
        elif isinstance(now, str):
            ref_dt = _parse_iso_datetime(now) or datetime.now(timezone.utc)
        elif isinstance(now, datetime):
            ref_dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        else:
            ref_dt = datetime.now(timezone.utc)

        cutoff = ref_dt - timedelta(days=max_age_days)
        to_delete: list[str] = []

        for listing_id, meta in self.seen.items():
            ts_str = meta.get("last_seen_at") or meta.get("first_seen_at")
            seen_dt = _parse_iso_datetime(ts_str)
            if seen_dt is not None and seen_dt < cutoff:
                to_delete.append(listing_id)

        for listing_id in to_delete:
            del self.seen[listing_id]

        return len(to_delete)

    def save(self) -> Path:
        """Write current state dictionary to disk as formatted JSON."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(self.seen, indent=2, ensure_ascii=False, sort_keys=True)
        self.path.write_text(serialized + "\n", encoding="utf-8")
        return self.path
