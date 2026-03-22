"""
NSE Holiday Auto-Updater
========================
Run this script once a year (or anytime) to automatically fetch
the latest NSE trading holidays and update config/settings.py.

Sources tried in order:
  1. NSE India official API  (most accurate)
  2. BSE India official API  (backup)
  3. pandas_market_calendars (offline fallback)

Usage:
    python update_holidays.py           # update current + next year
    python update_holidays.py --year 2027
    python update_holidays.py --dry-run # preview without saving
"""

import sys, os, re, json, argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "config", "settings.py")


# ── SOURCE 1: NSE INDIA OFFICIAL API ─────────────────────────────

def fetch_from_nse(year: int) -> list[str]:
    """
    NSE has a public JSON API for trading holidays.
    Returns list of "YYYY-MM-DD" strings.
    """
    import requests
    from bs4 import BeautifulSoup

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/",
    })

    # Step 1: hit homepage to get cookies (NSE blocks without valid session)
    try:
        session.get("https://www.nseindia.com/", timeout=10)
    except Exception:
        pass

    # Step 2: fetch holiday master
    url = "https://www.nseindia.com/api/holiday-master?type=trading"
    r   = session.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()

    holidays = []
    # NSE response: {"CM": [...], "FO": [...], ...}
    # CM = Capital Market (equities) — the one we want
    segment = data.get("CM") or data.get("FO") or list(data.values())[0]
    for item in segment:
        # Date field is like "20-Jan-2025" or "2025-01-20"
        raw_date = item.get("tradingDate") or item.get("date") or ""
        parsed   = _parse_date(raw_date)
        if parsed and str(year) in parsed:
            holidays.append(parsed)

    return sorted(set(holidays))


# ── SOURCE 2: BSE INDIA API ───────────────────────────────────────

def fetch_from_bse(year: int) -> list[str]:
    """
    BSE also publishes trading holidays via their API.
    """
    import requests

    url = "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer":    "https://www.bseindia.com/",
    }
    r    = requests.get(url, headers=headers, timeout=10)
    data = r.json()

    holidays = []
    items = data.get("Table") or data.get("holidayList") or []
    for item in items:
        raw_date = item.get("Holi_Date") or item.get("date") or ""
        parsed   = _parse_date(raw_date)
        if parsed and str(year) in parsed:
            holidays.append(parsed)

    return sorted(set(holidays))


# ── SOURCE 3: pandas_market_calendars (offline) ───────────────────

def fetch_from_pandas_market_cal(year: int) -> list[str]:
    """
    pandas_market_calendars has NSE calendar built-in.
    Install: pip install pandas_market_calendars
    """
    import pandas_market_calendars as mcal
    import pandas as pd

    nse = mcal.get_calendar("NSE")
    schedule = nse.schedule(
        start_date=f"{year}-01-01",
        end_date=f"{year}-12-31"
    )

    # All trading days in year
    all_weekdays = pd.bdate_range(f"{year}-01-01", f"{year}-12-31")
    trading_days = set(schedule.index.strftime("%Y-%m-%d"))
    weekday_strs = set(all_weekdays.strftime("%Y-%m-%d"))

    # Holidays = weekdays that are NOT trading days
    holidays = sorted(weekday_strs - trading_days)
    return holidays


# ── DATE PARSER ───────────────────────────────────────────────────

def _parse_date(raw: str) -> str | None:
    """Parse various date formats into YYYY-MM-DD."""
    if not raw:
        return None
    raw = raw.strip()
    # Try common formats
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y",
                "%d-%m-%Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── SETTINGS UPDATER ─────────────────────────────────────────────

def read_current_holidays() -> set[str]:
    """Read existing NSE_HOLIDAYS from settings.py."""
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Extract all "YYYY-MM-DD" dates inside NSE_HOLIDAYS block
    match = re.search(
        r"NSE_HOLIDAYS\s*=\s*\{(.*?)\}",
        content, re.DOTALL
    )
    if not match:
        return set()
    block = match.group(1)
    dates = re.findall(r'"(\d{4}-\d{2}-\d{2})"', block)
    return set(dates)


def write_holidays_to_settings(all_holidays: dict[str, list]) -> bool:
    """
    Update NSE_HOLIDAYS block in settings.py.
    all_holidays = {year_str: [(date, description), ...]}
    Returns True if file was changed.
    """
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Build new block
    lines = []
    for year, entries in sorted(all_holidays.items()):
        lines.append(f"    # {year}")
        for date, desc in sorted(entries):
            comment = f"  # {desc}" if desc else ""
            lines.append(f'    "{date}",{comment}')
    block_inner = "\n".join(lines)

    new_block = f"NSE_HOLIDAYS = {{\n{block_inner}\n}}"

    # Replace existing block
    updated = re.sub(
        r"NSE_HOLIDAYS\s*=\s*\{.*?\}",
        new_block,
        content,
        flags=re.DOTALL
    )

    if updated == content:
        return False  # no change

    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        f.write(updated)
    return True


