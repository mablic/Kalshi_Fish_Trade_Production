#!/usr/bin/env python3
"""
Analyze Kalshi trades and PnL.
  Option A: From API (GET /portfolio/fills and /settlements)
  Option B: From Kalshi transactions CSV export (all amounts in CENTS).
Run: python scripts/analyze_pnl.py                    # API, last 7 days
     python scripts/analyze_pnl.py --month MAR        # API, March markets
     python scripts/analyze_pnl.py --csv path/to/Kalshi-Transactions-2026.csv  # CSV (cents)
     python scripts/analyze_pnl.py --csv path/to/file.csv --month MAR
"""
import argparse
import csv
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from clients import KalshiHttpClient, Environment


def extract_city(ticker: str) -> str:
    t = (ticker or "").upper()
    if t.startswith("KXLOWT"):
        city = t[6:].split("-")[0]
        return "NYC" if city == "NY" else city
    if t.startswith("KXHIGH"):
        city = t[6:].split("-")[0]
        return "NYC" if city == "NY" else city
    return ""


def is_fish_ticker(ticker: str) -> bool:
    return (ticker or "").upper().startswith("KXLOWT") or (ticker or "").upper().startswith("KXHIGH")


def extract_market_date_from_ticker(ticker: str) -> str | None:
    """Extract YYYY-MM-DD from ticker e.g. KXLOWTCHI-26MAR07 -> 2026-03-07."""
    t = (ticker or "").upper()
    try:
        parts = t.split("-")
        if len(parts) < 2 or len(parts[1]) < 7:
            return None
        date_part = parts[1]
        yy = int(date_part[:2])
        mm_str = date_part[2:5]
        dd = int(date_part[5:7])
        month_names = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
        if mm_str not in month_names:
            return None
        mm = month_names.index(mm_str) + 1
        return f"20{yy:02d}-{mm:02d}-{dd:02d}"
    except (ValueError, IndexError):
        return None


def ticker_in_month(ticker: str, month_filter: str) -> bool:
    """month_filter: 'MAR' or '2026-03'."""
    d = extract_market_date_from_ticker(ticker)
    if not d:
        return False
    m = (month_filter or "").strip().upper()
    month_names = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    if m in month_names:
        return d[5:7] == f"{month_names.index(m) + 1:02d}"
    # 2026-03
    if len(m) >= 7 and m[4] == "-":
        return d.startswith(m[:7])
    return False


def get_portfolio_fills(client, min_ts: int, max_ts: int, limit: int = 200, subaccount: int = 0):
    """GET /portfolio/fills - min_ts, max_ts, subaccount=0 for primary."""
    all_fills = []
    cursor = None
    while True:
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": limit, "subaccount": subaccount}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(client.portfolio_url + "/fills", params=params)
        fills = resp.get("fills", [])
        all_fills.extend(fills)
        cursor = resp.get("cursor")
        if not cursor or not fills:
            break
    return all_fills


def get_portfolio_settlements(client, min_ts: int, max_ts: int, limit: int = 200, subaccount: int = 0):
    """GET /portfolio/settlements - min_ts, max_ts, subaccount=0 for primary."""
    all_settlements = []
    cursor = None
    while True:
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": limit, "subaccount": subaccount}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(client.portfolio_url + "/settlements", params=params)
        settlements = resp.get("settlements", [])
        all_settlements.extend(settlements)
        cursor = resp.get("cursor")
        if not cursor or not settlements:
            break
    return all_settlements


def parse_ts(s: str) -> int:
    if not s:
        return 0
    try:
        s = s.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return 0


