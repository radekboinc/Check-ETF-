#!/usr/bin/env python3
"""
ETF Monitor
-----------
Sprawdza NAV, Net Assets i Shares Outstanding dla dwóch funduszy ETF,
zapisuje historię odczytów, liczy zmianę procentową względem poprzedniego
odczytu, generuje stronę-dashboard (docs/index.html) oraz wysyła
powiadomienie push (ntfy.sh), gdy coś się zmieniło.

Uruchamiane automatycznie przez GitHub Actions (patrz .github/workflows/check.yml),
ale można je też odpalić lokalnie: python check_etf.py
"""

import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================
# KONFIGURACJA — to jest jedyne miejsce, które musisz edytować
# ============================================================

# Nazwa "tematu" ntfy.sh, na który przyjdzie powiadomienie.
# ZMIEŃ końcówkę na coś swojego i losowego, żeby nikt inny nie zgadł
# nazwy i nie widział Twoich powiadomień (ntfy.sh jest publiczne).
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "etf-monitor-zmien-to-jp92xk")

BASE_DIR = Path(__file__).resolve().parent
HISTORY_FILE = BASE_DIR / "history.json"
DASHBOARD_FILE = BASE_DIR / "docs" / "index.html"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

FUNDS = [
    {
        "id": "nordic",
        "name": "Xtrackers MSCI Nordic UCITS ETF 1C",
        "isin": "IE000BO2Y0T8",
        "source": "dws",
        # strona pokazywana człowiekowi (link w dashboardzie)
        "display_url": "https://etf.dws.com/en-gb/IE000BO2Y0T8-msci-nordic-ucits-etf-1c/",
        # strona faktycznie odpytywana przez bota
        "fetch_url": "https://etf.dws.com/en-gb/IE000BO2Y0T8-msci-nordic-ucits-etf-1c/",
        "currency": "EUR",
    },
    {
        "id": "healthcare",
        "name": "iShares MSCI Europe Health Care Sector UCITS ETF",
        "isin": "IE00BMW42181",
        "source": "ishares",
        "display_url": "https://www.ishares.com/uk/professional/en/products/315806/ishares-msci-europe-health-care-sector-ucits-etf",
        # oryginalna strona iShares blokuje proste zapytania ekranem
        # T&C / wyboru kraju — używamy zamiast tego wersji regionalnej
        # BlackRock.com, która pokazuje te same dane bez tej blokady.
        "fetch_url": "https://www.blackrock.com/se/individual/products/315806/ishares-msci-europe-health-care-sector-ucits-etf",
        "currency": "EUR",
    },
]

METRICS = ["nav", "net_assets", "shares_outstanding"]
METRIC_LABELS = {
    "nav": "NAV",
    "net_assets": "Net Assets",
    "shares_outstanding": "Shares Outstanding",
}


# ============================================================
# POMOCNICZE — czyszczenie liczb
# ============================================================

def clean_number(raw: str):
    """Zamienia '903 870 138' albo '7,4252' albo '1,234.56' na float."""
    if raw is None:
        return None
    txt = raw.strip().replace("\xa0", " ")
    txt = re.sub(r"[^\d.,]", "", txt)
    if not txt:
        return None
    if "," in txt and "." in txt:
        # np. 1,234.56 (przecinek = tysiące, kropka = dziesiętne)
        txt = txt.replace(",", "")
    elif "," in txt:
        # np. 903 870 138 -> spacje już usunięte -> "903870138"
        # albo 7,4252 -> dziesiętne po przecinku (europejski zapis)
        parts = txt.split(",")
        if len(parts[-1]) in (1, 2, 3, 4) and len(parts) == 2:
            txt = txt.replace(",", ".")
        else:
            txt = txt.replace(",", "")
    try:
        return float(txt)
    except ValueError:
        return None


# ============================================================
# POBIERANIE DANYCH — iShares / BlackRock (zwykłe query, bez JS)
# ============================================================

