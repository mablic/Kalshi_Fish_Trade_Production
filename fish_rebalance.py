import csv
import sys
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from fish_market_ticker import FISH_MARKET_TICKER


_ET = "America/New_York"

# Same row order as your Excel screenshot (union with any extra city seen in data).
FISH_CITY_ROWS = frozenset({
    "AUS", "BTC", "CHI", "DEN", "INCENTIVE", "LAX", "MIA", "NY", "NYC", "PHIL",
    "TATL", "TBOS", "TDAL", "TDC", "THOU", "TLV", "TMIN", "TNOLA", "TOKC",
    "TPHX", "TSATX", "TSEA", "TSFO",
})

# Print order to match your Excel table
_CITY_PRINT_ORDER = (
    "AUS", "BTC", "CHI", "DEN", "INCENTIVE", "LAX", "MIA", "NY", "NYC", "PHIL",
    "TATL", "TBOS", "TDAL", "TDC", "THOU", "TLV", "TMIN", "TNOLA", "TOKC",
    "TPHX", "TSATX", "TSEA", "TSFO",
)
_ORDER_IDX = {c: i for i, c in enumerate(_CITY_PRINT_ORDER)}


def _sort_city_totals(rows):
    return sorted(rows, key=lambda x: (_ORDER_IDX.get(x[0], 10_000), x[0]))


def _fp_dollars_to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return 0.0


def _fp_count(s: dict, fp_key: str, leg_key: str) -> float:
    v = s.get(fp_key)
    if v is not None and str(v).strip() != "":
        try:
            return float(str(v).strip())
        except (TypeError, ValueError):
            pass
    v2 = s.get(leg_key)
    if v2 is None:
        return 0.0
    try:
        return float(v2)
    except (TypeError, ValueError):
        return 0.0


def _settlement_gross_payout_cents(s: dict) -> int:
    """
    Cash credited at settlement. `revenue` is YES-side payout only; when NO wins it is often 0 while
    NO still pays (100 - value) cents per NO contract. See Kalshi Settlement schema (`value`, count_fp fields).
    """
    rev = int(round(float(s.get("revenue") or 0)))
    val = s.get("value")
    if val is None:
        return rev
    try:
        v = int(val)
    except (TypeError, ValueError):
        return rev
    yc = _fp_count(s, "yes_count_fp", "yes_count")
    nc = _fp_count(s, "no_count_fp", "no_count")
    if yc == 0 and nc == 0:
        return rev
    return int(yc * v + nc * (100 - v))


def _settlement_economics_cents(s: dict, use_dollars: bool) -> Optional[Tuple[int, int, int, int]]:
    """
    Returns (gross_payout_cents, yes_cost_cents, no_cost_cents, fee_cents) for PnL =
    gross - yes_cost - no_cost - fee. None if no position/fees.
    """
    fee_cents = int(round(_fp_dollars_to_float(s.get("fee_cost")) * 100))

    if "yes_total_cost_dollars" in s or "no_total_cost_dollars" in s:
        yes_cents = int(round(_fp_dollars_to_float(s.get("yes_total_cost_dollars")) * 100))
        no_cents = int(round(_fp_dollars_to_float(s.get("no_total_cost_dollars")) * 100))
        result = (s.get("market_result") or "").strip().lower()
        if result == "void":
            gross_cents = yes_cents + no_cents
        else:
            gross_cents = _settlement_gross_payout_cents(s)
    else:
        rev_raw = float(s.get("revenue") or 0)
        yes_raw = float(s.get("yes_total_cost") or 0)
        no_raw = float(s.get("no_total_cost") or 0)
        if rev_raw == 0 and yes_raw == 0 and no_raw == 0:
            return None
        if use_dollars:
            gross_cents = int(round(rev_raw * 100))
            yes_cents = int(round(yes_raw * 100))
            no_cents = int(round(no_raw * 100))
        else:
            gross_cents = int(rev_raw)
            yes_cents = int(yes_raw)
            no_cents = int(no_raw)

    if gross_cents == 0 and yes_cents == 0 and no_cents == 0 and fee_cents == 0:
        return None
    return gross_cents, yes_cents, no_cents, fee_cents


def _et_today() -> datetime.date:
    if ZoneInfo:
        return datetime.now(ZoneInfo(_ET)).date()
    return datetime.now().date()


def _et_day_start_epoch(d: datetime.date) -> int:
    if ZoneInfo:
        t = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=ZoneInfo(_ET))
        return int(t.timestamp())
    return int(datetime.combine(d, datetime.min.time()).timestamp())


