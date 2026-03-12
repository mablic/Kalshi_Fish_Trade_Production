#!/usr/bin/env python3
"""
For each city in fish_parse_weather's site_dict and each of the past 7 days:
  - Get weather prediction: predicted_high and predicted_low for that city on that date.
  - We would buy the 3 tickers around the prediction (e.g. if predicted high = 70, buy 69, 70, 71).
  - From Kalshi, get the actual settled high/low for that city on that date.
  - Count correct: actual value is within our 3-ticker range (pred-1, pred, pred+1).
Output: correct percentage per city (HIGH and LOW) and overall, for all 7 days.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from clients import KalshiHttpClient, Environment
from fish_parse_weather import FISH_PARSE_WEATHER
from fish_market_ticker import FISH_MARKET_TICKER


# Same site_dict as fish_parse_weather.py __main__ (all cities with 3 URLs for obhistory)
def get_site_dict():
    return {
        "PHIL": [
            "https://forecast.weather.gov/product.php?site=PHI&product=CLI&issuedby=PHL",
            "https://forecast.weather.gov/MapClick.php?lat=39.8764&lon=-75.2422&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KPHL",
        ],
        "CHI": [
            "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW",
            "https://forecast.weather.gov/MapClick.php?lat=41.7885&lon=-87.7417&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMDW",
        ],
        "NYC": [
            "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            "https://forecast.weather.gov/MapClick.php?lat=40.6849&lon=-73.8444&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KNYC",
        ],
        "AUS": [
            "https://forecast.weather.gov/product.php?site=EWX&product=CLI&issuedby=AUS",
            "https://forecast.weather.gov/MapClick.php?lat=30.1945&lon=-97.6699&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KAUS",
        ],
        "LAX": [
            "https://forecast.weather.gov/product.php?site=LOX&product=CLI&issuedby=LAX",
            "https://forecast.weather.gov/MapClick.php?lat=33.9435&lon=-118.4086&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KLAX",
        ],
        "MIA": [
            "https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MIA",
            "https://forecast.weather.gov/MapClick.php?lat=25.795&lon=-80.2798&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMIA",
        ],
        "DEN": [
            "https://forecast.weather.gov/product.php?site=BOU&product=CLI&issuedby=DEN",
            "https://forecast.weather.gov/MapClick.php?lat=39.8482&lon=-104.6738&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KDEN",
        ],
        "OKC": [
            "https://forecast.weather.gov/product.php?site=OUN&product=CLI&issuedby=OKC",
            "https://forecast.weather.gov/MapClick.php?lat=35.3931&lon=-97.6009&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KOKC",
        ],
        "MIN": [
            "https://forecast.weather.gov/product.php?site=FSD&product=CLI&issuedby=MSP",
            "https://forecast.weather.gov/MapClick.php?lat=44.882&lon=-93.2218&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMSP",
        ],
        "TATL": [
            "https://forecast.weather.gov/product.php?site=FFC&product=CLI&issuedby=ATL",
            "https://forecast.weather.gov/MapClick.php?lat=33.7485&lon=-84.3915&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KATL",
        ],
        "TNOLA": [
            "https://forecast.weather.gov/product.php?site=LIX&product=CLI&issuedby=MSY",
            "https://forecast.weather.gov/MapClick.php?lat=29.9933&lon=-90.259&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMSY",
        ],
        "TPHX": [
            "https://forecast.weather.gov/product.php?site=TUC&product=CLI&issuedby=PHX",
            "https://forecast.weather.gov/MapClick.php?lat=33.4355&lon=-111.998&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KPHX",
        ],
        "TSATX": [
            "https://forecast.weather.gov/product.php?site=CRP&product=CLI&issuedby=SAT",
            "https://forecast.weather.gov/MapClick.php?lat=29.5338&lon=-98.47&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KSAT",
        ],
        "TDAL": [
            "https://forecast.weather.gov/product.php?site=FWD&product=CLI&issuedby=DFW",
            "https://forecast.weather.gov/MapClick.php?lat=32.8975&lon=-97.0444&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KDFW",
        ],
        "TSFO": [
            "https://forecast.weather.gov/product.php?site=MTR&product=CLI&issuedby=SFO",
            "https://forecast.weather.gov/MapClick.php?lat=37.7801&lon=-122.4202&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KSFO",
        ],
    }


def date_to_ticker_fmt(date_str: str) -> str:
    """'2026-03-09' -> '26MAR09'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        return f"{dt.strftime('%y')}{month_names[dt.month - 1]}{dt.strftime('%d')}"
    except ValueError:
        return ""


def extract_temp_from_ticker(ticker: str) -> float | None:
    """KXLOWTCHI-26MAR09-T27.5 -> 27.5."""
    return FISH_MARKET_TICKER()._extract_temp_from_ticker(ticker)


