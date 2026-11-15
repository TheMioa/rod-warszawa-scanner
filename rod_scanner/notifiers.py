"""Markdown report formatting and Email + GitHub notifications for ROD listings."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
import html
import json
import os
from pathlib import Path
import smtplib
from typing import Any, Optional
import urllib.error
import urllib.request

from rod_scanner.models import Listing


USER_AGENT = "rod-warszawa-scanner/1.0 (+https://github.com)"


def _format_price_pln(price_pln: Optional[float], price_raw: Optional[str] = None) -> str:
    """Format a numeric PLN price consistently with commas, falling back to raw price text."""
    if price_pln is not None:
        rounded = int(round(price_pln))
        return f"{rounded:,} PLN"
    if price_raw:
        cleaned = price_raw.strip()
        if cleaned and cleaned.lower() not in ("cena nieznana", "brak ceny"):
            return cleaned
    return "Price not listed"


def _format_unit_price(price_pln: Optional[float], area_m2: Optional[float]) -> Optional[str]:
    """Calculate and format PLN/m² when both price and positive area are present."""
    if price_pln is not None and area_m2 is not None and area_m2 > 0:
        unit = price_pln / area_m2
        return f"{unit:,.0f} PLN/m²"
    return None


def _district_badge(district_match: str) -> str:
    """Return a prominent uppercase English badge for the matched district."""
    norm = (district_match or "").strip().lower()
    if norm in ("mokotow", "mokotów"):
        return "[MOKOTÓW]"
    if norm == "ochota":
        return "[OCHOTA]"
    if norm == "warszawa_unassigned":
        return "[WARSAW - VERIFY DISTRICT]"
    return f"[{norm.upper() or 'UNKNOWN'}]"


def _split_listings(
    new_listings: list[Listing],
) -> tuple[list[Listing], list[Listing]]:
    """Split listings into confirmed (Mokotów/Ochota) and unassigned Warsaw lists."""
    confirmed = [
        item
        for item in new_listings
        if (item.district_match or "").lower() in ("mokotow", "mokotów", "ochota")
    ]
    unassigned = [
        item
        for item in new_listings
        if (item.district_match or "").lower() == "warszawa_unassigned"
    ]
    return confirmed, unassigned


def _extract_stats_summary(
    new_listings: list[Listing],
    price_drops: list[tuple[Listing, float]],
    stats: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Normalize run statistics dictionary with sensible defaults."""
    stats = stats or {}
    sources_val = stats.get("sources_scanned") or stats.get("sources") or "all"
    if isinstance(sources_val, (list, tuple, set)):
        sources_str = ", ".join(str(s) for s in sources_val) if sources_val else "none"
    else:
        sources_str = str(sources_val)

    total_scanned = stats.get(
        "total_scanned",
        stats.get("total_raw", stats.get("total", len(new_listings))),
    )
    new_count = stats.get("new_count", len(new_listings))
    price_drops_count = stats.get("price_drops_count", len(price_drops))
    warnings_list = stats.get("warnings") or stats.get("errors") or []
    if isinstance(warnings_list, str):
        warnings_list = [warnings_list]

    return {
        "sources_str": sources_str,
        "total_scanned": total_scanned,
        "new_count": new_count,
        "price_drops_count": price_drops_count,
        "warnings_list": list(warnings_list),
    }


def _format_listing_bullet(listing: Listing, include_badge: bool = True) -> str:
    """Format a single Listing into a rich Markdown bullet line."""
    badge = f"**{_district_badge(listing.district_match)}** " if include_badge else ""
    garden = f"**{listing.matched_garden}** — " if listing.matched_garden else ""
    price_str = _format_price_pln(listing.price_pln, listing.price_raw)

    details: list[str] = [price_str]
    if listing.area_m2 is not None and listing.area_m2 > 0:
        area_str = f"{listing.area_m2:g} m²"
        unit_str = _format_unit_price(listing.price_pln, listing.area_m2)
        if unit_str:
            details.append(f"{area_str} ({unit_str})")
        else:
            details.append(area_str)
    elif _format_unit_price(listing.price_pln, listing.area_m2):
        details.append(_format_unit_price(listing.price_pln, listing.area_m2) or "")

    details.append(f"source: `{listing.source}`")
    if listing.location_raw:
        details.append(f"loc: {listing.location_raw}")

    meta_str = " | ".join(details)
    line = f"- {badge}{garden}[{listing.title}]({listing.url}) — {meta_str}"
    if listing.match_reason:
        line += f" _(match: {listing.match_reason})_"
    return line


