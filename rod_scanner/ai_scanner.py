"""LLM-powered search, web discovery, semantic location verification, and English translation for ROD listings.

Supports two automatic backends with zero external dependencies (standard library `urllib.request`):
- **Backend A (Gemini API)**: used when `GEMINI_API_KEY`, `AGY_API_KEY`, or `GOOGLE_API_KEY` is set.
- **Backend B (GitHub Models API)**: used automatically in GitHub Actions via built-in `GITHUB_TOKEN`
  (`https://models.github.ai/inference/chat/completions`, fallback `https://models.inference.ai.azure.com/chat/completions`)
  with `openai/gpt-4o-mini` / `gpt-4o-mini`.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from typing import Any, Optional, Sequence
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .matcher import _is_document_or_admin_notice, classify_listing, filter_listings
from .models import Listing
from .scrapers.portals import is_individual_offer_url

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_GITHUB_MODEL = "openai/gpt-4o-mini"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GITHUB_MODELS_ENDPOINTS = (
    "https://models.github.ai/inference/chat/completions",
    "https://models.inference.ai.azure.com/chat/completions",
)

WEB_SEARCH_QUERIES: tuple[str, ...] = (
    "site:olx.pl/d/oferta działka ROD Warszawa Mokotów OR Ochota",
    "site:otodom.pl/pl/oferta działka ROD Warszawa Mokotów OR Ochota",
    "site:gratka.pl działka ROD Warszawa Mokotów OR Ochota",
    "site:sprzedajemy.pl działka ROD Warszawa",
    "site:mazowiecki.pzd.pl działki Warszawa",
)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_VALID_DISTRICTS = {"mokotow", "ochota", "warszawa_unassigned", "excluded"}
_DISTRICT_SORT_PRIORITY = {
    "mokotow": 0,
    "ochota": 0,
    "warszawa_unassigned": 1,
}

_SEARCH_PROMPT = """You are an expert Warsaw real-estate and ROD (Rodzinne Ogrody Działkowe) allotment research agent.
Use Google Search to find current, active listings and official announcements of ROD allotment plots ("działki ROD", "ogródki działkowe", "prawo do działki ROD") for sale or allocation in Warsaw, Poland — specifically in the districts of **Mokotów** and **Ochota**.

Search across:
1. Major Polish classified portals: OLX.pl, Otodom.pl, Gratka.pl, Sprzedajemy.pl, Morizon.pl, Nieruchomosci-online.pl
2. Official PZD Okręg Mazowiecki announcements (`mazowiecki.pzd.pl`) and Warsaw ROD garden websites
3. Key target ROD gardens in Mokotów and Ochota:
   - Mokotów: ROD im. Obrońców Pokoju (ul. Odyńca / Racławicka / al. Niepodległości), ROD Rakowiec (ul. Rostafińskich / Chodkiewicza / Pole Mokotowskie), ROD Czerniaków (ul. Bernardyńska / Czerniakowska / Gołkowska), ROD Siekierki / ROD 1 Maja / ROD Antoniewska / Bartycka / Bluszczańska / Polska / Gościniec / Gwintowa, ROD SGGW, ROD Służewiec, ROD Stegny, ROD Sadyba
   - Ochota: ROD Fort Szczęśliwice (ul. Drawska / Śmigłowca / Szczęśliwicka / Włodarzewska / Dickensa), ROD im. Żwirki i Wigury (ul. Żwirki i Wigury / Księcia Trojdena / Pawińskiego / Banacha)

Strictly reject bait listings outside Warsaw/Mokotów/Ochota (e.g. "20 min od Mokotowa", Piaseczno, Konstancin, Białołęka, Wawer, Targówek), reject apartments, offices, or building plots ("działka budowlana"), and strictly reject PZD/ROD documents, PDFs, form templates ("wzór umowy/wniosku"), statutes ("statut"), regulations ("regulamin"), fee notices ("opłaty", "składki"), utility notices ("woda", "prąd", "śmieci"), meeting announcements ("walne zebranie"), or general guides that are NOT an actual available plot for sale or allocation.

