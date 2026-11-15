"""Scrapers for PZD Okręg Mazowiecki and individual Mokotów/Ochota ROD garden websites."""

from __future__ import annotations

import html as html_lib
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from rod_scanner.models import Listing
from rod_scanner.scrapers.portals import (
    fetch_url_text,
    make_listing_id,
    parse_area_m2,
    parse_price_pln,
    strip_html_tags,
)

# Strict keywords indicating actual vacant plots, plot sales, transfers, tenders, or new garden allocations
PZD_RELEVANCE_KEYWORDS: tuple[str, ...] = (
    "wolna działka",
    "wolna dzialka",
    "wolne działki",
    "wolne dzialki",
    "wolnych działek",
    "wolnych dzialek",
    "wolnych działkach",
    "wolnych dzialkach",
    "wykaz wolnych",
    "sprzedam działk",
    "sprzedam dzialk",
    "sprzedaż działk",
    "sprzedaz dzialk",
    "na sprzedaż",
    "na sprzedaz",
    "odstąpię działk",
    "odstapie dzialk",
    "odstąpię prawo",
    "odstapie prawo",
    "przetarg na",
    "nabór na",
    "nabor na",
    "przydział działk",
    "przydzial dzialk",
    "nowy ogród",
    "nowy ogrod",
    "nowe działki",
    "nowe dzialki",
)

_DOCUMENT_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".rtf",
)

_DOCUMENT_URL_PATHS: tuple[str, ...] = (
    "/wp-content/uploads/",
    "/dokumenty/",
    "/pliki/",
    "/wzory/",
    "/prawo/",
    "/ustawa/",
    "/statut/",
    "/regulamin/",
)

_EXPLICIT_PLOT_OFFER_PHRASES: tuple[str, ...] = (
    "wolna działka",
    "wolna dzialka",
    "wolne działki",
    "wolne dzialki",
    "wolnych działek",
    "wolnych dzialek",
    "wolnych działkach",
    "wolnych dzialkach",
    "wykaz wolnych",
    "sprzedam działk",
    "sprzedam dzialk",
    "odstąpię działk",
    "odstapie dzialk",
    "odstąpię prawo",
    "odstapie prawo",
)

_ADMIN_OR_DOCUMENT_TITLE_KEYWORDS: tuple[str, ...] = (
    "ustawa",
    "statut",
    "regulamin",
    "uchwała",
    "uchwala",
    "zarządzenie",
    "zarzadzenie",
    "wzór",
    "wzor",
    "wniosek",
    "deklaracja",
    "umowa",
    "opłaty",
    "oplaty",
    "składki",
    "skladki",
    "walne zebranie",
    "zebranie",
    "komunikat dotyczący",
    "komunikat dotyczacy",
    "wod",
    "prąd",
    "prad",
    "śmieci",
    "smieci",
    "odpad",
    "harmonogram",
    "dyżur",
    "dyzur",
    "szkoleni",
    "konkurs",
    "instrukcj",
    "zasady",
    "informacja o",
    "poradnik",
    "jak kupić",
    "jak kupic",
    "jak nabyć",
    "jak nabyc",
)


_UTILITY_NOTICE_KEYWORDS: tuple[str, ...] = (
    "wod",
    "prąd",
    "prad",
)


def _is_document_or_admin_notice(title: str, url: str) -> bool:
    """Return True if the URL or title indicates a static document, bylaw, form template, or admin notice."""
    raw_url = (url or "").strip().lower()
    if raw_url:
        parsed_path = urllib.parse.urlparse(raw_url).path.lower()
        if any(parsed_path.endswith(ext) for ext in _DOCUMENT_EXTENSIONS):
            return True
        if any(seg in raw_url for seg in _DOCUMENT_URL_PATHS):
            return True

    low_title = (title or "").strip().lower()
    if not low_title:
        return False

    if any(phrase in low_title for phrase in _EXPLICIT_PLOT_OFFER_PHRASES):
        return False

    for kw in _ADMIN_OR_DOCUMENT_TITLE_KEYWORDS:
        if kw in _UTILITY_NOTICE_KEYWORDS:
            # Allow classified plot titles mentioning utilities alongside area/price (e.g. "Działka ROD 400 m² z prądem")
            has_plot_and_size = (
                ("działk" in low_title or "dzialk" in low_title or "allotment" in low_title)
                and bool(re.search(r"\b\d{2,4}\s*m(?:2|²)\b", low_title))
            )
            if has_plot_and_size:
                continue
        if kw in low_title:
            return True

    return False

