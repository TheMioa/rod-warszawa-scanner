"""Smart location and ROD-relevance matcher for Mokotów & Ochota (Warsaw)."""

from __future__ import annotations

import re
from typing import Optional

from .models import Listing

# Polish diacritic translation table (lowercase & uppercase)
_DIACRITIC_MAP = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
        "Ą": "A",
        "Ć": "C",
        "Ę": "E",
        "Ł": "L",
        "Ń": "N",
        "Ó": "O",
        "Ś": "S",
        "Ź": "Z",
        "Ż": "Z",
    }
)


def strip_diacritics(text: str) -> str:
    """Replace Polish diacritics with their ASCII equivalents."""
    if not text:
        return ""
    return text.translate(_DIACRITIC_MAP)


def normalize_text(text: str) -> str:
    """Lowercase, strip Polish diacritics, and normalize whitespace."""
    if not text:
        return ""
    ascii_lower = strip_diacritics(text).lower()
    return re.sub(r"\s+", " ", ascii_lower).strip()


# ---------------------------------------------------------------------------
# Gazetteer of Mokotów & Ochota ROD gardens, streets, and neighborhoods
# ---------------------------------------------------------------------------

MOKOTOW_GARDENS: list[tuple[str, list[str]]] = [
    (
        "ROD im. Obrońców Pokoju",
        [
            "obroncow pokoju",
            "rod odynca",
            "ogrody odynca",
            "odynca",
            "raclawicka",
            "raclawickiej",
            "al. niepodleglosci",
            "al niepodleglosci",
            "aleja niepodleglosci",
            "alei niepodleglosci",
        ],
    ),
    (
        "ROD Czerniaków",
        [
            "rod czerniakow",
            "ogrody czerniakow",
            "czerniakow",
            "czerniakowie",
            "bernardynska",
            "bernardynskiej",
            "czerniakowska",
            "czerniakowskiej",
            "golkowska",
            "golkowskiej",
            "jeziorko czerniakowskie",
            "jeziorka czerniakowskiego",
        ],
    ),
    (
        "ROD Siekierki",
        [
            "rod siekierki",
            "siekierki",
            "siekierkach",
            "siekierkowski",
            "rod 1 maja",
            "1 maja",
            "rod antoniewska",
            "antoniewska",
            "antoniewskiej",
            "bartycka",
            "bartyckiej",
            "bluszczanska",
            "bluszczanskiej",
            "ul. polska",
            "ul polska",
            "ulica polska",
            "ulicy polskiej",
            "rod polska",
            "gosciniec",
            "gwintowa",
            "gwintowej",
        ],
    ),
    (
        "ROD Rakowiec",
        [
            "rod rakowiec",
            "ogrody rakowiec",
            "rostafinskich",
            "chodkiewicza",
            "pole mokotowskie",
            "pola mokotowskiego",
            "polu mokotowskim",
            "rakowiec",
            "rakowcu",
        ],
    ),
    (
        "ROD SGGW",
        [
            "rod sggw",
            "sggw",
        ],
    ),
    (
        "ROD Służewiec",
        [
            "rod sluzewiec",
            "sluzewiec",
            "sluzewcu",
        ],
    ),
    (
        "ROD Stegny",
        [
            "rod stegny",
            "stegny",
            "stegnach",
        ],
    ),
    (
        "ROD Sadyba",
        [
            "rod sadyba",
            "sadyba",
            "sadybie",
        ],
    ),
]

MOKOTOW_LOCATIONS: list[str] = [
    "mokotow",
    "mokotowie",
    "dolny mokotow",
    "dolnym mokotowie",
    "gorny mokotow",
    "gornym mokotowie",
    "wygledow",
    "wygledowie",
    "wierzbno",
    "wierzbnie",
    "sluzew",
    "sluzewie",
    "sluzewiu",
    "krolikarnia",
    "krolikarni",
    "woronicza",
    "bukowinska",
    "bukowinskiej",
    "idzikowskiego",
    "sobieskiego",
    "sikorskiego",
    "dolna",
    "dolnej",
    "chelmska",
    "chelmskiej",
    "gagarina",
    "bobrowiecka",
    "bobrowieckiej",
]

