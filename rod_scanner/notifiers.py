"""Markdown report formatting and Email + GitHub notifications for ROD listings."""

from __future__ import annotations

from datetime import datetime, timezone
from email.message import EmailMessage
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
    """Format a numeric PLN price consistently, falling back to raw price text."""
    if price_pln is not None:
        rounded = int(round(price_pln))
        formatted_num = f"{rounded:,}".replace(",", " ")
        return f"{formatted_num} PLN"
    if price_raw:
        return price_raw.strip()
    return "cena nieznana"


def _format_unit_price(price_pln: Optional[float], area_m2: Optional[float]) -> Optional[str]:
    """Calculate and format PLN/m² when both price and positive area are present."""
    if price_pln is not None and area_m2 is not None and area_m2 > 0:
        unit = price_pln / area_m2
        return f"{unit:.0f} PLN/m²"
    return None


def _district_badge(district_match: str) -> str:
    """Return a prominent uppercase badge for the matched district."""
    norm = (district_match or "").strip().lower()
    if norm in ("mokotow", "mokotów"):
        return "[MOKOTÓW]"
    if norm == "ochota":
        return "[OCHOTA]"
    if norm == "warszawa_unassigned":
        return "[WARSZAWA]"
    return f"[{norm.upper() or 'UNKNOWN'}]"


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
    """Render a clean Markdown digest grouped into 4 sections."""
    stats = stats or {}
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

    lines: list[str] = [
        "# Raport Działek ROD — Warszawa (Mokotów & Ochota)",
        "",
        "## 1. Confirmed Mokotów & Ochota ROD Listings",
        "",
    ]

    if confirmed:
        for item in confirmed:
            lines.append(_format_listing_bullet(item, include_badge=True))
    else:
        lines.append("_Brak nowych potwierdzonych ogłoszeń dla dzielnic Mokotów i Ochota._")

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
        lines.append("_Brak obniżek cen dla wcześniej zaobserwowanych ogłoszeń._")

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
        lines.append("_Brak nieprzypisanych ogłoszeń z Warszawy._")

    # Section 4: Run statistics footer
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

    lines.extend(
        [
            "",
            "---",
            "### Run Statistics",
            "",
            f"- **Sources scanned:** {sources_str}",
            f"- **Total scanned:** {total_scanned}",
            f"- **New count:** {new_count} (Confirmed Mokotów/Ochota: {len(confirmed)}, Unassigned Warsaw: {len(unassigned)})",
            f"- **Price drops:** {price_drops_count}",
            f"- **Warnings:** {len(warnings_list)}",
        ]
    )

    for warning in warnings_list:
        lines.append(f"  - ⚠️ {warning}")

    return "\n".join(lines) + "\n"


def send_email(subject: str, report_text: str, smtp_config: dict[str, Any]) -> bool:
    """Send report via SMTP using standard library ``smtplib``."""
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
) -> dict[str, str]:
    """Check environment variables and dispatch Email and GitHub notifications."""
    results: dict[str, str] = {}
    should_alert = bool(new_listings or price_drops or force_notify)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subject = (
        f"[ROD Warszawa] {len(new_listings)} nowych ogłoszeń, "
        f"{len(price_drops)} obniżek cen ({date_str})"
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
            results["email"] = (
                "sent" if send_email(subject, report_md, smtp_config) else "failed"
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