# Navigation/boilerplate link titles to ignore when scanning general news pages
IGNORED_LINK_TITLES: frozenset[str] = frozenset(
    {
        "strona główna",
        "kontakt",
        "o nas",
        "aktualności",
        "komunikaty",
        "galeria",
        "statut",
        "regulamin",
        "zarząd",
        "opłaty",
        "czytaj więcej",
        "więcej",
        "dalej",
        "logowanie",
        "polityka prywatności",
        "rodo",
        "mapa strony",
    }
)


@dataclass(frozen=True)
class GardenTarget:
    """Configuration for an individual ROD garden website in Mokotów or Ochota."""

    name: str
    district: str  # e.g. "Warszawa, Mokotów" or "Warszawa, Ochota"
    url: str
    street_hint: str = ""

    @property
    def location_raw(self) -> str:
        parts = [self.district, self.name]
        if self.street_hint:
            parts.append(self.street_hint)
        return ", ".join(p for p in parts if p)


DEFAULT_GARDEN_TARGETS: tuple[GardenTarget, ...] = (
    GardenTarget(
        name="ROD im. Obrońców Pokoju",
        district="Warszawa, Mokotów",
        url="https://rodobroncowpokoju.pl/",
        street_hint="ul. Odyńca / al. Niepodległości",
    ),
    GardenTarget(
        name="ROD Rakowiec",
        district="Warszawa, Mokotów",
        url="https://rodrakowiec.pl/",
        street_hint="ul. Rostafińskich, Pole Mokotowskie",
    ),
    GardenTarget(
        name="ROD Fort Szczęśliwice",
        district="Warszawa, Ochota",
        url="https://rodfortszczesliwice.pl/",
        street_hint="ul. Drawska / Szczęśliwice",
    ),
    GardenTarget(
        name="ROD im. Żwirki i Wigury",
        district="Warszawa, Ochota",
        url="https://rodzwirkiiwigury.pl/",
        street_hint="ul. Żwirki i Wigury / Księcia Trojdena",
    ),
    GardenTarget(
        name="ROD Czerniaków",
        district="Warszawa, Mokotów",
        url="https://rodczerniakow.pl/",
        street_hint="ul. Bernardyńska / Czerniakowska",
    ),
    GardenTarget(
        name="ROD Siekierki",
        district="Warszawa, Mokotów",
        url="https://rodsiekierki.pl/",
        street_hint="ul. Bartycka / Bluszczańska",
    ),
)


def _matches_pzd_keywords(text: str) -> bool:
    low = text.lower()
    return any(kw in low for kw in PZD_RELEVANCE_KEYWORDS)


