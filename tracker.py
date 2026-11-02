"""
Daily S&P 500 tracker.

Fetches S&P 500 constituents (with GICS sector / sub-industry), pulls the latest
two daily closes for every stock from Yahoo Finance, computes stock, sub-industry
and sector moves (cap-weighted) plus each group's contribution to the index move,
runs sanity checks, and writes a new dated tab to a Google Sheet.

Environment variables:
  GOOGLE_CREDENTIALS  - service-account JSON (the whole file contents)
  SHEET_ID            - the Google Sheet ID (from its URL)
  MOVE_THRESHOLD      - optional, default 3 (percent) for "news needed" flags
  DRY_RUN             - optional, "1" = skip Google, just write output.csv
"""

import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

THRESHOLD = float(os.environ.get("MOVE_THRESHOLD", "3"))
SHARES_CACHE = Path("shares_cache.json")
SHARES_MAX_AGE_DAYS = 7
CONSTITUENTS_URLS = [
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
]


def log(msg):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- constituents
def get_constituents() -> pd.DataFrame:
    for url in CONSTITUENTS_URLS:
        try:
            if url.endswith(".csv"):
                df = pd.read_csv(url)
            else:
                import requests
                from io import StringIO
                html = requests.get(url, headers={"User-Agent": "sp500-tracker"}, timeout=30).text
                df = pd.read_html(StringIO(html))[0]
            df = df.rename(columns={"Security": "Company"})
            df = df[["Symbol", "Company", "GICS Sector", "GICS Sub-Industry"]].copy()
            df["YahooSymbol"] = df["Symbol"].str.replace(".", "-", regex=False)
            if len(df) >= 490:
                log(f"Constituents: {len(df)} from {url}")
                return df
        except Exception as e:  # try next source
            log(f"Constituent source failed ({url}): {e}")
    sys.exit("ERROR: could not load S&P 500 constituents")


# ---------------------------------------------------------------------- prices
def with_retries(fn, what, tries=5):
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            wait = 30 * (i + 1)
            log(f"{what} failed ({e}); retry {i + 1}/{tries} in {wait}s")
            time.sleep(wait)
    sys.exit(f"ERROR: {what} failed after {tries} tries (Yahoo may be rate-limiting)")


def get_closes(symbols) -> pd.DataFrame:
    import yfinance as yf

    def dl():
        data = yf.download(symbols, period="7d", interval="1d", auto_adjust=False,
                           group_by="column", threads=True, progress=False)
        closes = data["Close"]
        if closes.dropna(how="all").shape[0] < 2:
            raise RuntimeError("empty price data")
        return closes

    return with_retries(dl, "Price download")


def get_shares(symbols) -> dict:
    """Shares outstanding change slowly, so cache them and refresh weekly."""
    import yfinance as yf

    cache = {}
    if SHARES_CACHE.exists():
        cache = json.loads(SHARES_CACHE.read_text())
    fetched = cache.get("_fetched", "2000-01-01")
    age = (date.today() - date.fromisoformat(fetched)).days
    missing = [s for s in symbols if not cache.get(s)]

    to_fetch = symbols if age > SHARES_MAX_AGE_DAYS else missing
    if to_fetch:
        log(f"Fetching shares outstanding for {len(to_fetch)} stocks (slow, ~weekly)")
        for i, s in enumerate(to_fetch):
            for attempt in range(3):
                try:
                    sh = yf.Ticker(s).fast_info.get("shares")
                    if sh:
                        cache[s] = float(sh)
                    break
                except Exception:
                    time.sleep(5 * (attempt + 1))
            time.sleep(0.4)  # be gentle with Yahoo
            if i % 100 == 99:
                log(f"  ...{i + 1}/{len(to_fetch)}")
        if age > SHARES_MAX_AGE_DAYS:
            cache["_fetched"] = date.today().isoformat()
        SHARES_CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    return cache