Return ONLY a valid JSON array of objects (no markdown prose outside the JSON array). Each object must have:
- "id": string (optional unique identifier, e.g. "olx:12345")
- "source": string (e.g. "olx", "otodom", "gratka", "sprzedajemy", "morizon", "nieruchomosci_online", "pzd_mazowiecki", "rod_garden", or "ai_search")
- "title": string — clear **English title** preserving the Polish garden/street name (e.g. "300 m² ROD Allotment at ROD Obrońców Pokoju (ul. Odyńca, Mokotów) — Brick Cottage & Electricity")
- "url": string — direct URL to the listing or announcement
- "price_pln": number or null — price in PLN if stated
- "price_raw": string or null — raw price string (e.g. "85,000 PLN")
- "area_m2": number or null — plot area in m² if stated
- "location_raw": string — location string from the listing (e.g. "Warsaw, Mokotów")
- "description_snippet": string — short English summary of the listing
- "published_at": string or null — publication date if available
- "district_match": string — one of "mokotow", "ochota", or "warszawa_unassigned"
- "matched_garden": string or null — canonical ROD garden name (e.g. "ROD im. Obrońców Pokoju", "ROD Fort Szczęśliwice", "ROD Rakowiec")
- "match_reason": string — clear English explanation of why this listing matches Mokotów or Ochota
"""

_ENRICH_SYSTEM_PROMPT = """You are an expert Warsaw ROD (Rodzinne Ogrody Działkowe) allotment evaluator and Polish-to-English translator.
You will receive a JSON array of raw scraped Polish classified listings and announcements.
Evaluate every listing and return a JSON object `{"listings": [...]}` (or JSON array `[...]`) containing one enriched object per input listing, keeping the exact `"id"`.

For each listing:
1. **Semantically verify location**:
   - Reject bait listings that are actually outside Mokotów/Ochota/Warsaw (for example: "20 min od Mokotowa", "blisko Ochoty - Piaseczno", "dojazd z Mokotowa 15 min", or listings located in Białołęka, Wawer, Targówek, Bemowo, Bielany, Ursynów, Wola, Praga, Ursus, Wilanów, Włochy, Piaseczno, Marki, Legionowo, etc.).
   - Reject non-ROD listings such as apartments ("mieszkanie", "kawalerka"), commercial offices ("lokal biurowy"), or building plots ("działka budowlana").
   - Strictly reject (set `"district_match": "excluded"`) any PZD/ROD document, PDF/DOC/XLS file, form template ("wzór umowy", "wniosek", "deklaracja"), statute ("statut"), regulation ("regulamin", "uchwała", "ustawa"), fee/dues notice ("opłaty", "składki"), utility notice ("woda", "prąd", "śmieci", "odpady"), meeting announcement ("walne zebranie", "dyżur"), or general article/guide ("poradnik", "jak kupić działkę") that is NOT an actual available plot for sale, transfer, tender, or allocation.
   - Set "district_match" to accurately reflect the true location:
     - "mokotow" — confirmed in Warsaw Mokotów (e.g. ROD im. Obrońców Pokoju, ROD Rakowiec, ROD Czerniaków, ROD Siekierki, Odyńca, Stegny, Sadyba, Służewiec, Czerniakowska, Bartycka, Antoniewska, Polska)
     - "ochota" — confirmed in Warsaw Ochota (e.g. ROD Fort Szczęśliwice, ROD im. Żwirki i Wigury, Drawska, Szczęśliwicka, Włodarzewska, Śmigłowca, Księcia Trojdena, Pawińskiego, Grójecka)
     - "warszawa_unassigned" — confirmed ROD plot in Warsaw, but exact district is unspecified (and not in an excluded district)
     - "excluded" — outside Mokotów/Ochota (or outside Warsaw) or not a ROD allotment
2. **Identify exact ROD garden**:
   - Populate "matched_garden" with the canonical garden name if identifiable (e.g. "ROD im. Obrońców Pokoju", "ROD Fort Szczęśliwice", "ROD Rakowiec", "ROD Czerniaków", "ROD Siekierki", "ROD im. Żwirki i Wigury", "ROD SGGW", "ROD Służewiec", "ROD Stegny", "ROD Sadyba"), or null if unknown.
3. **Translate to English**:
   - Rewrite "title" into a clear, natural **English title** while keeping the Polish garden and street name (e.g. "300 m² ROD Allotment at ROD Obrońców Pokoju (ul. Odyńca, Mokotów) — Brick Cottage & Electricity").
   - Set "match_reason" in clear **English** explaining why the listing matched Mokotów/Ochota (or why it was excluded).
4. **Extract missing numeric metrics**:
   - Extract numeric "price_pln" (in PLN) and "area_m2" (in square meters) from the Polish title or description if they are null/missing.

