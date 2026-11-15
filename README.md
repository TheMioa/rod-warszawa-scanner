# Warsaw ROD Działki Scanner (Mokotów & Ochota)

An automated daily scanner that monitors Polish real-estate classified portals, **PZD Okręg Mazowiecki**, and official **Warsaw ROD (Rodzinne Ogrody Działkowe)** garden websites for allotment plots (*działki ROD / ogródki działkowe*) available in **Mokotów** and **Ochota**.

Designed to run **100% free on GitHub Actions** on a daily cron schedule (`17 6 * * *` UTC — 07:17 CET / 08:17 CEST), persisting seen listings directly in the repository (`data/seen_listings.json`) so you only get alerted when **brand-new listings** appear or when a previously seen plot **drops in price**.

---

## How the Scanner Works

### 1. Multi-Source Scraping (`rod_scanner.scrapers`)
The scanner queries six categories of sources on every run, isolating network or parsing errors per source so one temporarily unavailable website never breaks the daily pipeline:

| Source ID | Portal / Website | What It Scans |
| :--- | :--- | :--- |
| `olx` | **OLX.pl** | Warsaw plot & recreational allotment searches (`?q=ROD`, `?q=ogrodek+dzialkowy`), parsing HTML cards and embedded `__PRERENDERED_STATE__` / JSON-LD metadata. |
| `otodom` | **Otodom.pl** | Warsaw recreational plots (`dzialka,rekreacyjna/mazowieckie/warszawa`), parsing Next.js `__NEXT_DATA__` payloads and fallback HTML cards. |
| `gratka` | **Gratka.pl** | Recreational allotment listings in Warsaw (`dzialki-grunty/rekreacyjna/warszawa`). |
| `sprzedajemy` | **Sprzedajemy.pl** | Warsaw land & allotment classifieds matching `ROD` / `działka`. |
| `pzd_mazowiecki` | **PZD Okręg Mazowiecki** | Official Mazovian District Board announcements (`mazowiecki.pzd.pl`) regarding available plots, tenders (*przetargi*), or rights transfers (*nabycie prawa do działki*). |
| `rod_garden` | **Mokotów & Ochota ROD Boards** | Direct checks of local ROD garden notice boards (e.g., *ROD im. Obrońców Pokoju*, *ROD Rakowiec*, *ROD Fort Szczęśliwice*, *ROD Czerniaków*, *ROD Siekierki*, *ROD im. Żwirki i Wigury*), plus any custom URLs configured via `ROD_EXTRA_GARDEN_URLS`. |

---

### 2. Smart Mokotów & Ochota Matcher (`rod_scanner.matcher`)
Sellers frequently omit the district name in their listing title and instead mention a garden name, street, or landmark (for example, *"Działka ROD Odyńca"* or *"Ogródek Fort Szczęśliwice"*), or post without Polish diacritics (*"Mokotow"*, *"Odynca"*, *"Zwirki i Wigury"*).