def calc_fills_pnl(fills, min_ts: int, max_ts: int):
    """Round-trip PnL from fills. Match Buy YES/Sell NO (opens) with Sell YES/Buy NO (closes)."""
    fish_fills = [
        f for f in fills
        if is_fish_ticker(f.get("ticker", ""))
        and (min_ts <= (f.get("ts") or parse_ts(f.get("created_time", ""))) <= max_ts)
    ]

    def parse_fill(f):
        side = f.get("side", "yes")
        # YES-equivalent price: API may use dollars (0.03) or cents (3). For sell-NO use 1 - no_price.
        raw_yes = float(f.get("yes_price_fixed") or 0)
        raw_no = float(f.get("no_price_fixed") or 0)
        if side == "no":
            price = 1.0 - (raw_no if raw_no <= 1 else raw_no / 100)
        else:
            price = raw_yes if raw_yes <= 1 else raw_yes / 100
        return {
            "ticker": f.get("ticker"),
            "side": side,
            "action": f.get("action", "buy"),
            "count": int(f.get("count", 0)),
            "price": price,
            "ts": f.get("ts") or parse_ts(f.get("created_time", "")),
            "created_time": f.get("created_time"),
        }

    by_ticker = defaultdict(list)
    for f in fish_fills:
        pf = parse_fill(f)
        by_ticker[pf["ticker"]].append(pf)

    for t in by_ticker:
        by_ticker[t].sort(key=lambda x: x["ts"] or 0)

    trades = []
    for ticker, ticker_fills in by_ticker.items():
        opens = []
        for pf in ticker_fills:
            if (pf["action"] == "buy" and pf["side"] == "yes") or (pf["action"] == "sell" and pf["side"] == "no"):
                opens.append(pf)
            elif (pf["action"] == "sell" and pf["side"] == "yes") or (pf["action"] == "buy" and pf["side"] == "no"):
                qty = pf["count"]
                close_price = pf["price"]
                while qty > 0 and opens:
                    o = opens[0]
                    match_qty = min(qty, o["count"])
                    pnl = (close_price - o["price"]) * match_qty
                    trades.append({
                        "ticker": ticker,
                        "city": extract_city(ticker),
                        "qty": match_qty,
                        "entry": o["price"],
                        "exit": close_price,
                        "pnl": pnl,
                        "is_low": "LOW" in (ticker or "").upper(),
                        "is_high": "HIGH" in (ticker or "").upper(),
                    })
                    o["count"] -= match_qty
                    if o["count"] <= 0:
                        opens.pop(0)
                    qty -= match_qty

    return sum(t["pnl"] for t in trades), trades


def calc_settlements_pnl(settlements, cutoff: str):
    """
    PnL from settlements. Per Kalshi docs:
    - revenue, yes_total_cost: in CENTS (integer). Some APIs return DOLLARS (e.g. 0.56 for 56¢).
    - fee_cost: dollars.
    We auto-detect: if non-zero yes_total_cost are all in (0, 1) (e.g. 0.56), treat as dollars.
    Skip settlements with cost=0 and revenue=0 (no position).
    """
    fish = [
        s for s in settlements
        if is_fish_ticker(s.get("ticker", "")) and (s.get("settled_time") or "") >= cutoff
    ]
    raw_yes_values = [float(s.get("yes_total_cost") or 0) for s in fish if float(s.get("yes_total_cost") or 0) > 0]
    # If all non-zero costs are < 1 (e.g. 0.56), API likely returned dollars (56¢ = $0.56).
    use_dollars = bool(raw_yes_values and max(raw_yes_values) < 1)

    total_cents = 0
    details = []
    for s in fish:
        rev_raw = float(s.get("revenue") or 0)
        yes_raw = float(s.get("yes_total_cost") or 0)
        if yes_raw == 0 and rev_raw == 0:
            continue
        fee_dollars = float(s.get("fee_cost") or "0")
        fee_cents = round(fee_dollars * 100)
        if use_dollars:
            # 0.56 = $0.56, so store as 56 cents
            revenue_cents = round(rev_raw * 100)
            yes_cost_cents = round(yes_raw * 100)
        else:
            revenue_cents = int(rev_raw)
            yes_cost_cents = int(yes_raw)
        pnl_cents = revenue_cents - yes_cost_cents - fee_cents
        total_cents += pnl_cents
        details.append({
            "ticker": s.get("ticker"),
            "pnl_cents": pnl_cents,
            "result": s.get("market_result"),
            "yes_cost": yes_cost_cents,
            "revenue": revenue_cents,
        })
    return total_cents / 100.0, details


