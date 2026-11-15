"""Scraper registry, multi-source runner, and offline demo fixtures."""

from __future__ import annotations

from typing import Callable, Optional, Union

from rod_scanner.models import Listing
from rod_scanner.scrapers.portals import (
    BasePortalScraper,
    GratkaScraper,
    OLXScraper,
    OtodomScraper,
    SprzedajemyScraper,
    fetch_url_text,
    make_listing_id,
    parse_area_m2,
    parse_price_pln,
    strip_html_tags,
)
from rod_scanner.scrapers.pzd import (
    DEFAULT_GARDEN_TARGETS,
    GardenTarget,
    PZDMazowieckiScraper,
    RODGardensScraper,
)

ALL_SOURCES: tuple[str, ...] = (
    "olx",
    "otodom",
    "gratka",
    "sprzedajemy",
    "pzd_mazowiecki",
    "rod_garden",
)

SOURCE_ALIASES: dict[str, str] = {
    "olx": "olx",
    "otodom": "otodom",
    "gratka": "gratka",
    "sprzedajemy": "sprzedajemy",
    "pzd": "pzd_mazowiecki",
    "pzd_mazowiecki": "pzd_mazowiecki",
    "rod_garden": "rod_garden",
    "rod_gardens": "rod_garden",
    "gardens": "rod_garden",
}


def _normalize_sources(sources: Optional[Union[list[str], str]]) -> list[str]:
    if sources is None:
        return list(ALL_SOURCES)
    if isinstance(sources, str):
        tokens = [t.strip().lower() for t in sources.split(",") if t.strip()]
    else:
        tokens = []
        for item in sources:
            for sub in str(item).split(","):
                if sub.strip():
                    tokens.append(sub.strip().lower())
    if not tokens or "all" in tokens:
        return list(ALL_SOURCES)

    resolved: list[str] = []
    for tok in tokens:
        canonical = SOURCE_ALIASES.get(tok, tok)
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


def fetch_all_listings(
    sources: Optional[Union[list[str], str]] = None,
    timeout: int = 15,
) -> tuple[list[Listing], list[str]]:
    """Run all selected scrapers, catching network/parse errors per source gracefully.

    Returns:
        (listings, warnings_or_errors)
    """
    selected = _normalize_sources(sources)
    scraper_factories: dict[str, Callable[[], object]] = {
        "olx": OLXScraper,
        "otodom": OtodomScraper,
        "gratka": GratkaScraper,
        "sprzedajemy": SprzedajemyScraper,
        "pzd_mazowiecki": PZDMazowieckiScraper,
        "rod_garden": RODGardensScraper,
    }

    all_listings: list[Listing] = []
    seen_ids: set[str] = set()
    warnings: list[str] = []

    for src in selected:
        factory = scraper_factories.get(src)
        if factory is None:
            warnings.append(f"Unknown source '{src}' skipped")
            continue

        scraper = factory()
        try:
            if isinstance(scraper, RODGardensScraper):
                items, garden_warnings = scraper.fetch_listings_with_errors(timeout=timeout)
                warnings.extend(garden_warnings)
            else:
                items = scraper.fetch_listings(timeout=timeout)  # type: ignore[attr-defined]
            for item in items:
                if item.id not in seen_ids:
                    seen_ids.add(item.id)
                    all_listings.append(item)
        except Exception as exc:
            warnings.append(f"{src}: {type(exc).__name__}: {exc}")

    return all_listings, warnings