OCHOTA_GARDENS: list[tuple[str, list[str]]] = [
    (
        "ROD Fort Szczęśliwice",
        [
            "fort szczesliwice",
            "rod szczesliwice",
            "ogrody szczesliwice",
            "szczesliwice",
            "szczesliwicach",
            "drawska",
            "drawskiej",
            "smiglowca",
            "szczesliwicka",
            "szczesliwickiej",
            "wlodarzewska",
            "wlodarzewskiej",
            "dickensa",
            "park szczesliwicki",
            "parku szczesliwickim",
            "parku szczesliwickiego",
        ],
    ),
    (
        "ROD im. Żwirki i Wigury",
        [
            "rod im. zwirki i wigury",
            "rod zwirki i wigury",
            "zwirki i wigury",
            "ksiecia trojdena",
            "trojdena",
            "pawinskiego",
            "banacha",
            "hankiewicza",
            "korotynskiego",
        ],
    ),
]

OCHOTA_LOCATIONS: list[str] = [
    "ochota",
    "ochocie",
    "stara ochota",
    "starej ochocie",
    "filtry",
    "filtrach",
    "kolonia lubeckiego",
    "kolonii lubeckiego",
    "grojecka",
    "grojeckiej",
    "wawelska",
    "wawelskiej",
    "filtrowa",
    "filtrowej",
    "niemcewicza",
    "rakowiec",
    "rakowcu",
]

# Canonical list of excluded Warsaw districts and nearby suburbs
EXCLUDED_LOCATIONS: list[tuple[str, list[str]]] = [
    ("Białołęka", ["bialoleka", "bialolece"]),
    ("Wawer", ["wawer", "wawrze"]),
    ("Targówek", ["targowek", "targowku"]),
    ("Bemowo", ["bemowo", "bemowie"]),
    ("Bielany", ["bielany", "bielanach"]),
    ("Ursynów", ["ursynow", "ursynowie"]),
    ("Wola", ["wola", "na woli", "warszawa wola", "dzielnica wola"]),
    ("Praga-Południe", ["praga-poludnie", "praga poludnie", "pradze poludnie"]),
    ("Praga-Północ", ["praga-polnoc", "praga polnoc", "pradze polnoc"]),
    ("Praga", ["praga", "pradze"]),
    ("Grochów", ["grochow", "grochowie"]),
    ("Saska Kępa", ["saska kepa", "saskiej kepie"]),
    ("Gocław", ["goclaw", "goclawiu"]),
    ("Tarchomin", ["tarchomin", "tarchominie"]),
    ("Bródno", ["brodno", "brodnie"]),
    ("Rembertów", ["rembertow", "rembertowie"]),
    ("Wesoła", ["wesola", "wesolej"]),
    ("Ursus", ["ursus", "ursusie"]),
    ("Wilanów", ["wilanow", "wilanowie"]),
    ("Żoliborz", ["zoliborz", "zoliborzu"]),
    ("Włochy", ["wlochy", "wlochach"]),
    ("Okęcie", ["okecie", "okeciu"]),
    ("Śródmieście", ["srodmiescie", "srodmiesciu"]),
    ("Marki", ["marki", "markach"]),
    ("Ząbki", ["zabki", "zabkach"]),
    ("Piaseczno", ["piaseczno", "piasecznie"]),
    ("Konstancin", ["konstancin", "konstancinie", "konstancin-jeziorna"]),
    ("Jabłonna", ["jablonna", "jablonnie", "jablonnej"]),
    ("Nieporęt", ["nieporet", "nieporecie"]),
    ("Legionowo", ["legionowo", "legionowie"]),
    ("Wołomin", ["wolomin", "wolominie"]),
    ("Pruszków", ["pruszkow", "pruszkowie"]),
    ("Otwock", ["otwock", "otwocku"]),
    ("Góra Kalwaria", ["gora kalwaria", "gorze kalwarii"]),
    ("Radzymin", ["radzymin", "radzyminie"]),
    ("Łomianki", ["lomianki", "lomiankach"]),
    ("Serock", ["serock", "serocku"]),
    ("Nadarzyn", ["nadarzyn", "nadarzynie"]),
]