def load_pnl_from_kalshi_csv(csv_path: str, month_filter: str | None) -> tuple[float, list[dict]]:
    """
    Load PnL from Kalshi transactions CSV export. All amounts in the CSV are in CENTS.
    Columns: type, quantity, market_ticker, side, entry_price_cents, exit_price_cents,
             open_fees_cents, close_fees_cents, realized_pnl_without_fees_cents, realized_pnl_with_fees_cents,
             close_timestamp, open_timestamp
    Returns (total_pnl_dollars, list of trade dicts with ticker, city, qty, entry, exit, pnl, is_low, is_high).
    """
    trades = []
    total_cents = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("type") or "").strip().lower() != "trade":
                continue
            ticker = (row.get("market_ticker") or "").strip()
            if not ticker or not is_fish_ticker(ticker):
                continue
            if month_filter and not ticker_in_month(ticker, month_filter):
                continue
            try:
                qty = int(row.get("quantity") or 0)
                pnl_cents = int(row.get("realized_pnl_with_fees_cents") or 0)
                entry_c = int(row.get("entry_price_cents") or 0)
                exit_c = int(row.get("exit_price_cents") or 0)
            except (ValueError, TypeError):
                continue
            total_cents += pnl_cents
            entry_d = entry_c / 100.0
            exit_d = exit_c / 100.0
            pnl_dollars = pnl_cents / 100.0
            trades.append({
                "ticker": ticker,
                "city": extract_city(ticker),
                "qty": qty,
                "entry": entry_d,
                "exit": exit_d,
                "pnl": pnl_dollars,
                "is_low": "LOW" in ticker.upper(),
                "is_high": "HIGH" in ticker.upper(),
            })
    return total_cents / 100.0, trades