# ------------------------------------------------------------------- compute
def build_table(cons: pd.DataFrame, closes: pd.DataFrame, shares: dict):
    closes = closes.dropna(how="all")
    trade_date = closes.index[-1].date()
    last, prev = closes.iloc[-1], closes.iloc[-2]

    df = cons.copy()
    df["Close"] = df["YahooSymbol"].map(last)
    df["Prev Close"] = df["YahooSymbol"].map(prev)
    df["Shares"] = df["YahooSymbol"].map(lambda s: shares.get(s))
    df["$ Change"] = df["Close"] - df["Prev Close"]
    df["% Change"] = df["$ Change"] / df["Prev Close"] * 100
    df["Prev Mkt Cap"] = df["Prev Close"] * df["Shares"]
    df["Mkt Cap Change"] = df["$ Change"] * df["Shares"]

    ok = df.dropna(subset=["Close", "Prev Close", "Shares"])
    total_prev = ok["Prev Mkt Cap"].sum()
    df["Contribution (pts)"] = df["Mkt Cap Change"] / total_prev * 100

    def agg(g):
        g = g.dropna(subset=["Prev Mkt Cap"])
        return pd.Series({
            "Prev Mkt Cap": g["Prev Mkt Cap"].sum(),
            "Mkt Cap Change": g["Mkt Cap Change"].sum(),
            "% Change": g["Mkt Cap Change"].sum() / g["Prev Mkt Cap"].sum() * 100,
            "Contribution (pts)": g["Mkt Cap Change"].sum() / total_prev * 100,
            "Count": len(g),
        })

    sectors = ok.groupby("GICS Sector").apply(agg, include_groups=False)
    subs = ok.groupby(["GICS Sector", "GICS Sub-Industry"]).apply(agg, include_groups=False)
    index_pct = ok["Mkt Cap Change"].sum() / total_prev * 100
    return trade_date, df, sectors, subs, index_pct


def sanity_checks(df, index_pct, closes) -> list:
    issues = []
    priced = df["Close"].notna().sum()
    if priced < len(df) - 5:
        missing = df.loc[df["Close"].isna(), "Symbol"].tolist()
        issues.append(f"Missing prices for {len(df) - priced} stocks: {', '.join(missing[:15])}")
    no_shares = df["Shares"].isna().sum()
    if no_shares > 5:
        issues.append(f"Missing share counts for {no_shares} stocks (excluded from weighting)")
    wild = df.loc[df["% Change"].abs() > 50, "Symbol"].tolist()
    if wild:
        issues.append(f"Moves over 50% (check for splits/bad data): {', '.join(wild)}")
    try:  # compare with SPY's actual move
        import yfinance as yf
        spy = yf.download("SPY", period="7d", interval="1d", auto_adjust=False,
                          progress=False)["Close"].dropna().squeeze()
        spy_pct = (spy.iloc[-1] / spy.iloc[-2] - 1) * 100
        if abs(spy_pct - index_pct) > 0.15:
            issues.append(f"Computed index move {index_pct:+.2f}% differs from SPY {spy_pct:+.2f}%")
    except Exception as e:
        issues.append(f"Could not cross-check against SPY ({e})")
    return issues


# --------------------------------------------------------------------- layout
HEADER = ["Level", "Sector", "Sub-Industry", "Ticker", "Company", "Close", "Prev Close",
          "$ Change", "% Change", "Mkt Cap Change ($B)", "Contribution to S&P (pts)",
          "Why it moved"]


