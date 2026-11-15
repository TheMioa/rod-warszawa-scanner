"""Unit tests for rod_scanner.state and rod_scanner.notifiers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rod_scanner.models import Listing
from rod_scanner.notifiers import (
    dispatch_notifications,
    format_markdown_report,
    send_email,
    send_github_issue,
    write_github_step_summary,
)
from rod_scanner.state import StateStore


class TestStateStore(unittest.TestCase):
    """Tests for StateStore diffing, deduplication, price drops, persistence, and pruning."""

    def _sample_listings(self) -> list[Listing]:
        return [
            Listing(
                id="olx:101",
                source="olx",
                title="Działka ROD Obrońców Pokoju Odyńca Mokotów",
                url="https://www.olx.pl/d/oferta/101",
                price_pln=90000.0,
                area_m2=300.0,
                location_raw="Warszawa, Mokotów",
                district_match="mokotow",
                matched_garden="ROD im. Obrońców Pokoju",
            ),
            Listing(
                id="otodom:202",
                source="otodom",
                title="Ogródek działkowy ROD Fort Szczęśliwice",
                url="https://www.otodom.pl/pl/oferta/202",
                price_pln=75000.0,
                area_m2=250.0,
                location_raw="Warszawa, Ochota",
                district_match="ochota",
                matched_garden="ROD Fort Szczęśliwice",
            ),
        ]

    def test_new_listings_and_second_run_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "seen_listings.json"
            store = StateStore(state_path)
            listings = self._sample_listings()

            new_first, drops_first = store.diff_and_update(
                listings, now_iso="2026-05-01T08:00:00+00:00"
            )
            self.assertEqual(len(new_first), 2)
            self.assertEqual(len(drops_first), 0)
            self.assertIn("olx:101", store)
            self.assertEqual(store["olx:101"]["first_seen_at"], "2026-05-01T08:00:00+00:00")
            self.assertEqual(store["olx:101"]["last_seen_at"], "2026-05-01T08:00:00+00:00")

            # Second run with identical listings -> 0 new, 0 drops, updated last_seen_at
            new_second, drops_second = store.diff_and_update(
                listings, now_iso="2026-05-02T08:00:00+00:00"
            )
            self.assertEqual(len(new_second), 0)
            self.assertEqual(len(drops_second), 0)
            self.assertEqual(store["olx:101"]["first_seen_at"], "2026-05-01T08:00:00+00:00")
            self.assertEqual(store["olx:101"]["last_seen_at"], "2026-05-02T08:00:00+00:00")

    def test_price_drop_detection_and_ignore_price_increase(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "seen_listings.json"
            store = StateStore(state_path)
            listings = self._sample_listings()
            store.diff_and_update(listings, now_iso="2026-05-01T08:00:00+00:00")

            # Price drop on olx:101 (90000 -> 81000) and price increase on otodom:202 (75000 -> 80000)
            updated_listings = [
                Listing(
                    id="olx:101",
                    source="olx",
                    title="Działka ROD Obrońców Pokoju Odyńca Mokotów",
                    url="https://www.olx.pl/d/oferta/101",
                    price_pln=81000.0,
                    area_m2=300.0,
                    district_match="mokotow",
                    matched_garden="ROD im. Obrońców Pokoju",
                ),
                Listing(
                    id="otodom:202",
                    source="otodom",
                    title="Ogródek działkowy ROD Fort Szczęśliwice",
                    url="https://www.otodom.pl/pl/oferta/202",
                    price_pln=80000.0,
                    area_m2=250.0,
                    district_match="ochota",
                    matched_garden="ROD Fort Szczęśliwice",
                ),
            ]
            new_items, price_drops = store.diff_and_update(
                updated_listings, now_iso="2026-05-03T08:00:00+00:00"
            )
            self.assertEqual(len(new_items), 0)
            self.assertEqual(len(price_drops), 1)
            dropped_listing, prev_price = price_drops[0]
            self.assertEqual(dropped_listing.id, "olx:101")
            self.assertEqual(prev_price, 90000.0)
            self.assertEqual(dropped_listing.price_pln, 81000.0)
            self.assertEqual(store["olx:101"]["price_pln"], 81000.0)

    def test_json_persistence_save_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "nested" / "seen_listings.json"
            store = StateStore(state_path)
            store.diff_and_update(self._sample_listings(), now_iso="2026-05-01T08:00:00+00:00")
            saved_path = store.save()
            self.assertTrue(saved_path.exists())

            reloaded = StateStore(state_path)
            self.assertEqual(len(reloaded), 2)
            self.assertEqual(reloaded["olx:101"]["matched_garden"], "ROD im. Obrońców Pokoju")
            self.assertEqual(reloaded["otodom:202"]["district_match"], "ochota")

            raw_json = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("olx:101", raw_json)
            self.assertEqual(raw_json["olx:101"]["price_pln"], 90000.0)

    def test_prune_old_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "seen_listings.json"
            store = StateStore(state_path)
            old_listing = Listing(
                id="olx:old",
                source="olx",
                title="Stara oferta",
                url="https://www.olx.pl/d/oferta/old",
                price_pln=50000.0,
                district_match="mokotow",
            )
            recent_listing = Listing(
                id="olx:recent",
                source="olx",
                title="Nowa oferta",
                url="https://www.olx.pl/d/oferta/recent",
                price_pln=60000.0,
                district_match="ochota",
            )
            store.diff_and_update([old_listing], now_iso="2025-01-01T00:00:00+00:00")
            store.diff_and_update([recent_listing], now_iso="2026-05-01T00:00:00+00:00")

            pruned = store.prune_old(max_age_days=180, now="2026-05-15T00:00:00+00:00")
            self.assertEqual(pruned, 1)
            self.assertNotIn("olx:old", store)
            self.assertIn("olx:recent", store)


class TestNotifiers(unittest.TestCase):
    """Tests for Markdown report formatting and Email + GitHub notification dispatch."""

    def test_format_markdown_report_sections_and_pln_per_m2(self) -> None:
        mokotow_item = Listing(
            id="olx:1",
            source="olx",
            title="ROD Obrońców Pokoju - ul. Odyńca",
            url="https://www.olx.pl/d/oferta/1",
            price_pln=90000.0,
            area_m2=300.0,
            location_raw="Warszawa, Mokotów",
            district_match="mokotow",
            matched_garden="ROD im. Obrońców Pokoju",
            match_reason="garden: ROD im. Obrońców Pokoju",
        )
        ochota_item = Listing(
            id="otodom:2",
            source="otodom",
            title="ROD Fort Szczęśliwice - piękna działka",
            url="https://www.otodom.pl/pl/oferta/2",
            price_pln=80000.0,
            area_m2=250.0,
            location_raw="Warszawa, Ochota",
            district_match="ochota",
            matched_garden="ROD Fort Szczęśliwice",
        )
        unassigned_item = Listing(
            id="gratka:3",
            source="gratka",
            title="Działka ROD Warszawa",
            url="https://gratka.pl/oferta/3",
            price_pln=55000.0,
            area_m2=None,
            location_raw="Warszawa",
            district_match="warszawa_unassigned",
        )
        dropped_item = Listing(
            id="olx:4",
            source="olx",
            title="ROD Rakowiec przecena",
            url="https://www.olx.pl/d/oferta/4",
            price_pln=70000.0,
            area_m2=280.0,
            district_match="mokotow",
            matched_garden="ROD Rakowiec",
        )

        report = format_markdown_report(
            new_listings=[mokotow_item, ochota_item, unassigned_item],
            price_drops=[(dropped_item, 80000.0)],
            stats={
                "sources_scanned": ["olx", "otodom", "gratka"],
                "total_scanned": 12,
                "new_count": 3,
                "warnings": ["gratka timeout on page 2"],
            },
        )

        self.assertIn("Confirmed Mokotów & Ochota ROD Listings", report)
        self.assertIn("[MOKOTÓW]", report)
        self.assertIn("[OCHOTA]", report)
        self.assertIn("ROD im. Obrońców Pokoju", report)
        self.assertIn("90 000 PLN", report)
        self.assertIn("300 PLN/m²", report)
        self.assertIn("320 PLN/m²", report)
        self.assertIn("Price Drops on Previously Seen Listings", report)
        self.assertIn("80 000 PLN -> 70 000 PLN (-12.5%)", report)
        self.assertIn("Unassigned Warsaw Listings (Verify if Mokotów/Ochota)", report)
        self.assertIn("https://gratka.pl/oferta/3", report)
        self.assertIn("gratka timeout on page 2", report)

    @mock.patch("urllib.request.urlopen")
    def test_send_github_issue(self, mock_urlopen: mock.MagicMock) -> None:
        mock_resp = mock.MagicMock()
        mock_resp.status = 201
        mock_resp.getcode.return_value = 201
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        self.assertTrue(send_github_issue("Alert Title", "## Body", "owner/repo", "ghs_secret"))
        gh_req = mock_urlopen.call_args[0][0]
        self.assertEqual(gh_req.full_url, "https://api.github.com/repos/owner/repo/issues")
        body_json = json.loads(gh_req.data.decode("utf-8"))
        self.assertEqual(body_json["labels"], ["rod-alert"])

    @mock.patch("smtplib.SMTP")
    def test_send_email_smtp(self, mock_smtp_cls: mock.MagicMock) -> None:
        smtp_instance = mock.MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = smtp_instance

        ok = send_email(
            subject="ROD Alert",
            report_text="Treść raportu",
            smtp_config={
                "host": "smtp.example.com",
                "port": 587,
                "user": "scanner@example.com",
                "password": "secret",
                "to": "buyer@example.com",
            },
        )
        self.assertTrue(ok)
        smtp_instance.starttls.assert_called_once()
        smtp_instance.login.assert_called_once_with("scanner@example.com", "secret")
        smtp_instance.send_message.assert_called_once()

    def test_write_github_step_summary_and_dry_run_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_file = Path(tmpdir) / "step_summary.md"
            listing = Listing(
                id="olx:999",
                source="olx",
                title="ROD Mokotów",
                url="https://www.olx.pl/d/oferta/999",
                price_pln=70000.0,
                district_match="mokotow",
            )
            env = {
                "SMTP_HOST": "smtp.example.com",
                "NOTIFY_EMAIL_TO": "user@example.com",
                "GITHUB_ISSUE_ALERTS": "true",
                "GITHUB_TOKEN": "gh_tok",
                "GITHUB_REPOSITORY": "user/repo",
                "GITHUB_STEP_SUMMARY": str(summary_file),
            }
            with mock.patch.dict(os.environ, env, clear=True):
                # Dry-run should not invoke network or write summary file
                dry_res = dispatch_notifications(
                    new_listings=[listing],
                    price_drops=[],
                    report_md="# Dry Run Report\n",
                    dry_run=True,
                )
                self.assertEqual(dry_res["email"], "dry_run")
                self.assertEqual(dry_res["github_issue"], "dry_run")
                self.assertEqual(dry_res["github_step_summary"], "dry_run")
                self.assertNotIn("telegram", dry_res)
                self.assertNotIn("discord", dry_res)
                self.assertFalse(summary_file.exists())

                # Direct write_github_step_summary call writes file
                self.assertTrue(write_github_step_summary("# Summary Content"))
                self.assertIn("# Summary Content", summary_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
