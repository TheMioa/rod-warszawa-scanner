"""Unit tests for `rod_scanner.ai_scanner` (Gemini Search grounding, GitHub Models, web search, & semantic enrichment)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rod_scanner.ai_scanner import (
    ai_enrich_and_filter_listings,
    ai_search_rod_listings,
    detect_ai_backend,
    extract_grounding_urls,
    get_api_key,
    get_gemini_model,
    web_search_rod_listings,
)
from rod_scanner.models import Listing


class _FakeHTTPResponse:
    """Minimal context-manager mock for `urllib.request.urlopen`."""

    def __init__(self, payload: dict | str, status: int = 200) -> None:
        if isinstance(payload, str):
            self._bytes = payload.encode("utf-8")
        else:
            self._bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._bytes

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        return False


class TestAiScanner(unittest.TestCase):
    """Tests for API key/backend detection, grounded search, web discovery, and semantic enrichment."""

    def test_get_api_key_and_detect_backend_priority(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_api_key())
            self.assertEqual(detect_ai_backend(), ("none", ""))

        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghs_actions_token_123"}, clear=True):
            self.assertEqual(get_api_key(), "ghs_actions_token_123")
            self.assertEqual(
                detect_ai_backend(),
                ("github_models", "ghs_actions_token_123"),
            )

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "google-key-123"}, clear=True):
            self.assertEqual(get_api_key(), "google-key-123")
            self.assertEqual(detect_ai_backend(), ("gemini", "google-key-123"))

        with patch.dict(
            os.environ,
            {"AGY_API_KEY": "agy-key-456", "GOOGLE_API_KEY": "google-key-123"},
            clear=True,
        ):
            self.assertEqual(get_api_key(), "agy-key-456")

        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "gemini-key-789",
                "AGY_API_KEY": "agy-key-456",
                "GITHUB_TOKEN": "ghs_actions_token_123",
            },
            clear=True,
        ):
            self.assertEqual(get_api_key(), "gemini-key-789")
            self.assertEqual(detect_ai_backend(), ("gemini", "gemini-key-789"))

    def test_get_gemini_model(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_gemini_model(), "gemini-2.5-flash")
            self.assertEqual(get_gemini_model("gemini-2.5-pro"), "gemini-2.5-pro")

        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-2.5-pro-preview"}, clear=True):
            self.assertEqual(get_gemini_model(), "gemini-2.5-pro-preview")

    def test_ai_search_rod_listings_with_grounding(self) -> None:
        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {
                                "text": "```json\n"
                                + json.dumps(
                                    [
                                        {
                                            "id": "olx:odynca-101",
                                            "source": "olx",
                                            "title": "320 m² ROD Allotment at ROD im. Obrońców Pokoju (ul. Odyńca, Mokotów) — Brick Cottage & Electricity",
                                            "url": "https://www.olx.pl/d/oferta/dzialka-rod-obroncow-pokoju-odynca-ID101.html",
                                            "price_pln": 95000,
                                            "price_raw": "95,000 PLN",
                                            "area_m2": 320,
                                            "location_raw": "Warsaw, Mokotów",
                                            "description_snippet": "Well-kept ROD plot on ul. Odyńca in Mokotów with brick cottage, electricity, and water.",
                                            "published_at": "2026-11-15",
                                            "district_match": "mokotow",
                                            "matched_garden": "ROD im. Obrońców Pokoju",
                                            "match_reason": "Located at ROD im. Obrońców Pokoju on ul. Odyńca in Warsaw Mokotów",
                                        },
                                        {
                                            "source": "otodom",
                                            "title": "280 m² ROD Allotment at ROD Fort Szczęśliwice (ul. Drawska, Ochota)",
                                            "url": "",  # Missing direct URL -> falls back to grounding chunk URL #2
                                            "price_pln": 78000,
                                            "area_m2": 280,
                                            "location_raw": "Warsaw, Ochota",
                                            "description_snippet": "ROD allotment at ROD Fort Szczęśliwice next to Szczęśliwicki Park.",
                                            "district_match": "ochota",
                                            "matched_garden": "ROD Fort Szczęśliwice",
                                            "match_reason": "Located at ROD Fort Szczęśliwice on ul. Drawska in Warsaw Ochota",
                                        },
                                    ],
                                    ensure_ascii=False,
                                )
                                + "\n```"
                            }
                        ],
                    },
                    "groundingMetadata": {
                        "groundingChunks": [
                            {
                                "web": {
                                    "uri": "https://www.olx.pl/d/oferta/dzialka-rod-obroncow-pokoju-odynca-ID101.html",
                                    "title": "Działka ROD Obrońców Pokoju Odyńca Mokotów - OLX.pl",
                                }
                            },
                            {
                                "web": {
                                    "uri": "https://www.otodom.pl/pl/oferta/dzialka-rod-fort-szczesliwice-ochota-ID202",
                                    "title": "Działka ROD Fort Szczęśliwice Ochota - Otodom",
                                }
                            },
                        ]
                    },
                }
            ]
        }

        grounding_urls = extract_grounding_urls(mock_gemini_response)
        self.assertEqual(
            grounding_urls,
            [
                "https://www.olx.pl/d/oferta/dzialka-rod-obroncow-pokoju-odynca-ID101.html",
                "https://www.otodom.pl/pl/oferta/dzialka-rod-fort-szczesliwice-ochota-ID202",
            ],
        )

        captured_requests = []

        def fake_urlopen(req, timeout=45):
            captured_requests.append((req, timeout))
            return _FakeHTTPResponse(mock_gemini_response)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            listings, warnings = ai_search_rod_listings(
                api_key="test-gemini-key",
                model="gemini-2.5-flash",
                timeout=30,
            )

        self.assertEqual(warnings, [])
        self.assertEqual(len(captured_requests), 1)
        req_obj, req_timeout = captured_requests[0]
        self.assertEqual(req_timeout, 30)
        self.assertIn("gemini-2.5-flash:generateContent", req_obj.full_url)
        req_body = json.loads(req_obj.data.decode("utf-8"))
        self.assertEqual(req_body["tools"], [{"google_search": {}}])

        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].id, "olx:odynca-101")
        self.assertEqual(listings[0].district_match, "mokotow")
        self.assertEqual(listings[0].matched_garden, "ROD im. Obrońców Pokoju")
        self.assertEqual(listings[0].price_pln, 95000.0)
        self.assertEqual(listings[0].area_m2, 320.0)

        self.assertEqual(
            listings[1].url,
            "https://www.otodom.pl/pl/oferta/dzialka-rod-fort-szczesliwice-ochota-ID202",
        )
        self.assertEqual(listings[1].district_match, "ochota")
        self.assertEqual(listings[1].matched_garden, "ROD Fort Szczęśliwice")
        self.assertTrue(listings[1].id.startswith("otodom:"))

    def test_web_search_rod_listings_bing_and_ddg(self) -> None:
        fake_bing_xml = """<?xml version="1.0" encoding="utf-8" ?>
        <rss version="2.0"><channel>
          <item>
            <title>Działka ROD Rakowiec ul. Rostafińskich Mokotów 310 m2 - 92 000 zł</title>
            <link>https://www.olx.pl/d/oferta/dzialka-rod-rakowiec-mokotow-ID777.html</link>
            <description>Ogródek działkowy ROD Rakowiec przy Polu Mokotowskim, 310m2, cena 92 000 zł.</description>
          </item>
        </channel></rss>"""

        fake_ddg_html = """
        <div class="result">
          <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.otodom.pl%2Fpl%2Foferta%2Fdzialka-rod-fort-szczesliwice-ochota-ID888">
            Ogródek działkowy ROD Fort Szczęśliwice ul. Drawska Ochota 290 m² 75 000 PLN
          </a>
          <a class="result__snippet">Zadbana działka ROD na Szczęśliwicach (Ochota).</a>
        </div>
        """

        def fake_urlopen(req, timeout=15):
            if "bing.com" in req.full_url:
                return _FakeHTTPResponse(fake_bing_xml)
            return _FakeHTTPResponse(fake_ddg_html)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            discovered = web_search_rod_listings(
                timeout=10,
                queries=["site:olx.pl/d/oferta działka ROD Warszawa Mokotów OR Ochota"],
            )

        self.assertEqual(len(discovered), 2)
        self.assertEqual(discovered[0].source, "olx")
        self.assertEqual(discovered[0].district_match, "mokotow")
        self.assertEqual(discovered[0].matched_garden, "ROD Rakowiec")
        self.assertEqual(discovered[0].price_pln, 92000.0)
        self.assertEqual(discovered[0].area_m2, 310.0)

        self.assertEqual(discovered[1].source, "otodom")
        self.assertEqual(discovered[1].district_match, "ochota")
        self.assertEqual(discovered[1].matched_garden, "ROD Fort Szczęśliwice")
        self.assertEqual(discovered[1].price_pln, 75000.0)
        self.assertEqual(discovered[1].area_m2, 290.0)

    def test_ai_enrich_and_filter_listings_translates_and_rejects_bait_gemini(self) -> None:
        raw_listings = [
            Listing(
                id="olx:real-mokotow",
                source="olx",
                title="Sprzedam działkę ROD Obrońców Pokoju ul. Odyńca 300m2 cena 89000 zł",
                url="https://www.olx.pl/d/oferta/rod-odynca-ID1.html",
                price_pln=None,
                area_m2=None,
                location_raw="Warszawa, Mokotów",
                description_snippet="Domek murowany, prąd całoroczny, woda miejska, ul. Odyńca.",
            ),
            Listing(
                id="olx:bait-piaseczno",
                source="olx",
                title="Piękna działka ROD tylko 20 min od Mokotowa! Blisko Ochoty - Piaseczno",
                url="https://www.olx.pl/d/oferta/rod-piaseczno-ID2.html",
                price_pln=45000.0,
                area_m2=400.0,
                location_raw="Warszawa, Mokotów",
                description_snippet="Działka położona w Zalesiu Dolnym koło Piaseczna, szybki dojazd 20 min z Mokotowa.",
            ),
            Listing(
                id="gratka:real-ochota",
                source="gratka",
                title="Ogródek działkowy ROD Żwirki i Wigury 250 m2 - 65 000 PLN",
                url="https://gratka.pl/nieruchomosci/rod-zwirki-ID3",
                price_pln=None,
                area_m2=None,
                location_raw="Warszawa, Ochota",
                description_snippet="Działka rekreacyjna przy ul. Księcia Trojdena / Żwirki i Wigury.",
            ),
        ]

        mock_enrichment_json = [
            {
                "id": "olx:real-mokotow",
                "title": "300 m² ROD Allotment at ROD im. Obrońców Pokoju (ul. Odyńca, Mokotów) — Brick Cottage & Year-Round Electricity",
                "district_match": "mokotow",
                "matched_garden": "ROD im. Obrońców Pokoju",
                "match_reason": "Verified ROD allotment at ROD im. Obrońców Pokoju on ul. Odyńca in Mokotów",
                "price_pln": 89000,
                "area_m2": 300,
            },
            {
                "id": "olx:bait-piaseczno",
                "title": "400 m² ROD Allotment in Piaseczno (Outside Warsaw — Bait '20 min from Mokotów')",
                "district_match": "excluded",
                "matched_garden": None,
                "match_reason": "Excluded: plot is actually in Piaseczno/Zalesie Dolne outside Warsaw, only advertising travel time from Mokotów",
                "price_pln": 45000,
                "area_m2": 400,
            },
            {
                "id": "gratka:real-ochota",
                "title": "250 m² ROD Allotment at ROD im. Żwirki i Wigury (ul. Księcia Trojdena, Ochota)",
                "district_match": "ochota",
                "matched_garden": "ROD im. Żwirki i Wigury",
                "match_reason": "Verified ROD allotment at ROD im. Żwirki i Wigury on ul. Księcia Trojdena in Ochota",
                "price_pln": 65000,
                "area_m2": 250,
            },
        ]

        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [{"text": json.dumps(mock_enrichment_json, ensure_ascii=False)}],
                    }
                }
            ]
        }

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeHTTPResponse(mock_gemini_response),
        ) as mock_urlopen:
            filtered, warnings = ai_enrich_and_filter_listings(
                raw_listings,
                api_key="test-gemini-key",
                model="gemini-2.5-flash",
                include_unassigned=True,
            )

        self.assertEqual(warnings, [])
        self.assertEqual(mock_urlopen.call_count, 1)

        self.assertEqual(len(filtered), 2)
        self.assertEqual([item.id for item in filtered], ["olx:real-mokotow", "gratka:real-ochota"])

        mok = filtered[0]
        self.assertEqual(
            mok.title,
            "300 m² ROD Allotment at ROD im. Obrońców Pokoju (ul. Odyńca, Mokotów) — Brick Cottage & Year-Round Electricity",
        )
        self.assertEqual(mok.district_match, "mokotow")
        self.assertEqual(mok.matched_garden, "ROD im. Obrońców Pokoju")
        self.assertEqual(mok.price_pln, 89000.0)
        self.assertEqual(mok.area_m2, 300.0)

        och = filtered[1]
        self.assertEqual(och.district_match, "ochota")
        self.assertEqual(och.matched_garden, "ROD im. Żwirki i Wigury")
        self.assertEqual(och.price_pln, 65000.0)
        self.assertEqual(och.area_m2, 250.0)

    def test_ai_enrich_and_filter_listings_github_models_backend(self) -> None:
        sample = [
            Listing(
                id="olx:siekierki-1",
                source="olx",
                title="Działka ROD Siekierki ul. Antoniewska 350 m2 110000 zł",
                url="https://www.olx.pl/d/oferta/rod-siekierki-ID10.html",
                location_raw="Warszawa, Mokotów",
            )
        ]

        mock_gh_models_response = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "listings": [
                                    {
                                        "id": "olx:siekierki-1",
                                        "title": "350 m² ROD Allotment at ROD Siekierki (ul. Antoniewska, Mokotów)",
                                        "district_match": "mokotow",
                                        "matched_garden": "ROD Siekierki",
                                        "match_reason": "Verified ROD plot at ROD Siekierki on ul. Antoniewska in Warsaw Mokotów",
                                        "price_pln": 110000,
                                        "area_m2": 350,
                                    }
                                ]
                            }
                        ),
                    }
                }
            ]
        }

        captured_urls = []

        def fake_urlopen(req, timeout=45):
            captured_urls.append(req.full_url)
            return _FakeHTTPResponse(mock_gh_models_response)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            filtered, warnings = ai_enrich_and_filter_listings(
                sample,
                api_key="ghs_github_actions_token_xyz",
            )

        self.assertEqual(warnings, [])
        self.assertEqual(len(captured_urls), 1)
        self.assertIn("models.github.ai/inference/chat/completions", captured_urls[0])
        self.assertEqual(len(filtered), 1)
        self.assertEqual(
            filtered[0].title,
            "350 m² ROD Allotment at ROD Siekierki (ul. Antoniewska, Mokotów)",
        )
        self.assertEqual(filtered[0].price_pln, 110000.0)
        self.assertEqual(filtered[0].price_raw, "110,000 PLN")
        self.assertEqual(filtered[0].area_m2, 350.0)

    def test_ai_enrich_fallback_on_api_error_or_missing_key(self) -> None:
        sample = [
            Listing(
                id="olx:fallback-1",
                source="olx",
                title="Działka ROD Fort Szczęśliwice ul. Drawska Ochota",
                url="https://www.olx.pl/d/oferta/rod-drawska-ID99.html",
                location_raw="Warszawa, Ochota",
            ),
            Listing(
                id="olx:fallback-excluded",
                source="olx",
                title="Działka ROD Białołęka 300m2",
                url="https://www.olx.pl/d/oferta/rod-bialoleka-ID100.html",
                location_raw="Warszawa, Białołęka",
            ),
        ]

        with patch.dict(os.environ, {}, clear=True):
            matched_no_key, warnings_no_key = ai_enrich_and_filter_listings(
                sample, api_key=""
            )
        self.assertEqual(len(matched_no_key), 1)
        self.assertEqual(matched_no_key[0].id, "olx:fallback-1")
        self.assertEqual(matched_no_key[0].district_match, "ochota")
        self.assertEqual(len(warnings_no_key), 1)
        self.assertIn("no API key configured", warnings_no_key[0])

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection timed out"),
        ):
            matched_err, warnings_err = ai_enrich_and_filter_listings(
                sample, api_key="test-gemini-key"
            )
        self.assertEqual(len(matched_err), 1)
        self.assertEqual(matched_err[0].id, "olx:fallback-1")
        self.assertEqual(len(warnings_err), 1)
        self.assertIn("falling back to keyword matcher", warnings_err[0])


if __name__ == "__main__":
    unittest.main()