class FISH_RABALANCE:

    __instance = None

    def __new__(cls):
        if cls.__instance is None:
            inst = super().__new__(cls)
            inst.fish_rabalance = {}
            inst.filled_pnl = {}
            inst.rebalance_pnl = {}
            inst.aggregate_rows = []
            inst.fill_pnl_events = []
            inst.settlement_pnl_events = []
            inst.city_totals = []
            cls.__instance = inst
        return cls.__instance

    def _fill_ts_for_sort(self, fill: dict) -> int:
        if fill.get("ts") is not None:
            try:
                return int(fill["ts"])
            except (TypeError, ValueError):
                pass
        s = (fill.get("created_time") or "").strip().replace("Z", "+00:00")
        if not s:
            return 0
        try:
            return int(datetime.fromisoformat(s).timestamp())
        except (ValueError, TypeError, OSError):
            return 0

    def _parse_fill_kalshi(self, fill: dict):
        side = (fill.get("side") or "yes").lower()
        raw_yes = float(fill.get("yes_price_fixed") or fill.get("yes_price_dollars") or 0)
        raw_no = float(fill.get("no_price_fixed") or fill.get("no_price_dollars") or 0)
        if side == "no":
            rno = raw_no if raw_no <= 1 else raw_no / 100.0
            price = 1.0 - rno
        else:
            price = raw_yes if raw_yes <= 1 else raw_yes / 100.0
        cnt = fill.get("count")
        if cnt is None and fill.get("count_fp") is not None:
            try:
                cnt = int(float(fill["count_fp"]))
            except (ValueError, TypeError):
                cnt = 0
        cnt = int(cnt) if cnt is not None else 0
        if cnt <= 0:
            return None
        return {
            "action": (fill.get("action") or "buy").lower(),
            "side": side,
            "count": cnt,
            "price": price,
            "fill": fill,
        }

    def _et_date_from_iso_or_ts(self, fill: dict) -> str:
        s = fill.get("created_time")
        if s:
            return self._iso_to_et_date(str(s))
        ts = fill.get("ts")
        if ts is not None and ZoneInfo:
            try:
                t = int(ts)
                return datetime.fromtimestamp(t, tz=ZoneInfo("UTC")).astimezone(ZoneInfo(_ET)).strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                pass
        if s:
            return str(s)[:10]
        return ""

    def _iso_to_et_date(self, s: str) -> str:
        s = (s or "").strip().replace("Z", "+00:00")
        if not s:
            return ""
        try:
            dt = datetime.fromisoformat(s)
            if ZoneInfo:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                return dt.astimezone(ZoneInfo(_ET)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
        return s[:10] if len(s) >= 10 else ""

    def _et_date_from_settlement(self, s: dict) -> str:
        for key in ("settled_time", "created_time", "updated_time"):
            v = s.get(key)
            if v:
                d = self._iso_to_et_date(str(v))
                if d:
                    return d
        ticker = (s.get("ticker") or "").strip()
        if ticker:
            dt = FISH_MARKET_TICKER().get_market_datetime_from_ticker(ticker)
            if dt:
                return dt.strftime("%Y-%m-%d")
        return ""

    def _fifo_realized_events(self, ticker: str, fills: list) -> list:

        parsed = []
        for f in fills:
            p = self._parse_fill_kalshi(f)
            if p:
                parsed.append(p)
        parsed.sort(key=lambda x: self._fill_ts_for_sort(x["fill"]))

        opens = []
        events = []
        for p in parsed:
            fill = p["fill"]
            close_date = self._et_date_from_iso_or_ts(fill)
            action, side = p["action"], p["side"]
            if (action == "buy" and side == "yes") or (action == "sell" and side == "no"):
                opens.append({"count": p["count"], "price": p["price"]})
                continue
            if (action == "sell" and side == "yes") or (action == "buy" and side == "no"):
                qty = p["count"]
                close_price = p["price"]
                while qty > 0 and opens:
                    o = opens[0]
                    m = min(qty, o["count"])
                    pnl = (close_price - o["price"]) * m
                    events.append({"pnl": round(pnl, 6), "date": close_date, "ticker": ticker})
                    o["count"] -= m
                    if o["count"] <= 0:
                        opens.pop(0)
                    qty -= m
                continue
        return events

    def get_filled_orders(self, filled_orders: dict):
        self.fill_pnl_events = []
        if not isinstance(filled_orders, dict):
            self.filled_pnl = {}
            self.fish_rabalance = {}
            return self.filled_pnl

        fills = filled_orders.get("fills")
        if not isinstance(fills, list):
            self.filled_pnl = {}
            self.fish_rabalance = {}
            return self.filled_pnl

        by_ticker = {}
        for fill in fills:
            if not isinstance(fill, dict):
                continue
            ticker = (fill.get("ticker") or "").strip()
            if not ticker:
                continue
            by_ticker.setdefault(ticker, []).append(fill)

        self.filled_pnl = {}
        for ticker, t_fills in by_ticker.items():
            ev = self._fifo_realized_events(ticker, t_fills)
            self.fill_pnl_events.extend(ev)
            self.filled_pnl[ticker] = round(sum(e["pnl"] for e in ev), 6)

        self.fish_rabalance = dict(self.filled_pnl)
        return self.filled_pnl

    @staticmethod
    def _settlement_use_dollars_flag(settlements: list) -> bool:
        raw_yes: List[float] = []
        for s in settlements:
            if not isinstance(s, dict):
                continue
            if "yes_total_cost_dollars" in s or "no_total_cost_dollars" in s:
                continue
            y = float(s.get("yes_total_cost") or 0)
            if y > 0:
                raw_yes.append(y)
        return bool(raw_yes and max(raw_yes) < 1)

    def _settlement_row_pnl_dollars(self, s: dict, use_dollars: bool):
        econ = _settlement_economics_cents(s, use_dollars)
        if econ is None:
            return None
        gross_cents, yes_cents, no_cents, fee_cents = econ
        return (gross_cents - yes_cents - no_cents - fee_cents) / 100.0

    def get_settled_orders(self, settled_orders: dict):
        self.settlement_pnl_events = []
        if not isinstance(settled_orders, dict):
            self.rebalance_pnl = {}
            return self.rebalance_pnl

        settlements = settled_orders.get("settlements")
        if not isinstance(settlements, list):
            self.rebalance_pnl = {}
            return self.rebalance_pnl

        raw = [s for s in settlements if isinstance(s, dict) and (s.get("ticker") or "")]
        use_dollars = self._settlement_use_dollars_flag(raw)
        self.rebalance_pnl = {}
        for s in raw:
            ticker = (s.get("ticker") or "").strip()
            pnl = self._settlement_row_pnl_dollars(s, use_dollars)
            if pnl is None:
                continue
            d = self._et_date_from_settlement(s)
            self.settlement_pnl_events.append({"pnl": round(pnl, 6), "date": d, "ticker": ticker})
            self.rebalance_pnl[ticker] = round(self.rebalance_pnl.get(ticker, 0) + pnl, 6)
        return self.rebalance_pnl

    def _row_code(self, ticker: str):
        u = (ticker or "").upper()
        if "BTC" in u:
            return "BTC"
        mt = FISH_MARKET_TICKER()
        return (mt.ticker_to_symbol(ticker) or "").strip()

    def _market_date_iso(self, ticker: str) -> str:
        """Event date from ticker (26MAR29 → YYYY-MM-DD). Excel day columns use this, not fill time."""
        dt = FISH_MARKET_TICKER().get_market_datetime_from_ticker(ticker or "")
        return dt.strftime("%Y-%m-%d") if dt else ""

    def _bucket_date(self, ticker: str, realization_date: str, bucket: str) -> str:
        if bucket == "realization":
            return realization_date or ""
        md = self._market_date_iso(ticker)
        return md if md else (realization_date or "")

    @staticmethod
    def _in_date_window(d: str, start: str, end: str) -> bool:
        if not d:
            return False
        return (not start or d >= start) and (not end or d <= end)

    def aggregate_pnl(self, display_date_start: str, display_date_end: str, bucket: str = "market"):
        """
        bucket="market" (default): count PnL when the ticker's market/event date falls in the window.
        That matches Excel weather sheets where columns are market days (3/29–4/3), not when Kalshi posted the fill.

        bucket="realization": filter on US/Eastern fill/settlement calendar date (old behavior).
        """
        by_city = defaultdict(float)
        by_code_date = defaultdict(float)

        for ev in self.fill_pnl_events:
            bd = self._bucket_date(ev["ticker"], ev.get("date") or "", bucket)
            if not self._in_date_window(bd, display_date_start, display_date_end):
                continue
            code = self._row_code(ev["ticker"]) or "OTHER"
            by_city[code] += ev["pnl"]
            by_code_date[(code, bd)] += ev["pnl"]

        for ev in self.settlement_pnl_events:
            bd = self._bucket_date(ev["ticker"], ev.get("date") or "", bucket)
            if not self._in_date_window(bd, display_date_start, display_date_end):
                continue
            code = self._row_code(ev["ticker"]) or "OTHER"
            by_city[code] += ev["pnl"]
            by_code_date[(code, bd)] += ev["pnl"]

        self.aggregate_rows = [
            {"city": c, "date": d, "pnl": round(v, 6)}
            for (c, d), v in sorted(by_code_date.items(), key=lambda x: (x[0][0], x[0][1]))
        ]

        all_codes = set(FISH_CITY_ROWS) | set(by_city.keys())
        self.city_totals = _sort_city_totals(
            [(c, round(by_city.get(c, 0.0), 4)) for c in all_codes]
        )
        return self.city_totals


def ticker_in_weather_or_btc_book(ticker: str) -> bool:
    """KXLOWT* / KXHIGH* (incl. KXHIGHT*) weather book, or tickers containing BTC (e.g. KXBTC*)."""
    u = (ticker or "").upper()
    return u.startswith("KXLOWT") or u.startswith("KXHIGH") or "BTC" in u


def pnl_by_city_ticker_substring(
    rb: FISH_RABALANCE,
    needle: str,
    ticker_filter: Optional[Callable[[str], bool]] = None,
) -> List[Tuple[str, float]]:
    """Sum fill-FIFO + settlement PnL for events whose ticker contains needle (case-insensitive)."""
    nu = (needle or "").strip().upper()
    if not nu:
        return []
    by_city: Dict[str, float] = defaultdict(float)
    for ev in rb.fill_pnl_events:
        t = ev.get("ticker") or ""
        if nu not in t.upper():
            continue
        if ticker_filter is not None and not ticker_filter(t):
            continue
        code = rb._row_code(t) or "OTHER"
        by_city[code] += float(ev.get("pnl") or 0)
    for ev in rb.settlement_pnl_events:
        t = ev.get("ticker") or ""
        if nu not in t.upper():
            continue
        if ticker_filter is not None and not ticker_filter(t):
            continue
        code = rb._row_code(t) or "OTHER"
        by_city[code] += float(ev.get("pnl") or 0)
    cities = sorted(by_city.keys(), key=lambda c: (_ORDER_IDX.get(c, 10_000), c))
    return [(c, round(by_city[c], 4)) for c in cities]


def city_win_rate_last_n_days(
    rb: FISH_RABALANCE,
    city_code: str,
    num_days: int = 5,
    *,
    end_date: Optional[datetime.date] = None,
) -> Dict[str, Any]:
    """
    Win rate from FIFO fill-realized + settlement PnL already loaded on ``rb``.

    Uses US/Eastern calendar days ending at ``end_date`` (default: today ET). For each day in
    that window: if there is no PnL event for this city that day, the day is skipped. Otherwise
    the day is a win (net PnL > 0), loss (net < 0), or tie (net == 0). Ties do not count as
    wins or losses.

    Win rate = wins / (wins + losses). If there are no win/loss days (all no-trade or ties),
    ``win_rate`` is None.

    Example: 5 days → 2 wins, 2 losses, 1 no trade → win_rate = 2 / (2 + 2) = 0.5.
    """
    n = int(num_days)
    if n <= 0:
        return {
            "city": (city_code or "").strip().upper(),
            "win_rate": None,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "no_trade_days": 0,
            "decisive_days": 0,
            "days_in_window": 0,
            "window_start": "",
            "window_end": "",
        }

    end = end_date if end_date is not None else _et_today()
    window_dates = [(end - timedelta(days=n - 1 - i)).strftime("%Y-%m-%d") for i in range(n)]
    window_set = set(window_dates)
    city_u = (city_code or "").strip().upper()

    daily_pnl: Dict[str, float] = defaultdict(float)
    traded: set[str] = set()

    for ev in rb.fill_pnl_events:
        t = ev.get("ticker") or ""
        if (rb._row_code(t) or "OTHER").upper() != city_u:
            continue
        d = (ev.get("date") or "").strip()
        if d not in window_set:
            continue
        daily_pnl[d] += float(ev.get("pnl") or 0)
        traded.add(d)

    for ev in rb.settlement_pnl_events:
        t = ev.get("ticker") or ""
        if (rb._row_code(t) or "OTHER").upper() != city_u:
            continue
        d = (ev.get("date") or "").strip()
        if d not in window_set:
            continue
        daily_pnl[d] += float(ev.get("pnl") or 0)
        traded.add(d)

    wins = losses = ties = no_trade = 0
    eps = 1e-9
    for d in window_dates:
        if d not in traded:
            no_trade += 1
            continue
        net = daily_pnl.get(d, 0.0)
        if net > eps:
            wins += 1
        elif net < -eps:
            losses += 1
        else:
            ties += 1

    decisive = wins + losses
    wr: Optional[float] = (wins / decisive) if decisive else None

    return {
        "city": city_u,
        "win_rate": wr,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "no_trade_days": no_trade,
        "decisive_days": decisive,
        "days_in_window": n,
        "window_start": window_dates[0],
        "window_end": window_dates[-1],
    }


def _print_pnl_by_city_rows(rows: List[Tuple[str, float]], title: str) -> None:
    print(title)
    print(f"{'City':<12} {'PnL ($)':>14}")
    print("-" * 28)
    grand = 0.0
    for city, pnl in rows:
        grand += pnl
        print(f"{city:<12} {_fmt_money(pnl):>14}")
    print("-" * 28)
    print(f"{'ALL':<12} {_fmt_money(grand):>14}")


def _fmt_money(x: float) -> str:
    if abs(x) < 1e-9:
        return "0.00"
    if x < 0:
        return f"({abs(x):.2f})"
    return f"{x:.2f}"


def _utc_ts_to_et_date_str(sec: int) -> str:
    if ZoneInfo:
        return datetime.fromtimestamp(sec, tz=ZoneInfo("UTC")).astimezone(ZoneInfo(_ET)).strftime("%Y-%m-%d")
    return datetime.utcfromtimestamp(sec).strftime("%Y-%m-%d")


def _fill_close_et_date_and_label(fill: dict) -> tuple[str, str]:
    """
    US/Eastern calendar date of the fill's close, and a short label of which field was used.
    Order: close_timestamp (Kalshi), created_time, ts.
    """
    for key in ("close_timestamp", "created_time"):
        v = fill.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)):
            sec = int(v // 1000) if v > 1e12 else int(v)
            try:
                return _utc_ts_to_et_date_str(sec), f"{key}={v}"
            except (ValueError, OSError):
                continue
        s = str(v).strip().replace("Z", "+00:00")
        if not s:
            continue
        try:
            dt = datetime.fromisoformat(s)
            if ZoneInfo:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                et = dt.astimezone(ZoneInfo(_ET))
                return et.strftime("%Y-%m-%d"), f"{key}={v}"
        except (ValueError, TypeError):
            continue
    ts = fill.get("ts")
    if ts is not None:
        try:
            sec = int(ts)
            return _utc_ts_to_et_date_str(sec), f"ts={ts}"
        except (TypeError, ValueError, OSError):
            pass
    return "", ""


def _settlement_et_date_and_label(s: dict) -> tuple[str, str]:
    for key in ("settled_time", "close_timestamp", "created_time", "updated_time"):
        v = s.get(key)
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)):
            sec = int(v // 1000) if v > 1e12 else int(v)
            try:
                return _utc_ts_to_et_date_str(sec), f"{key}={v}"
            except (ValueError, OSError):
                continue
        s2 = str(v).strip().replace("Z", "+00:00")
        if not s2:
            continue
        try:
            dt = datetime.fromisoformat(s2)
            if ZoneInfo:
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
                et = dt.astimezone(ZoneInfo(_ET))
                return et.strftime("%Y-%m-%d"), f"{key}={v}"
        except (ValueError, TypeError):
            continue
    return "", ""


def _ticker_contains(ticker: str, needle: Optional[str]) -> bool:
    if not needle:
        return True
    return needle.upper() in (ticker or "").upper()


def _default_closes_csv_name(target_date: Optional[str], ticker_contains: Optional[str]) -> str:
    safe_needle = ""
    if ticker_contains:
        safe_needle = "".join(c if (c.isalnum() or c in "._-") else "_" for c in ticker_contains.strip())
    if not target_date:
        return f"kalshi_ticker_{safe_needle or 'all'}.csv"
    if not ticker_contains:
        return f"kalshi_closes_{target_date}.csv"
    return f"kalshi_closes_{target_date}_{safe_needle}.csv"


# Spreadsheet-style export (matches trading log / PnL template).
_CLOSES_CSV_FIELDS = [
    "type",
    "quantity",
    "market_ticker",
    "side",
    "entry_price_cents",
    "exit_price_cents",
    "open_fees_cents",
    "close_fees_cents",
    "realized_pnl_cents",
    "realized_pnl_with_fees_cents",
    "close_timestamp",
    "open_timestamp",
]


def _fill_fee_cents(fill: dict) -> int:
    try:
        return int(round(float(fill.get("fee_cost") or 0) * 100))
    except (TypeError, ValueError):
        return 0


def _fill_close_timestamp_str(fill: dict) -> str:
    v = fill.get("close_timestamp") or fill.get("created_time")
    if v is None:
        return ""
    return str(v).strip()


def _csv_row_for_fill_excel(fill: dict, rb: FISH_RABALANCE) -> Dict[str, Any]:
    t = (fill.get("ticker") or "").strip()
    side_raw = (fill.get("side") or "").lower()
    cnt = fill.get("count_fp")
    if cnt is None:
        cnt = fill.get("count")
    if cnt is not None:
        try:
            cf = float(cnt)
            cnt = int(cf) if cf == int(cf) else cf
        except (TypeError, ValueError):
            pass

    fee_c = _fill_fee_cents(fill)
    entry_c: Any = ""
    exit_c: Any = ""
    open_fc: Any = ""
    close_fc: Any = ""

    p = rb._parse_fill_kalshi(fill)
    if p:
        action, side = p["action"], p["side"]
        pc = int(round(p["price"] * 100))
        is_open = (action == "buy" and side == "yes") or (action == "sell" and side == "no")
        is_close = (action == "sell" and side == "yes") or (action == "buy" and side == "no")
        if is_open:
            entry_c = pc
            if fee_c:
                open_fc = fee_c
        elif is_close:
            exit_c = pc
            if fee_c:
                close_fc = fee_c
        else:
            exit_c = pc

    return {
        "type": "fill",
        "quantity": cnt,
        "market_ticker": t,
        "side": side_raw,
        "entry_price_cents": entry_c,
        "exit_price_cents": exit_c,
        "open_fees_cents": open_fc,
        "close_fees_cents": close_fc,
        "realized_pnl_cents": "",
        "realized_pnl_with_fees_cents": "",
        "close_timestamp": _fill_close_timestamp_str(fill),
        "open_timestamp": "",
    }


def _csv_row_for_settlement_excel(s: dict, use_dollars: bool) -> Dict[str, Any]:
    t = (s.get("ticker") or "").strip()
    econ = _settlement_economics_cents(s, use_dollars)

    gross: Any = ""
    net: Any = ""
    entry_c: Any = ""
    exit_c: Any = ""
    fee_cents = 0

    if econ is not None:
        gross_cents, yes_cost_cents, no_cost_cents, fee_cents = econ
        gross = gross_cents - yes_cost_cents - no_cost_cents
        net = gross - fee_cents
        entry_c = yes_cost_cents + no_cost_cents
        exit_c = gross_cents

    qty = s.get("count")
    if qty is None:
        qty = s.get("yes_count")
    if qty is None:
        qty = s.get("no_count")
    if qty is not None:
        try:
            qf = float(qty)
            qty = int(qf) if qf == int(qf) else qf
        except (TypeError, ValueError):
            pass

    close_ts = ""
    for k in ("close_timestamp", "settled_time", "created_time"):
        if s.get(k):
            close_ts = str(s.get(k)).strip()
            break

    return {
        "type": "settlement",
        "quantity": qty if qty is not None else "",
        "market_ticker": t,
        "side": "",
        "entry_price_cents": entry_c,
        "exit_price_cents": exit_c,
        "open_fees_cents": "",
        "close_fees_cents": fee_cents if fee_cents else "",
        "realized_pnl_cents": gross,
        "realized_pnl_with_fees_cents": net,
        "close_timestamp": close_ts,
        "open_timestamp": "",
    }


def export_ticker_csv(
    client,
    ticker_substring: str = "26APR02",
    csv_path: Optional[str] = None,
    lookback_days: int = 180,
) -> Path:
    """All fills + settlements in the lookback window whose ticker contains ticker_substring (case-insensitive)."""
    needle = (ticker_substring or "").strip() or None
    if not needle:
        print("ticker_substring is required", file=sys.stderr)
        sys.exit(1)
    return print_all_closes_on_date(
        client,
        target_date=None,
        lookback_days=lookback_days,
        csv_path=csv_path,
        ticker_contains=needle,
    )


def print_all_closes_on_date(
    client,
    target_date: Optional[str] = None,
    lookback_days: int = 180,
    csv_path: Optional[str] = None,
    ticker_contains: Optional[str] = None,
) -> Path:
    """
    If target_date is set: only rows whose US/Eastern close/settled day equals that date.
    If target_date is None: no calendar filter — only ticker_contains (if set) limits rows.
    """
    if target_date is not None:
        try:
            datetime.strptime(target_date, "%Y-%m-%d")
        except ValueError:
            print("Date must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
        td = datetime.strptime(target_date, "%Y-%m-%d").date()
        min_ts_api = _et_day_start_epoch(td - timedelta(days=lookback_days))
    else:
        td = None
        min_ts_api = _et_day_start_epoch(_et_today() - timedelta(days=lookback_days))
    max_ts = int(datetime.now().timestamp())
    fills = _fetch_all_fills(client, min_ts_api, max_ts)
    settlements = _fetch_all_settlements(client, min_ts_api, max_ts)
    rb = FISH_RABALANCE()
    use_dollars = rb._settlement_use_dollars_flag(settlements)

    out_csv = csv_path if csv_path else _default_closes_csv_name(target_date, ticker_contains)
    csv_rows: List[Dict[str, Any]] = []

    if target_date is not None:
        filt = f"  |  ticker contains {ticker_contains!r} (case-insensitive)" if ticker_contains else ""
        print(
            f"close_date_et == {target_date} (America/New_York){filt}  |  "
            f"scanned fills={len(fills)} settlements={len(settlements)}"
        )
    else:
        print(
            f"ticker contains {ticker_contains!r} (case-insensitive), no calendar filter  |  "
            f"scanned fills={len(fills)} settlements={len(settlements)}"
        )
    print()

    n_f = 0
    for f in fills:
        if not isinstance(f, dict):
            continue
        t = f.get("ticker", "")
        if target_date is not None:
            d_et, src = _fill_close_et_date_and_label(f)
            if d_et != target_date:
                continue
        else:
            src = ""
        if not _ticker_contains(t, ticker_contains):
            continue
        n_f += 1
        cnt = f.get("count_fp") or f.get("count", "")
        print(
            f"FILL  ticker={t}  action={f.get('action')}  side={f.get('side')}  count={cnt}  "
            f"yes=${f.get('yes_price_dollars','')}  no=${f.get('no_price_dollars','')}  "
            f"close_timestamp={f.get('close_timestamp')}  created_time={f.get('created_time')}  ts={f.get('ts')}  ({src})"
        )
        csv_rows.append(_csv_row_for_fill_excel(f, rb))

    n_s = 0
    for s in settlements:
        if not isinstance(s, dict):
            continue
        t = s.get("ticker", "")
        if target_date is not None:
            d_et, src = _settlement_et_date_and_label(s)
            if d_et != target_date:
                continue
        else:
            src = ""
        if not _ticker_contains(t, ticker_contains):
            continue
        n_s += 1
        pnl = rb._settlement_row_pnl_dollars(s, use_dollars)
        print(
            f"SETTLE  ticker={t}  pnl~={pnl}  revenue={s.get('revenue')}  "
            f"yes$={s.get('yes_total_cost_dollars', s.get('yes_total_cost'))}  "
            f"no$={s.get('no_total_cost_dollars', s.get('no_total_cost'))}  "
            f"settled_time={s.get('settled_time')}  close_timestamp={s.get('close_timestamp')}  ({src})"
        )
        csv_rows.append(_csv_row_for_settlement_excel(s, use_dollars))

    out_path = Path(out_csv).expanduser()
    with out_path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=_CLOSES_CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(csv_rows)

    print()
    print(f"total lines: {n_f} fills, {n_s} settlements")
    print(f"CSV: {out_path.resolve()} ({len(csv_rows)} rows)")
    return out_path


def _fetch_all_fills(client, min_ts: int, max_ts: int, limit: int = 200) -> list:
    out = []
    cursor = None
    while True:
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": limit, "subaccount": 0}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(client.portfolio_url + "/fills", params=params)
        chunk = resp.get("fills") or []
        out.extend(chunk)
        cursor = resp.get("cursor")
        if not cursor or not chunk:
            break
    return out


def _fetch_all_settlements(client, min_ts: int, max_ts: int, limit: int = 200) -> list:
    out = []
    cursor = None
    while True:
        params = {"min_ts": min_ts, "max_ts": max_ts, "limit": limit, "subaccount": 0}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(client.portfolio_url + "/settlements", params=params)
        chunk = resp.get("settlements") or []
        out.extend(chunk)
        cursor = resp.get("cursor")
        if not cursor or not chunk:
            break
    return out


def main():
    import argparse
    import os

    from cryptography.hazmat.primitives import serialization
    from dotenv import load_dotenv

    from clients import Environment, KalshiHttpClient

    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Total PnL per city over a fixed US/Eastern calendar window (fills + settlements)."
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="First day of window (inclusive). Default: today minus (--days-1) in US/Eastern.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=6,
        help="Number of calendar days from --since (default 6, e.g. 3/29–4/3)",
    )
    parser.add_argument(
        "--fifo-lookback-days",
        type=int,
        default=120,
        help="How far back to fetch fills + settlements for FIFO and late-settled markets (default 120)",
    )
    parser.add_argument(
        "--bucket",
        choices=("market", "realization"),
        default="market",
        help="market = filter by ticker event date (Excel-style). realization = by fill/settlement day.",
    )
    parser.add_argument(
        "--incentive-pnl",
        type=float,
        default=None,
        metavar="USD",
        help="Add to INCENTIVE row (volume rewards etc. are not in /settlements the same way)",
    )
    parser.add_argument("--demo", action="store_true", help="Use demo keys")
    parser.add_argument(
        "--26APR02",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        dest="apr02_export",
        help="Export CSV: fills+settlements in lookback whose ticker contains 26APR02; optional output path (default kalshi_ticker_26APR02.csv)",
    )
    parser.add_argument(
        "--26APR02-pnl-by-city",
        action="store_true",
        dest="apr02_pnl_by_city",
        help="Weather (KXLOWT/KXHIGH*) + BTC tickers containing 26APR02; use --incentive-pnl for rewards row",
    )
    parser.add_argument(
        "--city-win-rate",
        type=str,
        default=None,
        metavar="CODE",
        help="Print last-N-day win rate for one city (TBOS, MIA, …); uses FIFO + settlement events (US/Eastern days)",
    )
    parser.add_argument(
        "--win-rate-days",
        type=int,
        default=5,
        metavar="N",
        help="Calendar days in the window for --city-win-rate (default 5)",
    )
    parser.add_argument(
        "--win-rate-end",
        type=str,
        default=None,
        metavar="YYYY-MM-DD",
        help="Last day of window for --city-win-rate (US/Eastern); default: today ET",
    )
    args = parser.parse_args()

    env = Environment.DEMO if args.demo else Environment.PROD
    key_id = os.getenv("DEMO_KEYID") if env == Environment.DEMO else os.getenv("PROD_KEYID")
    key_file = os.getenv("DEMO_KEYFILE") if env == Environment.DEMO else os.getenv("PROD_KEYFILE")

    if not key_id or not key_file:
        need = "DEMO_KEYID + DEMO_KEYFILE" if env == Environment.DEMO else "PROD_KEYID + PROD_KEYFILE"
        print(f"Missing {need} in .env (same as fish_trade.py).", file=sys.stderr)
        sys.exit(1)

    try:
        with open(key_file, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)
    except FileNotFoundError:
        print(f"Private key file not found: {key_file}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error loading private key: {e}", file=sys.stderr)
        sys.exit(1)

    client = KalshiHttpClient(key_id=key_id, private_key=private_key, environment=env)

    if args.city_win_rate:
        code = (args.city_win_rate or "").strip()
        if not code:
            print("--city-win-rate requires a city code", file=sys.stderr)
            sys.exit(1)
        nwr = max(1, int(args.win_rate_days))
        end_d = None
        if args.win_rate_end:
            try:
                end_d = datetime.strptime(args.win_rate_end.strip(), "%Y-%m-%d").date()
            except ValueError:
                print("--win-rate-end must be YYYY-MM-DD", file=sys.stderr)
                sys.exit(1)
        lb = max(args.fifo_lookback_days, nwr + 60)
        min_ts_api = _et_day_start_epoch(_et_today() - timedelta(days=lb))
        max_ts = int(datetime.now().timestamp())
        fills = _fetch_all_fills(client, min_ts_api, max_ts)
        settlements = _fetch_all_settlements(client, min_ts_api, max_ts)
        rb = FISH_RABALANCE()
        rb.get_filled_orders({"fills": fills})
        rb.get_settled_orders({"settlements": settlements})
        stats = city_win_rate_last_n_days(rb, code, nwr, end_date=end_d)
        env_label = "DEMO" if env == Environment.DEMO else "PROD"
        wr = stats["win_rate"]
        wr_s = f"{100.0 * wr:.1f}%" if wr is not None else "n/a (no win/loss days)"
        print(f"{env_label}  |  city_win_rate  |  fills={len(fills)}  settlements={len(settlements)}")
        print(f"city={stats['city']}  window {stats['window_start']} .. {stats['window_end']}  ({stats['days_in_window']} ET days)")
        print(f"  wins={stats['wins']}  losses={stats['losses']}  ties={stats['ties']}  no_trade_days={stats['no_trade_days']}")
        print(f"  win_rate = wins / (wins+losses) = {wr_s}")
        return

    if args.apr02_pnl_by_city:
        lb = max(args.fifo_lookback_days, 180)
        min_ts_api = _et_day_start_epoch(_et_today() - timedelta(days=lb))
        max_ts = int(datetime.now().timestamp())
        fills = _fetch_all_fills(client, min_ts_api, max_ts)
        settlements = _fetch_all_settlements(client, min_ts_api, max_ts)
        rb = FISH_RABALANCE()
        rb.get_filled_orders({"fills": fills})
        rb.get_settled_orders({"settlements": settlements})
        rows = pnl_by_city_ticker_substring(rb, "26APR02", ticker_filter=ticker_in_weather_or_btc_book)
        if args.incentive_pnl is not None:
            inc_by: Dict[str, float] = defaultdict(float)
            for c, p in rows:
                inc_by[c] += p
            inc_by["INCENTIVE"] += float(args.incentive_pnl)
            rows = sorted(inc_by.items(), key=lambda x: (_ORDER_IDX.get(x[0], 10_000), x[0]))
            rows = [(c, round(p, 4)) for c, p in rows]
        env_label = "DEMO" if env == Environment.DEMO else "PROD"
        _print_pnl_by_city_rows(
            rows,
            f"{env_label}  |  *26APR02* weather+BTC  |  FIFO + settlements  |  fills={len(fills)} settlements={len(settlements)}",
        )
        return

    if args.apr02_export is not None:
        out = args.apr02_export if args.apr02_export else None
        export_ticker_csv(
            client,
            ticker_substring="26APR02",
            csv_path=out,
            lookback_days=max(args.fifo_lookback_days, 180),
        )
        return

    if args.since:
        try:
            start_d = datetime.strptime(args.since, "%Y-%m-%d").date()
        except ValueError:
            print("--since must be YYYY-MM-DD", file=sys.stderr)
            sys.exit(1)
    else:
        start_d = _et_today() - timedelta(days=args.days - 1)
    end_d = start_d + timedelta(days=args.days - 1)

    start_s = start_d.strftime("%Y-%m-%d")
    end_s = end_d.strftime("%Y-%m-%d")

    max_ts = int(datetime.now().timestamp())
    lookback_start = start_d - timedelta(days=args.fifo_lookback_days)
    min_ts_api = _et_day_start_epoch(lookback_start)

    # Same wide window for fills and settlements: markets that *settle* after your date range still need to be pulled.
    fills = _fetch_all_fills(client, min_ts_api, max_ts)
    settlements = _fetch_all_settlements(client, min_ts_api, max_ts)

    rb = FISH_RABALANCE()
    rb.get_filled_orders({"fills": fills})
    rb.get_settled_orders({"settlements": settlements})
    rb.aggregate_pnl(display_date_start=start_s, display_date_end=end_s, bucket=args.bucket)

    if args.incentive_pnl is not None:
        adj = list(rb.city_totals)
        found = False
        for i, (c, p) in enumerate(adj):
            if c == "INCENTIVE":
                adj[i] = (c, round(p + args.incentive_pnl, 4))
                found = True
                break
        if not found:
            adj.append(("INCENTIVE", round(args.incentive_pnl, 4)))
        rb.city_totals = _sort_city_totals(adj)

    env_label = "DEMO" if env == Environment.DEMO else "PROD"
    print(f"{env_label}  |  window {start_s} .. {end_s}  ({args.days} days)  |  bucket={args.bucket}")
    print(f"API lookback from {lookback_start}  |  fills={len(fills)}  settlements={len(settlements)}")
    print()
    print(f"{'City':<12} {'Total PnL ($)':>14}")
    print("-" * 28)
    grand = 0.0
    for city, pnl in rb.city_totals:
        grand += pnl
        print(f"{city:<12} {_fmt_money(pnl):>14}")
    print("-" * 28)
    print(f"{'ALL':<12} {_fmt_money(grand):>14}")


if __name__ == "__main__":
    main()