Return ONLY valid JSON with keys per item:
"id", "title", "district_match", "matched_garden", "match_reason", "price_pln", "area_m2".
"""


def get_api_key() -> Optional[str]:
    """Return the configured Gemini / AGY API key or GitHub Models token from environment variables."""
    for env_name in (
        "GEMINI_API_KEY",
        "AGY_API_KEY",
        "GOOGLE_API_KEY",
        "GITHUB_TOKEN",
        "GH_MODELS_TOKEN",
    ):
        val = os.environ.get(env_name)
        if val and val.strip():
            return val.strip()
    return None


def detect_ai_backend(api_key: Optional[str] = None) -> tuple[str, str]:
    """Determine which LLM backend ('gemini', 'github_models', or 'none') and token to use."""
    gemini_env = (
        (os.environ.get("GEMINI_API_KEY") or "").strip()
        or (os.environ.get("AGY_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_API_KEY") or "").strip()
    )
    gh_env = (
        (os.environ.get("GITHUB_TOKEN") or "").strip()
        or (os.environ.get("GH_MODELS_TOKEN") or "").strip()
    )

    if api_key is not None:
        cleaned = api_key.strip()
        if not cleaned:
            return "none", ""
        if cleaned.startswith(("ghp_", "ghs_", "gho_", "ghu_", "github_pat_")):
            return "github_models", cleaned
        if gh_env and cleaned == gh_env and cleaned != gemini_env:
            return "github_models", cleaned
        return "gemini", cleaned

    if gemini_env:
        return "gemini", gemini_env
    if gh_env:
        return "github_models", gh_env
    return "none", ""


def get_gemini_model(model: Optional[str] = None) -> str:
    """Resolve the Gemini model name from explicit parameter or GEMINI_MODEL env var."""
    env_model = (os.environ.get("GEMINI_MODEL") or "").strip()
    if model and model.strip() and model.strip() != DEFAULT_GEMINI_MODEL:
        return model.strip()
    if env_model:
        return env_model
    if model and model.strip():
        return model.strip()
    return DEFAULT_GEMINI_MODEL


def get_github_model(model: Optional[str] = None) -> str:
    """Resolve the GitHub Models model name from parameter or GITHUB_MODELS_MODEL env var."""
    env_model = (os.environ.get("GITHUB_MODELS_MODEL") or "").strip()
    if (
        model
        and model.strip()
        and model.strip() not in (DEFAULT_GEMINI_MODEL, DEFAULT_GITHUB_MODEL)
        and not model.strip().startswith("gemini-")
    ):
        return model.strip()
    if env_model:
        return env_model
    return DEFAULT_GITHUB_MODEL


def _sanitize_error(exc: Exception, secret: Optional[str] = None) -> str:
    """Scrub any API key, token, or query-string secret from an exception message."""
    msg = str(exc)
    if secret and secret.strip():
        msg = msg.replace(secret.strip(), "[REDACTED]")
        encoded = urllib.parse.quote(secret.strip(), safe="")
        if encoded:
            msg = msg.replace(encoded, "[REDACTED]")
    msg = re.sub(r"([?&]key=)[^&\s\"']+", r"\1[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"\b(gh[psou]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]+)\b", "[REDACTED]", msg)
    return msg


def _call_gemini_generate_content(
    api_key: str,
    model: str,
    payload: dict[str, Any],
    timeout: int = 45,
) -> dict[str, Any]:
    """Call the Gemini REST `generateContent` endpoint using standard library `urllib.request`."""
    effective_model = get_gemini_model(model)
    encoded_model = urllib.parse.quote(effective_model, safe="-_.")
    url = f"{GEMINI_API_BASE}/{encoded_model}:generateContent"

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw_bytes = resp.read()
        return json.loads(raw_bytes.decode("utf-8", errors="replace"))


def _call_github_models_chat(
    token: str,
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    timeout: int = 45,
) -> str:
    """Call GitHub Models Chat Completions API (primary `models.github.ai`, fallback `models.inference.ai.azure.com`)."""
    preferred_model = get_github_model(model)
    last_error: Optional[Exception] = None

    for endpoint in GITHUB_MODELS_ENDPOINTS:
        # On Azure inference endpoint, both "gpt-4o-mini" and "openai/gpt-4o-mini" may be tried
        candidate_models = [preferred_model]
        if "azure.com" in endpoint and "/" in preferred_model:
            short_model = preferred_model.split("/", 1)[1]
            if short_model not in candidate_models:
                candidate_models.append(short_model)

        for candidate_model in candidate_models:
            payload: dict[str, Any] = {
                "model": candidate_model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "rod-warszawa-scanner/0.1",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw_bytes = resp.read()
                    data = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content")
                    if isinstance(content, str) and content.strip():
                        return content.strip()
            except Exception as exc:
                last_error = exc
                continue

    raise RuntimeError(f"GitHub Models API call failed: {last_error}")


def extract_grounding_urls(response_data: dict[str, Any]) -> list[str]:
    """Extract grounding web URLs from `candidates[0].groundingMetadata.groundingChunks`."""
    urls: list[str] = []
    seen: set[str] = set()
    candidates = response_data.get("candidates")
    if not isinstance(candidates, list):
        return urls

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        grounding_meta = candidate.get("groundingMetadata")
        if not isinstance(grounding_meta, dict):
            continue
        chunks = grounding_meta.get("groundingChunks")
        if not isinstance(chunks, list):
            continue
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            web_info = chunk.get("web")
            if isinstance(web_info, dict):
                uri = web_info.get("uri") or web_info.get("url")
                if isinstance(uri, str) and uri.strip() and uri.strip() not in seen:
                    seen.add(uri.strip())
                    urls.append(uri.strip())
            retrieved = chunk.get("retrievedContext")
            if isinstance(retrieved, dict):
                uri = retrieved.get("uri") or retrieved.get("url")
                if isinstance(uri, str) and uri.strip() and uri.strip() not in seen:
                    seen.add(uri.strip())
                    urls.append(uri.strip())
    return urls


def _extract_text_from_response(response_data: dict[str, Any]) -> str:
    """Extract concatenated text parts from the first candidate in a Gemini response."""
    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""
    first = candidates[0]
    if not isinstance(first, dict):
        return ""
    content = first.get("content")
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if not isinstance(parts, list):
        return ""

    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True:
            continue
        text_val = part.get("text")
        if isinstance(text_val, str) and text_val.strip():
            texts.append(text_val)
    return "\n".join(texts).strip()


def _parse_json_from_text(text: str) -> Any:
    """Parse JSON from model text, stripping markdown code fences or surrounding prose if present."""
    if not text or not text.strip():
        raise ValueError("Empty response text from LLM API")

    cleaned = text.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, flags=re.IGNORECASE)
    if fence_match:
        fenced_body = fence_match.group(1).strip()
        try:
            return json.loads(fenced_body)
        except json.JSONDecodeError:
            pass

    first_bracket = cleaned.find("[")
    last_bracket = cleaned.rfind("]")
    if first_bracket != -1 and last_bracket > first_bracket:
        try:
            return json.loads(cleaned[first_bracket : last_bracket + 1])
        except json.JSONDecodeError:
            pass

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        try:
            return json.loads(cleaned[first_brace : last_brace + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse JSON from LLM response text: {cleaned[:200]!r}")


def _coerce_optional_float(val: Any) -> Optional[float]:
    """Safely convert numeric or string values into a float, or None."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val) if val > 0 else None
    if isinstance(val, str):
        cleaned = val.strip().replace("\xa0", " ").replace(" ", "")
        cleaned = re.sub(r"[^\d.,]", "", cleaned)
        if not cleaned:
            return None
        cleaned = cleaned.replace(",", ".")
        try:
            num = float(cleaned)
            return num if num > 0 else None
        except ValueError:
            return None
    return None