The smart matcher applies three stages:
1. **Diacritic & Case Normalization**: Strips Polish accents (`ą→a, ć→c, ę→e, ł→l, ń→n, ó→o, ś→s, ź→z, ż→z`) and normalizes punctuation.
2. **ROD Relevance Verification**: Filters out non-allotment real estate (commercial offices, apartments, buildable investment lots) unless the listing originates from an official PZD/ROD source or matches ROD/allotment keywords (*ROD, ogródek działkowy, działka rekreacyjna, PZD, altana, odstąpię prawo do działki*).
3. **Street, Neighborhood & Garden Gazetteer + Unassigned Fallback**:
   - **Confirmed Mokotów (`mokotow`)**: Matches Mokotów ROD gardens and streets including *ROD im. Obrońców Pokoju (Odyńca, Racławicka, al. Niepodległości)*, *ROD Rakowiec (Rostafińskich, Chodkiewicza, Pole Mokotowskie)*, *ROD Czerniaków (Bernardyńska, Czerniakowska, Gołkowska, Jeziorko Czerniakowskie)*, *ROD Siekierki / 1 Maja / Antoniewska / Bartycka / Bluszczańska / Polska / Gościniec / Gwintowa*, *ROD SGGW*, *ROD Służewiec*, *ROD Stegny*, *ROD Sadyba*, *Wyględów, Wierzbno, Służew, Królikarnia, Dolny/Górny Mokotów, Woronicza, Bukowińska, Idzikowskiego, Sobieskiego, Sikorskiego, Dolna, Chełmska, Gagarina, Bobrowiecka*.
   - **Confirmed Ochota (`ochota`)**: Matches Ochota ROD gardens and streets including *ROD Fort Szczęśliwice (Drawska, Śmigłowca, Szczęśliwice, Szczęśliwicka, Włodarzewska, Dickensa, Park Szczęśliwicki)*, *ROD im. Żwirki i Wigury (Żwirki i Wigury, Księcia Trojdena, Pawińskiego, Banacha, Hankiewicza, Korotyńskiego)*, *Stara Ochota, Filtry, Kolonia Lubeckiego, Grójecka, Wawelska, Filtrowa, Niemcewicza*.
   - **Excluded Districts (`excluded`)**: Automatically rejects listings located in other Warsaw districts or suburbs (*Białołęka, Wawer, Targówek, Bemowo, Bielany, Ursynów, Wola, Praga, Grochów, Saska Kępa, Gocław, Tarchomin, Bródno, Rembertów, Wesoła, Ursus, Wilanów, Żoliborz, Włochy, Okęcie, Śródmieście, Marki, Ząbki, Piaseczno, Konstancin, Legionowo, Pruszków, Otwock*, etc.).
   - **Unassigned Warsaw Fallback (`warszawa_unassigned`)**: Many OLX/Gratka sellers simply select *"Warszawa"* as the location without writing the district in the text. Unless `--strict-districts-only` is passed, these listings are retained in a separate **"Unassigned Warsaw Listings (Verify if Mokotów/Ochota)"** section so you never miss a vaguely described plot.

---

### 3. State Deduplication & Price Drop Tracking (`rod_scanner.state`)
- Every matched listing is recorded in `data/seen_listings.json` keyed by `listing.id` (`source:id`) with `first_seen_at`, `last_seen_at`, `price_pln`, `title`, `url`, `district_match`, and `matched_garden`.
- On subsequent runs:
  - Previously seen listings with unchanged prices are suppressed from notifications.
  - Any previously seen listing whose price drops by **at least 1 PLN** is highlighted in the **Price Drops on Previously Seen Listings** section showing the old price, new price, and percentage reduction.
  - Listings not seen for over 180 days are automatically pruned.

---

## Step-by-Step GitHub Setup

### Step 1: Create the Repository & Push
```bash
git init
git add .
git commit -m "Initial commit: Warsaw ROD Działki Scanner"
git branch -M main
git remote add origin https://github.com/<your-username>/rod-warszawa-scanner.git
git push -u origin main
```

### Step 2: Enable GitHub Actions Read & Write Permissions
Because the workflow commits updated `data/seen_listings.json` and `data/latest_report.md` back to the repository (and can optionally open GitHub Issues on new matches):
1. Open your repository on GitHub and go to **Settings → Actions → General**.
2. Scroll down to **Workflow permissions**.
3. Select **Read and write permissions** and click **Save**.

### Step 3: Configure Notification Secrets (Email & GitHub)
Every run automatically publishes a formatted Markdown report to the **GitHub Actions Step Summary**. For active alerts when new plots or price drops are discovered, go to **Settings → Secrets and variables → Actions → New repository secret** and configure **GitHub Issue Alerts**, **Email (SMTP)**, or both:

#### Option A: GitHub Issue Alerts (Zero External Accounts Needed)
- `GITHUB_ISSUE_ALERTS`: Set to `true`.
- (`GITHUB_TOKEN` is provided automatically by GitHub Actions). Whenever new plots or price drops are found, the workflow creates a GitHub Issue labeled `rod-alert` containing the full Markdown digest, which triggers standard GitHub email and mobile notifications.