def in_buy_range(actual: float | None, predicted: int) -> bool:
    """We buy (pred-1, pred, pred+1). Correct if Kalshi actual is in that range."""
    if actual is None:
        return False
    t = int(round(actual))
    return t in (predicted - 1, predicted, predicted + 1)


def get_kalshi_actual_high_low(client: KalshiHttpClient, city: str, date_str: str) -> tuple[float | None, float | None]:
    """
    For this (city, date), get from Kalshi closed markets:
      actual_high = max temp among HIGH markets that settled YES.
      actual_low  = max temp among LOW markets that settled YES (for "at or below T", YES means actual low <= T).
    Returns (actual_high, actual_low).
    """
    date_fmt = date_to_ticker_fmt(date_str)
    if not date_fmt:
        return None, None
    kalshi_city = city.upper()
    actual_high = None
    actual_low = None
    for series, is_high in [(f"KXHIGH{kalshi_city}-{date_fmt}", True), (f"KXLOWT{kalshi_city}-{date_fmt}", False)]:
        try:
            resp = client.get_markets_by_series(series_ticker=series, status="closed", limit=200)
        except Exception:
            continue
        markets = resp.get("markets") or []
        for m in markets:
            result = (m.get("result") or "").strip().lower()
            if result != "yes":
                continue
            temp = extract_temp_from_ticker(m.get("ticker") or "")
            if temp is None:
                continue
            if is_high:
                actual_high = max(actual_high, temp) if actual_high is not None else temp
            else:
                actual_low = max(actual_low, temp) if actual_low is not None else temp
    return actual_high, actual_low