# Keywords confirming a listing is about a ROD / allotment / recreational garden
_ROD_POSITIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brod\b"),
    re.compile(r"\bpzd\b"),
    re.compile(r"\bogrod(?:ek|ki|y|ow|ach|kach)?\s+dzialkow\w*"),
    re.compile(r"\brodzinn(?:y|e|ych)\s+ogrod\w*"),
    re.compile(r"\bdzialk(?:a|e|i|o|ach)\s+rekreacyjn\w*"),
    re.compile(r"\bdzialk(?:a|e|i|o)\s+rod\b"),
    re.compile(r"\bdzialk(?:a|e|i|o)\s+ogrodow\w*"),
    re.compile(r"\bprawo\s+do\s+dzialki\b"),
    re.compile(r"\bodstapie\s+dzialke\b"),
    re.compile(r"\bprzeniesienie\s+praw\s+do\s+dzialki\b"),
    re.compile(r"\baltan(?:a|ka|ke|ki)\s+na\s+dzialce\b"),
]

# Keywords indicating non-ROD commercial/residential real estate
_NON_ROD_NEGATIVE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bmieszkani(?:e|a)\b"),
    re.compile(r"\bapartament\w*\b"),
    re.compile(r"\bkawalerk(?:a|e|i)\b"),
    re.compile(r"\blokal\s+(?:biurow\w*|uzytkow\w*|handlow\w*)"),
    re.compile(r"\bbiuro\s+do\s+wynajecia\b"),
    re.compile(r"\bpowierzchnia\s+biurowa\b"),
    re.compile(r"\bdzialk(?:a|e|i)\s+(?:budowlan\w*|inwestycyjn\w*|przemyslow\w*|uslugow\w*)"),
    re.compile(r"\bhal(?:a|e)\s+magazynow\w*"),
]

_WARSAW_PATTERN = re.compile(r"\b(?:warszaw\w*|w-wa|wwa)\b")


def _contains_keyword(normalized_text: str, keyword: str) -> bool:
    """Match keyword on word boundaries inside normalized_text."""
    if not normalized_text or not keyword:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def _has_explicit_rod_marker(normalized_text: str) -> bool:
    """Return True if text has an unambiguous ROD / allotment garden phrase."""
    for pat in _ROD_POSITIVE_PATTERNS:
        if pat.search(normalized_text):
            return True
    for _, garden_keywords in MOKOTOW_GARDENS + OCHOTA_GARDENS:
        for kw in garden_keywords:
            if kw.startswith("rod ") or kw in (
                "obroncow pokoju",
                "fort szczesliwice",
                "zwirki i wigury",
            ):
                if _contains_keyword(normalized_text, kw):
                    return True
    return False


def is_rod_related(listing: Listing) -> bool:
    """Verify the listing is about a ROD / działka rekreacyjna / ogródek działkowy / PZD."""
    if listing.source in ("pzd_mazowiecki", "rod_garden"):
        return True

    title_norm = normalize_text(listing.title)
    desc_norm = normalize_text(listing.description_snippet)
    loc_norm = normalize_text(listing.location_raw)
    combined = f"{title_norm} {desc_norm} {loc_norm}".strip()

    has_strong_rod_in_title = _has_explicit_rod_marker(title_norm)
    has_strong_rod_anywhere = _has_explicit_rod_marker(combined)

    # Reject obvious commercial offices, apartments, or building plots unless explicitly ROD
    for neg_pat in _NON_ROD_NEGATIVE_PATTERNS:
        if neg_pat.search(title_norm) and not has_strong_rod_in_title:
            return False
        if neg_pat.search(combined) and not has_strong_rod_anywhere:
            return False

    return has_strong_rod_anywhere


