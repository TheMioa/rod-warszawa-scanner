"""Unit tests for rod_scanner.models and rod_scanner.matcher."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rod_scanner.models import Listing
from rod_scanner.matcher import (
    classify_listing,
    filter_listings,
    is_rod_related,
    normalize_text,
    strip_diacritics,
)


class TestModels(unittest.TestCase):
    def test_listing_to_dict_and_from_dict(self) -> None:
        item = Listing(
            id="olx:999",
            source="olx",
            title="Działka ROD Mokotów Odyńca",
            url="https://www.olx.pl/d/oferta/dzialka-rod-ID999.html",
            price_pln=75000.0,
            price_raw="75 000 zł",
            area_m2=300.0,
            location_raw="Warszawa, Mokotów",
            description_snippet="Piękna działka przy ul. Odyńca",
            published_at="2026-11-15",
        )
        data = item.to_dict()
        self.assertEqual(data["id"], "olx:999")
        self.assertEqual(data["price_pln"], 75000.0)
        self.assertEqual(item.price_per_m2, 250.0)

        data_with_extra = dict(data, unknown_extra_field="ignored")
        restored = Listing.from_dict(data_with_extra)
        self.assertEqual(restored, item)


class TestMatcherNormalization(unittest.TestCase):
    def test_strip_polish_diacritics(self) -> None:
        raw = "Zażółć gęślą jaźń ĄĆĘŁŃÓŚŹŻ"
        self.assertEqual(strip_diacritics(raw), "Zazolc gesla jazn ACELNOSZZ")

    def test_normalize_text(self) -> None:
        raw = "  ROD  im.  Obrońców   Pokoju — Odyńca / Mokotów  "
        self.assertEqual(
            normalize_text(raw),
            "rod im. obroncow pokoju — odynca / mokotow",
        )


class TestIsRodRelated(unittest.TestCase):
    def test_positive_rod_listings(self) -> None:
        cases = [
            Listing(id="1", source="olx", title="Sprzedam działkę ROD 300m2", url="https://olx.pl/1"),
            Listing(id="2", source="otodom", title="Ogródek działkowy na Mokotowie", url="https://otodom.pl/2"),
            Listing(id="3", source="gratka", title="Działka rekreacyjna Warszawa Ochota", url="https://gratka.pl/3"),
            Listing(id="4", source="sprzedajemy", title="Odstąpię prawo do działki PZD", url="https://sprzedajemy.pl/4"),
            Listing(id="5", source="pzd_mazowiecki", title="Komunikat w sprawie przetargu", url="http://mazowiecki.pzd.pl/5"),
            Listing(id="6", source="rod_garden", title="Wolna działka nr 42", url="https://rodobroncowpokoju.pl/6"),
        ]
        for case in cases:
            with self.subTest(id=case.id):
                self.assertTrue(is_rod_related(case))

    def test_negative_non_rod_listings(self) -> None:
        cases = [
            Listing(id="n1", source="olx", title="Mieszkanie 3-pokojowe Mokotów Odyńca", url="https://olx.pl/n1"),
            Listing(id="n2", source="otodom", title="Lokal biurowy do wynajęcia Ochota Grójecka", url="https://otodom.pl/n2"),
            Listing(id="n3", source="gratka", title="Działka budowlana 1500m2 Warszawa Mokotów", url="https://gratka.pl/n3"),
            Listing(id="n4", source="olx", title="Apartament z ogródkiem Warszawa Wierzbno", url="https://olx.pl/n4"),
        ]
        for case in cases:
            with self.subTest(id=case.id):
                self.assertFalse(is_rod_related(case))
                classified = classify_listing(case)
                self.assertEqual(classified.district_match, "excluded")


class TestMokotowMatching(unittest.TestCase):
    def test_mokotow_gardens(self) -> None:
        test_cases = [
            (
                "Działka ROD im. Obrońców Pokoju ul. Odyńca",
                "Warszawa",
                "ROD im. Obrońców Pokoju",
            ),
            (
                "Ogródek działkowy przy ul. Racławickiej / al. Niepodległości",
                "Warszawa, Mokotów",
                "ROD im. Obrońców Pokoju",
            ),
            (
                "Sprzedam działkę ROD Siekierki ul. Antoniewska",
                "Warszawa",
                "ROD Siekierki",
            ),
            (
                "Działka rekreacyjna ROD 1 Maja Bartycka / Bluszczańska",
                "Warszawa, Mokotów",
                "ROD Siekierki",
            ),
            (
                "ROD przy ul. Gościniec / Gwintowa",
                "Warszawa",
                "ROD Siekierki",
            ),
            (
                "Ogródek działkowy ROD Czerniaków ul. Bernardyńska",
                "Warszawa",
                "ROD Czerniaków",
            ),
            (
                "Działka ROD Jeziorko Czerniakowskie ul. Gołkowska",
                "Warszawa, Czerniaków",
                "ROD Czerniaków",
            ),
            (
                "Działka ROD Rakowiec ul. Rostafińskich przy Polu Mokotowskim",
                "Warszawa",
                "ROD Rakowiec",
            ),
            (
                "Ogródek działkowy ROD Chodkiewicza",
                "Warszawa, Mokotów",
                "ROD Rakowiec",
            ),
            (
                "Działka ROD SGGW",
                "Warszawa, Mokotów",
                "ROD SGGW",
            ),
            (
                "Działka ROD Służewiec",
                "Warszawa",
                "ROD Służewiec",
            ),
            (
                "Działka ROD Stegny",
                "Warszawa",
                "ROD Stegny",
            ),
            (
                "Działka ROD Sadyba",
                "Warszawa",
                "ROD Sadyba",
            ),
        ]
        for idx, (title, loc, expected_garden) in enumerate(test_cases):
            with self.subTest(idx=idx, title=title):
                listing = Listing(
                    id=f"mok:{idx}",
                    source="olx",
                    title=title,
                    url=f"https://olx.pl/{idx}",
                    location_raw=loc,
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, "mokotow")
                self.assertEqual(listing.matched_garden, expected_garden)
                self.assertTrue(listing.match_reason)

    def test_mokotow_streets_and_neighborhoods(self) -> None:
        streets = [
            "Wyględów",
            "Wierzbno",
            "Służew",
            "Królikarnia",
            "Dolny Mokotów",
            "Górny Mokotów",
            "Woronicza",
            "Bukowińska",
            "Idzikowskiego",
            "Sobieskiego",
            "Sikorskiego",
            "Dolna",
            "Chełmska",
            "Gagarina",
            "Bobrowiecka",
        ]
        for street in streets:
            with self.subTest(street=street):
                listing = Listing(
                    id=f"mok_st:{street}",
                    source="olx",
                    title=f"Działka rekreacyjna ROD - {street}",
                    url="https://olx.pl/mok",
                    location_raw="Warszawa",
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, "mokotow")


class TestOchotaMatching(unittest.TestCase):
    def test_ochota_gardens(self) -> None:
        test_cases = [
            (
                "Działka ROD Fort Szczęśliwice ul. Drawska",
                "Warszawa, Ochota",
                "ROD Fort Szczęśliwice",
            ),
            (
                "Ogródek działkowy przy Parku Szczęśliwickim (Śmigłowca / Włodarzewska)",
                "Warszawa",
                "ROD Fort Szczęśliwice",
            ),
            (
                "Działka rekreacyjna ROD ul. Dickensa / Szczęśliwicka",
                "Warszawa, Ochota",
                "ROD Fort Szczęśliwice",
            ),
            (
                "Sprzedam działkę ROD im. Żwirki i Wigury",
                "Warszawa, Ochota",
                "ROD im. Żwirki i Wigury",
            ),
            (
                "Działka ROD ul. Księcia Trojdena / Pawińskiego / Banacha",
                "Warszawa",
                "ROD im. Żwirki i Wigury",
            ),
            (
                "Ogródek działkowy ROD Hankiewicza / Korotyńskiego",
                "Warszawa, Ochota",
                "ROD im. Żwirki i Wigury",
            ),
        ]
        for idx, (title, loc, expected_garden) in enumerate(test_cases):
            with self.subTest(idx=idx, title=title):
                listing = Listing(
                    id=f"och:{idx}",
                    source="olx",
                    title=title,
                    url=f"https://olx.pl/och/{idx}",
                    location_raw=loc,
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, "ochota")
                self.assertEqual(listing.matched_garden, expected_garden)

    def test_ochota_streets_and_neighborhoods(self) -> None:
        locations = [
            "Ochota",
            "Stara Ochota",
            "Filtry",
            "Kolonia Lubeckiego",
            "Grójecka",
            "Wawelska",
            "Filtrowa",
            "Niemcewicza",
        ]
        for loc_name in locations:
            with self.subTest(loc_name=loc_name):
                listing = Listing(
                    id=f"och_st:{loc_name}",
                    source="otodom",
                    title=f"Działka ROD Warszawa {loc_name}",
                    url="https://otodom.pl/och",
                    location_raw="Warszawa",
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, "ochota")


class TestDiacriticFreeAndFallbackMatching(unittest.TestCase):
    def test_diacritic_free_matching(self) -> None:
        cases = [
            ("Dzialka ROD Warszawa Mokotow ul. Odynca", "mokotow", "ROD im. Obrońców Pokoju"),
            ("Ogrodek dzialkowy ROD Szczesliwice Warszawa Ochota", "ochota", "ROD Fort Szczęśliwice"),
            ("Dzialka rekreacyjna ROD Zwirki i Wigury", "ochota", "ROD im. Żwirki i Wigury"),
            ("Dzialka ROD Czerniakow ul. Golkowska", "mokotow", "ROD Czerniaków"),
        ]
        for title, expected_district, expected_garden in cases:
            with self.subTest(title=title):
                listing = Listing(
                    id="ascii:1",
                    source="olx",
                    title=title,
                    url="https://olx.pl/ascii",
                    location_raw="Warszawa",
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, expected_district)
                self.assertEqual(listing.matched_garden, expected_garden)

    def test_warszawa_unassigned_fallback(self) -> None:
        listing = Listing(
            id="unassigned:1",
            source="olx",
            title="Sprzedam działkę rekreacyjną ROD 350m2 domek murowany",
            url="https://www.olx.pl/d/oferta/dzialka-warszawa-ID1.html",
            location_raw="Warszawa",
            description_snippet="Piękna cicha działka ROD z prądem i wodą w Warszawie.",
        )
        classify_listing(listing)
        self.assertEqual(listing.district_match, "warszawa_unassigned")
        self.assertIsNone(listing.matched_garden)

    def test_excluded_districts_and_suburbs(self) -> None:
        excluded_places = [
            "Białołęka",
            "Bialoleka",
            "Wawer",
            "Targówek",
            "Bemowo",
            "Bielany",
            "Ursynów",
            "Wola",
            "Praga-Południe",
            "Praga-Północ",
            "Grochów",
            "Saska Kępa",
            "Gocław",
            "Tarchomin",
            "Bródno",
            "Rembertów",
            "Wesoła",
            "Ursus",
            "Wilanów",
            "Żoliborz",
            "Włochy",
            "Okęcie",
            "Śródmieście",
            "Marki",
            "Ząbki",
            "Piaseczno",
            "Konstancin",
            "Jabłonna",
            "Nieporęt",
            "Legionowo",
            "Wołomin",
            "Pruszków",
            "Otwock",
            "Góra Kalwaria",
            "Radzymin",
            "Łomianki",
            "Serock",
            "Nadarzyn",
        ]
        for place in excluded_places:
            with self.subTest(place=place):
                listing = Listing(
                    id=f"excl:{place}",
                    source="olx",
                    title=f"Działka ROD 400m2 {place}",
                    url="https://olx.pl/excl",
                    location_raw=f"Warszawa, {place}",
                )
                classify_listing(listing)
                self.assertEqual(listing.district_match, "excluded")


class TestFilterListings(unittest.TestCase):
    def test_filter_and_sort_order(self) -> None:
        items = [
            Listing(
                id="1",
                source="olx",
                title="Działka ROD Białołęka",
                url="https://olx.pl/1",
                location_raw="Warszawa, Białołęka",
            ),
            Listing(
                id="2",
                source="olx",
                title="Działka ROD 300m2 w Warszawie",
                url="https://olx.pl/2",
                location_raw="Warszawa",
            ),
            Listing(
                id="3",
                source="olx",
                title="Działka ROD Fort Szczęśliwice",
                url="https://olx.pl/3",
                location_raw="Warszawa, Ochota",
            ),
            Listing(
                id="4",
                source="olx",
                title="Działka ROD Obrońców Pokoju Odyńca",
                url="https://olx.pl/4",
                location_raw="Warszawa, Mokotów",
            ),
        ]

        res_with_unassigned = filter_listings(items, include_unassigned=True)
        self.assertEqual([x.id for x in res_with_unassigned], ["3", "4", "2"])

        res_strict = filter_listings(items, include_unassigned=False)
        self.assertEqual([x.id for x in res_strict], ["3", "4"])


if __name__ == "__main__":
    unittest.main()
