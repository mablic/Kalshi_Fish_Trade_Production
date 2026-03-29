"""
fish_tester.py - Test fish_trade.py without calling the Kalshi API.

Strategy times (from fish_trade_time):
  Today: 10AM start tmr low+high, 4PM stop tmr low, 8PM stop tmr high
  Next day: 0AM start today low, 2-4AM close low (stage 1-3), 5-7AM close tmr high, 8AM start today high, 9-11AM close today high
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
from datetime import datetime, timedelta
from freezegun import freeze_time
from fish_trade import FISH_TRADE
from fish_orders import FISH_ORDERS, FISH_ORDERS_MANAGER, PNL_LOG_FILE, STATE_FILE, ensure_pnl_csv_exists


# --- Mock data ---

TEST_TICKER = "KXLOWTCHI-26MAR02-B21.5"
TEST_TICKER_HIGH = "KXHIGHPHIL-26MAR02-B35.5"
TEST_ORDER_ID = "test-order-123"
TEST_SELL_ORDER_ID = "test-sell-order-456"

MOCK_ORDER_BOOK = {
    "orderbook": {
        "yes_dollars": [["0.05", 500], ["0.06", 300], ["0.07", 200], ["0.08", 150], ["0.09", 100]],
        "no_dollars": [["0.91", 100], ["0.92", 150]],
    }
}

MOCK_ORDER_BOOK_SELL = {
    "orderbook": {
        "yes_dollars": [["0.15", 100], ["0.18", 150]],
        "no_dollars": [["0.82", 100], ["0.85", 150]],
    }
}

# Order book with 0.01 bid on YES (stage 3 should respond with this price)
MOCK_ORDER_BOOK_01_BID = {
    "orderbook": {
        "yes_dollars": [["0.01", 500], ["0.02", 300], ["0.03", 200]],
        "no_dollars": [],
    }
} 


class MockKalshiClient:
    """Simulates Kalshi API. Returns staged data - no real API calls."""

    def __init__(self, test_data: dict):
        self.test_data = test_data
        self.fills_idx = 0
        self.orders_idx = 0
        self.create_idx = 0
        self.placed_orders = []
        self.cancel_404_tickers = set()

    def get_fills(self, min_ts=None):
        fills = self.test_data.get("fills_queue", [[]])
        idx = min(self.fills_idx, len(fills) - 1)
        result = fills[idx] if idx < len(fills) else []
        self.fills_idx += 1
        return {"fills": result}

    def get_open_orders(self):
        orders = self.test_data.get("open_orders_queue", [[]])
        idx = min(self.orders_idx, len(orders) - 1)
        result = orders[idx] if idx < len(orders) else []
        self.orders_idx += 1
        return {"orders": result}

    def create_open_order(self, ticker, side, action, count, type_, yes_price_dollars, no_price_dollars=None, time_in_force=None):
        responses = self.test_data.get("create_order_responses", [])
        idx = min(self.create_idx, len(responses) - 1)
        resp = responses[idx] if idx < len(responses) else {"order": {"order_id": f"mock-order-{self.create_idx}"}}
        self.create_idx += 1
        self.placed_orders.append({"ticker": ticker, "side": side, "action": action, "price": yes_price_dollars})
        return resp

    def cancel_open_order(self, order_id):
        if self.test_data.get("cancel_returns_404"):
            from requests.exceptions import HTTPError
            resp = type('Resp', (), {'status_code': 404})()
            raise HTTPError("404 Not Found", response=resp)

    def get_market_ticker_order_book(self, ticker):
        return self.test_data.get("order_books", {}).get(ticker, MOCK_ORDER_BOOK)

    def get_markets_by_series(self, series_ticker=None, event_ticker=None, status=None, limit=1000, fetch_all=True):
        return {"markets": [{"ticker": TEST_TICKER}], "cursor": None}


class MockParseWeather:
    def get_all_weather(self):
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "CHI": {today: {"forecast": [21, 28]}, tomorrow: {"forecast": [21, 28]}},
        }


class MockMarketTicker:
    def __init__(self):
        self._trade_time = None

    def set_reference_trade_time(self, trade_time):
        self._trade_time = trade_time

    def _extract_date_from_ticker(self, ticker):
        try:
            parts = ticker.upper().split("-")
            if len(parts) >= 2 and len(parts[1]) >= 6:
                return parts[1]
        except Exception:
            pass
        return None

    def get_tickers_for_date(self, client, city_code, date_str, weather_range=None, ticker_type=None):
        return [TEST_TICKER]

    def ticker_to_symbol(self, ticker):
        return ticker

    def get_market_datetime_from_ticker(self, ticker, hour=0):
        if "26MAR02" in ticker:
            return datetime(2026, 3, 2, hour, 0, 0)
        return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)

    def is_today_low_ticker(self, ticker):
        if "KXLOWT" not in ticker.upper():
            return False
        td = self._extract_date_from_ticker(ticker)
        return td and self._trade_time and td == self._trade_time.get_today_date_formatted()

    def is_today_high_ticker(self, ticker):
        if "KXHIGH" not in ticker.upper():
            return False
        td = self._extract_date_from_ticker(ticker)
        return td and self._trade_time and td == self._trade_time.get_today_date_formatted()

    def is_tomorrow_low_ticker(self, ticker):
        if "KXLOWT" not in ticker.upper():
            return False
        td = self._extract_date_from_ticker(ticker)
        return td and self._trade_time and td == self._trade_time.get_tomorrow_date_formatted()

    def is_tomorrow_high_ticker(self, ticker):
        if "KXHIGH" not in ticker.upper():
            return False
        td = self._extract_date_from_ticker(ticker)
        return td and self._trade_time and td == self._trade_time.get_tomorrow_date_formatted()


class MockTradeTime:
    """Mock FISH_TRADE_TIME for tests. today_date/tomorrow_date control ticker matching."""
    def __init__(self, **kwargs):
        self._start_today_low = kwargs.get("start_today_low", False)
        self._start_today_high = kwargs.get("start_today_high", False)
        self._start_tomorrow_low = kwargs.get("start_tomorrow_low", False)
        self._start_tomorrow_high = kwargs.get("start_tomorrow_high", False)
        self._stop_today_low = kwargs.get("stop_today_low", False)
        self._stop_today_high = kwargs.get("stop_today_high", False)
        self._stop_tomorrow_low = kwargs.get("stop_tomorrow_low", False)
        self._stop_tomorrow_high = kwargs.get("stop_tomorrow_high", False)
        self._used = {k: False for k in ["today_low", "today_high", "tomorrow_low", "tomorrow_high"]}
        self._close_stage_low = kwargs.get("close_stage_low", 0)
        self._close_stage_high = kwargs.get("close_stage_high", 0)
        self._today_date = kwargs.get("today_date", "26MAR02")
        self._tomorrow_date = kwargs.get("tomorrow_date", "26MAR03")

    def is_today_low_start_trade_time(self):
        if self._start_today_low and not self._used["today_low"]:
            self._used["today_low"] = True
            return True
        return False

    def is_today_high_start_trade_time(self):
        if self._start_today_high and not self._used["today_high"]:
            self._used["today_high"] = True
            return True
        return False

    def is_tomorrow_low_start_trade_time(self):
        if self._start_tomorrow_low and not self._used["tomorrow_low"]:
            self._used["tomorrow_low"] = True
            return True
        return False

    def is_tomorrow_high_start_trade_time(self):
        if self._start_tomorrow_high and not self._used["tomorrow_high"]:
            self._used["tomorrow_high"] = True
            return True
        return False

    def is_today_low_stop_trade_time(self):
        return self._stop_today_low

    def is_today_high_stop_trade_time(self):
        return self._stop_today_high

    def is_tomorrow_low_stop_trade_time(self):
        return self._stop_tomorrow_low

    def is_tomorrow_high_stop_trade_time(self):
        return self._stop_tomorrow_high

    def get_close_stage_for_low(self):
        return self._close_stage_low

    def get_close_stage_for_high(self):
        return self._close_stage_high

    @property
    def today_low_determine_time(self):
        return 4

    @property
    def today_high_determine_time(self):
        return 11

    @property
    def tomorrow_low_determine_time(self):
        return 4

    @property
    def tomorrow_high_determine_time(self):
        return 7

    def get_today_date_formatted(self):
        return self._today_date

    def get_tomorrow_date_formatted(self):
        return self._tomorrow_date

    def update_dates(self, today_str, tomorrow_str):
        pass


def _reset_singletons():
    mgr = FISH_ORDERS_MANAGER()
    mgr.open_buy_orders.clear()
    mgr.open_sell_orders.clear()
    mgr.filled_orders.clear()
    mgr.settled_orders.clear()
    mgr.fish_orders.clear()
    mgr.executed_sell_orders.clear()
    mgr.placed_order_ids.clear()
    mgr.processed_fill_ids.clear()


def _make_fish_trade(test_data, mock_trade_time):
    site_dict = {"CHI": ["http://example.com"]}
    return FISH_TRADE(
        client=None,
        site_dict=site_dict,
        test_mode=True,
        test_client=MockKalshiClient(test_data),
        test_parse_weather=MockParseWeather(),
        test_market_ticker=MockMarketTicker(),
        test_trade_time=mock_trade_time,
    )


# --- Test 1: Full trade lifecycle ---
def run_test_1():
    """Open trade -> Buy filled -> Create sell -> Price update -> Sell filled -> Complete."""
    print("\n" + "=" * 60)
    print("TEST 1: Full trade lifecycle")
    print("=" * 60)
    _reset_singletons()

    now = datetime.now()
    buy_fill = {
        "order_id": TEST_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": now.isoformat() + "Z",
        "action": "buy", "side": "yes", "count": 1,
        "yes_price_fixed": 0.09, "no_price_fixed": 0.91,
    }
    sell_fill = {
        "order_id": TEST_SELL_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": (now + timedelta(hours=2)).isoformat() + "Z",
        "action": "sell", "side": "yes", "count": 1,
        "yes_price_fixed": 0.18, "no_price_fixed": 0.82,
    }

    test_data = {
        "fills_queue": [[buy_fill], [sell_fill]],
        "open_orders_queue": [[], []],
        "create_order_responses": [{"order": {"order_id": TEST_ORDER_ID}}, {"order": {"order_id": TEST_SELL_ORDER_ID}}],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
    }

    t = _make_fish_trade(test_data, MockTradeTime(start_tomorrow_low=True))
    t.log_file = "logs/fish_tester.log"

    t.create_fish_buy_order()
    assert len(t.orders_manager.get_open_buy_orders()) > 0

    t.get_fills()
    assert TEST_TICKER in t.orders_manager.filled_orders

    t.create_fish_sell_order()
    assert len(t.orders_manager.get_open_sell_orders()) > 0

    t.get_fills()
    assert TEST_TICKER in t.orders_manager.settled_orders
    assert TEST_TICKER not in t.orders_manager.filled_orders

    print("  PASSED\n")


# --- Test 2: Cancel returns 404 - order already gone ---
def run_test_2():
    """Cancel order when order already filled/cancelled (404). Should log and continue, not raise."""
    print("\n" + "=" * 60)
    print("TEST 2: Cancel returns 404 - order already gone")
    print("=" * 60)
    _reset_singletons()

    test_data = {
        "fills_queue": [[]],
        "open_orders_queue": [[]],
        "create_order_responses": [],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
        "cancel_returns_404": True,
    }

    t = _make_fish_trade(test_data, MockTradeTime(stop_tomorrow_low=True, today_date="26MAR01", tomorrow_date="26MAR02"))
    t.log_file = "logs/fish_tester.log"

    t.orders_manager.create_fish_buy_order(TEST_TICKER, 0.09)
    order = t.orders_manager.open_buy_orders[TEST_TICKER]
    order.order_id = "fake-order-id"
    order.order_execution_type = "pending"

    t.check_outstanding_orders()
    assert TEST_TICKER not in t.orders_manager.open_buy_orders
    print("  PASSED (404 handled, no raise)\n")


# --- Test 3: 10:00 open tmr trade (low + high) ---
def run_test_3():
    """At 10:00, open both tomorrow low and high trades."""
    print("\n" + "=" * 60)
    print("TEST 3: 10:00 open tmr trade (low + high)")
    print("=" * 60)
    _reset_singletons()

    test_data = {
        "fills_queue": [[]],
        "open_orders_queue": [[]],
        "create_order_responses": [{"order": {"order_id": f"mock-{i}"}} for i in range(5)],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
    }

    t = _make_fish_trade(test_data, MockTradeTime(start_tomorrow_low=True, start_tomorrow_high=True))
    t.log_file = "logs/fish_tester.log"

    t.create_fish_buy_order()
    open_buys = t.orders_manager.get_open_buy_orders()
    assert len(open_buys) > 0
    print(f"  Created {len(open_buys)} buy orders: {list(open_buys.keys())}")
    print("  PASSED\n")


# --- Test 4: 8:00 open today high trade ---
def run_test_4():
    """At 8:00, open today high trade."""
    print("\n" + "=" * 60)
    print("TEST 4: 8:00 open today high trade")
    print("=" * 60)
    _reset_singletons()

    test_data = {
        "fills_queue": [[]],
        "open_orders_queue": [[]],
        "create_order_responses": [{"order": {"order_id": "mock-1"}}],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
    }

    t = _make_fish_trade(test_data, MockTradeTime(start_today_high=True))
    t.log_file = "logs/fish_tester.log"

    t.create_fish_buy_order()
    open_buys = t.orders_manager.get_open_buy_orders()
    assert len(open_buys) > 0
    print(f"  Created {len(open_buys)} buy orders: {list(open_buys.keys())}")
    print("  PASSED\n")


# --- Test 5: Sell price update every hour (fish_price_strategy) ---
def run_test_5():
    """When in stop_trade_time, sell orders get price strategy check. Uses market date from ticker for escape_time."""
    print("\n" + "=" * 60)
    print("TEST 5: Sell price update (fish_price_strategy)")
    print("=" * 60)
    _reset_singletons()

    test_data = {
        "fills_queue": [[]],
        "open_orders_queue": [[]],
        "create_order_responses": [],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK_SELL},
    }

    # close_stage_low=2: (price_above + lowest_ask) / 2. price_above = stage 1 result = (0.18+0.09)/2=0.135
    # today_date=26MAR02 so ticker KXLOWTCHI-26MAR02 is today_low
    t = _make_fish_trade(test_data, MockTradeTime(close_stage_low=2, today_date="26MAR02", tomorrow_date="26MAR03"))
    t.log_file = "logs/fish_tester.log"

    from fish_orders import FISH_ORDERS
    # Pre-set price to stage 1 result (0.135) to simulate we ran stage 1 at 2AM
    sell_order = FISH_ORDERS(
        order_id="", ticker=TEST_TICKER, symbol=TEST_TICKER,
        order_date="2026-03-02",
        order_type="open", order_execution_type="new",
        action="sell", side="yes", quantity=1, remaining_quantity=1,
        entry_price=0.09, price=0.135,  # stage 1 result
        created_at="2026-03-02T00:00:00", last_updated_at=None,
        trade_type="fish_order",
    )
    t.orders_manager.open_sell_orders[TEST_TICKER] = sell_order
    t.orders_manager.fish_orders[TEST_TICKER] = sell_order

    old_price = sell_order.price
    t.check_outstanding_orders()
    new_order = t.orders_manager.open_sell_orders.get(TEST_TICKER)
    assert new_order is not None, "Sell order should still exist"
    assert new_order.price != old_price, f"Price should update from {old_price} (stage 2)"
    # stage2 = (0.14 + 0.15) / 2 = 0.145 (FISH_ORDERS rounds to 2 decimals, so 0.135 -> 0.14)
    assert new_order.price == 0.145, f"Stage 2: (0.14+0.15)/2=0.145, got {new_order.price}"
    print(f"  Sell price: {old_price} -> {new_order.price} (stage 2: (stage1_result+lowest_ask)/2)")
    print("  PASSED\n")


# --- Test 5b: Order book with 0.01 YES bid -> response price ---
def run_test_5b():
    """Pass order book with 0.01 bid on YES; run stage 3 and print response price."""
    print("\n" + "=" * 60)
    print("TEST 5b: Order book 0.01 YES bid -> response price")
    print("=" * 60)
    _reset_singletons()

    test_data = {
        "fills_queue": [[]],
        "open_orders_queue": [[]],
        "create_order_responses": [],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK_01_BID},
    }

    # Stage 3: trade_price = lowest_ask (from YES book only when no NO used)
    t = _make_fish_trade(test_data, MockTradeTime(close_stage_low=3, today_date="26MAR02", tomorrow_date="26MAR03"))
    t.log_file = "logs/fish_tester.log"

    from fish_orders import FISH_ORDERS
    sell_order = FISH_ORDERS(
        order_id="", ticker=TEST_TICKER, symbol=TEST_TICKER,
        order_date="2026-03-02",
        order_type="open", order_execution_type="new",
        action="sell", side="yes", quantity=1, remaining_quantity=1,
        entry_price=0.09, price=0.15,
        created_at="2026-03-02T00:00:00", last_updated_at=None,
        trade_type="fish_order",
    )
    t.orders_manager.open_sell_orders[TEST_TICKER] = sell_order
    t.orders_manager.fish_orders[TEST_TICKER] = sell_order

    old_price = sell_order.price
    t.check_outstanding_orders()
    new_order = t.orders_manager.open_sell_orders.get(TEST_TICKER)
    response_price = new_order.price if new_order else None

    print(f"  Order book: yes_dollars = [0.01, 0.02, 0.03] (0.01 bid on YES)")
    print(f"  Stage 3: sell price {old_price} -> response price: {response_price}")
    print("  PASSED\n")


# --- Test 6: Sell fill logs realized PnL ---
def run_test_6():
    """When sell order fills, log realized PnL to fish_pnl.log."""
    print("\n" + "=" * 60)
    print("TEST 6: Sell fill -> Realized PnL logged")
    print("=" * 60)
    _reset_singletons()

    # Clear pnl log for clean output
    with open(PNL_LOG_FILE, "w") as f:
        f.write("")

    now = datetime.now()
    buy_fill = {
        "order_id": TEST_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": now.isoformat() + "Z",
        "action": "buy", "side": "yes", "count": 1,
        "yes_price_fixed": 0.09, "no_price_fixed": 0.91,
    }
    sell_fill = {
        "order_id": TEST_SELL_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": (now + timedelta(hours=2)).isoformat() + "Z",
        "action": "sell", "side": "yes", "count": 1,
        "yes_price_fixed": 0.18, "no_price_fixed": 0.82,
    }

    test_data = {
        "fills_queue": [[buy_fill], [sell_fill]],
        "open_orders_queue": [[], []],
        "create_order_responses": [{"order": {"order_id": TEST_ORDER_ID}}, {"order": {"order_id": TEST_SELL_ORDER_ID}}],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
    }

    t = _make_fish_trade(test_data, MockTradeTime(start_tomorrow_low=True))
    t.log_file = "logs/fish_tester.log"
    t.orders_manager.pnl_log_file = PNL_LOG_FILE

    t.create_fish_buy_order()
    t.get_fills()
    t.create_fish_sell_order()
    t.get_fills()

    with open(PNL_LOG_FILE) as f:
        pnl_content = f.read()

    assert "datetime,city,ticker,qty,entry_price,exit_price,pnl" in pnl_content, "Expected CSV header"
    assert "0.09" in pnl_content and "0.18" in pnl_content
    assert "CHI" in pnl_content
    print(f"  PnL logged to {PNL_LOG_FILE}:")
    for line in pnl_content.strip().split("\n"):
        print(f"    {line}")
    print("  PASSED\n")


# --- Test 6b: Partial sell (100 qty, close 50) ---
def run_test_6b():
    """Partial sell: buy 100, sell 50. PnL = (sell - buy) * 50."""
    print("\n" + "=" * 60)
    print("TEST 6b: Partial sell (100 qty, close 50)")
    print("=" * 60)
    _reset_singletons()

    with open(PNL_LOG_FILE, "w") as f:
        f.write("")

    now = datetime.now()
    buy_fill = {
        "order_id": TEST_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": now.isoformat() + "Z",
        "action": "buy", "side": "yes", "count": 100,
        "yes_price_fixed": 0.09, "no_price_fixed": 0.91,
    }
    sell_fill = {
        "order_id": TEST_SELL_ORDER_ID,
        "ticker": TEST_TICKER,
        "created_time": (now + timedelta(hours=2)).isoformat() + "Z",
        "action": "sell", "side": "yes", "count": 50,
        "yes_price_fixed": 0.18, "no_price_fixed": 0.82,
    }

    test_data = {
        "fills_queue": [[buy_fill], [sell_fill]],
        "open_orders_queue": [[], []],
        "create_order_responses": [{"order": {"order_id": TEST_ORDER_ID}}, {"order": {"order_id": TEST_SELL_ORDER_ID}}],
        "order_books": {TEST_TICKER: MOCK_ORDER_BOOK},
    }

    t = _make_fish_trade(test_data, MockTradeTime(start_tomorrow_low=True))
    t.log_file = "logs/fish_tester.log"
    t.orders_manager.pnl_log_file = PNL_LOG_FILE

    t.create_fish_buy_order()
    t.get_fills()
    assert t.orders_manager.filled_orders[TEST_TICKER].quantity == 100
    t.create_fish_sell_order()
    t.get_fills()
    assert t.orders_manager.filled_orders[TEST_TICKER].quantity == 50

    with open(PNL_LOG_FILE) as f:
        pnl_content = f.read()

    assert ",50," in pnl_content  # qty column
    assert "4.5" in pnl_content  # (0.18 - 0.09) * 50 = 4.50
    print(f"  PnL logged (partial 50 of 100):")
    for line in pnl_content.strip().split("\n"):
        print(f"    {line}")
    print("  PASSED\n")


# --- Test 7: Midnight expiry logs negative PnL ---
def run_test_7():
    """At midnight, open sell orders for expired markets log negative PnL."""
    print("\n" + "=" * 60)
    print("TEST 7: Midnight expiry -> Expired PnL logged")
    print("=" * 60)
    _reset_singletons()

    from fish_orders import FISH_ORDERS

    # Create open sell order for market 26MAR02 (expires when today is 2026-03-03)
    sell_order = FISH_ORDERS(
        order_id="", ticker=TEST_TICKER, symbol=TEST_TICKER,
        order_date="2026-03-02",
        order_type="open", order_execution_type="new",
        action="sell", side="yes", quantity=1, remaining_quantity=1,
        entry_price=0.09, price=0.18,
        created_at="2026-03-02T00:00:00", last_updated_at=None,
        trade_type="fish_order",
    )
    mgr = FISH_ORDERS_MANAGER()
    mgr.open_sell_orders[TEST_TICKER] = sell_order
    mgr.fish_orders[TEST_TICKER] = sell_order
    mgr.pnl_log_file = PNL_LOG_FILE

    # At 2026-03-03 00:00, market 26MAR02 has expired
    with freeze_time("2026-03-03 00:05:00"):
        mgr.process_midnight_expiry("2026-03-03")

    assert TEST_TICKER not in mgr.open_sell_orders, "Expired order should be removed"

    with open(PNL_LOG_FILE) as f:
        pnl_content = f.read()

    assert "-0.09" in pnl_content, "Expected negative PnL for expired"
    print(f"  Expired PnL logged to {PNL_LOG_FILE}:")
    for line in pnl_content.strip().split("\n")[-10:]:
        print(f"    {line}")
    print("  PASSED\n")


# --- Test 8: ensure_pnl_csv_exists creates file with header ---
def run_test_8():
    """ensure_pnl_csv_exists creates fish_pnl.csv with header if it doesn't exist."""
    print("\n" + "=" * 60)
    print("TEST 8: ensure_pnl_csv_exists creates file with header")
    print("=" * 60)

    import tempfile
    test_pnl = os.path.join(tempfile.gettempdir(), "fish_tester_pnl.csv")
    if os.path.exists(test_pnl):
        os.remove(test_pnl)

    ensure_pnl_csv_exists(test_pnl)
    assert os.path.exists(test_pnl), f"Expected {test_pnl} to exist"
    with open(test_pnl) as f:
        content = f.read()
    assert "datetime,city,ticker,qty,entry_price,exit_price,pnl" in content, "Expected CSV header"
    print(f"  Created {test_pnl} with header")
    print("  PASSED\n")
    os.remove(test_pnl)