def _match_mokotow_or_ochota(text_norm: str) -> Optional[tuple[str, Optional[str], str]]:
    """Attempt to match Mokotów or Ochota gardens/locations in normalized text.

    Returns (district_match, matched_garden, match_reason) or None.
    """
    if not text_norm:
        return None

    has_explicit_ochota = _contains_keyword(text_norm, "ochota") or _contains_keyword(
        text_norm, "ochocie"
    )
    has_explicit_mokotow = _contains_keyword(text_norm, "mokotow") or _contains_keyword(
        text_norm, "mokotowie"
    )

    # 1. Check specific Ochota gardens first when Ochota is explicitly mentioned,
    # or check all named gardens.
    for garden_name, keywords in OCHOTA_GARDENS:
        for kw in keywords:
            if _contains_keyword(text_norm, kw):
                return (
                    "ochota",
                    garden_name,
                    f"Matched Ochota garden '{garden_name}' via keyword '{kw}'",
                )

    for garden_name, keywords in MOKOTOW_GARDENS:
        for kw in keywords:
            if _contains_keyword(text_norm, kw):
                # Special case: bare "rakowiec"/"rakowcu" when "ochota" is explicitly stated
                # and no explicit Mokotów or "rod rakowiec" keyword is present
                if (
                    garden_name == "ROD Rakowiec"
                    and kw in ("rakowiec", "rakowcu")
                    and has_explicit_ochota
                    and not has_explicit_mokotow
                    and not _contains_keyword(text_norm, "rod rakowiec")
                    and not _contains_keyword(text_norm, "rostafinskich")
                    and not _contains_keyword(text_norm, "chodkiewicza")
                ):
                    return (
                        "ochota",
                        "ROD Rakowiec",
                        f"Matched Ochota (Rakowiec) via keyword '{kw}'",
                    )
                return (
                    "mokotow",
                    garden_name,
                    f"Matched Mokotów garden '{garden_name}' via keyword '{kw}'",
                )

    # Check bare "Polska" street reference on Siekierki (avoiding "Warszawa, Polska")
    if _contains_keyword(text_norm, "polska") and not re.search(
        r"\bwarszawa\s*,\s*polska\b", text_norm
    ):
        return (
            "mokotow",
            "ROD Siekierki",
            "Matched Mokotów garden 'ROD Siekierki' via street 'Polska'",
        )

    # 2. Check general Mokotów & Ochota neighborhoods and streets
    for kw in MOKOTOW_LOCATIONS:
        if _contains_keyword(text_norm, kw):
            return (
                "mokotow",
                None,
                f"Matched Mokotów location/street '{kw}'",
            )

    for kw in OCHOTA_LOCATIONS:
        if _contains_keyword(text_norm, kw):
            return (
                "ochota",
                None,
                f"Matched Ochota location/street '{kw}'",
            )

    return None


def _match_excluded_location(text_norm: str) -> Optional[str]:
    """Return canonical excluded district/suburb name if matched in text_norm."""
    if not text_norm:
        return None
    for canonical_name, keywords in EXCLUDED_LOCATIONS:
        for kw in keywords:
            if _contains_keyword(text_norm, kw):
                return canonical_name
    return None


