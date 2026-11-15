"""Persistent JSON state store and historical archive exporters for ROD listings."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Optional, Union

from rod_scanner.models import Listing

_DISTRICT_SORT_PRIORITY: dict[str, int] = {
    "mokotow": 0,
    "ochota": 0,
    "warszawa_unassigned": 1,
}

HISTORY_CSV_COLUMNS: tuple[str, ...] = (
    "id",
    "source",
    "district_match",
    "matched_garden",
    "title",
    "price_pln",
    "area_m2",
    "price_per_m2",
    "first_seen_at",
    "last_seen_at",
    "times_seen",
    "price_changes",
    "url",
    "location_raw",
)


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


def _listing_from_meta(listing_id: str, meta: dict[str, Any]) -> Listing:
    """Reconstruct a ``Listing`` instance from a stored state metadata entry."""
    item_id = str(meta.get("id") or listing_id)
    source = str(meta.get("source") or (item_id.split(":", 1)[0] if ":" in item_id else ""))
    price_raw_val = meta.get("price_pln")
    price_pln = float(price_raw_val) if price_raw_val is not None else None
    area_raw_val = meta.get("area_m2")
    area_m2 = float(area_raw_val) if area_raw_val is not None else None

    return Listing(
        id=item_id,
        source=source,
        title=str(meta.get("title") or ""),
        url=str(meta.get("url") or ""),
        price_pln=price_pln,
        price_raw=meta.get("price_raw"),
        area_m2=area_m2,
        location_raw=str(meta.get("location_raw") or ""),
        description_snippet=str(meta.get("description_snippet") or ""),
        published_at=meta.get("published_at"),
        district_match=str(meta.get("district_match") or "unknown"),
        matched_garden=meta.get("matched_garden"),
        match_reason=str(meta.get("match_reason") or ""),
    )


def append_run_log(
    stats: dict[str, Any],
    log_path: Union[str, Path] = "data/history/runs_log.jsonl",
    *,
    now_iso: Optional[str] = None,
) -> Path:
    """Append a single JSON line with scan run metrics to ``runs_log.jsonl``."""
    target = Path(log_path)
    if target.suffix.lower() not in (".jsonl", ".json", ".log"):
        target = target / "runs_log.jsonl"

    timestamp = (
        stats.get("timestamp")
        or now_iso
        or datetime.now(timezone.utc).isoformat()
    )
    warnings_val = stats.get("warnings")
    if "warnings_count" in stats and stats["warnings_count"] is not None:
        warnings_count = int(stats["warnings_count"])
    elif isinstance(warnings_val, (list, tuple, set)):
        warnings_count = len(warnings_val)
    elif isinstance(warnings_val, int):
        warnings_count = warnings_val
    else:
        warnings_count = 0

    sources_val = stats.get("sources_scanned", stats.get("sources", []))
    if isinstance(sources_val, str):
        sources_list = [s.strip() for s in sources_val.split(",") if s.strip()]
    elif isinstance(sources_val, (list, tuple, set)):
        sources_list = [str(s) for s in sources_val]
    else:
        sources_list = []

    record = {
        "timestamp": str(timestamp),
        "mode": str(stats.get("mode") or "digest"),
        "total_scanned": int(stats.get("total_scanned", stats.get("total_raw", 0)) or 0),
        "new_count": int(stats.get("new_count", 0) or 0),
        "price_drops_count": int(stats.get("price_drops_count", 0) or 0),
        "sources_scanned": sources_list,
        "warnings_count": warnings_count,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return target


def save_daily_snapshot(
    report_md: str,
    history_dir: Union[str, Path] = "data/history",
    now_iso: Optional[str] = None,
) -> Path:
    """Write ``data/history/YYYY-MM/YYYY-MM-DD.md`` so daily reports are archived."""
    ref_dt = _parse_iso_datetime(now_iso) if now_iso else None
    if ref_dt is None:
        ref_dt = datetime.now(timezone.utc)

    month_dir = Path(history_dir) / ref_dt.strftime("%Y-%m")
    month_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = month_dir / f"{ref_dt.strftime('%Y-%m-%d')}.md"
    content = report_md if report_md.endswith("\n") else report_md + "\n"
    snapshot_path.write_text(content, encoding="utf-8")
    return snapshot_path


class StateStore:
    """Persists seen ROD listings to JSON and computes new listings, price drops, and digests."""

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
        mark_notified: bool = True,
    ) -> tuple[list[Listing], list[tuple[Listing, float]]]:
        """Compare incoming listings against seen state and update metadata.

        Args:
            listings: Incoming matched listings from current scan.
            now_iso: Optional ISO-8601 timestamp override.
            mark_notified: When ``True`` (default), newly seen listings and price drops
                are immediately marked as notified (``notified_at = now_iso`` and
                ``last_notified_price_pln = price_pln``). When ``False`` (used by 6-hour
                background scans), new listings keep ``notified_at = None`` and price drops
                leave ``last_notified_price_pln`` unchanged until ``mark_digest_sent`` runs.

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

            curr_price = float(listing.price_pln) if listing.price_pln is not None else None
            existing = self.seen.get(listing.id)

            if existing is None:
                new_listings.append(listing)
                price_history: list[dict[str, Any]] = []
                if curr_price is not None:
                    price_history.append({"timestamp": now_iso, "price_pln": curr_price})

                self.seen[listing.id] = {
                    "id": listing.id,
                    "source": listing.source,
                    "title": listing.title,
                    "url": listing.url,
                    "price_pln": curr_price,
                    "price_raw": listing.price_raw,
                    "area_m2": listing.area_m2,
                    "location_raw": listing.location_raw,
                    "description_snippet": listing.description_snippet,
                    "published_at": listing.published_at,
                    "district_match": listing.district_match,
                    "matched_garden": listing.matched_garden,
                    "match_reason": listing.match_reason,
                    "first_seen_at": now_iso,
                    "last_seen_at": now_iso,
                    "times_seen": 1,
                    "price_history": price_history,
                    "notified_at": now_iso if mark_notified else None,
                    "last_notified_price_pln": curr_price if mark_notified else None,
                }
            else:
                prev_price_raw = existing.get("price_pln")
                prev_price = float(prev_price_raw) if prev_price_raw is not None else None

                is_price_drop = (
                    prev_price is not None
                    and curr_price is not None
                    and (prev_price - curr_price) >= 1.0
                )
                if is_price_drop and prev_price is not None:
                    price_changed_listings.append((listing, prev_price))

                first_seen = existing.get("first_seen_at") or now_iso
                updated_price = curr_price if curr_price is not None else prev_price
                times_seen = int(existing.get("times_seen") or 1) + 1

                # Maintain price_history whenever first seen with a price or price changes
                raw_history = existing.get("price_history")
                price_history = (
                    [dict(entry) for entry in raw_history if isinstance(entry, dict)]
                    if isinstance(raw_history, list)
                    else []
                )
                if not price_history and prev_price is not None:
                    price_history.append({"timestamp": first_seen, "price_pln": prev_price})
                if curr_price is not None:
                    if not price_history or float(price_history[-1].get("price_pln", -1.0)) != curr_price:
                        price_history.append({"timestamp": now_iso, "price_pln": curr_price})

                existing_notified_at = existing.get("notified_at")
                existing_last_notified_price_raw = existing.get("last_notified_price_pln")
                existing_last_notified_price = (
                    float(existing_last_notified_price_raw)
                    if existing_last_notified_price_raw is not None
                    else (prev_price if existing_notified_at is not None else None)
                )

                if mark_notified:
                    notified_at = existing_notified_at or now_iso
                    if curr_price is not None and (is_price_drop or existing_last_notified_price is None):
                        last_notified_price = curr_price
                    else:
                        last_notified_price = existing_last_notified_price
                else:
                    notified_at = existing_notified_at
                    last_notified_price = existing_last_notified_price

                self.seen[listing.id] = {
                    "id": listing.id,
                    "source": listing.source or existing.get("source", ""),
                    "title": listing.title or existing.get("title", ""),
                    "url": listing.url or existing.get("url", ""),
                    "price_pln": updated_price,
                    "price_raw": (
                        listing.price_raw
                        if listing.price_raw is not None
                        else existing.get("price_raw")
                    ),
                    "area_m2": (
                        listing.area_m2
                        if listing.area_m2 is not None
                        else existing.get("area_m2")
                    ),
                    "location_raw": listing.location_raw or existing.get("location_raw", ""),
                    "description_snippet": (
                        listing.description_snippet
                        or existing.get("description_snippet", "")
                    ),
                    "published_at": (
                        listing.published_at
                        if listing.published_at is not None
                        else existing.get("published_at")
                    ),
                    "district_match": (
                        listing.district_match
                        if listing.district_match and listing.district_match != "unknown"
                        else existing.get("district_match", listing.district_match or "unknown")
                    ),
                    "matched_garden": (
                        listing.matched_garden
                        if listing.matched_garden is not None
                        else existing.get("matched_garden")
                    ),
                    "match_reason": listing.match_reason or existing.get("match_reason", ""),
                    "first_seen_at": first_seen,
                    "last_seen_at": now_iso,
                    "times_seen": times_seen,
                    "price_history": price_history,
                    "notified_at": notified_at,
                    "last_notified_price_pln": last_notified_price,
                }

        return new_listings, price_changed_listings

    def get_pending_digest_items(
        self,
    ) -> tuple[list[Listing], list[tuple[Listing, float]]]:
        """Return ``(unnotified_new_listings, unnotified_price_drops)`` across all stored listings.

        Includes discoveries accumulated across earlier 6-hour background scans, sorted
        with Mokotów & Ochota listings first.
        """
        unnotified_new: list[Listing] = []
        unnotified_drops: list[tuple[Listing, float]] = []

        for listing_id, meta in self.seen.items():
            if not isinstance(meta, dict):
                continue
            listing = _listing_from_meta(listing_id, meta)
            if listing.district_match == "excluded":
                continue

            notified_at = meta.get("notified_at")
            if notified_at is None:
                unnotified_new.append(listing)
            else:
                curr_price = listing.price_pln
                last_notified_raw = meta.get("last_notified_price_pln")
                if last_notified_raw is None:
                    history = meta.get("price_history")
                    if isinstance(history, list) and history:
                        first_hist_price = history[0].get("price_pln")
                        if first_hist_price is not None:
                            last_notified_raw = first_hist_price

                if last_notified_raw is not None and curr_price is not None:
                    ref_price = float(last_notified_raw)
                    if (ref_price - float(curr_price)) >= 1.0:
                        unnotified_drops.append((listing, ref_price))

        unnotified_new.sort(
            key=lambda item: _DISTRICT_SORT_PRIORITY.get(item.district_match, 99)
        )
        unnotified_drops.sort(
            key=lambda pair: _DISTRICT_SORT_PRIORITY.get(pair[0].district_match, 99)
        )
        return unnotified_new, unnotified_drops

    def mark_digest_sent(
        self,
        new_listings: list[Listing],
        price_drops: list[tuple[Listing, float]],
        now_iso: Optional[str] = None,
    ) -> None:
        """Mark all items included in a sent digest as notified at ``now_iso``."""
        if now_iso is None:
            now_iso = datetime.now(timezone.utc).isoformat()

        for listing in new_listings:
            meta = self.seen.get(listing.id)
            if meta is not None:
                meta["notified_at"] = now_iso
                price_val = (
                    listing.price_pln
                    if listing.price_pln is not None
                    else meta.get("price_pln")
                )
                meta["last_notified_price_pln"] = (
                    float(price_val) if price_val is not None else None
                )

        for listing, _prev_price in price_drops:
            meta = self.seen.get(listing.id)
            if meta is not None:
                if meta.get("notified_at") is None:
                    meta["notified_at"] = now_iso
                price_val = (
                    listing.price_pln
                    if listing.price_pln is not None
                    else meta.get("price_pln")
                )
                meta["last_notified_price_pln"] = (
                    float(price_val) if price_val is not None else None
                )

    def export_history_csv(
        self,
        csv_path: Union[str, Path] = "data/history/listings_history.csv",
    ) -> Path:
        """Write a UTF-8 CSV of all listings in ``self.seen`` sorted by ``first_seen_at`` descending."""
        target = Path(csv_path)
        if target.suffix.lower() != ".csv":
            target = target / "listings_history.csv"
        target.parent.mkdir(parents=True, exist_ok=True)

        rows: list[tuple[str, str, dict[str, Any]]] = []
        for listing_id, meta in self.seen.items():
            if not isinstance(meta, dict):
                continue
            first_seen = str(meta.get("first_seen_at") or "")
            rows.append((first_seen, str(listing_id), meta))

        # Sort by first_seen_at descending, then id ascending for deterministic output
        rows.sort(key=lambda item: item[1])
        rows.sort(key=lambda item: item[0], reverse=True)

        with target.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(HISTORY_CSV_COLUMNS))
            writer.writeheader()
            for _first_seen, listing_id, meta in rows:
                listing = _listing_from_meta(listing_id, meta)
                history = meta.get("price_history")
                if isinstance(history, list) and len(history) > 1:
                    price_changes = len(history) - 1
                else:
                    price_changes = 0

                writer.writerow(
                    {
                        "id": listing.id,
                        "source": listing.source,
                        "district_match": listing.district_match,
                        "matched_garden": listing.matched_garden or "",
                        "title": listing.title,
                        "price_pln": (
                            listing.price_pln if listing.price_pln is not None else ""
                        ),
                        "area_m2": (
                            listing.area_m2 if listing.area_m2 is not None else ""
                        ),
                        "price_per_m2": (
                            listing.price_per_m2
                            if listing.price_per_m2 is not None
                            else ""
                        ),
                        "first_seen_at": meta.get("first_seen_at") or "",
                        "last_seen_at": meta.get("last_seen_at") or "",
                        "times_seen": int(meta.get("times_seen") or 1),
                        "price_changes": price_changes,
                        "url": listing.url,
                        "location_raw": listing.location_raw,
                    }
                )

        return target

    def append_run_log(
        self,
        stats: dict[str, Any],
        log_path: Union[str, Path] = "data/history/runs_log.jsonl",
        *,
        now_iso: Optional[str] = None,
    ) -> Path:
        """Append a single JSON line with scan run metrics to ``runs_log.jsonl``."""
        return append_run_log(stats, log_path=log_path, now_iso=now_iso)

    def save_daily_snapshot(
        self,
        report_md: str,
        history_dir: Union[str, Path] = "data/history",
        now_iso: Optional[str] = None,
    ) -> Path:
        """Write ``data/history/YYYY-MM/YYYY-MM-DD.md`` so daily reports are archived."""
        return save_daily_snapshot(report_md, history_dir=history_dir, now_iso=now_iso)

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


def export_history_csv(
    csv_path: Union[str, Path] = "data/history/listings_history.csv",
    state_path: Union[str, Path] = "data/seen_listings.json",
) -> Path:
    """Module-level helper to export ``StateStore(state_path)`` history to CSV."""
    store = StateStore(state_path)
    return store.export_history_csv(csv_path)
