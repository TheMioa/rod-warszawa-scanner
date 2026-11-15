"""Scrapers for Polish classified portals (OLX, Otodom, Gratka, Sprzedajemy)."""

from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Optional

from rod_scanner.models import Listing

try:
    from curl_cffi import requests as _curl_requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _curl_requests = None

try:
    import requests as _requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _requests = None

try:
    from bs4 import BeautifulSoup as _BeautifulSoup  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    _BeautifulSoup = None


DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def is_individual_offer_url(url: str, source: str = "") -> bool:
    """Return True only if URL points to a specific individual offer rather than a category/search index page."""
    if not url or not url.strip():
        return False
    parsed = urllib.parse.urlparse(url.strip())
    host = parsed.netloc.lower()
    path = parsed.path
    path_lower = path.lower()
    src = (source or "").strip().lower()

    if src == "gratka" or "gratka.pl" in host:
        return bool(re.search(r"/(?:ob|oi)/\d+", path_lower))
    if src == "olx" or "olx.pl" in host:
        return ("/d/oferta/" in path_lower or "/oferta/" in path_lower) and bool(
            re.search(r"id[a-z0-9]+", path_lower)
        )
    if src == "otodom" or "otodom.pl" in host:
        return "/pl/oferta/" in path_lower or "/oferta/" in path_lower
    if src == "sprzedajemy" or "sprzedajemy.pl" in host:
        return bool(re.search(r"-nr\d+", path_lower))

    return bool(path_lower and path_lower != "/")


def fetch_url_text(url: str, timeout: int = 15, headers: Optional[dict[str, str]] = None) -> str:
    """Fetch text content from a URL using curl_cffi or requests if available, falling back to urllib.request."""
    merged_headers = dict(DEFAULT_HEADERS)
    if headers:
        merged_headers.update(headers)

    if _curl_requests is not None:
        try:
            resp = _curl_requests.get(
                url,
                headers=merged_headers,
                impersonate="chrome120",
                timeout=timeout,
            )
            resp.raise_for_status()
            return str(resp.text)
        except Exception:
            if _requests is None:
                raise

    if _requests is not None:
        resp = _requests.get(url, headers=merged_headers, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return str(resp.text)

    req = urllib.request.Request(url, headers=merged_headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw_bytes = response.read()
        content_type = response.headers.get("Content-Type", "")
        charset_match = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE)
        encoding = charset_match.group(1) if charset_match else "utf-8"
        try:
            return raw_bytes.decode(encoding, errors="replace")
        except LookupError:
            return raw_bytes.decode("utf-8", errors="replace")


def strip_html_tags(text: str) -> str:
    """Strip HTML tags and normalize whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html_lib.unescape(cleaned)
    cleaned = cleaned.replace("\xa0", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def make_listing_id(source: str, raw_id_or_url: str) -> str:
    """Create a deterministic listing ID prefixed with the source name."""
    cleaned = str(raw_id_or_url).strip()
    if cleaned.startswith(f"{source}:"):
        return cleaned
    # If it looks like a short alphanumeric ID, use directly; if a URL, hash or extract slug
    if re.fullmatch(r"[A-Za-z0-9_-]{2,48}", cleaned):
        return f"{source}:{cleaned}"
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:12]
    return f"{source}:{digest}"


def parse_price_pln(text: Optional[str]) -> Optional[float]:
    """Extract numeric PLN price from Polish price text.

    Examples:
        "85 000 zł" -> 85000.0
        "120 500,50 PLN" -> 120500.5
        "95.000 zł (do negocjacji)" -> 95000.0
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None

    norm = html_lib.unescape(str(text)).replace("\xa0", " ").strip()
    if not norm:
        return None

    # Ignore explicit "free" / "ask for price" strings without numbers
    # First look for a number followed by zł / pln
    currency_pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:[ .]\d{3})+(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:z[łl]|pln)\b",
        re.IGNORECASE,
    )
    match = currency_pattern.search(norm)
    if not match:
        # Fallback: if the string itself is mostly a price (e.g. from a dedicated price node)
        plain_pattern = re.compile(
            r"^\D*(\d{1,3}(?:[ .]\d{3})+(?:,\d{1,2})?|\d{3,9}(?:[.,]\d{1,2})?)\D*$"
        )
        match = plain_pattern.search(norm)
        if not match:
            return None

    num_str = match.group(1).replace(" ", "")
    # Handle dot used as thousands separator (e.g., "95.000") vs decimal point
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d{1,2})?", num_str):
        num_str = num_str.replace(".", "").replace(",", ".")
    else:
        num_str = num_str.replace(",", ".")

    try:
        value = float(num_str)
        return value if value > 0 else None
    except ValueError:
        return None