def get_demo_listings() -> list[Listing]:
    """Return realistic sample listings for `--demo-fixture` offline verification mode."""
    return [
        Listing(
            id="olx:demo-mokotow-obroncow-pokoju",
            source="olx",
            title="Działka rekreacyjna ROD im. Obrońców Pokoju 300 m² Warszawa Mokotów (ul. Odyńca)",
            url="https://www.olx.pl/d/oferta/dzialka-rod-obroncow-pokoju-mokotow-CID3-IDdemo1.html",
            price_pln=95000.0,
            price_raw="95 000 PLN",
            area_m2=300.0,
            location_raw="Warszawa, Mokotów",
            description_snippet=(
                "Odstąpię prawo do działki ROD im. Obrońców Pokoju przy ul. Odyńca / al. Niepodległości "
                "na Górnym Mokotowie. Powierzchnia 300 m2, murowana altana, prąd, woda miejska."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="otodom:demo-mokotow-czerniakow",
            source="otodom",
            title="Ogródek działkowy ROD Czerniaków 350 m² Sadyba / Mokotów",
            url="https://www.otodom.pl/pl/oferta/ogrodek-dzialkowy-rod-czerniakow-mokotow-IDdemo2",
            price_pln=78000.0,
            price_raw="78 000 PLN",
            area_m2=350.0,
            location_raw="Warszawa, Mokotów, Czerniaków, ul. Bernardyńska",
            description_snippet=(
                "Zadbana działka ROD Czerniaków w pobliżu Jeziorka Czerniakowskiego (Dolny Mokotów). "
                "Drzewa owocowe, domek drewniany 25 m2."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="gratka:demo-ochota-fort-szczesliwice",
            source="gratka",
            title="Działka ROD Fort Szczęśliwice 320 m² Warszawa Ochota ul. Drawska",
            url="https://gratka.pl/nieruchomosci/dzialka-rod-fort-szczesliwice-ochota/ob/99001",
            price_pln=88000.0,
            price_raw="88 000 PLN",
            area_m2=320.0,
            location_raw="Warszawa, Ochota, Szczęśliwice",
            description_snippet=(
                "Sprzedam prawa do działki w ROD Fort Szczęśliwice przy ul. Drawskiej na Ochocie. "
                "Powierzchnia 320 m², cicha alejka, blisko Parku Szczęśliwickiego."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="rod_garden:demo-ochota-zwirki-i-wigury",
            source="rod_garden",
            title="[ROD im. Żwirki i Wigury] Ogłoszenie zarządu: wolna działka nr 142 (280 m²)",
            url="https://rodzwirkiiwigury.pl/ogloszenia/wolna-dzialka-142",
            price_pln=64000.0,
            price_raw="64 000 PLN",
            area_m2=280.0,
            location_raw="Warszawa, Ochota, ROD im. Żwirki i Wigury, ul. Żwirki i Wigury",
            description_snippet=(
                "Zarząd ROD im. Żwirki i Wigury (Warszawa Ochota / Rakowiec) informuje o możliwości "
                "nabycia prawa do działki nr 142 o powierzchni 280 m2."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="pzd_mazowiecki:demo-komunikat-mokotow",
            source="pzd_mazowiecki",
            title="Komunikat Okręgowego Zarządu Mazowieckiego PZD ws. wolnych działek ROD w Warszawie (Mokotów i Ochota)",
            url="http://mazowiecki.pzd.pl/komunikaty/wolne-dzialki-warszawa-2025",
            price_pln=None,
            price_raw="Price not listed",
            area_m2=300.0,
            location_raw="Warszawa, Mokotów (PZD Masovian District)",
            description_snippet=(
                "Wykaz wolnych działek rodzinnych zgłoszonych przez zarządy ROD na terenie dzielnic "
                "Mokotów (ROD Rakowiec) oraz Ochota."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="sprzedajemy:demo-warszawa-unassigned",
            source="sprzedajemy",
            title="Działka ROD 400 m² Warszawa z altaną murowaną i prądem",
            url="https://sprzedajemy.pl/dzialka-rod-400m2-warszawa-altana-nr881122",
            price_pln=72000.0,
            price_raw="72 000 PLN",
            area_m2=400.0,
            location_raw="Warszawa, Masovian Voivodeship",
            description_snippet=(
                "Odstąpię piękną działkę rekreacyjną ROD w Warszawie (blisko centrum, dogodny dojazd ZTM). "
                "Powierzchnia 400 m², pełne uzbrojenie, brak dokładnej dzielnicy w opisie."
            ),
            published_at="2025-05-10",
        ),
        Listing(
            id="olx:demo-excluded-bialoleka",
            source="olx",
            title="Działka rekreacyjna ROD 350 m² Warszawa Białołęka / Tarchomin",
            url="https://www.olx.pl/d/oferta/dzialka-rod-bialoleka-CID3-IDdemoExcluded.html",
            price_pln=55000.0,
            price_raw="55 000 PLN",
            area_m2=350.0,
            location_raw="Warszawa, Białołęka",
            description_snippet=(
                "Sprzedam działkę ROD na warszawskiej Białołęce w okolicach Kanału Żerańskiego."
            ),
            published_at="2025-05-10",
        ),
    ]


__all__ = [
    "ALL_SOURCES",
    "BasePortalScraper",
    "DEFAULT_GARDEN_TARGETS",
    "GardenTarget",
    "GratkaScraper",
    "OLXScraper",
    "OtodomScraper",
    "PZDMazowieckiScraper",
    "RODGardensScraper",
    "SprzedajemyScraper",
    "fetch_all_listings",
    "fetch_url_text",
    "get_demo_listings",
    "make_listing_id",
    "parse_area_m2",
    "parse_price_pln",
    "strip_html_tags",
]
