#!/usr/bin/env python3
"""
Print total trade volume for each fish ticker over the past 7 days.
Uses site_dict cities from fish_trade, fetches tickers via fish_market_ticker,
then sums volume from GET /markets/trades for each ticker.
Run: python scripts/tickers_volume.py
     python scripts/tickers_volume.py --days 3
"""
import argparse
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from clients import KalshiHttpClient, Environment
from fish_market_ticker import FISH_MARKET_TICKER

# Same site_dict as fish_trade.py (city codes only - we use keys)
SITE_DICT = {
    "PHIL": [],
    "CHI": [],
    "NYC": [],
    "AUS": [],
    "LAX": [],
    "DEN": [],
    "TOKC": [],
    "TMIN": [],
    "TATL": [],
    "TNOLA": [],
    "TPHX": [],
    "TSFO": [],
    "TSEA": [],
    "THOU": [],
    "TBOS": [],
    "TMSP": [],
}


def get_all_tickers(client, market_ticker, cities: dict, days: int = 7) -> set[str]:
    """Get all fish tickers (low + high) for each city for the past N days."""
    tickers = set()
    today = datetime.now().date()
    for city in cities:
        for d in range(days):
            date_obj = today - timedelta(days=d)
            date_str = date_obj.strftime("%Y-%m-%d")
            low_high = market_ticker.get_tickers_for_date(client, city, date_str, weather_range=None)
            tickers.update(low_high)
    return tickers


def get_ticker_volume(client, ticker: str, min_ts: int, max_ts: int) -> int:
    """Sum trade count for a ticker over the time range. Paginates through all trades."""
    total = 0
    cursor = None
    while True:
        try:
            resp = client.get_trades(
                ticker=ticker, min_ts=min_ts, max_ts=max_ts,
                cursor=cursor, limit=1000
            )
        except Exception:
            return total
        trades = resp.get("trades") or []
        for t in trades:
            cnt = t.get("count")
            if cnt is not None:
                total += int(cnt)
            elif t.get("count_fp") is not None:
                try:
                    total += int(float(t["count_fp"]))
                except (ValueError, TypeError):
                    pass
        cursor = resp.get("cursor")
        if not cursor or not trades:
            break
    return total


def main():
    parser = argparse.ArgumentParser(description="Print volume per fish ticker (past N days)")
    parser.add_argument("--days", type=int, default=7, help="Number of days to look back (default: 7)")
    args = parser.parse_args()

    load_dotenv()
    KEYID = os.getenv("PROD_KEYID")
    KEYFILE = os.getenv("PROD_KEYFILE")
    if not KEYID or not KEYFILE:
        print("Set PROD_KEYID and PROD_KEYFILE in .env")
        return

    with open(KEYFILE, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    client = KalshiHttpClient(key_id=KEYID, private_key=private_key, environment=Environment.PROD)
    market_ticker = FISH_MARKET_TICKER()

    days = args.days
    now = datetime.now()
    start = now - timedelta(days=days)
    min_ts = int(start.timestamp())
    max_ts = int(now.timestamp())

    print(f"Fetching tickers for all cities (past {days} days)...")
    tickers = get_all_tickers(client, market_ticker, SITE_DICT, days=days)
    print(f"  Found {len(tickers)} tickers\n")

    print(f"Fetching volume per ticker (past {days} days)...")
    volumes = {}
    for i, ticker in enumerate(sorted(tickers)):
        vol = get_ticker_volume(client, ticker, min_ts, max_ts)
        volumes[ticker] = vol
        if (i + 1) % 20 == 0:
            print(f"  ... {i + 1}/{len(tickers)}")

    # Group by city and series (e.g. KXHIGHTSEA-26MAR15)
    def ticker_to_series(t):
        """KXHIGHTSEA-26MAR15-B47.5 -> KXHIGHTSEA-26MAR15"""
        parts = t.split("-")
        return "-".join(parts[:2]) if len(parts) >= 2 else t

    def series_to_city(s):
        """KXHIGHTSEA-26MAR15 -> TSEA, KXLOWTCHI-26MAR15 -> CHI"""
        prefix = s.split("-")[0].upper()
        if prefix.startswith("KXHIGH"):
            return prefix[6:]
        if prefix.startswith("KXLOWT"):
            return prefix[6:]
        return prefix

    def series_to_date(s):
        """KXHIGHTSEA-26MAR15 -> 2026-03-15"""
        parts = s.split("-")
        if len(parts) < 2 or len(parts[1]) < 6:
            return ""
        try:
            dp = parts[1]
            yy, mm_str, dd = int(dp[:2]), dp[2:5], int(dp[5:7])
            months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
            mm = months.index(mm_str) + 1 if mm_str in months else 1
            return f"20{yy:02d}-{mm:02d}-{dd:02d}"
        except (ValueError, IndexError):
            return ""

    grouped = {}
    for ticker, vol in volumes.items():
        series = ticker_to_series(ticker)
        grouped[series] = grouped.get(series, 0) + vol

    # Sort by city, then date
    rows = []
    for series, vol in grouped.items():
        city = series_to_city(series)
        date_str = series_to_date(series)
        rows.append((city, series, date_str, vol))
    rows.sort(key=lambda r: (r[0], r[2]))

    lines = []
    lines.append("=" * 70)
    lines.append(f"VOLUME BY CITY & SERIES (past {days} days) — grouped by city, date")
    lines.append("=" * 70)
    for city, series, date_str, vol in rows:
        lines.append(f"  {city}  {series}  {date_str}  {vol:,}")
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"Total series: {len(rows)}")
    lines.append(f"Total volume (all): {sum(v for _, _, _, v in rows):,}")

    out_dir = Path(__file__).resolve().parent.parent / "logs"
    out_dir.mkdir(exist_ok=True)
    out_txt = out_dir / "tickers_volume.txt"
    out_csv = out_dir / "tickers_volume.csv"
    with open(out_txt, "w") as f:
        f.write("\n".join(lines))
    with open(out_csv, "w") as f:
        f.write("city,series,date,volume\n")
        for city, series, date_str, vol in rows:
            f.write(f"{city},{series},{date_str},{vol}\n")
    print("\n".join(lines))
    print(f"\nSaved to {out_txt} and {out_csv}")


if __name__ == "__main__":
    main()