def format_markdown_report(
    new_listings: list[Listing],
    price_drops: list[tuple[Listing, float]],
    stats: Optional[dict[str, Any]] = None,
) -> str:
    """Render a clean English Markdown digest grouped into 4 sections."""
    confirmed, unassigned = _split_listings(new_listings)
    summary = _extract_stats_summary(new_listings, price_drops, stats)

    lines: list[str] = [
        "# Warsaw ROD Allotment Report — Mokotów & Ochota",
        "",
        "## 1. Confirmed Mokotów & Ochota ROD Listings",
        "",
    ]

    if confirmed:
        for item in confirmed:
            lines.append(_format_listing_bullet(item, include_badge=True))
    else:
        lines.append("_No new confirmed listings for Mokotów or Ochota._")

    lines.extend(
        [
            "",
            "## 2. Price Drops on Previously Seen Listings",
            "",
        ]
    )

    if price_drops:
        for item, old_price in price_drops:
            new_price = item.price_pln if item.price_pln is not None else old_price
            drop_amount = old_price - new_price
            pct_drop = (drop_amount / old_price * 100.0) if old_price > 0 else 0.0
            old_str = _format_price_pln(old_price)
            new_str = _format_price_pln(new_price, item.price_raw)
            badge = _district_badge(item.district_match)
            garden = f" ({item.matched_garden})" if item.matched_garden else ""
            unit_str = _format_unit_price(item.price_pln, item.area_m2)
            unit_part = f" | {unit_str}" if unit_str else ""
            lines.append(
                f"- **{badge}**{garden} [{item.title}]({item.url}) — "
                f"**{old_str} -> {new_str} (-{pct_drop:.1f}%)**{unit_part} | source: `{item.source}`"
            )
    else:
        lines.append("_No price drops on previously seen listings._")

    lines.extend(
        [
            "",
            "## 3. Unassigned Warsaw Listings (Verify if Mokotów/Ochota)",
            "",
        ]
    )

    if unassigned:
        for item in unassigned:
            lines.append(_format_listing_bullet(item, include_badge=True))
    else:
        lines.append("_No unassigned Warsaw listings._")

    lines.extend(
        [
            "",
            "---",
            "### Run Statistics",
            "",
            f"- **Sources scanned:** {summary['sources_str']}",
            f"- **Total scanned:** {summary['total_scanned']}",
            f"- **New count:** {summary['new_count']} (Confirmed Mokotów/Ochota: {len(confirmed)}, Unassigned Warsaw: {len(unassigned)})",
            f"- **Price drops:** {summary['price_drops_count']}",
            f"- **Warnings:** {len(summary['warnings_list'])}",
        ]
    )

    for warning in summary["warnings_list"]:
        lines.append(f"  - ⚠️ {warning}")

    return "\n".join(lines) + "\n"


def _format_plain_listing(item: Listing) -> list[str]:
    """Format a single listing for plain-text email without raw Markdown characters."""
    badge = _district_badge(item.district_match)
    garden = f"{item.matched_garden} — " if item.matched_garden else ""
    price_str = _format_price_pln(item.price_pln, item.price_raw)

    meta_parts: list[str] = [f"Price: {price_str}"]
    if item.area_m2 is not None and item.area_m2 > 0:
        meta_parts.append(f"Area: {item.area_m2:g} m²")
    unit_str = _format_unit_price(item.price_pln, item.area_m2)
    if unit_str:
        meta_parts.append(f"Unit Price: {unit_str}")
    if item.source:
        meta_parts.append(f"Source: {item.source}")
    if item.location_raw:
        meta_parts.append(f"Location: {item.location_raw}")

    item_lines = [
        f"  • {badge} {garden}{item.title}",
        f"    {' | '.join(meta_parts)}",
    ]
    if item.match_reason:
        item_lines.append(f"    Match: {item.match_reason}")
    item_lines.append(f"    Link: {item.url}")
    return item_lines