def main():
    parser = argparse.ArgumentParser(description="Analyze Kalshi fish trades PnL (API or CSV export)")
    parser.add_argument("--csv", type=str, default=None, metavar="PATH", help="Use Kalshi transactions CSV (all amounts in CENTS)")
    parser.add_argument("--today", action="store_true", help="Analyze only today's trades (since midnight local)")
    parser.add_argument("--days", type=int, default=None, metavar="N", help="Analyze last N days (default: 7)")
    parser.add_argument("--month", type=str, default=None, metavar="MAR|2026-03", help="Filter to month: MAR or 2026-03 (market date from ticker)")
    args = parser.parse_args()

    month_filter = (args.month or "").strip().upper()
    if month_filter and len(month_filter) == 3 and month_filter not in "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split():
        month_filter = None
    if args.month and not month_filter and len((args.month or "").strip()) >= 7:
        month_filter = args.month.strip()[:7]

    # ---------- CSV path: use export file (all amounts in cents) ----------
    if args.csv:
        path = Path(args.csv).expanduser().resolve()
        if not path.exists():
            print(f"File not found: {path}")
            return
        print(f"Loading from CSV (all amounts in CENTS): {path}")
        total_pnl, trades = load_pnl_from_kalshi_csv(str(path), month_filter or None)
        if month_filter:
            print(f"Filtered to month: {args.month} -> {len(trades)} trades")
        print("\n" + "=" * 60)
        print("PNL SUMMARY (from Kalshi transactions CSV)" + (f" [MONTH={args.month}]" if month_filter else ""))
        print("=" * 60)
        print(f"  Total PnL (realized_pnl_with_fees_cents / 100): ${total_pnl:.2f}")

        low_trades = [t for t in trades if t["is_low"]]
        high_trades = [t for t in trades if t["is_high"]]
        low_pnl = sum(t["pnl"] for t in low_trades)
        high_pnl = sum(t["pnl"] for t in high_trades)
        print("\n" + "=" * 60)
        print("BY TYPE (LOW vs HIGH)")
        print("=" * 60)
        print(f"  LOW:  {len(low_trades)} trades, ${low_pnl:.2f}")
        print(f"  HIGH: {len(high_trades)} trades, ${high_pnl:.2f}")

        by_city = defaultdict(float)
        for t in trades:
            by_city[t["city"]] += t["pnl"]
        print("\n" + "=" * 60)
        print("BY CITY")
        print("=" * 60)
        for city in sorted(by_city.keys()):
            if city:
                print(f"  {city}: ${by_city[city]:.2f}")

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        print("\n" + "=" * 60)
        print("STRATEGY SUMMARY")
        print("=" * 60)
        print(f"  Trades: {len(trades)}, Wins: {len(wins)} (${sum(t['pnl'] for t in wins):.2f}), Losses: {len(losses)} (${sum(t['pnl'] for t in losses):.2f})")
        if trades:
            print(f"  Win rate: {100.0 * len(wins) / len(trades):.1f}%  |  Avg PnL per trade: ${total_pnl / len(trades):.2f}")

        out_path = Path(__file__).parent.parent / "logs" / "pnl_analysis.csv"
        out_path.parent.mkdir(exist_ok=True)
        suffix = "_csv" + (f"_{args.month.strip().replace('-', '')[:7]}" if month_filter else "")
        out_path = out_path.parent / (out_path.stem + suffix + out_path.suffix)
        with open(out_path, "w") as f:
            f.write("source,ticker,city,qty,entry,exit,pnl,type\n")
            for t in trades:
                ttype = "LOW" if t["is_low"] else "HIGH"
                f.write(f"csv,{t['ticker']},{t['city']},{t['qty']},{t['entry']:.4f},{t['exit']:.4f},{t['pnl']:.4f},{ttype}\n")
        print(f"\nSaved to {out_path}")
        return

    # ---------- API path ----------
    load_dotenv()
    KEYID = os.getenv("PROD_KEYID")
    KEYFILE = os.getenv("PROD_KEYFILE")
    if not KEYID or not KEYFILE:
        print("Set PROD_KEYID and PROD_KEYFILE in .env")
        return

    with open(KEYFILE, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    client = KalshiHttpClient(key_id=KEYID, private_key=private_key, environment=Environment.PROD)

    now = datetime.now()
    if args.today:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days_label = "today"
    else:
        n = args.days if args.days is not None else (60 if args.month else 7)
        start = now - timedelta(days=n)
        days_label = f"last {n} days"
    min_ts = int(start.timestamp())
    max_ts = int(now.timestamp())
    cutoff = start.isoformat()

    bal = client.get_balance()
    print(f"Account balance: ${bal.get('balance', 0)/100:.2f}")
    print(f"Period: {days_label} (from {start.strftime('%Y-%m-%d %H:%M')} to now)\n")

    print("Fetching GET /portfolio/fills (subaccount=0)...")
    fills = get_portfolio_fills(client, min_ts, max_ts)
    fish_fills = [f for f in fills if is_fish_ticker(f.get("ticker", ""))]
    print(f"  Fish fills: {len(fish_fills)}")

    print("Fetching GET /portfolio/settlements (subaccount=0)...")
    settlements = get_portfolio_settlements(client, min_ts, max_ts)
    fish_settlements = [s for s in settlements if is_fish_ticker(s.get("ticker", ""))]
    print(f"  Fish settlements: {len(fish_settlements)}")

    fills_pnl, trades = calc_fills_pnl(fills, min_ts, max_ts)
    settlements_pnl, settlement_details = calc_settlements_pnl(settlements, cutoff)

    # Optional: filter to one month (by market date in ticker)
    month_filter = (args.month or "").strip().upper()
    if month_filter and len(month_filter) == 3 and month_filter not in "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split():
        month_filter = None
    if month_filter:
        trades = [t for t in trades if ticker_in_month(t["ticker"], args.month.strip())]
        settlement_details = [d for d in settlement_details if ticker_in_month(d["ticker"], args.month.strip())]
        fills_pnl = sum(t["pnl"] for t in trades)
        settlements_pnl = sum(d["pnl_cents"] for d in settlement_details) / 100.0
        print(f"\nFiltered to month: {args.month} (by market date in ticker) -> {len(trades)} round-trades, {len(settlement_details)} settlements")

    total_pnl = fills_pnl + settlements_pnl

    balance_dollars = bal.get("balance", 0) / 100.0
    if balance_dollars > 0 and abs(total_pnl) > 2 * balance_dollars:
        print("\n  *** SANITY: Total PnL is much larger than current balance. ***")
        print("  If your balance never went far above current, the API may return amounts in DOLLARS not cents.")
        print("  Check one settlement in the Kalshi dashboard and compare to the 'cost' we show (we assume cents).")

    print("\n" + "=" * 60)
    print("PNL SUMMARY" + (" (TODAY)" if args.today else "") + (f" [MONTH={args.month}]" if month_filter else ""))
    print("=" * 60)
    print(f"  Fills (round-trips, sold before resolution): ${fills_pnl:.2f}")
    print(f"  Settlements (held to resolution):           ${settlements_pnl:.2f}")
    print(f"  TOTAL PnL:                                 ${total_pnl:.2f}")

    if settlement_details:
        wins = [d for d in settlement_details if d["pnl_cents"] > 0]
        losses = [d for d in settlement_details if d["pnl_cents"] < 0]
        print("\n" + "=" * 60)
        print("SETTLEMENTS (positions held to resolution)")
        print("=" * 60)
        print(f"  With position: {len(settlement_details)}")
        print(f"  Winning: {len(wins)}, ${sum(d['pnl_cents'] for d in wins)/100:.2f}")
        print(f"  Losing:  {len(losses)}, ${sum(d['pnl_cents'] for d in losses)/100:.2f}")
        print(f"  (When LOST: revenue=0, loss = cost we paid)")
        if losses:
            print("\n  Worst (cost we lost):")
            for d in sorted(settlement_details, key=lambda x: x["pnl_cents"])[:8]:
                print(f"    {d['ticker']}: cost=${d['yes_cost']/100:.2f} rev=${d['revenue']/100:.2f} -> pnl=${d['pnl_cents']/100:.2f} (result={d['result']})")

    if trades:
        winning = [t for t in trades if t["pnl"] > 0]
        losing = [t for t in trades if t["pnl"] < 0]
        print("\n" + "=" * 60)
        print("ROUND-TRIP TRADES (from fills)")
        print("=" * 60)
        print(f"  Trades: {len(trades)}")
        print(f"  Winning: {len(winning)}, ${sum(t['pnl'] for t in winning):.2f}")
        print(f"  Losing:  {len(losing)}, ${sum(t['pnl'] for t in losing):.2f}")
        if losing:
            print("\n  Worst:")
            for t in sorted(losing, key=lambda x: x["pnl"])[:8]:
                print(f"    {t['ticker']}: qty={t['qty']} entry={t['entry']:.2f} exit={t['exit']:.2f} pnl=${t['pnl']:.2f}")

    by_city = defaultdict(float)
    for t in trades:
        by_city[t["city"]] += t["pnl"]
    for d in settlement_details:
        by_city[extract_city(d["ticker"])] += d["pnl_cents"] / 100.0
    print("\n" + "=" * 60)
    print("BY CITY")
    print("=" * 60)
    for city in sorted(by_city.keys()):
        if city:
            print(f"  {city}: ${by_city[city]:.2f}")

    # LOW vs HIGH: round-trade (fills) vs settlements
    low_fills = [t for t in trades if t["is_low"]]
    high_fills = [t for t in trades if t["is_high"]]
    low_settle = [d for d in settlement_details if "LOW" in (d["ticker"] or "").upper()]
    high_settle = [d for d in settlement_details if "HIGH" in (d["ticker"] or "").upper()]
    low_fills_pnl = sum(t["pnl"] for t in low_fills)
    high_fills_pnl = sum(t["pnl"] for t in high_fills)
    low_settle_pnl = sum(d["pnl_cents"] for d in low_settle) / 100.0
    high_settle_pnl = sum(d["pnl_cents"] for d in high_settle) / 100.0
    print("\n" + "=" * 60)
    print("BY TYPE (LOW vs HIGH) — strategy breakdown")
    print("=" * 60)
    print("  LOW (buy low, sell or settle):")
    print(f"    Round-trades (sold before resolution): {len(low_fills)} trades, ${low_fills_pnl:.2f}")
    print(f"    Settlements (held to resolution):    {len(low_settle)} positions, ${low_settle_pnl:.2f}")
    print(f"    LOW total:                           ${low_fills_pnl + low_settle_pnl:.2f}")
    print("  HIGH (buy high, sell or settle):")
    print(f"    Round-trades (sold before resolution): {len(high_fills)} trades, ${high_fills_pnl:.2f}")
    print(f"    Settlements (held to resolution):    {len(high_settle)} positions, ${high_settle_pnl:.2f}")
    print(f"    HIGH total:                          ${high_fills_pnl + high_settle_pnl:.2f}")

    # Strategy summary: win rate and avg PnL per trade so you can see what's working
    print("\n" + "=" * 60)
    print("STRATEGY SUMMARY (why it might not be working)")
    print("=" * 60)
    for label, trade_list, settle_list in [
        ("LOW", low_fills, low_settle),
        ("HIGH", high_fills, high_settle),
    ]:
        n_round = len(trade_list)
        n_settle = len(settle_list)
        wins_round = sum(1 for t in trade_list if t["pnl"] > 0)
        losses_round = sum(1 for t in trade_list if t["pnl"] < 0)
        wins_settle = sum(1 for d in settle_list if d["pnl_cents"] > 0)
        losses_settle = sum(1 for d in settle_list if d["pnl_cents"] < 0)
        pnl_round = sum(t["pnl"] for t in trade_list)
        pnl_settle = sum(d["pnl_cents"] for d in settle_list) / 100.0
        total_n = n_round + n_settle
        total_wins = wins_round + wins_settle
        total_losses = losses_round + losses_settle
        win_rate = (100.0 * total_wins / total_n) if total_n else 0
        avg_pnl = (pnl_round + pnl_settle) / total_n if total_n else 0
        print(f"  {label}: {n_round} round-trades (wins={wins_round} losses={losses_round}), {n_settle} settlements (wins={wins_settle} losses={losses_settle})")
        print(f"       Win rate = {total_wins}/{total_n} = {win_rate:.1f}%  |  Total PnL = ${pnl_round + pnl_settle:.2f}  |  Avg PnL per position = ${avg_pnl:.2f}")

    out_path = Path(__file__).parent.parent / "logs" / "pnl_analysis.csv"
    out_path.parent.mkdir(exist_ok=True)
    suffix = "_today" if args.today else ""
    if month_filter:
        suffix = "_" + (args.month.strip().replace("-", "")[:7])  # e.g. MAR or 202603
    out_path = out_path.parent / (out_path.stem + suffix + out_path.suffix)
    with open(out_path, "w") as f:
        f.write("source,ticker,city,qty,entry,exit,pnl,type\n")
        for d in settlement_details:
            ttype = "LOW" if "LOW" in (d["ticker"] or "").upper() else "HIGH"
            f.write(f"settlement,{d['ticker']},{extract_city(d['ticker'])},,,,{d['pnl_cents']/100:.4f},{ttype}\n")
        for t in trades:
            ttype = "LOW" if t["is_low"] else "HIGH"
            f.write(f"fill,{t['ticker']},{t['city']},{t['qty']},{t['entry']:.4f},{t['exit']:.4f},{t['pnl']:.4f},{ttype}\n")
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