def classify_listing(listing: Listing) -> Listing:
    """Classify a Listing into 'mokotow', 'ochota', 'warszawa_unassigned', or 'excluded'.

    Mutates and returns the listing with `district_match`, `matched_garden`, and `match_reason`.
    """
    if not is_rod_related(listing):
        listing.district_match = "excluded"
        listing.matched_garden = None
        listing.match_reason = "Excluded: listing is not ROD / recreational plot related"
        return listing

    loc_norm = normalize_text(listing.location_raw)
    title_norm = normalize_text(listing.title)
    desc_norm = normalize_text(listing.description_snippet)
    url_norm = normalize_text(listing.url)

    primary_norm = f"{title_norm} {loc_norm}".strip()
    full_norm = f"{title_norm} {loc_norm} {desc_norm}".strip()

    # Check if location_raw explicitly names an excluded district/suburb without Mokotów/Ochota
    loc_target_match = _match_mokotow_or_ochota(loc_norm)
    loc_excluded = _match_excluded_location(loc_norm)
    title_target_match = _match_mokotow_or_ochota(title_norm)
    title_excluded = _match_excluded_location(title_norm)

    # If primary fields (title + location_raw) match Mokotów or Ochota (and location_raw isn't
    # explicitly an excluded district with no target match in title):
    primary_target = _match_mokotow_or_ochota(primary_norm)
    if primary_target is not None:
        if loc_excluded and not loc_target_match and not title_target_match:
            listing.district_match = "excluded"
            listing.matched_garden = None
            listing.match_reason = f"Excluded district/suburb in location: {loc_excluded}"
            return listing

        district, garden, reason = primary_target
        # If garden wasn't found in title/location, check description for a specific garden name
        if garden is None and desc_norm:
            desc_target = _match_mokotow_or_ochota(desc_norm)
            if desc_target and desc_target[0] == district and desc_target[1] is not None:
                garden = desc_target[1]
                reason = desc_target[2]
        listing.district_match = district
        listing.matched_garden = garden
        listing.match_reason = reason
        return listing

    # If primary fields explicitly name an excluded district/suburb, exclude immediately
    if loc_excluded or title_excluded:
        excluded_name = loc_excluded or title_excluded
        listing.district_match = "excluded"
        listing.matched_garden = None
        listing.match_reason = f"Excluded district/suburb: {excluded_name}"
        return listing

    # Next, check description_snippet for Mokotów/Ochota matches
    desc_target = _match_mokotow_or_ochota(desc_norm)
    if desc_target is not None:
        district, garden, reason = desc_target
        listing.district_match = district
        listing.matched_garden = garden
        listing.match_reason = reason
        return listing

    # Check description_snippet for excluded districts/suburbs
    desc_excluded = _match_excluded_location(desc_norm)
    if desc_excluded:
        listing.district_match = "excluded"
        listing.matched_garden = None
        listing.match_reason = f"Excluded district/suburb in description: {desc_excluded}"
        return listing

    # Check if listing is in Warsaw (unassigned district)
    in_warsaw = (
        bool(_WARSAW_PATTERN.search(full_norm))
        or bool(_WARSAW_PATTERN.search(url_norm))
        or listing.source in ("pzd_mazowiecki", "rod_garden")
    )
    if in_warsaw:
        listing.district_match = "warszawa_unassigned"
        listing.matched_garden = None
        listing.match_reason = "Warsaw ROD listing without specific district (unassigned)"
        return listing

    listing.district_match = "excluded"
    listing.matched_garden = None
    listing.match_reason = "Excluded: no Warsaw or Mokotów/Ochota location matched"
    return listing


_DISTRICT_SORT_PRIORITY = {
    "mokotow": 0,
    "ochota": 0,
    "warszawa_unassigned": 1,
}


def filter_listings(
    listings: list[Listing], include_unassigned: bool = True
) -> list[Listing]:
    """Classify all listings and return matching ones sorted with Mokotów & Ochota first."""
    allowed = {"mokotow", "ochota"}
    if include_unassigned:
        allowed.add("warszawa_unassigned")

    matched: list[Listing] = []
    for item in listings:
        classify_listing(item)
        if item.district_match in allowed:
            matched.append(item)

    matched.sort(key=lambda x: _DISTRICT_SORT_PRIORITY.get(x.district_match, 99))
    return matched