def format_plain_email(
    new_listings: list[Listing],
    price_drops: list[tuple[Listing, float]],
    stats: Optional[dict[str, Any]] = None,
) -> str:
    """Render a clean, readable plain-text email with zero raw Markdown syntax."""
    confirmed, unassigned = _split_listings(new_listings)
    summary = _extract_stats_summary(new_listings, price_drops, stats)

    lines: list[str] = [
        "WARSAW ROD ALLOTMENT SCANNER",
        "Daily Digest — Mokotów & Ochota",
        "=" * 64,
        (
            f"Summary: {len(confirmed)} confirmed (Mokotów/Ochota) | "
            f"{len(price_drops)} price drops | "
            f"{len(unassigned)} unassigned Warsaw | "
            f"{summary['total_scanned']} total scanned"
        ),
        "",
        "1. CONFIRMED MOKOTÓW & OCHOTA ROD LISTINGS",
        "-" * 64,
    ]

    if confirmed:
        for idx, item in enumerate(confirmed):
            if idx > 0:
                lines.append("")
            lines.extend(_format_plain_listing(item))
    else:
        lines.append("  No new confirmed listings for Mokotów or Ochota.")

    lines.extend(
        [
            "",
            "2. PRICE DROPS ON PREVIOUSLY SEEN LISTINGS",
            "-" * 64,
        ]
    )

    if price_drops:
        for idx, (item, old_price) in enumerate(price_drops):
            if idx > 0:
                lines.append("")
            new_price = item.price_pln if item.price_pln is not None else old_price
            drop_amount = old_price - new_price
            pct_drop = (drop_amount / old_price * 100.0) if old_price > 0 else 0.0
            old_str = _format_price_pln(old_price)
            new_str = _format_price_pln(new_price, item.price_raw)
            badge = _district_badge(item.district_match)
            garden = f"{item.matched_garden} — " if item.matched_garden else ""
            meta_parts = [f"Price Drop: {old_str} -> {new_str} (-{pct_drop:.1f}%)"]
            if item.area_m2 is not None and item.area_m2 > 0:
                meta_parts.append(f"Area: {item.area_m2:g} m²")
            unit_str = _format_unit_price(item.price_pln, item.area_m2)
            if unit_str:
                meta_parts.append(f"Unit Price: {unit_str}")
            if item.source:
                meta_parts.append(f"Source: {item.source}")
            lines.append(f"  • {badge} {garden}{item.title}")
            lines.append(f"    {' | '.join(meta_parts)}")
            lines.append(f"    Link: {item.url}")
    else:
        lines.append("  No price drops on previously seen listings.")

    lines.extend(
        [
            "",
            "3. UNASSIGNED WARSAW LISTINGS (VERIFY IF MOKOTÓW/OCHOTA)",
            "-" * 64,
        ]
    )

    if unassigned:
        for idx, item in enumerate(unassigned):
            if idx > 0:
                lines.append("")
            lines.extend(_format_plain_listing(item))
    else:
        lines.append("  No unassigned Warsaw listings.")

    lines.extend(
        [
            "",
            "RUN STATISTICS",
            "-" * 64,
            f"  Sources scanned: {summary['sources_str']}",
            f"  Total scanned:   {summary['total_scanned']}",
            f"  New listings:    {summary['new_count']} (Confirmed: {len(confirmed)}, Unassigned Warsaw: {len(unassigned)})",
            f"  Price drops:     {summary['price_drops_count']}",
            f"  Warnings:        {len(summary['warnings_list'])}",
        ]
    )

    for warning in summary["warnings_list"]:
        lines.append(f"    - Warning: {warning}")

    return "\n".join(lines) + "\n"


def _html_badge_pill(district_match: str) -> str:
    """Return an inline-styled HTML pill badge for the listing district."""
    norm = (district_match or "").strip().lower()
    if norm in ("mokotow", "mokotów"):
        bg, fg, label = "#dcfce7", "#166534", "MOKOTÓW"
    elif norm == "ochota":
        bg, fg, label = "#dbeafe", "#1e40af", "OCHOTA"
    elif norm == "warszawa_unassigned":
        bg, fg, label = "#fef3c7", "#92400e", "WARSAW - VERIFY"
    else:
        bg, fg, label = "#f3f4f6", "#374151", html.escape(norm.upper() or "UNKNOWN")

    return (
        f'<span style="display: inline-block; background: {bg}; color: {fg}; '
        f'font-size: 11px; font-weight: 700; padding: 3px 8px; border-radius: 9999px; '
        f'letter-spacing: 0.03em; text-transform: uppercase; vertical-align: middle;">'
        f"{label}</span>"
    )