def rows_for_sheet(trade_date, df, sectors, subs, index_pct, issues):
    r2 = lambda x: None if pd.isna(x) else round(float(x), 2)
    r4 = lambda x: None if pd.isna(x) else round(float(x), 4)
    bn = lambda x: None if pd.isna(x) else round(float(x) / 1e9, 2)

    status = "CHECKS PASSED" if not issues else "NEEDS REVIEW: " + " | ".join(issues)
    rows = [
        [f"S&P 500 daily tracker - {trade_date}"],
        [f"Index (cap-weighted, computed): {index_pct:+.2f}%", "", "", "", status],
        [f"'Why it moved' filled for stocks moving >= {THRESHOLD:g}%. Cells marked "
         f"NEWS NEEDED are awaiting research."],
        [],
        HEADER,
    ]
    for sector, srow in sectors.sort_values("% Change", ascending=False).iterrows():
        rows.append(["SECTOR", sector, "", "", f"{int(srow['Count'])} stocks", None, None,
                     None, r2(srow["% Change"]), bn(srow["Mkt Cap Change"]),
                     r4(srow["Contribution (pts)"]), ""])
        sector_subs = subs.loc[sector].sort_values("% Change", ascending=False)
        for sub, brow in sector_subs.iterrows():
            rows.append(["SUB-INDUSTRY", sector, sub, "", f"{int(brow['Count'])} stocks",
                         None, None, None, r2(brow["% Change"]), bn(brow["Mkt Cap Change"]),
                         r4(brow["Contribution (pts)"]), ""])
            stocks = df[(df["GICS Sector"] == sector) & (df["GICS Sub-Industry"] == sub)]
            for _, s in stocks.sort_values("% Change", ascending=False).iterrows():
                big = pd.notna(s["% Change"]) and abs(s["% Change"]) >= THRESHOLD
                rows.append(["STOCK", sector, sub, s["Symbol"], s["Company"], r2(s["Close"]),
                             r2(s["Prev Close"]), r2(s["$ Change"]), r2(s["% Change"]),
                             bn(s["Mkt Cap Change"]), r4(s["Contribution (pts)"]),
                             "NEWS NEEDED" if big else ""])
    return rows


# --------------------------------------------------------------- google sheet
def write_sheet(tab_name, rows):
    import gspread
    creds = json.loads(os.environ["GOOGLE_CREDENTIALS"])
    gc = gspread.service_account_from_dict(creds)
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    try:
        ws = sh.worksheet(tab_name)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab_name, rows=len(rows) + 20, cols=len(HEADER), index=0)
    clean = [[("" if v is None else v) for v in r] for r in rows]
    ws.update(values=clean, range_name="A1", value_input_option="USER_ENTERED")

    # light formatting: bold header/sector rows, freeze header
    hdr = 5
    ws.freeze(rows=hdr)
    reqs = [{"repeatCell": {
        "range": {"sheetId": ws.id, "startRowIndex": hdr - 1, "endRowIndex": hdr},
        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
        "fields": "userEnteredFormat.textFormat.bold"}}]
    for i, r in enumerate(rows):
        if r and r[0] in ("SECTOR", "SUB-INDUSTRY"):
            color = {"red": 0.85, "green": 0.9, "blue": 1} if r[0] == "SECTOR" \
                else {"red": 0.95, "green": 0.95, "blue": 0.95}
            reqs.append({"repeatCell": {
                "range": {"sheetId": ws.id, "startRowIndex": i, "endRowIndex": i + 1},
                "cell": {"userEnteredFormat": {"backgroundColor": color,
                                               "textFormat": {"bold": r[0] == "SECTOR"}}},
                "fields": "userEnteredFormat(backgroundColor,textFormat.bold)"}})
    sh.batch_update({"requests": reqs})
    log(f"Wrote {len(rows)} rows to tab '{tab_name}'")


# ------------------------------------------------------------------------ main
def main():
    cons = get_constituents()
    symbols = cons["YahooSymbol"].tolist()
    closes = get_closes(symbols)

    trade_date = closes.dropna(how="all").index[-1].date()
    if os.environ.get("REQUIRE_TODAY") == "1" and trade_date != date.today():
        log(f"Latest trading day is {trade_date}, not today - market holiday? Exiting.")
        return

    shares = get_shares(symbols)
    trade_date, df, sectors, subs, index_pct = build_table(cons, closes, shares)
    issues = sanity_checks(df, index_pct, closes)
    for i in issues:
        log(f"CHECK: {i}")
    rows = rows_for_sheet(trade_date, df, sectors, subs, index_pct, issues)

    pd.DataFrame(rows[5:], columns=HEADER).to_csv("output.csv", index=False)
    if os.environ.get("DRY_RUN") == "1":
        log("DRY_RUN: wrote output.csv only")
        return
    write_sheet(str(trade_date), rows)


if __name__ == "__main__":
    main()
