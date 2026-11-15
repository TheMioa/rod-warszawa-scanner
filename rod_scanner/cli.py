"""Command-line interface for the Warsaw ROD (Mokotów & Ochota) scanner."""

from __future__ import annotations

import argparse
import inspect
from pathlib import Path
import sys
from typing import Optional, Sequence

from rod_scanner.matcher import filter_listings
from rod_scanner.models import Listing
from rod_scanner.notifiers import dispatch_notifications, format_markdown_report
from rod_scanner.scrapers import fetch_all_listings, get_demo_listings
from rod_scanner.state import StateStore

DEFAULT_STATE_FILE = "data/seen_listings.json"
DEFAULT_OUTPUT_REPORT = "data/latest_report.md"


def build_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser for `rod_scanner`."""
    parser = argparse.ArgumentParser(
        prog="rod_scanner",
        description=(
            "Scan Polish classifieds, PZD Okręg Mazowiecki, and Warsaw ROD garden boards "
            "for allotment plots (działki ROD) in Mokotów and Ochota."
        ),
    )
    parser.add_argument(
        "--state-file",
        default=DEFAULT_STATE_FILE,
        help=f"Path to persistent seen-listings JSON state file (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--output-report",
        default=DEFAULT_OUTPUT_REPORT,
        help=f"Path to write the latest Markdown report (default: {DEFAULT_OUTPUT_REPORT})",
    )
    parser.add_argument(
        "--strict-districts-only",
        action="store_true",
        default=False,
        help=(
            "Only keep high-confidence Mokotów & Ochota matches "
            "(sets include_unassigned=False, dropping unassigned Warsaw listings)"
        ),
    )
    parser.add_argument(
        "--sources",
        default="all",
        help=(
            "Comma-separated list of scraper sources to query "
            "(e.g. olx,otodom,gratka,sprzedajemy,pzd_mazowiecki,rod_garden) or 'all' (default: all)"
        ),
    )
    parser.add_argument(
        "--demo-fixture",
        action="store_true",
        default=False,
        help="Use offline demo listings fixture instead of live network scraping",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Do not overwrite state file or send external notifications; print report to stdout",
    )
    parser.add_argument(
        "--force-notify",
        action="store_true",
        default=False,
        help="Send Email / GitHub notifications even if 0 new listings or price drops are found",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Per-request HTTP timeout in seconds when scraping live sources (default: 15)",
    )
    return parser


def _parse_sources(raw_sources: str) -> Optional[list[str]]:
    """Parse `--sources` argument into a list of source identifiers or None for all."""
    cleaned = (raw_sources or "").strip()
    if not cleaned or cleaned.lower() == "all":
        return None
    sources = [part.strip().lower() for part in cleaned.split(",") if part.strip()]
    return sources or None


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the ROD scanner pipeline from CLI arguments."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    selected_sources = _parse_sources(args.sources)
    warnings: list[str] = []

    if args.demo_fixture:
        raw_listings = list(get_demo_listings())
        if selected_sources is not None:
            allowed = set(selected_sources)
            raw_listings = [item for item in raw_listings if item.source.lower() in allowed]
    else:
        raw_listings, warnings = fetch_all_listings(
            sources=selected_sources,
            timeout=args.timeout,
        )

    include_unassigned = not bool(args.strict_districts_only)
    matched_listings = filter_listings(
        raw_listings,
        include_unassigned=include_unassigned,
    )

    store = StateStore(args.state_file)
    new_listings, price_drops = store.diff_and_update(matched_listings)
    pruned_count = store.prune_old()

    if not args.dry_run:
        store.save()

    if selected_sources is not None:
        sources_scanned = selected_sources
    elif raw_listings:
        sources_scanned = sorted({item.source for item in raw_listings if item.source})
    else:
        sources_scanned = [
            "olx",
            "otodom",
            "gratka",
            "sprzedajemy",
            "pzd_mazowiecki",
            "rod_garden",
        ]

    stats = {
        "sources_scanned": sources_scanned,
        "sources": sources_scanned,
        "total_scanned": len(raw_listings),
        "total_raw": len(raw_listings),
        "total_matched": len(matched_listings),
        "matched_count": len(matched_listings),
        "new_count": len(new_listings),
        "price_drops_count": len(price_drops),
        "pruned_count": pruned_count,
        "warnings": warnings,
        "strict_districts_only": bool(args.strict_districts_only),
        "demo_fixture": bool(args.demo_fixture),
        "dry_run": bool(args.dry_run),
    }

    report_md = format_markdown_report(new_listings, price_drops, stats)

    # Write Markdown report to disk unless in dry-run mode (or if --output-report was explicitly passed)
    explicit_output = argv is not None and any(
        arg == "--output-report" or arg.startswith("--output-report=") for arg in argv
    )
    if not args.dry_run or explicit_output:
        report_path = Path(args.output_report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_md, encoding="utf-8")

    # Always output the Markdown report to stdout so dry-run and CI logs show it clearly
    sys.stdout.write(report_md)
    if not report_md.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()

    # Dispatch notifications
    notify_listings = new_listings
    sig = inspect.signature(dispatch_notifications)
    dispatch_kwargs: dict[str, object] = {"dry_run": bool(args.dry_run)}
    if "force_notify" in sig.parameters:
        dispatch_kwargs["force_notify"] = bool(args.force_notify)
    elif "force" in sig.parameters:
        dispatch_kwargs["force"] = bool(args.force_notify)
    elif args.force_notify and not new_listings and not price_drops:
        # Ensure notifications are triggered when --force-notify is passed even if 0 new listings
        notify_listings = matched_listings[:1] or [
            Listing(
                id="force:test-notification",
                source="rod_scanner",
                title="Test powiadomień ROD Warszawa Scanner (--force-notify)",
                url="https://github.com",
                district_match="mokotow",
                matched_garden="ROD Mokotów / Ochota",
                match_reason="Forced notification test (--force-notify)",
            )
        ]

    dispatch_notifications(
        notify_listings,
        price_drops,
        report_md,
        **dispatch_kwargs,
    )

    return 0