def _html_listing_card(item: Listing) -> str:
    """Render a single listing card with inline CSS for Gmail desktop & mobile."""
    badge_html = _html_badge_pill(item.district_match)
    garden_html = (
        f' <strong style="color: #111827; font-size: 13px; margin-left: 6px; vertical-align: middle;">'
        f"{html.escape(item.matched_garden)}</strong>"
        if item.matched_garden
        else ""
    )
    safe_url = html.escape(item.url or "#", quote=True)
    safe_title = html.escape(item.title or "Untitled listing")
    price_str = html.escape(_format_price_pln(item.price_pln, item.price_raw))

    meta_pills: list[str] = [
        f'<span style="display: inline-block; background: #f0fdf4; color: #166534; '
        f'font-weight: 700; font-size: 13px; padding: 3px 8px; border-radius: 6px; '
        f'margin-right: 6px; margin-top: 4px;">Price: {price_str}</span>'
    ]
    if item.area_m2 is not None and item.area_m2 > 0:
        area_str = html.escape(f"{item.area_m2:g} m²")
        meta_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #374151; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Area: <strong>{area_str}</strong></span>"
        )
    unit_str = _format_unit_price(item.price_pln, item.area_m2)
    if unit_str:
        meta_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #374151; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Unit Price: <strong>{html.escape(unit_str)}</strong></span>"
        )
    if item.source:
        meta_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #4b5563; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Source: <strong>{html.escape(item.source)}</strong></span>"
        )
    if item.location_raw:
        meta_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #4b5563; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Location: {html.escape(item.location_raw)}</span>"
        )

    reason_html = (
        f'<div style="color: #6b7280; font-size: 12px; margin-top: 6px;">'
        f"Match reason: {html.escape(item.match_reason)}</div>"
        if item.match_reason
        else ""
    )

    return (
        '<div style="border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px; '
        'margin-bottom: 12px; background: #ffffff;">'
        f'<div style="margin-bottom: 6px;">{badge_html}{garden_html}</div>'
        f'<div style="margin-bottom: 8px;">'
        f'<a href="{safe_url}" style="color: #0f766e; font-size: 16px; font-weight: 600; '
        f'text-decoration: none;">{safe_title}</a>'
        f"</div>"
        f'<div style="line-height: 1.6;">{"".join(meta_pills)}</div>'
        f"{reason_html}"
        "</div>"
    )