def parse_area_m2(text: Optional[str]) -> Optional[float]:
    """Extract plot area in m² from Polish text.

    Examples:
        "Działka ROD 300 m² Mokotów" -> 300.0
        "Powierzchnia: 350,5 m2" -> 350.5
        "400 mkw" -> 400.0
        "3 ary" -> 300.0
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text > 0 else None

    norm = html_lib.unescape(str(text)).replace("\xa0", " ").strip()
    if not norm:
        return None

    m2_pattern = re.compile(
        r"(?<!\d)(\d{1,4}(?:[ .,]\d{1,2})?)\s*(?:m2|m²|m\.?\s*kw\.?|mkw|metr[óo]w\s+kwadratowych)(?!\w)",
        re.IGNORECASE,
    )
    match = m2_pattern.search(norm)
    if match:
        raw_val = match.group(1).replace(" ", "").replace(",", ".")
        try:
            val = float(raw_val)
            if 10.0 <= val <= 50000.0:
                return val
        except ValueError:
            pass

    ar_pattern = re.compile(
        r"(?<!\d)(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:ar(?:a|y|[óo]w)?)\b",
        re.IGNORECASE,
    )
    ar_match = ar_pattern.search(norm)
    if ar_match:
        raw_val = ar_match.group(1).replace(",", ".")
        try:
            val = float(raw_val) * 100.0
            if 10.0 <= val <= 50000.0:
                return val
        except ValueError:
            pass

    return None


class _AnchorAndCardParser(HTMLParser):
    """Lightweight stdlib HTML parser fallback when BeautifulSoup is unavailable."""

    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[tuple[dict[str, str], str]] = []
        self._in_script = False
        self._script_attrs: dict[str, str] = {}
        self._script_buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() == "script":
            self._in_script = True
            self._script_attrs = attr_dict
            self._script_buf = []

    def handle_data(self, data: str) -> None:
        if self._in_script:
            self._script_buf.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_script:
            self._in_script = False
            self.scripts.append((self._script_attrs, "".join(self._script_buf)))


def _extract_scripts(html: str) -> list[tuple[dict[str, str], str]]:
    """Extract all <script> tags and their attributes + text content."""
    parser = _AnchorAndCardParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    return parser.scripts


def _extract_json_ld_listings(html: str, source: str, base_url: str) -> list[Listing]:
    """Extract Listing objects from <script type='application/ld+json'> blocks."""
    results: list[Listing] = []
    for attrs, content in _extract_scripts(html):
        if "ld+json" not in attrs.get("type", "").lower():
            continue
        raw = content.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue

        items = _flatten_json_ld_items(data)
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("name") or item.get("headline") or "").strip()
            url = str(item.get("url") or "").strip()
            if not url and isinstance(item.get("offers"), dict):
                url = str(item["offers"].get("url") or "").strip()
            if not title or not url:
                continue
            full_url = urllib.parse.urljoin(base_url, url)
            if not is_individual_offer_url(full_url, source):
                continue
            description = strip_html_tags(str(item.get("description") or ""))

            price_pln: Optional[float] = None
            price_raw: Optional[str] = None
            offers = item.get("offers")
            if isinstance(offers, dict):
                raw_p = offers.get("price")
                if raw_p is not None:
                    price_pln = parse_price_pln(f"{raw_p} PLN")
                    price_raw = f"{raw_p} {offers.get('priceCurrency', 'PLN')}".strip()

            location_raw = ""
            address = item.get("address")
            if isinstance(address, dict):
                parts = [
                    str(address.get("streetAddress") or ""),
                    str(address.get("addressLocality") or ""),
                    str(address.get("addressRegion") or ""),
                ]
                location_raw = ", ".join(p for p in parts if p)
            elif isinstance(item.get("contentLocation"), dict):
                location_raw = str(item["contentLocation"].get("name") or "")

            area_m2 = parse_area_m2(f"{title} {description}")
            raw_id = str(item.get("sku") or item.get("productID") or item.get("@id") or full_url)

            results.append(
                Listing(
                    id=make_listing_id(source, raw_id),
                    source=source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=price_raw,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=description[:300],
                )
            )
    return results


def _flatten_json_ld_items(data: Any) -> list[dict[str, Any]]:
    """Recursively unwrap JSON-LD @graph and ItemList elements into item dicts."""
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        for entry in data:
            items.extend(_flatten_json_ld_items(entry))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            items.extend(_flatten_json_ld_items(data["@graph"]))
        if data.get("@type") == "ItemList" and isinstance(data.get("itemListElement"), list):
            for el in data["itemListElement"]:
                if isinstance(el, dict):
                    if isinstance(el.get("item"), dict):
                        items.append(el["item"])
                    else:
                        items.append(el)
        elif data.get("@type") in {
            "Product",
            "Offer",
            "SingleFamilyResidence",
            "Residence",
            "Place",
            "RealEstateListing",
        }:
            items.append(data)
    return items


class BasePortalScraper:
    """Base class for classified portal scrapers."""

    source: str = "portal"
    base_url: str = ""
    search_urls: list[str] = []

    def __init__(self, search_urls: Optional[list[str]] = None) -> None:
        self._custom_search_urls = search_urls is not None
        if search_urls is not None:
            self.search_urls = list(search_urls)

    def fetch_listings(self, timeout: int = 15) -> list[Listing]:
        """Fetch and parse listings across all configured search URLs for this portal."""
        seen_ids: set[str] = set()
        collected: list[Listing] = []
        for url in self.search_urls:
            html = fetch_url_text(url, timeout=timeout)
            for listing in self.parse_html(html, base_url=self.base_url or url):
                if listing.id not in seen_ids:
                    seen_ids.add(listing.id)
                    collected.append(listing)
        return collected

    def parse_html(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        raise NotImplementedError

    def parse_response(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        """Alias for parse_html used by unit tests and callers."""
        return self.parse_html(html, base_url=base_url)


class OLXScraper(BasePortalScraper):
    """Scraper for OLX.pl Warsaw ROD plot listings."""

    source = "olx"
    base_url = "https://www.olx.pl"
    api_urls = [
        "https://www.olx.pl/api/v1/offers/?offset=0&limit=50&query=dzia%C5%82ka%20ROD%20Warszawa",
        "https://www.olx.pl/api/v1/offers/?offset=0&limit=50&query=ogrodek%20dzialkowy%20Warszawa",
    ]
    search_urls = [
        "https://www.olx.pl/nieruchomosci/dzialki/sprzedaz/warszawa/?q=ROD",
        "https://www.olx.pl/nieruchomosci/dzialki/warszawa/?q=ogrodek+dzialkowy",
    ]

    def fetch_listings(self, timeout: int = 15) -> list[Listing]:
        seen_ids: set[str] = set()
        collected: list[Listing] = []
        last_error: Optional[Exception] = None
        any_succeeded = False

        if not getattr(self, "_custom_search_urls", False):
            for api_url in self.api_urls:
                try:
                    raw_json = fetch_url_text(
                        api_url,
                        timeout=timeout,
                        headers={"Accept": "application/json"},
                    )
                    any_succeeded = True
                    for listing in self.parse_api_json(raw_json):
                        if listing.id not in seen_ids:
                            seen_ids.add(listing.id)
                            collected.append(listing)
                except Exception as exc:
                    last_error = exc

        try:
            for listing in super().fetch_listings(timeout=timeout):
                any_succeeded = True
                if listing.id not in seen_ids:
                    seen_ids.add(listing.id)
                    collected.append(listing)
        except Exception as exc:
            last_error = exc

        if not any_succeeded and last_error is not None:
            raise last_error
        return collected

    def parse_api_json(self, raw_json: str) -> list[Listing]:
        """Parse OLX /api/v1/offers/ JSON response into Listing objects."""
        if not raw_json or not raw_json.strip():
            return []
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []

        offers = payload.get("data")
        if not isinstance(offers, list):
            return []

        by_id: dict[str, Listing] = {}
        for offer in offers:
            if not isinstance(offer, dict):
                continue
            title = str(offer.get("title") or "").strip()
            raw_url = str(offer.get("url") or "").strip()
            if not title or not raw_url:
                continue
            full_url = urllib.parse.urljoin(self.base_url, raw_url)
            if not is_individual_offer_url(full_url, self.source):
                continue

            offer_id = str(offer.get("id") or _extract_slug_id(full_url))
            listing_id = make_listing_id(self.source, offer_id)

            price_pln: Optional[float] = None
            price_raw: Optional[str] = None
            area_m2: Optional[float] = None

            params = offer.get("params")
            if isinstance(params, list):
                for param in params:
                    if not isinstance(param, dict):
                        continue
                    key = str(param.get("key") or "").strip().lower()
                    val = param.get("value")
                    if key == "price":
                        if isinstance(val, dict):
                            raw_num = val.get("value")
                            currency = str(val.get("currency") or "PLN").strip()
                            label = str(val.get("label") or "").strip()
                            if raw_num is not None:
                                try:
                                    price_pln = float(raw_num)
                                except (TypeError, ValueError):
                                    price_pln = None
                            price_raw = label or (
                                f"{int(price_pln)} {currency}" if price_pln is not None else None
                            )
                            if price_pln is None and price_raw:
                                price_pln = parse_price_pln(price_raw)
                        elif isinstance(val, (int, float)):
                            price_pln = float(val)
                            price_raw = f"{int(val)} zł"
                        elif isinstance(val, str):
                            price_raw = val.strip() or None
                            price_pln = parse_price_pln(val)
                    elif key == "m":
                        if isinstance(val, dict):
                            raw_area = val.get("key") or val.get("value") or val.get("label")
                            if raw_area is not None:
                                area_m2 = parse_area_m2(str(raw_area)) or parse_area_m2(
                                    f"{raw_area} m2"
                                )
                        elif val is not None:
                            area_m2 = parse_area_m2(str(val)) or parse_area_m2(f"{val} m2")

            loc = offer.get("location")
            location_parts: list[str] = []
            if isinstance(loc, dict):
                city_node = loc.get("city")
                district_node = loc.get("district")
                city_name = (
                    city_node.get("name")
                    if isinstance(city_node, dict)
                    else loc.get("cityName")
                )
                district_name = (
                    district_node.get("name")
                    if isinstance(district_node, dict)
                    else loc.get("districtName")
                )
                if city_name:
                    location_parts.append(str(city_name).strip())
                if district_name:
                    location_parts.append(str(district_name).strip())
            location_raw = ", ".join(p for p in location_parts if p)

            description = strip_html_tags(str(offer.get("description") or ""))
            if area_m2 is None:
                area_m2 = parse_area_m2(f"{title} {description}")
            if price_pln is None:
                price_pln = parse_price_pln(f"{title} {description}")

            published_at = (
                str(
                    offer.get("created_time")
                    or offer.get("last_refresh_time")
                    or offer.get("createdTime")
                    or ""
                ).strip()
                or None
            )

            if listing_id not in by_id:
                by_id[listing_id] = Listing(
                    id=listing_id,
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=price_raw,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=description[:300],
                    published_at=published_at,
                )
        return list(by_id.values())

    def parse_html(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        root_url = base_url or self.base_url
        by_id: dict[str, Listing] = {}

        # 1. Try window.__PRERENDERED_STATE__ JSON extraction
        for listing in self._parse_prerendered_state(html, root_url):
            if is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        # 2. Try HTML card extraction (<div data-cy="l-card"> or <div data-testid="l-card">)
        for listing in self._parse_cards(html, root_url):
            if listing.id not in by_id and is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        # 3. Try JSON-LD extraction
        for listing in _extract_json_ld_listings(html, self.source, root_url):
            if listing.id not in by_id and is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        return list(by_id.values())

    def _parse_prerendered_state(self, html: str, base_url: str) -> list[Listing]:
        results: list[Listing] = []
        # Match window.__PRERENDERED_STATE__= "..." or = {...};
        state_match = re.search(
            r"window\.__PRERENDERED_STATE__\s*=\s*(\"(?:\\.|[^\"\\])*\"|\{.*?\})\s*;\s*(?:window\.|</script>)",
            html,
            re.DOTALL,
        )
        if not state_match:
            state_match = re.search(
                r"window\.__PRERENDERED_STATE__\s*=\s*(\"(?:\\.|[^\"\\])*\"|\{[^\n]*\})\s*;?",
                html,
            )
        if not state_match:
            return results

        raw_val = state_match.group(1).strip()
        try:
            parsed = json.loads(raw_val)
            if isinstance(parsed, str):
                parsed = json.loads(parsed)
        except json.JSONDecodeError:
            return results

        ads = self._find_olx_ads(parsed)
        for ad in ads:
            if not isinstance(ad, dict):
                continue
            title = str(ad.get("title") or "").strip()
            url = str(ad.get("url") or ad.get("externalUrl") or "").strip()
            if not title or not url:
                continue
            full_url = urllib.parse.urljoin(base_url, url)
            ad_id = str(ad.get("id") or full_url)

            price_pln: Optional[float] = None
            price_raw: Optional[str] = None
            price_obj = ad.get("price")
            if isinstance(price_obj, dict):
                reg_price = price_obj.get("regularPrice") or price_obj
                if isinstance(reg_price, dict) and reg_price.get("value") is not None:
                    try:
                        price_pln = float(reg_price["value"])
                    except (TypeError, ValueError):
                        price_pln = None
                price_raw = str(
                    price_obj.get("displayValue")
                    or (reg_price.get("displayValue") if isinstance(reg_price, dict) else "")
                    or ""
                ).strip() or None
                if price_pln is None and price_raw:
                    price_pln = parse_price_pln(price_raw)
            elif isinstance(price_obj, (int, float)):
                price_pln = float(price_obj)
                price_raw = f"{int(price_obj)} zł"
            elif isinstance(price_obj, str):
                price_raw = price_obj
                price_pln = parse_price_pln(price_obj)

            # Location
            location_parts: list[str] = []
            loc = ad.get("location")
            if isinstance(loc, dict):
                city_name = (
                    loc.get("cityName")
                    or (loc.get("city", {}).get("name") if isinstance(loc.get("city"), dict) else "")
                )
                district_name = (
                    loc.get("districtName")
                    or (
                        loc.get("district", {}).get("name")
                        if isinstance(loc.get("district"), dict)
                        else ""
                    )
                )
                if city_name:
                    location_parts.append(str(city_name))
                if district_name:
                    location_parts.append(str(district_name))
            elif isinstance(loc, str):
                location_parts.append(loc)
            location_raw = ", ".join(p for p in location_parts if p)

            # Area from params or title/description
            area_m2: Optional[float] = None
            params = ad.get("params")
            if isinstance(params, list):
                for param in params:
                    if not isinstance(param, dict):
                        continue
                    key = str(param.get("key") or param.get("name") or "").lower()
                    val = str(param.get("value") or param.get("normalizedValue") or "")
                    if "m" in key or "area" in key or "powierzchnia" in key:
                        area_m2 = parse_area_m2(val) or parse_area_m2(f"{val} m2")
                        if area_m2:
                            break

            description = strip_html_tags(str(ad.get("description") or ""))
            if area_m2 is None:
                area_m2 = parse_area_m2(f"{title} {description}")

            published_at = str(ad.get("createdTime") or ad.get("lastRefreshTime") or "") or None

            results.append(
                Listing(
                    id=make_listing_id(self.source, ad_id),
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=price_raw,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=description[:300],
                    published_at=published_at,
                )
            )
        return results

    def _find_olx_ads(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, dict):
            # Common path: listing -> listing -> ads
            listing_node = data.get("listing")
            if isinstance(listing_node, dict):
                inner = listing_node.get("listing")
                if isinstance(inner, dict) and isinstance(inner.get("ads"), list):
                    return inner["ads"]
                if isinstance(listing_node.get("ads"), list):
                    return listing_node["ads"]
            if isinstance(data.get("ads"), list):
                return data["ads"]
            if isinstance(data.get("data"), list):
                return data["data"]
        return []

    def _parse_cards(self, html: str, base_url: str) -> list[Listing]:
        results: list[Listing] = []

        if _BeautifulSoup is not None:
            soup = _BeautifulSoup(html, "html.parser")
            cards = soup.find_all(
                lambda tag: tag.name in ("div", "article", "li")
                and (
                    tag.get("data-cy") == "l-card"
                    or tag.get("data-testid") == "l-card"
                    or "l-card" in (tag.get("class") or [])
                )
            )
            for card in cards:
                link = card.find("a", href=True)
                if not link:
                    continue
                href = str(link.get("href") or "").strip()
                if not href:
                    continue
                full_url = urllib.parse.urljoin(base_url, href)

                # Extract title from h6, h4, h3, or link text
                heading = card.find(["h6", "h4", "h3", "h2"])
                title = strip_html_tags(heading.get_text(" ")) if heading else ""
                if not title:
                    title = strip_html_tags(link.get_text(" "))
                if not title:
                    continue

                price_el = card.find(attrs={"data-testid": "ad-price"}) or card.find(
                    class_=re.compile(r"price", re.I)
                )
                price_raw = strip_html_tags(price_el.get_text(" ")) if price_el else None
                price_pln = parse_price_pln(price_raw) if price_raw else parse_price_pln(card.get_text(" "))

                loc_el = card.find(attrs={"data-testid": "location-date"}) or card.find(
                    class_=re.compile(r"location", re.I)
                )
                loc_date_text = strip_html_tags(loc_el.get_text(" ")) if loc_el else ""
                location_raw, published_at = _split_olx_location_date(loc_date_text)

                card_text = strip_html_tags(card.get_text(" "))
                area_m2 = parse_area_m2(card_text)
                card_id = str(card.get("id") or _extract_slug_id(full_url))

                results.append(
                    Listing(
                        id=make_listing_id(self.source, card_id),
                        source=self.source,
                        title=title,
                        url=full_url,
                        price_pln=price_pln,
                        price_raw=price_raw,
                        area_m2=area_m2,
                        location_raw=location_raw,
                        description_snippet=card_text[:300],
                        published_at=published_at,
                    )
                )
            if results:
                return results

        # Regex fallback for minimal environments without bs4
        card_pattern = re.compile(
            r"<(?:div|article)\b[^>]*data-(?:cy|testid)=[\"']l-card[\"'][^>]*>(.*?)(?=<(?:div|article)\b[^>]*data-(?:cy|testid)=[\"']l-card[\"']|\Z)",
            re.IGNORECASE | re.DOTALL,
        )
        for match in card_pattern.finditer(html):
            block = match.group(1)
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", block, re.I)
            if not href_m:
                continue
            full_url = urllib.parse.urljoin(base_url, html_lib.unescape(href_m.group(1)))

            title_m = re.search(r"<h[2-6]\b[^>]*>(.*?)</h[2-6]>", block, re.I | re.DOTALL)
            title = strip_html_tags(title_m.group(1)) if title_m else ""
            if not title:
                link_m = re.search(r"<a\b[^>]*>(.*?)</a>", block, re.I | re.DOTALL)
                title = strip_html_tags(link_m.group(1)) if link_m else ""
            if not title:
                continue

            price_m = re.search(
                r"data-testid=[\"']ad-price[\"'][^>]*>(.*?)</p>", block, re.I | re.DOTALL
            )
            price_raw = strip_html_tags(price_m.group(1)) if price_m else None
            block_text = strip_html_tags(block)
            price_pln = parse_price_pln(price_raw) if price_raw else parse_price_pln(block_text)

            loc_m = re.search(
                r"data-testid=[\"']location-date[\"'][^>]*>(.*?)</p>", block, re.I | re.DOTALL
            )
            loc_date_text = strip_html_tags(loc_m.group(1)) if loc_m else ""
            location_raw, published_at = _split_olx_location_date(loc_date_text)
            area_m2 = parse_area_m2(block_text)
            card_id = _extract_slug_id(full_url)

            results.append(
                Listing(
                    id=make_listing_id(self.source, card_id),
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=price_raw,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=block_text[:300],
                    published_at=published_at,
                )
            )
        return results


def _split_olx_location_date(loc_date_text: str) -> tuple[str, Optional[str]]:
    """Split OLX 'Warszawa, Mokotów - Dzisiaj o 12:30' into (location, date)."""
    if not loc_date_text:
        return "", None
    if " - " in loc_date_text:
        loc, date_part = loc_date_text.split(" - ", 1)
        return loc.strip(), date_part.strip() or None
    return loc_date_text.strip(), None


def _extract_slug_id(url: str) -> str:
    """Extract a stable ID from an OLX/Otodom/Gratka URL slug."""
    id_match = re.search(r"ID([A-Za-z0-9]+)\.html", url)
    if id_match:
        return id_match.group(1)
    otodom_match = re.search(r"-ID([A-Za-z0-9]+)(?:$|[/?#])", url)
    if otodom_match:
        return otodom_match.group(1)
    gratka_match = re.search(r"/(?:ob|oi)/(\d+)", url)
    if gratka_match:
        return gratka_match.group(1)
    sprzedajemy_match = re.search(r"-nr(\d+)", url)
    if sprzedajemy_match:
        return sprzedajemy_match.group(1)
    return url


class OtodomScraper(BasePortalScraper):
    """Scraper for Otodom.pl Warsaw recreational plot listings."""

    source = "otodom"
    base_url = "https://www.otodom.pl"
    search_urls = [
        "https://www.otodom.pl/pl/wyniki/sprzedaz/dzialka,rekreacyjna/mazowieckie/warszawa/warszawa/warszawa",
    ]

    def parse_html(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        root_url = base_url or self.base_url
        by_id: dict[str, Listing] = {}

        # 1. Parse <script id="__NEXT_DATA__"> JSON
        for listing in self._parse_next_data(html, root_url):
            if is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        # 2. Parse HTML article cards fallback
        for listing in self._parse_article_cards(html, root_url):
            if listing.id not in by_id and is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        # 3. Parse JSON-LD fallback
        for listing in _extract_json_ld_listings(html, self.source, root_url):
            if listing.id not in by_id and is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        return list(by_id.values())

    def _parse_next_data(self, html: str, base_url: str) -> list[Listing]:
        results: list[Listing] = []
        next_json_str: Optional[str] = None
        for attrs, content in _extract_scripts(html):
            if attrs.get("id") == "__NEXT_DATA__":
                next_json_str = content.strip()
                break

        if not next_json_str:
            m = re.search(
                r"<script\b[^>]*id=[\"']__NEXT_DATA__[\"'][^>]*>(.*?)</script>",
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if m:
                next_json_str = m.group(1).strip()

        if not next_json_str:
            return results

        try:
            data = json.loads(next_json_str)
        except json.JSONDecodeError:
            return results

        items = self._extract_otodom_items(data)
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            slug = str(item.get("slug") or "").strip()
            href = str(item.get("href") or item.get("url") or "").strip()
            if not href and slug:
                href = f"/pl/oferta/{slug}"
            if href and href.startswith("[lang]/"):
                href = "/" + href.replace("[lang]/", "pl/", 1)
            if not title or not href:
                continue
            full_url = urllib.parse.urljoin(base_url, href)
            raw_id = str(item.get("id") or slug or full_url)

            # Price extraction
            price_pln: Optional[float] = None
            price_raw: Optional[str] = None
            total_price = item.get("totalPrice") or item.get("price")
            if isinstance(total_price, dict):
                val = total_price.get("value")
                currency = total_price.get("currency", "PLN")
                if val is not None:
                    try:
                        price_pln = float(val)
                        price_raw = f"{int(price_pln)} {currency}"
                    except (TypeError, ValueError):
                        pass
            elif isinstance(total_price, (int, float)):
                price_pln = float(total_price)
                price_raw = f"{int(price_pln)} PLN"
            elif isinstance(total_price, str):
                price_raw = total_price
                price_pln = parse_price_pln(total_price)

            # Area extraction
            area_m2: Optional[float] = None
            raw_area = item.get("areaInSquareMeters") or item.get("area")
            if raw_area is not None:
                try:
                    area_m2 = float(raw_area)
                except (TypeError, ValueError):
                    area_m2 = parse_area_m2(str(raw_area))

            # Location extraction
            location_raw = self._extract_otodom_location(item)
            description = strip_html_tags(
                str(item.get("shortDescription") or item.get("description") or "")
            )
            if area_m2 is None:
                area_m2 = parse_area_m2(f"{title} {description}")

            published_at = str(item.get("dateCreatedFirst") or item.get("dateCreated") or "") or None

            results.append(
                Listing(
                    id=make_listing_id(self.source, raw_id),
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=price_raw,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=description[:300],
                    published_at=published_at,
                )
            )
        return results

    def _extract_otodom_items(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        props = data.get("props", {})
        page_props = props.get("pageProps", {}) if isinstance(props, dict) else {}
        if not isinstance(page_props, dict):
            return []

        inner_data = page_props.get("data", {})
        if isinstance(inner_data, dict):
            search_ads = inner_data.get("searchAds", {})
            if isinstance(search_ads, dict) and isinstance(search_ads.get("items"), list):
                return search_ads["items"]
        if isinstance(page_props.get("items"), list):
            return page_props["items"]
        return []

    def _extract_otodom_location(self, item: dict[str, Any]) -> str:
        loc = item.get("location")
        if isinstance(loc, str):
            return loc
        if not isinstance(loc, dict):
            return str(item.get("locationLabel", {}).get("value", "") if isinstance(item.get("locationLabel"), dict) else "")

        parts: list[str] = []
        address = loc.get("address")
        if isinstance(address, dict):
            for key in ("street", "subdistrict", "district", "city", "province"):
                node = address.get(key)
                if isinstance(node, dict) and node.get("name"):
                    parts.append(str(node["name"]))
                elif isinstance(node, str) and node:
                    parts.append(node)
        reverse_geo = loc.get("reverseGeocoding")
        if isinstance(reverse_geo, dict) and isinstance(reverse_geo.get("locations"), list):
            for loc_entry in reverse_geo["locations"]:
                if isinstance(loc_entry, dict) and loc_entry.get("fullName"):
                    parts.append(str(loc_entry["fullName"]))
        return ", ".join(dict.fromkeys(p for p in parts if p))

    def _parse_article_cards(self, html: str, base_url: str) -> list[Listing]:
        results: list[Listing] = []
        article_pattern = re.compile(r"<article\b[^>]*>(.*?)</article>", re.IGNORECASE | re.DOTALL)
        for match in article_pattern.finditer(html):
            block = match.group(1)
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", block, re.I)
            if not href_m:
                continue
            full_url = urllib.parse.urljoin(base_url, html_lib.unescape(href_m.group(1)))

            title_m = re.search(
                r"<(?:p|h[1-6]|span)\b[^>]*data-cy=[\"']listing-item-title[\"'][^>]*>(.*?)</(?:p|h[1-6]|span)>",
                block,
                re.I | re.DOTALL,
            )
            if not title_m:
                title_m = re.search(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", block, re.I | re.DOTALL)
            title = strip_html_tags(title_m.group(1)) if title_m else ""
            if not title:
                continue

            loc_m = re.search(
                r"<p\b[^>]*data-testid=[\"']advert-card-address[\"'][^>]*>(.*?)</p>",
                block,
                re.I | re.DOTALL,
            )
            location_raw = strip_html_tags(loc_m.group(1)) if loc_m else ""

            block_text = strip_html_tags(block)
            price_pln = parse_price_pln(block_text)
            area_m2 = parse_area_m2(block_text)
            raw_id = _extract_slug_id(full_url)

            results.append(
                Listing(
                    id=make_listing_id(self.source, raw_id),
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=f"{int(price_pln)} zł" if price_pln else None,
                    area_m2=area_m2,
                    location_raw=location_raw or block_text[:120],
                    description_snippet=block_text[:300],
                )
            )
        return results


class GratkaScraper(BasePortalScraper):
    """Scraper for Gratka.pl Warsaw recreational plot listings."""

    source = "gratka"
    base_url = "https://gratka.pl"
    search_urls = [
        "https://gratka.pl/nieruchomosci/dzialki-grunty/rekreacyjna/warszawa",
    ]

    def parse_html(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        root_url = base_url or self.base_url
        by_id: dict[str, Listing] = {}

        for listing in _extract_json_ld_listings(html, self.source, root_url):
            if is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        card_pattern = re.compile(
            r"<article\b[^>]*>(.*?)</article>|<div\b[^>]*class=[\"'][^\"']*(?:teaserUnified|property-card|listing-card)[^\"']*[\"'][^>]*>(.*?)</div>\s*</div>",
            re.IGNORECASE | re.DOTALL,
        )
        for match in card_pattern.finditer(html):
            block = match.group(1) or match.group(2) or ""
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", block, re.I)
            if not href_m:
                continue
            full_url = urllib.parse.urljoin(root_url, html_lib.unescape(href_m.group(1)))
            if not is_individual_offer_url(full_url, self.source):
                continue

            title_m = re.search(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", block, re.I | re.DOTALL)
            if not title_m:
                title_m = re.search(
                    r"<a\b[^>]*class=[\"'][^\"']*teaserUnified__anchor[^\"']*[\"'][^>]*>(.*?)</a>",
                    block,
                    re.I | re.DOTALL,
                )
            title = strip_html_tags(title_m.group(1)) if title_m else ""
            if not title:
                continue

            loc_m = re.search(
                r"<span\b[^>]*class=[\"'][^\"']*(?:location|address)[^\"']*[\"'][^>]*>(.*?)</span>",
                block,
                re.I | re.DOTALL,
            )
            block_text = strip_html_tags(block)
            location_raw = strip_html_tags(loc_m.group(1)) if loc_m else block_text[:120]
            price_pln = parse_price_pln(block_text)
            area_m2 = parse_area_m2(block_text)
            raw_id = _extract_slug_id(full_url)
            listing_id = make_listing_id(self.source, raw_id)

            if listing_id not in by_id:
                by_id[listing_id] = Listing(
                    id=listing_id,
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=f"{int(price_pln)} zł" if price_pln else None,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=block_text[:300],
                )

        return list(by_id.values())


class SprzedajemyScraper(BasePortalScraper):
    """Scraper for Sprzedajemy.pl Warsaw ROD plot listings."""

    source = "sprzedajemy"
    base_url = "https://sprzedajemy.pl"
    search_urls = [
        "https://sprzedajemy.pl/warszawa/nieruchomosci/dzialki-i-grunty?inp_text%5Bv%5D=ROD",
    ]

    def parse_html(self, html: str, base_url: Optional[str] = None) -> list[Listing]:
        root_url = base_url or self.base_url
        by_id: dict[str, Listing] = {}

        for listing in _extract_json_ld_listings(html, self.source, root_url):
            if is_individual_offer_url(listing.url, self.source):
                by_id[listing.id] = listing

        card_pattern = re.compile(
            r"<(?:article|li)\b[^>]*class=[\"'][^\"']*(?:element|offer-item|offerCard|cnt)[^\"']*[\"'][^>]*>(.*?)</(?:article|li)>|<article\b[^>]*>(.*?)</article>",
            re.IGNORECASE | re.DOTALL,
        )
        for match in card_pattern.finditer(html):
            block = match.group(1) or match.group(2) or ""
            href_m = re.search(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>", block, re.I)
            if not href_m:
                continue
            full_url = urllib.parse.urljoin(root_url, html_lib.unescape(href_m.group(1)))
            if not is_individual_offer_url(full_url, self.source):
                continue

            title_m = re.search(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", block, re.I | re.DOTALL)
            title = strip_html_tags(title_m.group(1)) if title_m else ""
            if not title:
                continue

            loc_m = re.search(
                r"<(?:span|p|div)\b[^>]*class=[\"'][^\"']*(?:location|city|address)[^\"']*[\"'][^>]*>(.*?)</(?:span|p|div)>",
                block,
                re.I | re.DOTALL,
            )
            block_text = strip_html_tags(block)
            location_raw = strip_html_tags(loc_m.group(1)) if loc_m else block_text[:120]
            price_pln = parse_price_pln(block_text)
            area_m2 = parse_area_m2(block_text)
            raw_id = _extract_slug_id(full_url)
            listing_id = make_listing_id(self.source, raw_id)

            if listing_id not in by_id:
                by_id[listing_id] = Listing(
                    id=listing_id,
                    source=self.source,
                    title=title,
                    url=full_url,
                    price_pln=price_pln,
                    price_raw=f"{int(price_pln)} zł" if price_pln else None,
                    area_m2=area_m2,
                    location_raw=location_raw,
                    description_snippet=block_text[:300],
                )

        return list(by_id.values())
