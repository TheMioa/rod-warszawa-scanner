"""Offline unit tests for rod_scanner.scrapers (portals, PZD, ROD gardens, and orchestrator)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rod_scanner.scrapers import (
    GratkaScraper,
    OLXScraper,
    OtodomScraper,
    PZDMazowieckiScraper,
    RODGardensScraper,
    SprzedajemyScraper,
    fetch_all_listings,
    get_demo_listings,
    parse_area_m2,
    parse_price_pln,
)


class TestPriceAndAreaParsers(unittest.TestCase):
    def test_parse_price_pln(self) -> None:
        self.assertEqual(parse_price_pln("85 000 zł"), 85000.0)
        self.assertEqual(parse_price_pln("120\xa0500,50 PLN"), 120500.5)
        self.assertEqual(parse_price_pln("Cena: 95.000 zł (do negocjacji)"), 95000.0)
        self.assertEqual(parse_price_pln("64000 zl"), 64000.0)
        self.assertIsNone(parse_price_pln("Za darmo"))
        self.assertIsNone(parse_price_pln(""))
        self.assertIsNone(parse_price_pln(None))

    def test_parse_area_m2(self) -> None:
        self.assertEqual(parse_area_m2("Działka ROD 300 m² Mokotów"), 300.0)
        self.assertEqual(parse_area_m2("Powierzchnia: 350,5 m2"), 350.5)
        self.assertEqual(parse_area_m2("Ogródek 420 mkw z altaną"), 420.0)
        self.assertEqual(parse_area_m2("Działka 3,5 ara"), 350.0)
        self.assertIsNone(parse_area_m2("Brak informacji o metrażu"))
        self.assertIsNone(parse_area_m2(None))


class TestOLXScraper(unittest.TestCase):
    def test_parse_olx_cards_html(self) -> None:
        html = """
        <html><body>
          <div data-cy="l-card" id="90123456">
            <a href="/d/oferta/dzialka-rod-mokotow-odynca-300m2-CID3-IDZ9abc.html">
              <h6>Działka ROD Mokotów ul. Odyńca 300 m²</h6>
            </a>
            <p data-testid="ad-price">92 000 zł</p>
            <p data-testid="location-date">Warszawa, Mokotów - Dzisiaj o 09:15</p>
          </div>
          <div data-cy="l-card" id="90123457">
            <a href="/d/oferta/ogrodek-dzialkowy-ochota-szczesliwice-CID3-IDZ9def.html">
              <h6>Ogródek działkowy ROD Fort Szczęśliwice 340 m2</h6>
            </a>
            <p data-testid="ad-price">110 000 zł</p>
            <p data-testid="location-date">Warszawa, Ochota - Wczoraj o 18:40</p>
          </div>
        </body></html>
        """
        scraper = OLXScraper()
        listings = scraper.parse_html(html)
        self.assertEqual(len(listings), 2)

        first = listings[0]
        self.assertEqual(first.source, "olx")
        self.assertIn("Odyńca", first.title)
        self.assertEqual(first.price_pln, 92000.0)
        self.assertEqual(first.area_m2, 300.0)
        self.assertEqual(first.location_raw, "Warszawa, Mokotów")
        self.assertEqual(first.published_at, "Dzisiaj o 09:15")
        self.assertTrue(first.url.startswith("https://www.olx.pl/d/oferta/"))

    def test_parse_olx_prerendered_state(self) -> None:
        state_obj = {
            "listing": {
                "listing": {
                    "ads": [
                        {
                            "id": 778899,
                            "title": "Działka ROD Rakowiec 290 m2 Mokotów",
                            "url": "https://www.olx.pl/d/oferta/dzialka-rod-rakowiec-ID778899.html",
                            "price": {
                                "regularPrice": {"value": 85000, "displayValue": "85 000 zł"},
                            },
                            "location": {"cityName": "Warszawa", "districtName": "Mokotów"},
                            "params": [{"key": "m", "value": "290 m²"}],
                            "description": "Piękna działka przy Polu Mokotowskim.",
                            "createdTime": "2025-05-01T10:00:00Z",
                        }
                    ]
                }
            }
        }
        html = f"<script>window.__PRERENDERED_STATE__ = {json.dumps(json.dumps(state_obj))};</script>"
        scraper = OLXScraper()
        listings = scraper.parse_response(html)
        self.assertEqual(len(listings), 1)
        item = listings[0]
        self.assertEqual(item.id, "olx:778899")
        self.assertEqual(item.price_pln, 85000.0)
        self.assertEqual(item.area_m2, 290.0)
        self.assertEqual(item.location_raw, "Warszawa, Mokotów")


class TestOtodomScraper(unittest.TestCase):
    def test_parse_otodom_next_data(self) -> None:
        next_data = {
            "props": {
                "pageProps": {
                    "data": {
                        "searchAds": {
                            "items": [
                                {
                                    "id": 654321,
                                    "title": "Działka rekreacyjna ROD Czerniaków 360 m²",
                                    "slug": "dzialka-rekreacyjna-rod-czerniakow-ID654321",
                                    "totalPrice": {"value": 79000, "currency": "PLN"},
                                    "areaInSquareMeters": 360,
                                    "location": {
                                        "address": {
                                            "street": {"name": "ul. Bernardyńska"},
                                            "district": {"name": "Mokotów"},
                                            "city": {"name": "Warszawa"},
                                        }
                                    },
                                    "shortDescription": "Działka blisko Jeziorka Czerniakowskiego.",
                                    "dateCreatedFirst": "2025-05-02",
                                }
                            ]
                        }
                    }
                }
            }
        }
        html = f'<html><head><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></head><body></body></html>'
        scraper = OtodomScraper()
        listings = scraper.parse_html(html)
        self.assertEqual(len(listings), 1)
        item = listings[0]
        self.assertEqual(item.id, "otodom:654321")
        self.assertEqual(item.source, "otodom")
        self.assertEqual(item.price_pln, 79000.0)
        self.assertEqual(item.area_m2, 360.0)
        self.assertIn("Mokotów", item.location_raw)
        self.assertIn("Bernardyńska", item.location_raw)
        self.assertEqual(
            item.url,
            "https://www.otodom.pl/pl/oferta/dzialka-rekreacyjna-rod-czerniakow-ID654321",
        )


class TestGratkaAndSprzedajemyScrapers(unittest.TestCase):
    def test_parse_gratka_html(self) -> None:
        html = """
        <html><body>
          <article class="teaserUnified">
            <h2><a href="https://gratka.pl/nieruchomosci/dzialka-rod-ochota/ob/334455">Działka ROD Ochota Żwirki i Wigury 310 m²</a></h2>
            <span class="location">Warszawa, Ochota</span>
            <p class="price">69 000 zł</p>
          </article>
        </body></html>
        """
        scraper = GratkaScraper()
        listings = scraper.parse_html(html)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].id, "gratka:334455")
        self.assertEqual(listings[0].price_pln, 69000.0)
        self.assertEqual(listings[0].area_m2, 310.0)
        self.assertEqual(listings[0].location_raw, "Warszawa, Ochota")

    def test_parse_sprzedajemy_html(self) -> None:
        html = """
        <html><body>
          <li class="element">
            <h2><a href="/warszawa/dzialka-rod-siekierki-300m2-nr776655">Działka ROD Siekierki 300 m2 Mokotów</a></h2>
            <span class="location">Warszawa, Mokotów</span>
            <div class="price">58 000 zł</div>
          </li>
        </body></html>
        """
        scraper = SprzedajemyScraper()
        listings = scraper.parse_html(html)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].id, "sprzedajemy:776655")
        self.assertEqual(listings[0].price_pln, 58000.0)
        self.assertEqual(listings[0].area_m2, 300.0)


class TestPZDAndRODGardensScrapers(unittest.TestCase):
    def test_pzd_mazowiecki_scraper(self) -> None:
        html = """
        <html><body>
          <article class="post">
            <h2><a href="http://mazowiecki.pzd.pl/2025/05/ogloszenie-wolne-dzialki-mokotow/">Ogłoszenie o wolnych działkach w ROD na Mokotowie (300 m²)</a></h2>
            <time datetime="2025-05-05">05.05.2025</time>
            <p>Okręgowy Zarząd Mazowiecki PZD informuje o możliwości nabycia praw do działki ROD.</p>
          </article>
          <article class="post">
            <h2><a href="http://mazowiecki.pzd.pl/wp-content/uploads/2025/05/wzor-umowy-przeniesienia-praw-do-dzialki.pdf">Wzór umowy przeniesienia praw do działki ROD</a></h2>
            <p>Dokumenty i wzory wniosków PZD dla działkowców.</p>
          </article>
          <article class="post">
            <h2><a href="http://mazowiecki.pzd.pl/2025/05/uchwala-ws-oplat-ogrodowych/">Uchwała ws. opłat ogrodowych i składek PZD w ROD</a></h2>
          </article>
          <article class="post">
            <h2><a href="http://mazowiecki.pzd.pl/kontakt">Kontakt</a></h2>
          </article>
        </body></html>
        """
        scraper = PZDMazowieckiScraper()
        listings = scraper.parse_html(html)
        self.assertEqual(len(listings), 1)
        item = listings[0]
        self.assertEqual(item.source, "pzd_mazowiecki")
        self.assertEqual(item.area_m2, 300.0)
        self.assertIn("Mokotowie", item.title)
        self.assertEqual(item.published_at, "2025-05-05")

    def test_rod_gardens_scraper_and_extra_urls(self) -> None:
        html = """
        <html><body>
          <div class="ogloszenie">
            <h3><a href="/ogloszenia/odstapie-dzialke-nr-44">Odstąpię prawo do działki nr 44 (300 m2) - cena 75 000 zł</a></h3>
            <p>Działka słoneczna w alejce nr 3.</p>
          </div>
        </body></html>
        """
        scraper = RODGardensScraper(
            extra_urls_env="ROD Wierzbno|Warszawa, Mokotów|https://rodwierzbno.example.pl"
        )
        self.assertTrue(any(t.name == "ROD Wierzbno" for t in scraper.targets))

        listings = scraper.parse_html(
            html,
            garden_name="ROD im. Obrońców Pokoju",
            district="Warszawa, Mokotów",
            base_url="https://rodobroncowpokoju.pl/",
        )
        self.assertEqual(len(listings), 1)
        item = listings[0]
        self.assertEqual(item.source, "rod_garden")
        self.assertIn("ROD im. Obrońców Pokoju", item.title)
        self.assertEqual(item.price_pln, 75000.0)
        self.assertEqual(item.area_m2, 300.0)
        self.assertIn("Mokotów", item.location_raw)


class TestFetchAllAndDemoListings(unittest.TestCase):
    def test_fetch_all_listings_graceful_error_handling(self) -> None:
        with patch.object(
            OLXScraper, "fetch_listings", side_effect=RuntimeError("HTTP 403 Forbidden")
        ), patch.object(
            OtodomScraper,
            "fetch_listings",
            return_value=[get_demo_listings()[0]],
        ):
            listings, warnings = fetch_all_listings(sources=["olx", "otodom"])
            self.assertEqual(len(listings), 1)
            self.assertEqual(len(warnings), 1)
            self.assertIn("olx", warnings[0])

    def test_get_demo_listings(self) -> None:
        demos = get_demo_listings()
        self.assertGreaterEqual(len(demos), 5)
        sources = {d.source for d in demos}
        self.assertIn("olx", sources)
        self.assertIn("pzd_mazowiecki", sources)
        self.assertIn("rod_garden", sources)


if __name__ == "__main__":
    unittest.main()
