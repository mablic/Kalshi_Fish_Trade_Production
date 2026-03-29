#!/usr/bin/env python3
"""
One flat table: columns `city`, `date`, `low`, `high`, `src`.

- Fills dates from obhistory (last N days) per city; **src=obs**.
- Adds CLI min/max for the latest report date when that city/date has no obs row; **src=cli**.
- Same calendar date: **obs** is kept (CLI row skipped).

Run:
  python scripts/ticker_analyze.py
  python scripts/ticker_analyze.py --days 7
  python scripts/ticker_analyze.py --csv > logs/temps.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fish_parse_weather import FISH_PARSE_WEATHER

# Mirror fish_trade `site_dict` (first URL = NWS CLI).
FISH_SITE_DICT = {
    "PHIL": [
        "https://forecast.weather.gov/product.php?site=PHI&product=CLI&issuedby=PHL",
        "https://forecast.weather.gov/MapClick.php?lat=39.8764&lon=-75.2422&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KPHL",
        5,
    ],
    "CHI": [
        "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW",
        "https://forecast.weather.gov/MapClick.php?lat=41.7885&lon=-87.7417&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KMDW",
        5,
    ],
    "NYC": [
        "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
        "https://forecast.weather.gov/MapClick.php?lat=40.6849&lon=-73.8444&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KNYC",
        10,
    ],
    "AUS": [
        "https://forecast.weather.gov/product.php?site=EWX&product=CLI&issuedby=AUS",
        "https://forecast.weather.gov/MapClick.php?lat=30.1945&lon=-97.6699&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KAUS",
        50,
    ],
    "LAX": [
        "https://forecast.weather.gov/product.php?site=LOX&product=CLI&issuedby=LAX",
        "https://forecast.weather.gov/MapClick.php?lat=33.9435&lon=-118.4086&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KLAX",
        150,
    ],
    "MIA": [
        "https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MIA",
        "https://forecast.weather.gov/MapClick.php?lat=25.795&lon=-80.2798&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KMIA",
        150,
    ],
    "DEN": [
        "https://forecast.weather.gov/product.php?site=BOU&product=CLI&issuedby=DEN",
        "https://forecast.weather.gov/MapClick.php?lat=39.8482&lon=-104.6738&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KDEN",
        10,
    ],
    "TOKC": [
        "https://forecast.weather.gov/product.php?site=OUN&product=CLI&issuedby=OKC",
        "https://forecast.weather.gov/MapClick.php?lat=35.3931&lon=-97.6009&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KOKC",
        10,
    ],
    "TATL": [
        "https://forecast.weather.gov/product.php?site=FFC&product=CLI&issuedby=ATL",
        "https://forecast.weather.gov/MapClick.php?lat=33.7485&lon=-84.3915&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KATL",
        5,
    ],
    "TNOLA": [
        "https://forecast.weather.gov/product.php?site=LIX&product=CLI&issuedby=MSY",
        "https://forecast.weather.gov/MapClick.php?lat=29.9933&lon=-90.259&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KMSY",
        10,
    ],
    "TPHX": [
        "https://forecast.weather.gov/product.php?site=TUC&product=CLI&issuedby=PHX",
        "https://forecast.weather.gov/MapClick.php?lat=33.4355&lon=-111.998&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KPHX",
        100,
    ],
    "TSATX": [
        "https://forecast.weather.gov/product.php?site=CRP&product=CLI&issuedby=SAT",
        "https://forecast.weather.gov/MapClick.php?lat=29.5338&lon=-98.47&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KSAT",
        10,
    ],
    "TDAL": [
        "https://forecast.weather.gov/product.php?site=FWD&product=CLI&issuedby=DFW",
        "https://forecast.weather.gov/MapClick.php?lat=32.8975&lon=-97.0444&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KDFW",
        10,
    ],
    "TSFO": [
        "https://forecast.weather.gov/product.php?site=MTR&product=CLI&issuedby=SFO",
        "https://forecast.weather.gov/MapClick.php?lat=37.7801&lon=-122.4202&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KSFO",
        150,
    ],
    "TSEA": [
        "https://forecast.weather.gov/product.php?site=SEW&product=CLI&issuedby=SEA",
        "https://forecast.weather.gov/MapClick.php?lat=47.4479&lon=-122.3088&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KSEA",
        10,
    ],
    "THOU": [
        "https://forecast.weather.gov/product.php?site=OUN&product=CLI&issuedby=HOU",
        "https://forecast.weather.gov/MapClick.php?lat=29.7608&lon=-95.3695&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KHOU",
        50,
    ],
    "TBOS": [
        "https://forecast.weather.gov/product.php?site=PVD&product=CLI&issuedby=BOS",
        "https://forecast.weather.gov/MapClick.php?lat=42.359&lon=-71.0586&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KBOS",
        50,
    ],
    "TLV": [
        "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=LAS",
        "https://forecast.weather.gov/MapClick.php?lat=36.11478&lon=-115.1728&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KLAS",
        10,
    ],
    "TMSP": [
        "https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MSP",
        "https://forecast.weather.gov/MapClick.php?lat=44.882&lon=-93.2218&FcstType=digitalDWML",
        "https://www.weather.gov/wrh/timeseries?site=KMSP",
        2,
    ],
}


def build_rows(
    cities: list[str],
    cli_by_city: dict,
    hist_by_city: dict[str, dict[str, list]],
    n_days: int,
) -> list[tuple[str, str, str, str, str]]:
    """One row per (city, date): low, high, source. Obs wins over CLI when both exist."""
    merged: dict[tuple[str, str], tuple[str, str, str]] = {}
    for city in cities:
        hist = hist_by_city.get(city) or {}
        dates = sorted(hist.keys())[-n_days:] if hist else []
        for d in dates:
            pair = hist.get(d)
            if not pair or len(pair) < 2:
                continue
            lo, hi = pair[0], pair[1]
            if lo is None or hi is None:
                continue
            merged[(city, d)] = (str(int(lo)), str(int(hi)), "obs")
        cli = cli_by_city.get(city) or {}
        for d, pair in cli.items():
            if not pair or len(pair) < 2:
                continue
            lo, hi = pair[0], pair[1]
            if lo is None or hi is None:
                continue
            key = (city, d)
            if key not in merged:
                merged[key] = (str(int(lo)), str(int(hi)), "cli")
    out = [(c, d, merged[(c, d)][0], merged[(c, d)][1], merged[(c, d)][2]) for c, d in sorted(merged.keys(), key=lambda k: (k[1], k[0]))]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="City × date × low × high (one table).")
    parser.add_argument("--days", type=int, default=4, help="Obhistory days (default 4)")
    parser.add_argument("--city", default=None, help="Single city (default: all)")
    parser.add_argument("--csv", action="store_true", help="CSV to stdout")
    args = parser.parse_args()

    pw = FISH_PARSE_WEATHER(FISH_SITE_DICT)
    ref = datetime.now()
    cli_all = pw.get_daily_report_weather()
    hist_all = pw.get_historical_weather_past_n_days(n_days=args.days, reference_date=ref)

    cities = sorted(FISH_SITE_DICT.keys())
    if args.city:
        c = args.city.upper()
        if c not in FISH_SITE_DICT:
            print(f"Unknown city {c}. Known: {', '.join(cities)}", file=sys.stderr)
            sys.exit(1)
        cities = [c]

    rows = build_rows(cities, cli_all, hist_all, args.days)

    if args.csv:
        w = StringIO()
        cw = csv.writer(w)
        cw.writerow(["city", "date", "low", "high", "source"])
        for city, d, lo, hi, src in rows:
            cw.writerow([city, d, lo, hi, src])
        sys.stdout.write(w.getvalue())
        return

    print(f"city   date         low   high   src   (reference {ref.strftime('%Y-%m-%d %H:%M')} local)")
    print("-" * 52)
    for city, d, lo, hi, src in rows:
        print(f"{city:<6} {d}   {lo:>4}   {hi:>4}   {src}")
    print(f"\nrows={len(rows)}  src: obs=station obhistory  cli=NWS CLI (only if no obs for that date)")


if __name__ == "__main__":
    main()