def _infer_source_from_url(url: str, explicit_source: Optional[str] = None) -> str:
    """Infer a canonical scraper source identifier from a URL or explicit source string."""
    if explicit_source and isinstance(explicit_source, str):
        src_clean = explicit_source.strip().lower()
        if src_clean and src_clean not in ("unknown", "web", "google", "search"):
            return src_clean

    lower_url = (url or "").lower()
    if "olx.pl" in lower_url:
        return "olx"
    if "otodom.pl" in lower_url:
        return "otodom"
    if "gratka.pl" in lower_url:
        return "gratka"
    if "sprzedajemy.pl" in lower_url:
        return "sprzedajemy"
    if "morizon.pl" in lower_url:
        return "morizon"
    if "nieruchomosci-online.pl" in lower_url:
        return "nieruchomosci_online"
    if "pzd.pl" in lower_url:
        return "pzd_mazowiecki"
    if "rod" in lower_url:
        return "rod_garden"
    return "ai_search"


def _normalize_district_match(raw_district: Any) -> str:
    """Normalize a district_match string returned by an LLM."""
    if not isinstance(raw_district, str):
        return "unknown"
    cleaned = raw_district.strip().lower()
    if cleaned in ("mokotow", "mokotów"):
        return "mokotow"
    if cleaned in ("ochota",):
        return "ochota"
    if cleaned in ("warszawa_unassigned", "warszawa", "warsaw", "unassigned"):
        return "warszawa_unassigned"
    if cleaned in ("excluded", "exclude", "rejected", "none", "outside"):
        return "excluded"
    return "unknown"