def _extract_articles_and_links(
    html: str,
    base_url: str,
    source: str,
    default_location: str,
) -> list[Listing]:
    """Extract relevant announcements from WordPress/CMS articles, RSS items, or news lists."""
    by_id: dict[str, Listing] = {}

    # 1. Check RSS <item> blocks if the page is an XML feed
    item_pattern = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)
    for m in item_pattern.finditer(html):
        block = m.group(1)
        title_m = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.I | re.S)
        link_m = re.search(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", block, re.I | re.S)
        desc_m = re.search(
            r"<description[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>", block, re.I | re.S
        )
        pub_m = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", block, re.I | re.S)
        title = strip_html_tags(title_m.group(1)) if title_m else ""
        link = strip_html_tags(link_m.group(1)) if link_m else ""
        desc = strip_html_tags(desc_m.group(1)) if desc_m else ""
        pub = strip_html_tags(pub_m.group(1)) if pub_m else None
        if not title or not link:
            continue
        full_url = urllib.parse.urljoin(base_url, link)
        if _is_document_or_admin_notice(title, full_url):
            continue
        combined = f"{title} {desc}"
        if not _matches_pzd_keywords(combined):
            continue
        listing_id = make_listing_id(source, full_url)
        by_id[listing_id] = Listing(
            id=listing_id,
            source=source,
            title=title,
            url=full_url,
            price_pln=parse_price_pln(combined),
            area_m2=parse_area_m2(combined),
            location_raw=default_location,
            description_snippet=desc[:300],
            published_at=pub,
        )

    # 2. Check HTML <article> or news/announcement containers
    container_pattern = re.compile(
        r"<article\b[^>]*>(.*?)</article>|"
        r"<div\b[^>]*class=[\"'][^\"']*(?:post|news|komunikat|ogloszen|entry|announcement|article-item)[^\"']*[\"'][^>]*>(.*?)</div>",
        re.IGNORECASE | re.DOTALL,
    )
    for m in container_pattern.finditer(html):
        block = m.group(1) or m.group(2) or ""
        # Find primary heading with a link, or any heading + first link
        heading_m = re.search(r"<h[1-5]\b[^>]*>(.*?)</h[1-5]>", block, re.I | re.DOTALL)
        href_m = None
        if heading_m:
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", heading_m.group(1), re.I)
        if not href_m:
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", block, re.I)
        if not href_m:
            continue

        raw_href = html_lib.unescape(href_m.group(1)).strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urllib.parse.urljoin(base_url, raw_href)

        title = strip_html_tags(heading_m.group(1)) if heading_m else ""
        if not title:
            link_text_m = re.search(
                r"<a\b[^>]*href=[\"'][^\"']+[\"'][^>]*>(.*?)</a>", block, re.I | re.DOTALL
            )
            title = strip_html_tags(link_text_m.group(1)) if link_text_m else ""
        if not title or title.lower() in IGNORED_LINK_TITLES:
            continue
        if _is_document_or_admin_notice(title, full_url):
            continue

        block_text = strip_html_tags(block)
        combined = f"{title} {block_text}"
        if not _matches_pzd_keywords(combined):
            continue

        date_m = re.search(
            r"<time\b[^>]*datetime=[\"']([^\"']+)[\"'][^>]*>|(\b\d{4}-\d{2}-\d{2}\b|\b\d{2}\.\d{2}\.\d{4}\b)",
            block,
            re.I,
        )
        published_at = (date_m.group(1) or date_m.group(2)) if date_m else None
        price_pln = parse_price_pln(combined)
        area_m2 = parse_area_m2(combined)
        listing_id = make_listing_id(source, full_url)

        if listing_id not in by_id:
            by_id[listing_id] = Listing(
                id=listing_id,
                source=source,
                title=title,
                url=full_url,
                price_pln=price_pln,
                price_raw=f"{int(price_pln)} zł" if price_pln else None,
                area_m2=area_m2,
                location_raw=default_location,
                description_snippet=block_text[:300],
                published_at=published_at,
            )

    # 3. Fallback: scan standalone links `<a>` or `<li>` items if none found above
    if not by_id:
        link_pattern = re.compile(
            r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            re.IGNORECASE | re.DOTALL,
        )
        for m in link_pattern.finditer(html):
            raw_href = html_lib.unescape(m.group(1)).strip()
            title = strip_html_tags(m.group(2))
            if (
                not raw_href
                or raw_href.startswith(("#", "javascript:", "mailto:", "tel:"))
                or len(title) < 10
                or title.lower() in IGNORED_LINK_TITLES
            ):
                continue
            full_url = urllib.parse.urljoin(base_url, raw_href)
            if _is_document_or_admin_notice(title, full_url):
                continue
            if not _matches_pzd_keywords(title):
                continue
            listing_id = make_listing_id(source, full_url)
            if listing_id not in by_id:
                by_id[listing_id] = Listing(
                    id=listing_id,
                    source=source,
                    title=title,
                    url=full_url,
                    price_pln=parse_price_pln(title),
                    area_m2=parse_area_m2(title),
                    location_raw=default_location,
                    description_snippet=title,
                )

    return list(by_id.values())


class PZDMazowieckiScraper:
    """Scraper for PZD Okręg Mazowiecki announcements and plot availability notices."""

    source = "pzd_mazowiecki"
    base_url = "http://mazowiecki.pzd.pl"
    search_urls = [
        "http://mazowiecki.pzd.pl/",
        "http://mazowiecki.pzd.pl/komunikaty/",
    ]

    def __init__(self, search_urls: Optional[list[str]] = None) -> None:
        if search_urls is not None:
            self.search_urls = list(search_urls)

    def fetch_listings(self, timeout: int = 15) -> list[Listing]:
        seen_ids: set[str] = set()
        results: list[Listing] = []
        for url in self.search_urls:
            html = fetch_url_text(url, timeout=timeout)
            for listing in self.parse_html(html, base_url=url):
                if listing.id not in seen_ids:
                    seen_ids.add(listing.id)
                    results.append(listing)
        return results

    def parse_html(
        self,
        html: str,
        base_url: Optional[str] = None,
        location_raw: str = "Warszawa, Okręg Mazowiecki PZD",
    ) -> list[Listing]:
        root_url = base_url or self.base_url
        listings = _extract_articles_and_links(
            html=html,
            base_url=root_url,
            source=self.source,
            default_location=location_raw,
        )
        # Enrich location_raw with any title/snippet text so district matcher can spot Mokotów/Ochota if mentioned
        for item in listings:
            item.location_raw = f"{location_raw} ({item.title})"
        return listings

    def parse_response(
        self,
        html: str,
        base_url: Optional[str] = None,
        location_raw: str = "Warszawa, Okręg Mazowiecki PZD",
    ) -> list[Listing]:
        return self.parse_html(html, base_url=base_url, location_raw=location_raw)


class RODGardensScraper:
    """Scraper for Mokotów & Ochota ROD garden announcement boards."""

    source = "rod_garden"

    def __init__(
        self,
        targets: Optional[list[GardenTarget]] = None,
        extra_urls_env: Optional[str] = None,
    ) -> None:
        base_targets = list(targets) if targets is not None else list(DEFAULT_GARDEN_TARGETS)
        raw_extra = (
            extra_urls_env
            if extra_urls_env is not None
            else os.environ.get("ROD_EXTRA_GARDEN_URLS", "")
        )
        for extra in _parse_extra_garden_urls(raw_extra):
            base_targets.append(extra)
        self.targets: list[GardenTarget] = base_targets

    def fetch_listings(self, timeout: int = 15) -> list[Listing]:
        listings, _ = self.fetch_listings_with_errors(timeout=timeout)
        return listings

    def fetch_listings_with_errors(self, timeout: int = 15) -> tuple[list[Listing], list[str]]:
        """Fetch across all configured ROD gardens, tolerating offline individual garden sites."""
        seen_ids: set[str] = set()
        results: list[Listing] = []
        warnings: list[str] = []
        for target in self.targets:
            try:
                html = fetch_url_text(target.url, timeout=timeout)
                parsed = self.parse_html(
                    html,
                    garden_name=target.name,
                    district=target.district,
                    base_url=target.url,
                    location_raw=target.location_raw,
                )
                for item in parsed:
                    if item.id not in seen_ids:
                        seen_ids.add(item.id)
                        results.append(item)
            except Exception as exc:
                warnings.append(f"rod_garden ({target.name} - {target.url}): {exc}")
        return results, warnings

    def parse_html(
        self,
        html: str,
        garden_name: str = "ROD im. Obrońców Pokoju",
        district: str = "Warszawa, Mokotów",
        base_url: str = "https://rodobroncowpokoju.pl/",
        location_raw: Optional[str] = None,
    ) -> list[Listing]:
        loc = location_raw or f"{district}, {garden_name}"
        listings = _extract_articles_and_links(
            html=html,
            base_url=base_url,
            source=self.source,
            default_location=loc,
        )
        for item in listings:
            if garden_name.lower() not in item.title.lower():
                item.title = f"[{garden_name}] {item.title}"
        return listings

    def parse_response(
        self,
        html: str,
        garden_name: str = "ROD im. Obrońców Pokoju",
        district: str = "Warszawa, Mokotów",
        base_url: str = "https://rodobroncowpokoju.pl/",
        location_raw: Optional[str] = None,
    ) -> list[Listing]:
        return self.parse_html(
            html=html,
            garden_name=garden_name,
            district=district,
            base_url=base_url,
            location_raw=location_raw,
        )


def _parse_extra_garden_urls(raw: str) -> list[GardenTarget]:
    """Parse ROD_EXTRA_GARDEN_URLS env string.

    Accepts comma- or semicolon-separated entries, either plain URLs or
    `Name|District|URL` triples (e.g. `ROD Wierzbno|Warszawa, Mokotów|https://example.pl`).
    """
    if not raw or not raw.strip():
        return []
    targets: list[GardenTarget] = []
    for token in re.split(r"[;\n,]+", raw.strip()):
        entry = token.strip()
        if not entry:
            continue
        if "|" in entry:
            parts = [p.strip() for p in entry.split("|")]
            if len(parts) >= 3:
                targets.append(GardenTarget(name=parts[0], district=parts[1], url=parts[2]))
            elif len(parts) == 2:
                targets.append(
                    GardenTarget(name=parts[0], district="Warszawa, Mokotów", url=parts[1])
                )
        elif entry.startswith(("http://", "https://")):
            host = urllib.parse.urlparse(entry).netloc or "ROD"
            targets.append(
                GardenTarget(
                    name=f"ROD ({host})",
                    district="Warszawa, Mokotów / Ochota",
                    url=entry,
                )
            )
    return targets
