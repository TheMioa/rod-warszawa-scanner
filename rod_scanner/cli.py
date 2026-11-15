"""Command-line interface for the Warsaw ROD (Mokotów & Ochota) scanner."""

from __future__ import annotations

import argparse
import inspect
import os
from pathlib import Path
import sys
from typing import Optional, Sequence

from rod_scanner.matcher import filter_listings
from rod_scanner.models import Listing
from rod_scanner.notifiers import (
    dispatch_notifications,
    format_markdown_report,
    write_github_step_summary,
)
from rod_scanner.scrapers import fetch_all_listings, get_demo_listings
from rod_scanner.state import StateStore

DEFAULT_STATE_FILE = "data/seen_listings.json"
DEFAULT_OUTPUT_REPORT = "data/latest_report.md"
DEFAULT_HISTORY_DIR = "data/history"


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
        "--mode",
        choices=["digest", "scan-only", "scan-and-notify"],
        default="digest",
        help=(
            "Execution mode: 'scan-only' saves new listings/price drops as pending in state "
            "and updates history without sending Email/GitHub alerts; 'digest' (default) "
            "and 'scan-and-notify' include all pending discoveries since the last digest "
            "and send Email + GitHub notifications."
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
        "--history-dir",
        default=DEFAULT_HISTORY_DIR,
        help=f"Directory for historical CSV, run logs, and daily snapshots (default: {DEFAULT_HISTORY_DIR})",
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
        "--use-ai",
        action="store_true",
        default=False,
        help="Force Gemini / AGY LLM search and semantic enrichment even when --demo-fixture is set",
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


def _deduplicate_listings(listings: Sequence[Listing]) -> list[Listing]:
    """Deduplicate listings by ID and normalized URL while preserving order."""
    deduped: list[Listing] = []
    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    for item in listings:
        item_id = (item.id or "").strip()
        norm_url = (item.url or "").strip().rstrip("/").lower()
        if item_id and item_id in seen_ids:
            continue
        if norm_url and norm_url in seen_urls:
            continue
        if item_id:
            seen_ids.add(item_id)
        if norm_url:
            seen_urls.add(norm_url)
        deduped.append(item)
    return deduped


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

    # Check if AI agent / web search discovery is available
    api_key: Optional[str] = None
    ai_module = None
    try:
        from rod_scanner import ai_scanner as _ai_scanner

        ai_module = _ai_scanner
        if hasattr(_ai_scanner, "get_api_key"):
            api_key = _ai_scanner.get_api_key()
        if not api_key:
            api_key = (
                os.environ.get("GEMINI_API_KEY", "").strip()
                or os.environ.get("AGY_API_KEY", "").strip()
                or os.environ.get("GOOGLE_API_KEY", "").strip()
                or os.environ.get("GITHUB_TOKEN", "").strip()
                or None
            )
    except ImportError:
        ai_module = None

    # 1. Web Search Discovery (DuckDuckGo / Bing RSS) during live runs
    if (
        ai_module is not None
        and not args.demo_fixture
        and hasattr(ai_module, "web_search_rod_listings")
        and (
            selected_sources is None
            or "web_search" in selected_sources
            or "ai" in selected_sources
        )
    ):
        web_res = ai_module.web_search_rod_listings(timeout=max(args.timeout, 15))
        if isinstance(web_res, tuple) and len(web_res) == 2:
            web_found, web_warnings = web_res
            warnings.extend(web_warnings)
        else:
            web_found = web_res
        raw_listings = _deduplicate_listings(list(raw_listings) + list(web_found))

    should_run_ai = bool(
        ai_module is not None
        and api_key
        and (not args.demo_fixture or bool(args.use_ai))
    )

    if should_run_ai and ai_module is not None and api_key:
        backend_name = (
            ai_module.detect_ai_backend(api_key)[0]
            if hasattr(ai_module, "detect_ai_backend")
            else "gemini"
        )
        if (
            hasattr(ai_module, "ai_search_rod_listings")
            and (backend_name == "gemini" or args.demo_fixture)
            and (
                selected_sources is None
                or "gemini_ai" in selected_sources
                or "ai" in selected_sources
            )
        ):
            ai_found, ai_search_warnings = ai_module.ai_search_rod_listings(api_key)
            warnings.extend(ai_search_warnings)
            raw_listings = _deduplicate_listings(list(raw_listings) + list(ai_found))

        if hasattr(ai_module, "ai_enrich_and_filter_listings"):
            matched_listings, ai_enrich_warnings = ai_module.ai_enrich_and_filter_listings(
                raw_listings,
                api_key,
                include_unassigned=include_unassigned,
            )
            warnings.extend(ai_enrich_warnings)
        else:
            matched_listings = filter_listings(
                raw_listings,
                include_unassigned=include_unassigned,
            )
    else:
        matched_listings = filter_listings(
            raw_listings,
            include_unassigned=include_unassigned,
        )

    store = StateStore(args.state_file)
    diff_sig = inspect.signature(store.diff_and_update)
    if "mark_notified" in diff_sig.parameters:
        new_listings, price_drops = store.diff_and_update(
            matched_listings,
            mark_notified=False,
        )
    else:
        new_listings, price_drops = store.diff_and_update(matched_listings)

    is_digest_mode = args.mode in ("digest", "scan-and-notify")
    if is_digest_mode and hasattr(store, "get_pending_digest_items"):
        new_listings, price_drops = store.get_pending_digest_items()

    pruned_count = store.prune_old()

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
        "mode": args.mode,
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
        "warnings_count": len(warnings),
        "strict_districts_only": bool(args.strict_districts_only),
        "demo_fixture": bool(args.demo_fixture),
        "dry_run": bool(args.dry_run),
    }

    report_md = format_markdown_report(new_listings, price_drops, stats)

    history_dir = Path(args.history_dir)
    csv_path = history_dir / "listings_history.csv"
    run_log_path = history_dir / "runs_log.jsonl"

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

    if args.mode == "scan-only":
        if not args.dry_run:
            store.save()
            if hasattr(store, "export_history_csv"):
                store.export_history_csv(csv_path)
            if hasattr(store, "append_run_log"):
                store.append_run_log(stats, run_log_path)
            write_github_step_summary(report_md)
        return 0

    # Digest / scan-and-notify mode
    if not args.dry_run:
        if hasattr(store, "save_daily_snapshot"):
            store.save_daily_snapshot(report_md, history_dir)
        if hasattr(store, "export_history_csv"):
            store.export_history_csv(csv_path)
        if hasattr(store, "append_run_log"):
            store.append_run_log(stats, run_log_path)

    # Dispatch notifications
    notify_listings = new_listings
    sig = inspect.signature(dispatch_notifications)
    dispatch_kwargs: dict[str, object] = {"dry_run": bool(args.dry_run)}
    if "stats" in sig.parameters:
        dispatch_kwargs["stats"] = stats
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
                title="Warsaw ROD Scanner Notification Test (--force-notify)",
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

    if not args.dry_run:
        if hasattr(store, "mark_digest_sent"):
            store.mark_digest_sent(new_listings, price_drops)
        store.save()
        if hasattr(store, "export_history_csv"):
            store.export_history_csv(csv_path)

    return 0