def fetch_ishares_data(fund):
    from bs4 import BeautifulSoup

    resp = requests.get(fund["fetch_url"], headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    result = {"nav": None, "net_assets": None, "shares_outstanding": None,
              "as_of": None, "error": None}

    # 1) Net Assets i Shares Outstanding siedzą w tabelach "Key Facts"
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        label = cells[0].get_text(" ", strip=True)
        value = cells[1].get_text(" ", strip=True)
        low = label.lower()
        if result["net_assets"] is None and low.startswith("net assets"):
            result["net_assets"] = clean_number(value)
            m = re.search(r"as of\s+([\d.]{1,2}\.\w{3}\.\d{4})", label, re.I)
            if m:
                result["as_of"] = m.group(1)
        if result["shares_outstanding"] is None and low.startswith("shares outstanding"):
            result["shares_outstanding"] = clean_number(value)

    # 2) NAV pokazany jest jako "karta", nie wiersz tabeli — szukamy w tekście
    full_text = soup.get_text(" ", strip=True)
    m = re.search(
        r"NAV as of\s*([\d.]{1,2}\.\w{3}\.\d{4}).*?([A-Z]{3})\s*([\d]{1,3}(?:[ ,]\d{3})*(?:[.,]\d+)?)",
        full_text,
    )
    if m:
        result["nav"] = clean_number(m.group(3))
        if not result["as_of"]:
            result["as_of"] = m.group(1)

    if result["nav"] is None and result["net_assets"] is None and result["shares_outstanding"] is None:
        result["error"] = "Nie udało się znaleźć żadnej z wartości na stronie (zmieniła się struktura?)."

    return result


# ============================================================
# POBIERANIE DANYCH — DWS / Xtrackers (strona w całości JS, wymaga
# faktycznego wyrenderowania jak w przeglądarce -> Playwright)
# ============================================================

def fetch_dws_data(fund):
    from playwright.sync_api import sync_playwright

    result = {"nav": None, "net_assets": None, "shares_outstanding": None,
              "as_of": None, "error": None, "debug_text": None}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=HEADERS["User-Agent"])
            page.goto(fund["fetch_url"], wait_until="networkidle", timeout=45000)
            # daj stronie chwilę na dociągnięcie danych po networkidle
            page.wait_for_timeout(2000)
            text = page.inner_text("body")
            browser.close()
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"Playwright nie mógł otworzyć strony DWS: {exc}"
        return result

    result["debug_text"] = text[:4000]  # do diagnostyki, gdyby regex nie trafił

    def grab(patterns, group=1):
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(group)
        return None

    nav_raw = grab([
        r"NAV[^\d]{0,15}([\d]{1,3}(?:[ ,]\d{3})*(?:[.,]\d+)?)\s*EUR",
        r"NAV\s*\([^)]*\)\s*:?\s*([\d]{1,3}(?:[ ,]\d{3})*(?:[.,]\d+)?)",
    ])
    net_assets_raw = grab([
        r"(?:Net Assets|Fund Size|Net Asset Value of Fund|AUM)[^\d]{0,15}([\d]{1,3}(?:[ ,]\d{3})*(?:[.,]\d+)?)\s*(?:EUR|Million|m\b)",
        r"(?:Net Assets|Fund Size)[^\d]{0,20}([\d.,]+)",
    ])
    shares_raw = grab([
        r"(?:Shares Outstanding|Outstanding Shares|Units Outstanding)[^\d]{0,15}([\d]{1,3}(?:[ ,]\d{3})*)",
    ])

    result["nav"] = clean_number(nav_raw)
    result["net_assets"] = clean_number(net_assets_raw)
    result["shares_outstanding"] = clean_number(shares_raw)

    if result["nav"] is None and result["net_assets"] is None and result["shares_outstanding"] is None:
        result["error"] = (
            "Nie udało się dopasować żadnej wartości na stronie DWS. "
            "To pierwszy typowy problem po uruchomieniu — daj mi znać, "
            "wklej fragment 'debug_text' z historii, a poprawię wzorce."
        )

    return result


def fetch_fund_data(fund):
    try:
        if fund["source"] == "ishares":
            return fetch_ishares_data(fund)
        elif fund["source"] == "dws":
            return fetch_dws_data(fund)
        else:
            return {"error": f"Nieznane źródło: {fund['source']}"}
    except Exception:  # noqa: BLE001
        return {
            "nav": None, "net_assets": None, "shares_outstanding": None,
            "error": "Wyjątek podczas pobierania: " + traceback.format_exc(limit=3),
        }


# ============================================================
# HISTORIA
# ============================================================

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"runs": []}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def last_reading_for(history, fund_id):
    for run in reversed(history["runs"]):
        if fund_id in run.get("funds", {}):
            data = run["funds"][fund_id]
            if not data.get("error"):
                return data
    return None


def pct_change(old, new):
    if old in (None, 0) or new is None:
        return None
    return (new - old) / old * 100.0