# ── HOLIDAY NAME LOOKUP ───────────────────────────────────────────

# Approximate names — used when API doesn't provide description
KNOWN_HOLIDAYS = {
    "01-26": "Republic Day",
    "04-14": "Dr. Baba Saheb Ambedkar Jayanti",
    "05-01": "Maharashtra Day / Labour Day",
    "08-15": "Independence Day",
    "10-02": "Mahatma Gandhi Jayanti",
    "12-25": "Christmas",
}


def get_description(date_str: str, api_desc: str = "") -> str:
    if api_desc:
        return api_desc
    mm_dd = date_str[5:]  # "MM-DD"
    return KNOWN_HOLIDAYS.get(mm_dd, "")


# ── MAIN ──────────────────────────────────────────────────────────

def fetch_holidays_for_year(year: int) -> list[tuple[str, str]]:
    """
    Try all sources in order. Returns [(date, description), ...].
    """
    import requests

    # Source 1: NSE API
    print(f"  → Trying NSE India API for {year}...", end=" ", flush=True)
    try:
        dates = fetch_from_nse(year)
        if dates:
            print(f"✅ Got {len(dates)} holidays")
            return [(d, get_description(d)) for d in dates]
        print("empty response")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")

    # Source 2: BSE API
    print(f"  → Trying BSE India API for {year}...", end=" ", flush=True)
    try:
        dates = fetch_from_bse(year)
        if dates:
            print(f"✅ Got {len(dates)} holidays")
            return [(d, get_description(d)) for d in dates]
        print("empty response")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")

    # Source 3: pandas_market_calendars
    print(f"  → Trying pandas_market_calendars for {year}...", end=" ", flush=True)
    try:
        dates = fetch_from_pandas_market_cal(year)
        if dates:
            print(f"✅ Got {len(dates)} holidays")
            return [(d, get_description(d)) for d in dates]
        print("empty response")
    except ImportError:
        print("❌ Not installed — run: pip install pandas_market_calendars")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")

    print(f"  ⚠️  All sources failed for {year}. Manual update needed.")
    return []


def main():
    parser = argparse.ArgumentParser(description="Auto-update NSE holidays in settings.py")
    parser.add_argument("--year",    type=int, default=None,
                        help="Specific year to fetch (default: current + next)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview changes without saving")
    parser.add_argument("--force",   action="store_true",
                        help="Re-fetch even if year already present in settings")
    args = parser.parse_args()

    current_year = datetime.now().year
    years_to_fetch = [args.year] if args.year else [current_year, current_year + 1]

    print("=" * 60)
    print("  NSE Holiday Auto-Updater")
    print(f"  Settings file: {SETTINGS_FILE}")
    print("=" * 60)

    # Read what's already in settings
    existing = read_current_holidays()
    print(f"\nCurrently in settings.py: {len(existing)} holidays")

    # Build full holiday dict (keep existing, add new)
    # Group existing by year
    all_holidays: dict[str, list] = {}
    for d in existing:
        yr = d[:4]
        all_holidays.setdefault(yr, [])
        all_holidays[yr].append((d, get_description(d)))

    # Fetch for requested years
    updated_years = []
    for year in years_to_fetch:
        yr_str = str(year)
        if yr_str in all_holidays and not args.force:
            existing_count = len(all_holidays[yr_str])
            print(f"\n[{year}] Already have {existing_count} holidays — skipping "
                  f"(use --force to re-fetch)")
            continue

        print(f"\n[{year}] Fetching holidays...")
        entries = fetch_holidays_for_year(year)

        if entries:
            all_holidays[yr_str] = entries
            updated_years.append(year)
            print(f"  Dates: {[e[0] for e in entries]}")
        else:
            print(f"  No data fetched for {year} — keeping existing entries")

    if not updated_years:
        print("\n✅ Nothing to update — settings.py is already up to date.")
        print("   Use --force to re-fetch existing years.")
        return

    # Preview
    total_new = sum(len(v) for k, v in all_holidays.items()
                    if int(k) in updated_years)
    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Updating settings.py with "
          f"{total_new} new holiday entries for {updated_years}...")

    if args.dry_run:
        print("\n[DRY RUN] Would write the following NSE_HOLIDAYS block:")
        print("-" * 50)
        for year, entries in sorted(all_holidays.items()):
            print(f"    # {year}")
            for date, desc in sorted(entries):
                comment = f"  # {desc}" if desc else ""
                print(f'    "{date}",{comment}')
        print("-" * 50)
        print("[DRY RUN] No file was changed.")
        return

    changed = write_holidays_to_settings(all_holidays)
    if changed:
        total = sum(len(v) for v in all_holidays.values())
        print(f"✅ settings.py updated! Total holidays now: {total}")
        print(f"   Updated years: {updated_years}")
    else:
        print("ℹ️  No changes made (dates already match).")

    print("\nDone. Restart bot.py for changes to take effect.")


if __name__ == "__main__":
    main()