# --- Test 9: save_state / load_state persistence ---
def run_test_9():
    """save_state and load_state persist/restore open and filled orders."""
    print("\n" + "=" * 60)
    print("TEST 9: save_state / load_state persistence")
    print("=" * 60)
    _reset_singletons()

    import tempfile
    test_state = os.path.join(tempfile.gettempdir(), "fish_tester_state.json")
    mgr = FISH_ORDERS_MANAGER()
    mgr.state_file = test_state
    if os.path.exists(test_state):
        os.remove(test_state)

    # Add open buy and filled order
    buy_order = FISH_ORDERS(
        order_id="ord-1", ticker=TEST_TICKER, symbol=TEST_TICKER,
        order_date="2026-03-02", order_type="open", order_execution_type="pending",
        action="buy", side="yes", quantity=100, remaining_quantity=50,
        entry_price=0.09, price=0.09, created_at="2026-03-02T10:00:00",
        last_updated_at=None, trade_type="fish_order",
    )
    mgr.open_buy_orders[TEST_TICKER] = buy_order
    mgr.fish_orders[TEST_TICKER] = buy_order

    filled_order = FISH_ORDERS(
        order_id="ord-1", ticker=TEST_TICKER, symbol=TEST_TICKER,
        order_date="2026-03-02", order_type="fill", order_execution_type="",
        action="buy", side="yes", quantity=50, remaining_quantity=50,
        entry_price=0.09, price=0.09, created_at="2026-03-02T10:00:00",
        last_updated_at=None, trade_type="fish_order",
    )
    mgr.filled_orders[TEST_TICKER] = filled_order
    mgr.placed_order_ids.add("ord-1")
    mgr.processed_fill_ids.add("fill-1")

    mgr.save_state()
    assert os.path.exists(test_state), f"Expected {test_state} to exist"

    # Reset and load
    mgr.open_buy_orders.clear()
    mgr.open_sell_orders.clear()
    mgr.filled_orders.clear()
    mgr.fish_orders.clear()
    mgr.placed_order_ids.clear()
    mgr.processed_fill_ids.clear()

    mgr.load_state()
    assert TEST_TICKER in mgr.open_buy_orders, "Open buy order should be restored"
    assert TEST_TICKER in mgr.filled_orders, "Filled order should be restored"
    assert mgr.open_buy_orders[TEST_TICKER].quantity == 100
    assert mgr.filled_orders[TEST_TICKER].quantity == 50
    assert "ord-1" in mgr.placed_order_ids
    assert "fill-1" in mgr.processed_fill_ids

    print(f"  Saved and restored state from {test_state}")
    print("  PASSED\n")
    os.remove(test_state)


if __name__ == "__main__":
    run_test_1()
    run_test_2()
    run_test_3()
    run_test_4()
    run_test_5()
    run_test_5b()
    run_test_6()
    run_test_6b()
    run_test_7()
    run_test_8()
    run_test_9()
    print("=" * 60)
    print("ALL 10 TESTS PASSED")
    print("=" * 60)