def _strip_html_tags(raw_html: str) -> str:
    """Strip HTML tags and unescape entities into clean single-line text."""
    if not raw_html:
        return ""
    without_tags = re.sub(r"<[^>]+>", " ", raw_html)
    unescaped = html.unescape(without_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _extract_price_and_area_from_text(text: str) -> tuple[Optional[float], Optional[str], Optional[float]]:
    """Extract `(price_pln, price_raw, area_m2)` from a title/snippet string if present."""
    if not text:
        return None, None, None

    price_pln: Optional[float] = None
    price_raw: Optional[str] = None
    price_match = re.search(
        r"(?<!\d)(\d{2,3}(?:[\s\xa0.,]\d{3})+|\d{4,7})(?:[.,]\d{1,2})?\s*(?:zł|pln\b)",
        text,
        flags=re.IGNORECASE,
    )
    if price_match:
        digits = re.sub(r"[^\d]", "", price_match.group(1))
        if digits:
            val = float(digits)
            if 1000 <= val <= 2_000_000:
                price_pln = val
                price_raw = f"{int(val):,} PLN"

    area_m2: Optional[float] = None
    area_match = re.search(
        r"(?<!\d)(\d{2,4}(?:[.,]\d{1,2})?)\s*(?:m2|m²|mkw|m\.kw\.?)\b",
        text,
        flags=re.IGNORECASE,
    )
    if area_match:
        try:
            area_val = float(area_match.group(1).replace(",", "."))
            if 50 <= area_val <= 5000:
                area_m2 = area_val
        except ValueError:
            pass

    return price_pln, price_raw, area_m2


def _unwrap_ddg_url(href: str) -> str:
    """Resolve DuckDuckGo redirect links (`//duckduckgo.com/l/?uddg=...`) to the target URL."""
    cleaned = html.unescape((href or "").strip())
    if cleaned.startswith("//"):
        cleaned = "https:" + cleaned
    parsed = urllib.parse.urlparse(cleaned)
    if "duckduckgo.com" in parsed.netloc:
        qs = urllib.parse.parse_qs(parsed.query)
        uddg = qs.get("uddg")
        if uddg and uddg[0]:
            return uddg[0].strip()
    return cleaned


def _is_relevant_portal_or_rod_url(url: str) -> bool:
    """Return True if URL points to a Polish classified listing or PZD/ROD page rather than a search index."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if any(
        skip in host
        for skip in ("bing.com", "duckduckgo.com", "google.", "microsoft.com", "yahoo.com")
    ):
        return False

    if "olx.pl" in host:
        return is_individual_offer_url(url, "olx")
    if "otodom.pl" in host:
        return is_individual_offer_url(url, "otodom")
    if "gratka.pl" in host:
        return is_individual_offer_url(url, "gratka")
    if "sprzedajemy.pl" in host:
        return is_individual_offer_url(url, "sprzedajemy")
    if "morizon.pl" in host or "nieruchomosci-online.pl" in host:
        return len(path.strip("/")) > 8
    if "pzd.pl" in host or "rod" in host:
        return True
    return False


def _parse_bing_rss_items(xml_text: str) -> list[tuple[str, str, str]]:
    """Parse `(title, url, snippet)` tuples from Bing RSS XML."""
    results: list[tuple[str, str, str]] = []
    if not xml_text or not xml_text.strip():
        return results

    try:
        root = ET.fromstring(xml_text.strip())
        for item in root.findall(".//item"):
            title = _strip_html_tags(item.findtext("title") or "")
            link = (item.findtext("link") or "").strip()
            desc = _strip_html_tags(item.findtext("description") or "")
            if title and link:
                results.append((title, link, desc))
        return results
    except ET.ParseError:
        pass

    # Regex fallback if XML contains unescaped characters
    for match in re.finditer(r"<item>([\s\S]*?)</item>", xml_text, flags=re.IGNORECASE):
        block = match.group(1)
        t_m = re.search(r"<title>([\s\S]*?)</title>", block, flags=re.IGNORECASE)
        l_m = re.search(r"<link>([\s\S]*?)</link>", block, flags=re.IGNORECASE)
        d_m = re.search(r"<description>([\s\S]*?)</description>", block, flags=re.IGNORECASE)
        title = _strip_html_tags(t_m.group(1) if t_m else "")
        link = html.unescape((l_m.group(1) if l_m else "").strip())
        desc = _strip_html_tags(d_m.group(1) if d_m else "")
        if title and link:
            results.append((title, link, desc))
    return results


def _parse_ddg_html_items(html_text: str) -> list[tuple[str, str, str]]:
    """Parse `(title, url, snippet)` tuples from DuckDuckGo HTML search results."""
    results: list[tuple[str, str, str]] = []
    if not html_text:
        return results

    anchor_pattern = re.compile(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>([\s\S]*?)</a>',
        flags=re.IGNORECASE,
    )
    snippet_pattern = re.compile(
        r'class="[^"]*result__snippet[^"]*"[^>]*>([\s\S]*?)</(?:a|div|span)>',
        flags=re.IGNORECASE,
    )

    anchors = list(anchor_pattern.finditer(html_text))
    snippets = [ _strip_html_tags(m.group(1)) for m in snippet_pattern.finditer(html_text) ]

    for idx, match in enumerate(anchors):
        raw_href, raw_title = match.group(1), match.group(2)
        url = _unwrap_ddg_url(raw_href)
        title = _strip_html_tags(raw_title)
        snippet = snippets[idx] if idx < len(snippets) else ""
        if title and url:
            results.append((title, url, snippet))
    return results


def web_search_rod_listings(
    timeout: int = 20,
    queries: Sequence[str] = WEB_SEARCH_QUERIES,
) -> list[Listing]:
    """Discover ROD listings across Polish portals and PZD via Bing RSS and DuckDuckGo HTML search.

    Designed to work reliably from GitHub Actions runners even when portals block direct category scraping.
    """
    discovered: list[Listing] = []
    seen_urls: set[str] = set()
    per_request_timeout = max(5, min(timeout, 15))

    for query in queries:
        encoded_q = urllib.parse.quote_plus(query)
        raw_items: list[tuple[str, str, str]] = []

        # 1. Query Bing RSS
        bing_url = f"https://www.bing.com/search?format=rss&q={encoded_q}"
        try:
            req = urllib.request.Request(
                bing_url,
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
                    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            with urllib.request.urlopen(req, timeout=per_request_timeout) as resp:
                xml_body = resp.read().decode("utf-8", errors="replace")
            raw_items.extend(_parse_bing_rss_items(xml_body))
        except Exception:
            pass

        # 2. Query DuckDuckGo HTML
        ddg_url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        try:
            req = urllib.request.Request(
                ddg_url,
                headers={
                    "User-Agent": _BROWSER_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            with urllib.request.urlopen(req, timeout=per_request_timeout) as resp:
                html_body = resp.read().decode("utf-8", errors="replace")
            raw_items.extend(_parse_ddg_html_items(html_body))
        except Exception:
            pass

        for title, url, snippet in raw_items:
            norm_url = url.strip().rstrip("/")
            if not norm_url or norm_url.lower() in seen_urls:
                continue
            if not _is_relevant_portal_or_rod_url(norm_url):
                continue
            if _is_document_or_admin_notice(title, norm_url):
                continue

            seen_urls.add(norm_url.lower())
            source = _infer_source_from_url(norm_url)
            url_hash = hashlib.sha1(norm_url.encode("utf-8")).hexdigest()[:12]
            listing_id = f"{source}:{url_hash}"

            combined_text = f"{title} {snippet}"
            price_pln, price_raw, area_m2 = _extract_price_and_area_from_text(combined_text)

            listing = Listing(
                id=listing_id,
                source=source,
                title=title,
                url=norm_url,
                price_pln=price_pln,
                price_raw=price_raw or "Price not listed",
                area_m2=area_m2,
                location_raw="Warsaw",
                description_snippet=snippet,
            )
            classify_listing(listing)
            if listing.district_match != "excluded":
                discovered.append(listing)

    return discovered


def ai_search_rod_listings(
    api_key: Optional[str] = None,
    model: str = DEFAULT_GEMINI_MODEL,
    timeout: int = 45,
) -> tuple[list[Listing], list[str]]:
    """Search Polish classifieds & PZD boards for Warsaw (Mokotów/Ochota) ROD plots.

    - When Backend A (Gemini API) is configured, calls Gemini `generateContent` with Google Search grounding.
    - When Backend B (GitHub Models via `GITHUB_TOKEN`) is used, performs web search discovery via `web_search_rod_listings`.
    """
    warnings: list[str] = []
    backend, resolved_key = detect_ai_backend(api_key)
    if backend == "none" or not resolved_key:
        warnings.append("ai_search: no GEMINI_API_KEY, AGY_API_KEY, or GITHUB_TOKEN configured")
        return [], warnings

    if backend == "github_models":
        try:
            discovered = web_search_rod_listings(timeout=min(timeout, 20))
            return discovered, warnings
        except Exception as exc:
            warnings.append(f"ai_search: web search discovery failed: {exc}")
            return [], warnings

    payload: dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": _SEARCH_PROMPT}],
            }
        ],
        "tools": [{"google_search": {}}],
        "generationConfig": {
            "temperature": 0.1,
        },
    }

    try:
        response_data = _call_gemini_generate_content(
            api_key=resolved_key,
            model=model,
            payload=payload,
            timeout=timeout,
        )
    except Exception as exc:
        warnings.append(f"ai_search: Gemini API request failed: {_sanitize_error(exc, resolved_key)}")
        return [], warnings

    grounding_urls = extract_grounding_urls(response_data)
    text = _extract_text_from_response(response_data)
    if not text:
        warnings.append("ai_search: Gemini returned an empty text response")
        return [], warnings

    try:
        parsed = _parse_json_from_text(text)
    except ValueError as exc:
        warnings.append(f"ai_search: failed to parse JSON from Gemini response: {exc}")
        return [], warnings

    if isinstance(parsed, dict):
        items = parsed.get("listings") or parsed.get("results") or parsed.get("items") or []
    elif isinstance(parsed, list):
        items = parsed
    else:
        warnings.append("ai_search: unexpected JSON root type from Gemini response")
        return [], warnings

    listings: list[Listing] = []
    seen_urls: set[str] = set()

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        url = str(item.get("url") or "").strip()
        if not url and idx < len(grounding_urls):
            url = grounding_urls[idx]
        if not url:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        source = _infer_source_from_url(url, item.get("source"))
        raw_id = str(item.get("id") or "").strip()
        if not raw_id:
            url_hash = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
            raw_id = f"{source}:{url_hash}"

        title = str(item.get("title") or "").strip()
        if not title:
            continue
        if _is_document_or_admin_notice(title, url):
            continue

        price_pln = _coerce_optional_float(item.get("price_pln"))
        price_raw = item.get("price_raw")
        if price_raw is not None:
            price_raw = str(price_raw).strip() or None
        elif price_pln is not None:
            price_raw = f"{int(price_pln):,} PLN"

        area_m2 = _coerce_optional_float(item.get("area_m2"))
        location_raw = str(item.get("location_raw") or "Warsaw").strip()
        description_snippet = str(item.get("description_snippet") or "").strip()
        published_at = item.get("published_at")
        if published_at is not None:
            published_at = str(published_at).strip() or None

        district_match = _normalize_district_match(item.get("district_match"))
        matched_garden = item.get("matched_garden")
        if matched_garden is not None:
            matched_garden = str(matched_garden).strip() or None
        match_reason = str(item.get("match_reason") or "").strip()

        listing = Listing(
            id=raw_id,
            source=source,
            title=title,
            url=url,
            price_pln=price_pln,
            price_raw=price_raw,
            area_m2=area_m2,
            location_raw=location_raw,
            description_snippet=description_snippet,
            published_at=published_at,
            district_match=district_match,
            matched_garden=matched_garden,
            match_reason=match_reason,
        )

        if listing.district_match == "unknown":
            classify_listing(listing)
        elif not listing.match_reason and listing.district_match in ("mokotow", "ochota"):
            listing.match_reason = (
                f"AI-verified {listing.district_match.capitalize()} ROD listing"
                + (f" ({listing.matched_garden})" if listing.matched_garden else "")
            )

        if listing.district_match != "excluded":
            listings.append(listing)

    return listings, warnings


def _enrich_batch_via_llm(
    batch: list[Listing],
    backend: str,
    token: str,
    model: str,
    timeout: int,
) -> list[dict[str, Any]]:
    """Send a single batch of listings to Gemini or GitHub Models and return parsed enrichment dicts."""
    input_records = [
        {
            "id": item.id,
            "source": item.source,
            "title": item.title,
            "url": item.url,
            "price_pln": item.price_pln,
            "price_raw": item.price_raw,
            "area_m2": item.area_m2,
            "location_raw": item.location_raw,
            "description_snippet": item.description_snippet,
            "district_match": item.district_match,
            "matched_garden": item.matched_garden,
        }
        for item in batch
    ]

    user_json_text = f"Input listings JSON:\n{json.dumps(input_records, ensure_ascii=False)}"

    if backend == "github_models":
        text = _call_github_models_chat(
            token=token,
            system_prompt=_ENRICH_SYSTEM_PROMPT,
            user_prompt=user_json_text,
            model=model,
            timeout=timeout,
        )
    else:
        prompt_text = f"{_ENRICH_SYSTEM_PROMPT}\n\n{user_json_text}"
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt_text}],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.1,
            },
        }
        response_data = _call_gemini_generate_content(
            api_key=token,
            model=model,
            payload=payload,
            timeout=timeout,
        )
        text = _extract_text_from_response(response_data)

    parsed = _parse_json_from_text(text)
    if isinstance(parsed, dict):
        enriched_items = (
            parsed.get("listings")
            or parsed.get("results")
            or parsed.get("items")
            or []
        )
    elif isinstance(parsed, list):
        enriched_items = parsed
    else:
        raise ValueError("Unexpected JSON root type from LLM enrichment response")

    if not isinstance(enriched_items, list) or not enriched_items:
        raise ValueError("LLM returned no enriched items")
    return enriched_items


def ai_enrich_and_filter_listings(
    listings: list[Listing],
    api_key: Optional[str] = None,
    model: str = DEFAULT_GEMINI_MODEL,
    include_unassigned: bool = True,
    timeout: int = 45,
) -> tuple[list[Listing], list[str]]:
    """Semantically verify location, identify ROD garden, extract metrics, and translate titles to English.

    Supports both Backend A (Gemini API) and Backend B (GitHub Models via `GITHUB_TOKEN`).
    Falls back gracefully to `rod_scanner.matcher.filter_listings` if no API key/token is configured or the API call fails.
    """
    warnings: list[str] = []
    if not listings:
        return [], warnings

    backend, resolved_key = detect_ai_backend(api_key)
    if backend == "none" or not resolved_key:
        warnings.append("ai_enrich: no API key configured; falling back to keyword matcher")
        return filter_listings(listings, include_unassigned=include_unassigned), warnings

    batch_size = 25
    all_enriched_items: list[dict[str, Any]] = []
    try:
        for start in range(0, len(listings), batch_size):
            batch = listings[start : start + batch_size]
            batch_enriched = _enrich_batch_via_llm(
                batch=batch,
                backend=backend,
                token=resolved_key,
                model=model,
                timeout=timeout,
            )
            all_enriched_items.extend(batch_enriched)
    except Exception as exc:
        warnings.append(
            f"ai_enrich: {backend} enrichment failed ({_sanitize_error(exc, resolved_key)}); falling back to keyword matcher"
        )
        return filter_listings(listings, include_unassigned=include_unassigned), warnings

    by_id: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    for entry in all_enriched_items:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or "").strip()
        entry_url = str(entry.get("url") or "").strip()
        if entry_id:
            by_id[entry_id] = entry
        if entry_url:
            by_url[entry_url] = entry

    allowed_districts = {"mokotow", "ochota"}
    if include_unassigned:
        allowed_districts.add("warszawa_unassigned")

    matched: list[Listing] = []
    for idx, item in enumerate(listings):
        if _is_document_or_admin_notice(item.title, item.url):
            item.district_match = "excluded"
            item.matched_garden = None
            item.match_reason = "Excluded: PZD/ROD document or administrative notice, not an available plot"
            continue

        enriched = by_id.get(item.id) or by_url.get(item.url)
        if (
            enriched is None
            and len(all_enriched_items) == len(listings)
            and isinstance(all_enriched_items[idx], dict)
        ):
            enriched = all_enriched_items[idx]

        if enriched is not None:
            new_title = str(enriched.get("title") or "").strip()
            if new_title:
                item.title = new_title

            district = _normalize_district_match(enriched.get("district_match"))
            if district in _VALID_DISTRICTS:
                item.district_match = district
            else:
                classify_listing(item)

            if "matched_garden" in enriched:
                garden_val = enriched.get("matched_garden")
                item.matched_garden = str(garden_val).strip() if garden_val else None

            reason_val = str(enriched.get("match_reason") or "").strip()
            if reason_val:
                item.match_reason = reason_val

            extracted_price = _coerce_optional_float(enriched.get("price_pln"))
            if item.price_pln is None and extracted_price is not None:
                item.price_pln = extracted_price
                if not item.price_raw or item.price_raw == "Price not listed":
                    item.price_raw = f"{int(extracted_price):,} PLN"

            extracted_area = _coerce_optional_float(enriched.get("area_m2"))
            if item.area_m2 is None and extracted_area is not None:
                item.area_m2 = extracted_area
        else:
            classify_listing(item)

        if item.district_match in allowed_districts:
            matched.append(item)

    matched.sort(key=lambda x: _DISTRICT_SORT_PRIORITY.get(x.district_match, 99))
    return matched, warnings