# ============================================================
# DASHBOARD (HTML)
# ============================================================

def fmt_num(value, decimals=0):
    if value is None:
        return "—"
    if decimals:
        return f"{value:,.{decimals}f}".replace(",", " ")
    return f"{value:,.0f}".replace(",", " ")


def fmt_pct(value):
    if value is None:
        return "—"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def pct_class(value):
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def build_dashboard(history):
    runs = history["runs"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # karty "na teraz" dla każdego funduszu
    cards_html = ""
    for fund in FUNDS:
        latest = last_reading_for(history, fund["id"])
        prev = None
        seen_latest = False
        for run in reversed(runs):
            data = run.get("funds", {}).get(fund["id"])
            if not data or data.get("error"):
                continue
            if not seen_latest:
                seen_latest = True
                continue
            prev = data
            break

        rows = ""
        for metric in METRICS:
            cur_val = latest.get(metric) if latest else None
            prev_val = prev.get(metric) if prev else None
            change = pct_change(prev_val, cur_val)
            decimals = 4 if metric == "nav" else 0
            rows += f"""
            <div class="metric-row">
              <span class="metric-label">{METRIC_LABELS[metric]}</span>
              <span class="metric-value">{fmt_num(cur_val, decimals)}</span>
              <span class="metric-delta {pct_class(change)}">{fmt_pct(change)}</span>
            </div>"""

        as_of = latest.get("as_of") if latest else None
        as_of_html = f'<span class="as-of">stan na {as_of}</span>' if as_of else ""

        cards_html += f"""
        <section class="card">
          <header class="card-head">
            <h2>{fund['name']}</h2>
            <span class="isin">{fund['isin']}</span>
          </header>
          {as_of_html}
          <div class="metrics">{rows}
          </div>
          <a class="source-link" href="{fund['display_url']}" target="_blank" rel="noopener">źródło ↗</a>
        </section>"""

    # log chronologiczny (najnowsze na górze)
    log_rows = ""
    for run in reversed(runs[-120:]):
        ts = run.get("timestamp", "?")
        for fund in FUNDS:
            data = run.get("funds", {}).get(fund["id"])
            if not data:
                continue
            if data.get("error"):
                log_rows += f"""
                <tr class="log-error">
                  <td>{ts}</td><td>{fund['name']}</td>
                  <td colspan="3">⚠ {data['error'][:140]}</td>
                </tr>"""
                continue
            for metric in METRICS:
                val = data.get(metric)
                if val is None:
                    continue
                decimals = 4 if metric == "nav" else 0
                log_rows += f"""
                <tr>
                  <td>{ts}</td>
                  <td>{fund['name']}</td>
                  <td>{METRIC_LABELS[metric]}</td>
                  <td class="num">{fmt_num(val, decimals)}</td>
                </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Monitor ETF</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #eef0ee;
    --surface: #fbfcfb;
    --ink: #1b2321;
    --ink-soft: #55625c;
    --line: #d7dcd7;
    --accent: #3d6478;
    --up: #2f6f4e;
    --down: #a3403a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: 'IBM Plex Sans', sans-serif;
    padding: 24px 16px 64px;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  header.top {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 20px;
    border-bottom: 1px solid var(--line);
    padding-bottom: 14px;
  }}
  header.top h1 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 22px;
    font-weight: 600;
    letter-spacing: -0.01em;
    margin: 0;
  }}
  header.top .updated {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-soft);
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 18px 20px;
    margin-bottom: 16px;
  }}
  .card-head {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 12px;
    margin-bottom: 2px;
  }}
  .card-head h2 {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 16px;
    font-weight: 600;
    margin: 0;
  }}
  .isin {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: var(--ink-soft);
    white-space: nowrap;
  }}
  .as-of {{
    font-size: 11px;
    color: var(--ink-soft);
  }}
  .metrics {{ margin-top: 12px; }}
  .metric-row {{
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 14px;
    align-items: baseline;
    padding: 7px 0;
    border-top: 1px solid var(--line);
  }}
  .metric-row:first-child {{ border-top: none; }}
  .metric-label {{ font-size: 13px; color: var(--ink-soft); }}
  .metric-value {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 14px;
    font-variant-numeric: tabular-nums;
  }}
  .metric-delta {{
    font-family: 'IBM Plex Mono', monospace;
    font-size: 12px;
    min-width: 64px;
    text-align: right;
  }}
  .up {{ color: var(--up); }}
  .down {{ color: var(--down); }}
  .flat {{ color: var(--ink-soft); }}
  .source-link {{
    display: inline-block;
    margin-top: 12px;
    font-size: 11px;
    color: var(--accent);
    text-decoration: none;
  }}
  .source-link:hover {{ text-decoration: underline; }}
  h3.log-title {{
    font-family: 'Space Grotesk', sans-serif;
    font-size: 14px;
    font-weight: 600;
    margin: 28px 0 10px;
    color: var(--ink-soft);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: 10px;
    overflow: hidden;
  }}
  th, td {{
    text-align: left;
    padding: 8px 10px;
    border-top: 1px solid var(--line);
  }}
  th {{
    font-family: 'IBM Plex Mono', monospace;
    font-weight: 500;
    color: var(--ink-soft);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-top: none;
  }}
  td.num {{
    font-family: 'IBM Plex Mono', monospace;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }}
  tr.log-error td {{ color: var(--down); font-size: 11px; }}
  @media (prefers-reduced-motion: no-preference) {{
    .card {{ animation: fade-in 0.4s ease-out; }}
  }}
  @keyframes fade-in {{ from {{ opacity: 0; transform: translateY(4px); }} to {{ opacity: 1; }} }}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <h1>Monitor ETF</h1>
    <span class="updated">odświeżono {generated_at}</span>
  </header>

  {cards_html}

  <h3 class="log-title">Historia odczytów</h3>
  <table>
    <thead><tr><th>Data</th><th>Fundusz</th><th>Wskaźnik</th><th style="text-align:right">Wartość</th></tr></thead>
    <tbody>
      {log_rows if log_rows else '<tr><td colspan="4">Brak jeszcze żadnych odczytów.</td></tr>'}
    </tbody>
  </table>
</div>
</body>
</html>"""
    return html


# ============================================================
# POWIADOMIENIE (ntfy.sh)
# ============================================================

def send_notification(title, message):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Priority": "default"},
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        print("Nie udało się wysłać powiadomienia ntfy (to nie przerywa reszty).")


# ============================================================
# GŁÓWNA LOGIKA
# ============================================================

def main():
    history = load_history()
    run_record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "funds": {},
    }

    notification_lines = []

    for fund in FUNDS:
        prev = last_reading_for(history, fund["id"])
        data = fetch_fund_data(fund)
        run_record["funds"][fund["id"]] = data

        if data.get("error"):
            notification_lines.append(f"⚠ {fund['name']}: {data['error'][:150]}")
            continue

        changes = []
        for metric in METRICS:
            new_val = data.get(metric)
            old_val = prev.get(metric) if prev else None
            if new_val is None:
                continue
            change = pct_change(old_val, new_val)
            if old_val is not None and new_val != old_val:
                decimals = 4 if metric == "nav" else 0
                changes.append(
                    f"{METRIC_LABELS[metric]}: {fmt_num(old_val, decimals)} → "
                    f"{fmt_num(new_val, decimals)} ({fmt_pct(change)})"
                )
        if changes:
            notification_lines.append(f"{fund['name']}:\n" + "\n".join(changes))
        elif prev is None:
            # to pierwszy udany odczyt tego funduszu — brak "wczoraj" do porównania,
            # ale wysyłamy potwierdzenie, żeby było widać, że bot i powiadomienia działają
            decimals_map = {"nav": 4, "net_assets": 0, "shares_outstanding": 0}
            baseline = ", ".join(
                f"{METRIC_LABELS[m]}: {fmt_num(data.get(m), decimals_map[m])}"
                for m in METRICS if data.get(m) is not None
            )
            notification_lines.append(f"{fund['name']} — pierwszy odczyt (punkt odniesienia):\n{baseline}")

    history["runs"].append(run_record)
    # trzymaj max ~2 lata odczytów 2x dziennie, żeby plik nie rósł w nieskończoność
    history["runs"] = history["runs"][-1500:]
    save_history(history)

    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
        f.write(build_dashboard(history))

    if notification_lines:
        send_notification("Zmiana w monitorowanych ETF-ach", "\n\n".join(notification_lines))
        print("Wysłano powiadomienie:\n" + "\n\n".join(notification_lines))
    else:
        print("Brak zmian od ostatniego sprawdzenia (albo to pierwszy odczyt).")


if __name__ == "__main__":
    main()
    sys.exit(0)
