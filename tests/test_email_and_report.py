"""Unit tests for English report formatting, plain-text email, HTML email, and SMTP multipart dispatch."""

from __future__ import annotations

from email.message import EmailMessage
import os
from pathlib import Path
import re
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rod_scanner.models import Listing
from rod_scanner.notifiers import (
    _district_badge,
    _format_price_pln,
    dispatch_notifications,
    format_html_email,
    format_markdown_report,
    format_plain_email,
    send_email,
)


POLISH_FORBIDDEN_TEMPLATE_WORDS = (
    "Raport",
    "cena nieznana",
    "Brak ",
    "ogłoszeń",
    "obniżek",
    "nowych ogłoszeń",
    "nieprzypisanych",
)


class TestEmailAndReport(unittest.TestCase):
    """Verify 100% English templates, clean plain-text email, styled HTML email, and multipart SMTP."""

    def _sample_data(self) -> tuple[list[Listing], list[tuple[Listing, float]], dict]:
        mokotow = Listing(
            id="olx:101",
            source="olx",
            title="Sunny plot <ROD> & apple trees",
            url="https://www.olx.pl/d/oferta/101?a=1&b=2",
            price_pln=95000.0,
            area_m2=300.0,
            location_raw="Warsaw, Mokotów",
            district_match="mokotow",
            matched_garden="ROD im. Obrońców Pokoju",
            match_reason="garden: ROD im. Obrońców Pokoju",
        )
        ochota = Listing(
            id="otodom:202",
            source="otodom",
            title="Allotment plot near park",
            url="https://www.otodom.pl/pl/oferta/202",
            price_pln=None,
            price_raw=None,
            area_m2=250.0,
            location_raw="Warsaw, Ochota",
            district_match="ochota",
            matched_garden="ROD Fort Szczęśliwice",
            match_reason="garden: ROD Fort Szczęśliwice",
        )
        unassigned = Listing(
            id="gratka:303",
            source="gratka",
            title="Allotment plot in Warsaw",
            url="https://gratka.pl/oferta/303",
            price_pln=55000.0,
            area_m2=None,
            location_raw="Warsaw",
            district_match="warszawa_unassigned",
            match_reason="Warsaw fallback (verify district)",
        )
        dropped = Listing(
            id="olx:404",
            source="olx",
            title="ROD Rakowiec plot with brick cottage",
            url="https://www.olx.pl/d/oferta/404",
            price_pln=70000.0,
            area_m2=280.0,
            location_raw="Warsaw, Mokotów",
            district_match="mokotow",
            matched_garden="ROD Rakowiec",
        )
        stats = {
            "sources_scanned": ["olx", "otodom", "gratka"],
            "total_scanned": 24,
            "new_count": 3,
            "price_drops_count": 1,
            "warnings": ["gratka page 2 slow response"],
        }
        return [mokotow, ochota, unassigned], [(dropped, 80000.0)], stats

    def _assert_no_polish_template_words(self, text: str) -> None:
        for word in POLISH_FORBIDDEN_TEMPLATE_WORDS:
            self.assertNotIn(
                word.lower(),
                text.lower(),
                f"Found forbidden Polish template leftover {word!r} in output",
            )

    def test_price_and_district_badge_helpers(self) -> None:
        self.assertEqual(_format_price_pln(95000.0), "95,000 PLN")
        self.assertEqual(_format_price_pln(None, None), "Price not listed")
        self.assertEqual(_format_price_pln(None, ""), "Price not listed")
        self.assertEqual(_format_price_pln(None, "cena nieznana"), "Price not listed")

        self.assertEqual(_district_badge("mokotow"), "[MOKOTÓW]")
        self.assertEqual(_district_badge("ochota"), "[OCHOTA]")
        self.assertEqual(_district_badge("warszawa_unassigned"), "[WARSAW - VERIFY DISTRICT]")

    def test_markdown_report_english_populated_and_empty(self) -> None:
        listings, drops, stats = self._sample_data()
        md_populated = format_markdown_report(listings, drops, stats)
        self._assert_no_polish_template_words(md_populated)

        self.assertIn("# Warsaw ROD Allotment Report — Mokotów & Ochota", md_populated)
        self.assertIn("## 1. Confirmed Mokotów & Ochota ROD Listings", md_populated)
        self.assertIn("## 2. Price Drops on Previously Seen Listings", md_populated)
        self.assertIn("## 3. Unassigned Warsaw Listings (Verify if Mokotów/Ochota)", md_populated)
        self.assertIn("### Run Statistics", md_populated)
        self.assertIn("95,000 PLN", md_populated)
        self.assertIn("Price not listed", md_populated)
        self.assertIn("[WARSAW - VERIFY DISTRICT]", md_populated)
        self.assertIn("80,000 PLN -> 70,000 PLN (-12.5%)", md_populated)

        md_empty = format_markdown_report([], [], {})
        self._assert_no_polish_template_words(md_empty)
        self.assertIn("_No new confirmed listings for Mokotów or Ochota._", md_empty)
        self.assertIn("_No price drops on previously seen listings._", md_empty)
        self.assertIn("_No unassigned Warsaw listings._", md_empty)

    def test_plain_email_no_raw_markdown_and_english_only(self) -> None:
        listings, drops, stats = self._sample_data()
        plain = format_plain_email(listings, drops, stats)
        self._assert_no_polish_template_words(plain)

        # Must not contain raw Markdown formatting syntax
        self.assertNotIn("**", plain)
        self.assertIsNone(re.search(r"\[[^\]]+\]\(https?://", plain))
        self.assertNotIn("_No ", plain)
        self.assertNotIn("_(match:", plain)

        # Must contain clean structure and indented Link lines
        self.assertIn("WARSAW ROD ALLOTMENT SCANNER", plain)
        self.assertIn("Daily Digest — Mokotów & Ochota", plain)
        self.assertIn("95,000 PLN", plain)
        self.assertIn("Price not listed", plain)
        self.assertIn("    Link: https://www.olx.pl/d/oferta/101?a=1&b=2", plain)
        self.assertIn("    Link: https://www.olx.pl/d/oferta/404", plain)

        plain_empty = format_plain_email([], [], {})
        self._assert_no_polish_template_words(plain_empty)
        self.assertNotIn("**", plain_empty)
        self.assertNotIn("_", plain_empty)
        self.assertIn("No new confirmed listings for Mokotów or Ochota.", plain_empty)
        self.assertIn("No price drops on previously seen listings.", plain_empty)
        self.assertIn("No unassigned Warsaw listings.", plain_empty)

    def test_html_email_structure_escaping_and_styles(self) -> None:
        listings, drops, stats = self._sample_data()
        html_out = format_html_email(listings, drops, stats)
        self._assert_no_polish_template_words(html_out)

        # Banner & headings
        self.assertIn("Warsaw ROD Allotment Scanner", html_out)
        self.assertIn("Daily Digest — Mokotów &amp; Ochota", html_out)
        self.assertIn("#14532d", html_out)

        # Badges & colors
        self.assertIn("#dcfce7", html_out)
        self.assertIn("#166534", html_out)
        self.assertIn("#dbeafe", html_out)
        self.assertIn("#1e40af", html_out)
        self.assertIn("WARSAW - VERIFY", html_out)

        # HTML escaping & clickable links
        self.assertIn("Sunny plot &lt;ROD&gt; &amp; apple trees", html_out)
        self.assertNotIn("<ROD>", html_out)
        self.assertIn(
            'href="https://www.olx.pl/d/oferta/101?a=1&amp;b=2"',
            html_out,
        )

        # Price formatting & price drop strike-through
        self.assertIn("95,000 PLN", html_out)
        self.assertIn("Price not listed", html_out)
        self.assertIn("<del", html_out)
        self.assertIn("80,000 PLN</del>", html_out)
        self.assertIn("70,000 PLN</strong>", html_out)
        self.assertIn("-12.5%", html_out)

        # Footer link to historical CSV
        self.assertIn("data/history/listings_history.csv", html_out)

        # Empty state HTML check
        html_empty = format_html_email([], [], {})
        self._assert_no_polish_template_words(html_empty)
        self.assertIn("No new confirmed listings for Mokotów or Ochota.", html_empty)
        self.assertIn("No price drops on previously seen listings.", html_empty)
        self.assertIn("No unassigned Warsaw listings.", html_empty)

    @mock.patch("smtplib.SMTP")
    def test_send_email_constructs_multipart_alternative(self, mock_smtp_cls: mock.MagicMock) -> None:
        smtp_instance = mock.MagicMock()
        mock_smtp_cls.return_value.__enter__.return_value = smtp_instance

        listings, drops, stats = self._sample_data()
        plain_body = format_plain_email(listings, drops, stats)
        html_body = format_html_email(listings, drops, stats)

        ok = send_email(
            subject="[Warsaw ROD Scanner] Daily Digest: 3 new listings, 1 price drops (2026-11-15)",
            report_text=plain_body,
            smtp_config={
                "host": "smtp.example.com",
                "port": 587,
                "user": "scanner@example.com",
                "password": "app-password",
                "to": ["recipient1@example.com", "recipient2@example.com"],
            },
            html_body=html_body,
        )
        self.assertTrue(ok)
        smtp_instance.send_message.assert_called_once()

        sent_msg: EmailMessage = smtp_instance.send_message.call_args[0][0]
        self.assertEqual(sent_msg.get_content_type(), "multipart/alternative")

        parts = list(sent_msg.iter_parts())
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_content_type(), "text/plain")
        self.assertEqual(parts[1].get_content_type(), "text/html")

        plain_payload = parts[0].get_content()
        html_payload = parts[1].get_content()
        self.assertIn("Link: https://www.olx.pl/d/oferta/101?a=1&b=2", plain_payload)
        self.assertNotIn("**", plain_payload)
        self.assertIn('<a href="https://www.olx.pl/d/oferta/101?a=1&amp;b=2"', html_payload)
        self.assertIn("Sunny plot &lt;ROD&gt; &amp; apple trees", html_payload)

    @mock.patch("rod_scanner.notifiers.send_github_issue", return_value=True)
    @mock.patch("rod_scanner.notifiers.send_email", return_value=True)
    def test_dispatch_notifications_uses_english_subject_and_both_bodies(
        self,
        mock_send_email: mock.MagicMock,
        mock_send_gh: mock.MagicMock,
    ) -> None:
        listings, drops, stats = self._sample_data()
        report_md = format_markdown_report(listings, drops, stats)

        env = {
            "SMTP_HOST": "smtp.example.com",
            "NOTIFY_EMAIL_TO": "recipient1@example.com",
            "GITHUB_ISSUE_ALERTS": "true",
            "GITHUB_TOKEN": "gh_tok",
            "GITHUB_REPOSITORY": "TheMioa/rod-warszawa-scanner",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            res = dispatch_notifications(
                listings,
                drops,
                report_md,
                dry_run=False,
                stats=stats,
            )

        self.assertEqual(res["email"], "sent")
        self.assertEqual(res["github_issue"], "sent")

        mock_send_email.assert_called_once()
        call_args, call_kwargs = mock_send_email.call_args
        subject = call_args[0]
        plain_arg = call_args[1]
        html_arg = call_kwargs.get("html_body") or (call_args[3] if len(call_args) > 3 else None)

        self._assert_no_polish_template_words(subject)
        self.assertIn("[Warsaw ROD Scanner] Daily Digest: 3 new listings, 1 price drops", subject)
        self.assertIsNotNone(html_arg)
        self.assertNotIn("**", plain_arg)
        self.assertIn("<!DOCTYPE html>", html_arg)

        gh_subject = mock_send_gh.call_args[0][0]
        self._assert_no_polish_template_words(gh_subject)
        self.assertEqual(gh_subject, subject)


if __name__ == "__main__":
    unittest.main()