def main():
    parser = argparse.ArgumentParser(description="Per-city, per-day: predict high/low, buy 3 tickers, compare to Kalshi; report correct %%")
    parser.add_argument("--days", type=int, default=7, help="Number of past days (default 7)")
    parser.add_argument("--no-api", action="store_true", help="Skip Kalshi API (weather-only; no correct %%)")
    parser.add_argument("--demo", action="store_true", help="Use Kalshi demo environment")
    args = parser.parse_args()

    n_days = max(1, min(args.days, 31))
    ref_date = datetime.now()
    SITE_DICT = get_site_dict()

    # 1) Historical weather for all cities, past n_days
    print("Fetching historical weather (NWS obhistory) for past %d days, all cities..." % n_days)
    fish_weather = FISH_PARSE_WEATHER(SITE_DICT)
    historical = fish_weather.get_historical_weather_past_n_days(n_days=n_days, reference_date=ref_date)

    # Collect all (city, date) pairs that have weather
    city_dates = []
    for city, by_date in historical.items():
        for date_str in by_date.keys():
            city_dates.append((city, date_str))
    all_dates = sorted(set(d for _, d in city_dates), reverse=True)[:n_days]
    cities = sorted(historical.keys())

    if not city_dates:
        print("No historical weather data (obhistory may have failed).")
        sys.exit(1)

    # Date range used (NWS obhistory usually has ~3-4 days)
    date_list = sorted(set(d for _, d in city_dates))
    print("Date range: %s to %s (%d dates: %s)" % (
        date_list[0] if date_list else "?",
        date_list[-1] if date_list else "?",
        len(date_list),
        ", ".join(date_list),
    ))

    # 2) Kalshi client (optional)
    client = None
    if not args.no_api:
        load_dotenv()
        key_id = os.getenv("PROD_KEYID")
        key_file = os.getenv("PROD_KEYFILE")
        if args.demo:
            key_id = os.getenv("DEMO_KEYID")
            key_file = os.getenv("DEMO_KEYFILE")
        if key_id and key_file:
            try:
                with open(key_file, "rb") as f:
                    private_key = serialization.load_pem_private_key(f.read(), password=None)
                client = KalshiHttpClient(key_id=key_id, private_key=private_key,
                                         environment=Environment.DEMO if args.demo else Environment.PROD)
            except Exception as e:
                print("Kalshi client init failed: %s" % e)
        else:
            print("Set PROD_KEYID and PROD_KEYFILE (or DEMO_*) in .env for Kalshi API")

    # 3) For each (city, date): prediction = (pred_high, pred_low), buy set = (p-1, p, p+1); get Kalshi actual; correct if actual in set
    results = []  # list of { city, date, pred_high, pred_low, actual_high, actual_low, high_ok, low_ok }
    per_city_high = defaultdict(lambda: {"correct": 0, "total": 0})
    per_city_low = defaultdict(lambda: {"correct": 0, "total": 0})

    for city in cities:
        by_date = historical.get(city, {})
        for date_str in sorted(by_date.keys(), reverse=True)[:n_days]:
            vals = by_date.get(date_str)
            if not vals or len(vals) < 2:
                continue
            pred_low, pred_high = int(round(vals[0])), int(round(vals[1]))
            actual_high, actual_low = None, None
            if client is not None:
                actual_high, actual_low = get_kalshi_actual_high_low(client, city, date_str)
            high_ok = in_buy_range(actual_high, pred_high) if actual_high is not None else None
            low_ok = in_buy_range(actual_low, pred_low) if actual_low is not None else None

            results.append({
                "city": city,
                "date": date_str,
                "pred_high": pred_high,
                "pred_low": pred_low,
                "actual_high": actual_high,
                "actual_low": actual_low,
                "high_ok": high_ok,
                "low_ok": low_ok,
            })
            per_city_high[city]["total"] += 1
            per_city_low[city]["total"] += 1
            if high_ok is True:
                per_city_high[city]["correct"] += 1
            if low_ok is True:
                per_city_low[city]["correct"] += 1

    # 4) Output: correct percentage per city (HIGH and LOW) and overall
    scripts_dir = Path(__file__).resolve().parent
    out_json = scripts_dir / "weather_winner_results.json"
    out_csv = scripts_dir / "weather_winner_results.csv"

    total_high_correct = sum(p["correct"] for p in per_city_high.values())
    total_high_total = sum(p["total"] for p in per_city_high.values())
    total_low_correct = sum(p["correct"] for p in per_city_low.values())
    total_low_total = sum(p["total"] for p in per_city_low.values())
    overall_correct = total_high_correct + total_low_correct
    overall_total = total_high_total + total_low_total

    summary = {
        "generated_at": datetime.now().isoformat(),
        "n_days": n_days,
        "per_city": {},
        "overall": {
            "high": {"correct": total_high_correct, "total": total_high_total,
                     "pct": round(100.0 * total_high_correct / total_high_total, 1) if total_high_total else 0},
            "low": {"correct": total_low_correct, "total": total_low_total,
                    "pct": round(100.0 * total_low_correct / total_low_total, 1) if total_low_total else 0},
            "combined_pct": round(100.0 * overall_correct / overall_total, 1) if overall_total else 0,
        },
        "daily_results": results,
    }
    for city in cities:
        h = per_city_high[city]
        l = per_city_low[city]
        summary["per_city"][city] = {
            "high": {"correct": h["correct"], "total": h["total"],
                     "pct": round(100.0 * h["correct"] / h["total"], 1) if h["total"] else 0},
            "low": {"correct": l["correct"], "total": l["total"],
                    "pct": round(100.0 * l["correct"] / l["total"], 1) if l["total"] else 0},
        }
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    print("Wrote %s" % out_json)

    # CSV: one row per (city, date)
    with open(out_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "city", "date", "pred_high", "pred_low", "actual_high", "actual_low", "high_ok", "low_ok"
        ])
        writer.writeheader()
        for r in results:
            row = {k: r[k] for k in writer.fieldnames}
            row["high_ok"] = r["high_ok"] if r["high_ok"] is not None else ""
            row["low_ok"] = r["low_ok"] if r["low_ok"] is not None else ""
            writer.writerow(row)
    print("Wrote %s" % out_csv)

    # How many had Kalshi data?
    with_actual_high = sum(1 for r in results if r.get("actual_high") is not None)
    with_actual_low = sum(1 for r in results if r.get("actual_low") is not None)
    if with_actual_high == 0 and with_actual_low == 0:
        print("\n*** No Kalshi settlement data found for this date range (actual_high/actual_low are all null). ***")
        print("    So 0%% correct means no comparison was possible — not that your predictions were wrong.")
        print("    Check: (1) Kalshi series ticker format for your cities, (2) markets are closed/settled for these dates.\n")

    # Print correct percentage per city and overall
    print("--- Correct percentage (prediction in 3-ticker range vs Kalshi result) ---")
    print("Per city (HIGH = predicted high in {pred-1, pred, pred+1}; LOW = same for low):\n")
    for city in cities:
        h = per_city_high[city]
        l = per_city_low[city]
        high_pct = (100.0 * h["correct"] / h["total"]) if h["total"] else 0
        low_pct = (100.0 * l["correct"] / l["total"]) if l["total"] else 0
        print("  %s  HIGH %d/%d = %.1f%%   LOW %d/%d = %.1f%%" % (
            city, h["correct"], h["total"], high_pct, l["correct"], l["total"], low_pct))
    print("\n  OVERALL  HIGH %d/%d = %.1f%%   LOW %d/%d = %.1f%%   COMBINED %d/%d = %.1f%%" % (
        total_high_correct, total_high_total,
        (100.0 * total_high_correct / total_high_total) if total_high_total else 0,
        total_low_correct, total_low_total,
        (100.0 * total_low_correct / total_low_total) if total_low_total else 0,
        overall_correct, overall_total,
        (100.0 * overall_correct / overall_total) if overall_total else 0,
    ))
    if args.no_api or client is None:
        print("\n(No Kalshi API: actual_high/actual_low and correct %% are empty; run without --no-api for comparison.)")


if __name__ == "__main__":
    main()