def _html_price_drop_card(item: Listing, old_price: float) -> str:
    """Render a price drop card with struck-through old price and green new price."""
    badge_html = _html_badge_pill(item.district_match)
    garden_html = (
        f' <strong style="color: #111827; font-size: 13px; margin-left: 6px; vertical-align: middle;">'
        f"{html.escape(item.matched_garden)}</strong>"
        if item.matched_garden
        else ""
    )
    safe_url = html.escape(item.url or "#", quote=True)
    safe_title = html.escape(item.title or "Untitled listing")

    new_price = item.price_pln if item.price_pln is not None else old_price
    drop_amount = old_price - new_price
    pct_drop = (drop_amount / old_price * 100.0) if old_price > 0 else 0.0
    old_str = html.escape(_format_price_pln(old_price))
    new_str = html.escape(_format_price_pln(new_price, item.price_raw))

    extra_pills: list[str] = []
    if item.area_m2 is not None and item.area_m2 > 0:
        extra_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #374151; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Area: <strong>{html.escape(f'{item.area_m2:g} m²')}</strong></span>"
        )
    unit_str = _format_unit_price(item.price_pln, item.area_m2)
    if unit_str:
        extra_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #374151; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Unit Price: <strong>{html.escape(unit_str)}</strong></span>"
        )
    if item.source:
        extra_pills.append(
            f'<span style="display: inline-block; background: #f3f4f6; color: #4b5563; '
            f'font-size: 12px; padding: 3px 8px; border-radius: 6px; margin-right: 6px; margin-top: 4px;">'
            f"Source: <strong>{html.escape(item.source)}</strong></span>"
        )

    return (
        '<div style="border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px 16px; '
        'margin-bottom: 12px; background: #ffffff;">'
        f'<div style="margin-bottom: 6px;">{badge_html}{garden_html}</div>'
        f'<div style="margin-bottom: 8px;">'
        f'<a href="{safe_url}" style="color: #0f766e; font-size: 16px; font-weight: 600; '
        f'text-decoration: none;">{safe_title}</a>'
        f"</div>"
        '<div style="line-height: 1.6;">'
        f'<span style="display: inline-block; background: #f0fdf4; padding: 4px 10px; '
        f'border-radius: 6px; margin-right: 6px; margin-top: 4px; font-size: 13px;">'
        f'<del style="color: #6b7280;">{old_str}</del> &rarr; '
        f'<strong style="color: #166534;">{new_str}</strong>'
        f"</span>"
        f'<span style="display: inline-block; background: #dcfce7; color: #166534; '
        f'font-weight: 700; font-size: 12px; padding: 3px 8px; border-radius: 9999px; '
        f'margin-right: 6px; margin-top: 4px;">-{pct_drop:.1f}%</span>'
        f'{"".join(extra_pills)}'
        "</div>"
        "</div>"
    )