#### Option B: Email Notifications (SMTP)
Add repository secrets for your SMTP provider (e.g., Gmail App Password, Fastmail, Mailbox.org):
- `SMTP_HOST`: e.g. `smtp.gmail.com`
- `SMTP_PORT`: e.g. `587` (STARTTLS) or `465` (SSL)
- `SMTP_USER`: Your SMTP username / email address
- `SMTP_PASSWORD`: Your SMTP password or App Password
- `NOTIFY_EMAIL_TO`: Recipient email address(es)
- `NOTIFY_EMAIL_FROM`: Sender email address (defaults to `SMTP_USER` if omitted)

#### Optional: Extra ROD Garden Board URLs
- `ROD_EXTRA_GARDEN_URLS`: Comma-separated list of additional ROD garden notice board URLs to scrape on every run.

---

## Running Locally & Testing

### Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
*(Note: The scanner also includes standard-library fallbacks for HTTP and HTML parsing, so offline tests and `--demo-fixture` work even without third-party packages installed.)*

### Run the Offline Demo Pipeline (`--demo-fixture --dry-run`)
Test the entire pipeline end-to-end offline using realistic Mokotów, Ochota, PZD, unassigned Warsaw, and excluded Białołęka sample listings without touching `data/seen_listings.json`:
```bash
python3 -m rod_scanner --demo-fixture --dry-run
```

### Run a Live Scan Locally
```bash
# Standard scan (saves state to data/seen_listings.json and report to data/latest_report.md)
python3 -m rod_scanner

# High-confidence Mokotów & Ochota matches only (drop unassigned Warsaw fallback)
python3 -m rod_scanner --strict-districts-only

# Scan specific sources only in dry-run mode
python3 -m rod_scanner --sources olx,otodom,pzd_mazowiecki --dry-run

# Test your configured Email / GitHub notifications even if 0 new listings are found
python3 -m rod_scanner --demo-fixture --dry-run --force-notify
```

### CLI Options Reference
| Flag | Default | Description |
| :--- | :--- | :--- |
| `--state-file PATH` | `data/seen_listings.json` | Path to the persistent seen-listings JSON state file. |
| `--output-report PATH` | `data/latest_report.md` | Path where the generated Markdown digest is saved. |
| `--strict-districts-only` | `False` | Excludes `warszawa_unassigned` listings so only confirmed Mokotów & Ochota plots are included. |
| `--sources LIST` | `all` | Comma-separated list of sources (`olx,otodom,gratka,sprzedajemy,pzd_mazowiecki,rod_garden`) or `all`. |
| `--demo-fixture` | `False` | Uses built-in sample listings instead of making live HTTP requests. |
| `--dry-run` | `False` | Prints the Markdown report to `stdout` without overwriting the state file or sending external notifications. |
| `--force-notify` | `False` | Dispatches notifications even when `0` new listings or price drops are detected. |

### Run the Unit Test Suite
```bash
pytest -v
# or using standard library unittest:
python3 -m unittest discover -s tests -v
```

---

## Triggering a Manual Test Run on GitHub Actions (`workflow_dispatch`)

You do not need to wait for the daily `06:17 UTC` cron job to test your setup:
1. Navigate to your repository on GitHub and click the **Actions** tab.
2. In the left sidebar, select **Daily Warsaw ROD Działki Scanner (Mokotów & Ochota)**.
3. Click the **Run workflow** dropdown button on the right:
   - Check **`demo_fixture`** if you want to test the pipeline using sample Mokotów & Ochota listings without waiting on live portals.
   - Check **`force_notify`** if you want to verify your Email or GitHub Issue notifications immediately.
   - Check **`strict_districts_only`** if you only want confirmed Mokotów & Ochota matches.
4. Click **Run workflow**. Once complete, view the formatted Markdown digest directly in the workflow run's **Summary** tab or download the `rod-scan-report` artifact.