def format_html_email(
    new_listings: list[Listing],
    price_drops: list[tuple[Listing, float]],
    stats: Optional[dict[str, Any]] = None,
) -> str:
    """Generate a self-contained HTML email with inline CSS for Gmail desktop and mobile."""
    confirmed, unassigned = _split_listings(new_listings)
    summary = _extract_stats_summary(new_listings, price_drops, stats)

    gh_repo = os.environ.get("GITHUB_REPOSITORY", "").strip().strip("/")
    if gh_repo:
        history_url = f"https://github.com/{gh_repo}/blob/main/data/history/listings_history.csv"
    else:
        history_url = "https://github.com/TheMioa/rod-warszawa-scanner/blob/main/data/history/listings_history.csv"

    if confirmed:
        confirmed_html = "".join(_html_listing_card(item) for item in confirmed)
    else:
        confirmed_html = (
            '<div style="border: 1px dashed #d1d5db; border-radius: 8px; padding: 14px 16px; '
            'color: #6b7280; font-size: 14px; background: #f9fafb;">'
            "No new confirmed listings for Mokotów or Ochota."
            "</div>"
        )

    if price_drops:
        drops_html = "".join(_html_price_drop_card(item, old) for item, old in price_drops)
    else:
        drops_html = (
            '<div style="border: 1px dashed #d1d5db; border-radius: 8px; padding: 14px 16px; '
            'color: #6b7280; font-size: 14px; background: #f9fafb;">'
            "No price drops on previously seen listings."
            "</div>"
        )

    if unassigned:
        unassigned_html = "".join(_html_listing_card(item) for item in unassigned)
    else:
        unassigned_html = (
            '<div style="border: 1px dashed #d1d5db; border-radius: 8px; padding: 14px 16px; '
            'color: #6b7280; font-size: 14px; background: #f9fafb;">'
            "No unassigned Warsaw listings."
            "</div>"
        )

    warnings_html = ""
    if summary["warnings_list"]:
        warning_items = "".join(
            f'<li style="margin-bottom: 4px;">{html.escape(str(w))}</li>'
            for w in summary["warnings_list"]
        )
        warnings_html = (
            '<div style="margin-top: 8px; color: #92400e; font-size: 12px;">'
            "<strong>Warnings:</strong>"
            f'<ul style="margin: 4px 0 0 18px; padding: 0;">{warning_items}</ul>'
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Warsaw ROD Allotment Scanner — Daily Digest</title>
</head>
<body style="margin: 0; padding: 16px; background-color: #f3f4f6;">
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; color: #1f2937; max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e5e7eb;">
  <div style="background: #14532d; color: #ffffff; padding: 20px 24px; border-radius: 8px 8px 0 0;">
    <h1 style="margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.01em;">Warsaw ROD Allotment Scanner</h1>
    <p style="margin: 4px 0 0 0; font-size: 14px; color: #bbf7d0;">Daily Digest — Mokotów &amp; Ochota</p>
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse: collapse; background: #f8fafc; border-bottom: 1px solid #e5e7eb;">
    <tr>
      <td width="25%" align="center" style="padding: 12px 8px; border-right: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #166534;">{len(confirmed)}</div>
        <div style="font-size: 11px; color: #4b5563; font-weight: 600;">Confirmed (Mokotów/Ochota)</div>
      </td>
      <td width="25%" align="center" style="padding: 12px 8px; border-right: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #0f766e;">{len(price_drops)}</div>
        <div style="font-size: 11px; color: #4b5563; font-weight: 600;">Price Drops</div>
      </td>
      <td width="25%" align="center" style="padding: 12px 8px; border-right: 1px solid #e5e7eb;">
        <div style="font-size: 20px; font-weight: 700; color: #92400e;">{len(unassigned)}</div>
        <div style="font-size: 11px; color: #4b5563; font-weight: 600;">Unassigned Warsaw</div>
      </td>
      <td width="25%" align="center" style="padding: 12px 8px;">
        <div style="font-size: 20px; font-weight: 700; color: #1f2937;">{html.escape(str(summary['total_scanned']))}</div>
        <div style="font-size: 11px; color: #4b5563; font-weight: 600;">Total Scanned</div>
      </td>
    </tr>
  </table>

  <div style="padding: 20px 24px;">
    <h2 style="margin: 0 0 12px 0; font-size: 16px; font-weight: 700; color: #111827; border-bottom: 2px solid #dcfce7; padding-bottom: 6px;">
      1. Confirmed Mokotów &amp; Ochota ROD Listings
    </h2>
    {confirmed_html}

    <h2 style="margin: 24px 0 12px 0; font-size: 16px; font-weight: 700; color: #111827; border-bottom: 2px solid #ccfbf1; padding-bottom: 6px;">
      2. Price Drops on Previously Seen Listings
    </h2>
    {drops_html}

    <h2 style="margin: 24px 0 12px 0; font-size: 16px; font-weight: 700; color: #111827; border-bottom: 2px solid #fef3c7; padding-bottom: 6px;">
      3. Unassigned Warsaw Listings — Verify District
    </h2>
    {unassigned_html}
  </div>

  <div style="background: #f9fafb; border-top: 1px solid #e5e7eb; padding: 16px 24px; font-size: 12px; color: #4b5563; line-height: 1.6;">
    <div style="font-weight: 700; color: #1f2937; margin-bottom: 4px;">Run Statistics</div>
    <div>
      <strong>Sources scanned:</strong> {html.escape(summary['sources_str'])} &bull;
      <strong>Total scanned:</strong> {html.escape(str(summary['total_scanned']))} &bull;
      <strong>New listings:</strong> {html.escape(str(summary['new_count']))} &bull;
      <strong>Price drops:</strong> {html.escape(str(summary['price_drops_count']))} &bull;
      <strong>Warnings:</strong> {len(summary['warnings_list'])}
    </div>
    {warnings_html}
    <div style="margin-top: 8px;">
      Historical archive:
      <a href="{html.escape(history_url, quote=True)}" style="color: #0f766e; font-weight: 600; text-decoration: none;">data/history/listings_history.csv</a>
    </div>
  </div>
</div>
</body>
</html>
"""


def send_email(
    subject: str,
    report_text: str,
    smtp_config: dict[str, Any],
    html_body: Optional[str] = None,
) -> bool:
    """Send report via SMTP using standard library ``smtplib`` with optional HTML alternative."""
    if not smtp_config:
        return False

    host = (
        smtp_config.get("host")
        or smtp_config.get("smtp_host")
        or smtp_config.get("SMTP_HOST")
        or ""
    ).strip()
    to_addr = (
        smtp_config.get("to")
        or smtp_config.get("email_to")
        or smtp_config.get("notify_email_to")
        or smtp_config.get("NOTIFY_EMAIL_TO")
        or ""
    )
    if isinstance(to_addr, (list, tuple)):
        to_addr = ", ".join(str(a) for a in to_addr)
    to_addr = str(to_addr).strip()

    if not host or not to_addr:
        return False

    raw_port = (
        smtp_config.get("port")
        or smtp_config.get("smtp_port")
        or smtp_config.get("SMTP_PORT")
        or 587
    )
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = 587

    user = (
        smtp_config.get("user")
        or smtp_config.get("username")
        or smtp_config.get("smtp_user")
        or smtp_config.get("SMTP_USER")
        or ""
    ).strip()
    password = (
        smtp_config.get("password")
        or smtp_config.get("smtp_password")
        or smtp_config.get("SMTP_PASSWORD")
        or ""
    )
    from_addr = (
        smtp_config.get("from")
        or smtp_config.get("email_from")
        or smtp_config.get("notify_email_from")
        or smtp_config.get("NOTIFY_EMAIL_FROM")
        or user
        or "rod-scanner@localhost"
    ).strip()
    use_tls = smtp_config.get("use_tls", port != 465)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(report_text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                if use_tls:
                    server.starttls()
                if user and password:
                    server.login(user, password)
                server.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError):
        return False


def send_github_issue(title: str, report_md: str, repo: str, token: str) -> bool:
    """Create a new GitHub Issue labeled ``rod-alert`` via GitHub REST API."""
    if not repo or not token:
        return False

    clean_repo = repo.strip().strip("/")
    url = f"https://api.github.com/repos/{clean_repo}/issues"
    payload = json.dumps(
        {
            "title": title,
            "body": report_md,
            "labels": ["rod-alert"],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = getattr(resp, "status", resp.getcode())
            return 200 <= int(status) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def write_github_step_summary(report_md: str) -> bool:
    """Append ``report_md`` to ``$GITHUB_STEP_SUMMARY`` if set in environment."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return False

    try:
        path = Path(summary_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(report_md)
            if not report_md.endswith("\n"):
                fh.write("\n")
        return True
    except OSError:
        return False


def dispatch_notifications(
    new_listings: list[Listing],
    price_drops: list[tuple[Listing, float]],
    report_md: str,
    dry_run: bool = False,
    force_notify: bool = False,
    stats: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """Check environment variables and dispatch Email and GitHub notifications."""
    results: dict[str, str] = {}
    should_alert = bool(new_listings or price_drops or force_notify)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = (
        f"[Warsaw ROD Scanner] Daily Digest: {len(new_listings)} new listings, "
        f"{len(price_drops)} price drops ({date_str})"
    )

    # 1. Email (SMTP)
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    email_to = os.environ.get("NOTIFY_EMAIL_TO", "").strip()
    if smtp_host and email_to:
        if not should_alert:
            results["email"] = "skipped_no_changes"
        elif dry_run:
            results["email"] = "dry_run"
        else:
            smtp_config = {
                "host": smtp_host,
                "port": os.environ.get("SMTP_PORT", "587").strip() or "587",
                "user": os.environ.get("SMTP_USER", "").strip(),
                "password": os.environ.get("SMTP_PASSWORD", ""),
                "to": email_to,
                "from": os.environ.get("NOTIFY_EMAIL_FROM", "").strip(),
            }
            plain_body = format_plain_email(new_listings, price_drops, stats)
            html_body = format_html_email(new_listings, price_drops, stats)
            results["email"] = (
                "sent"
                if send_email(subject, plain_body, smtp_config, html_body=html_body)
                else "failed"
            )

    # 2. GitHub Issue Alerts
    gh_alerts = os.environ.get("GITHUB_ISSUE_ALERTS", "").strip().lower() == "true"
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    gh_repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if gh_alerts and gh_token and gh_repo:
        if not should_alert:
            results["github_issue"] = "skipped_no_changes"
        elif dry_run:
            results["github_issue"] = "dry_run"
        else:
            results["github_issue"] = (
                "sent"
                if send_github_issue(subject, report_md, gh_repo, gh_token)
                else "failed"
            )

    # 3. GitHub Step Summary (always written when GITHUB_STEP_SUMMARY is set)
    if os.environ.get("GITHUB_STEP_SUMMARY", "").strip():
        if dry_run:
            results["github_step_summary"] = "dry_run"
        else:
            results["github_step_summary"] = (
                "written" if write_github_step_summary(report_md) else "failed"
            )

    return results
