"""
test_fish_strategy.py - Unit tests for fish_trade_time and fish_price_strategy.

Run from project root: python -m pytest test/test_fish_strategy.py -v
Or:  python test/test_fish_strategy.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
from freezegun import freeze_time

from fish_trade_time import FISH_TRADE_TIME
from fish_price_strategy import FISH_PRICE_STRATEGY


# --- fish_trade_time tests ---

def _get_trade_time():
    """Get fresh instance (singleton)."""
    return FISH_TRADE_TIME()


def test_trade_time_start_tomorrow_low_at_10am():
    """10AM: start tomorrow low."""
    tt = _get_trade_time()
    tt.reset_start_trade_time()
    with freeze_time("2026-03-01 10:00:00"):
        assert tt.is_tomorrow_low_start_trade_time() is True
    with freeze_time("2026-03-01 10:01:00"):
        assert tt.is_tomorrow_low_start_trade_time() is False  # one-shot


def test_trade_time_stop_tomorrow_low_at_4pm():
    """4PM: stop tomorrow low."""
    tt = _get_trade_time()
    with freeze_time("2026-03-01 16:00:00"):
        assert tt.is_tomorrow_low_stop_trade_time() is True
    with freeze_time("2026-03-01 15:59:00"):
        assert tt.is_tomorrow_low_stop_trade_time() is False


def test_trade_time_stop_tomorrow_high_at_8pm():
    """8PM: stop tomorrow high."""
    tt = _get_trade_time()
    with freeze_time("2026-03-01 20:00:00"):
        assert tt.is_tomorrow_high_stop_trade_time() is True
    with freeze_time("2026-03-01 19:59:00"):
        assert tt.is_tomorrow_high_stop_trade_time() is False


def test_trade_time_start_today_low_at_0am():
    """0AM: start today low."""
    tt = _get_trade_time()
    tt.reset_start_trade_time()
    with freeze_time("2026-03-02 00:00:00"):
        assert tt.is_today_low_start_trade_time() is True
    with freeze_time("2026-03-02 00:01:00"):
        assert tt.is_today_low_start_trade_time() is False


def test_trade_time_close_stage_low_2am_3am_4am():
    """2AM=stage1, 3AM=stage2, 4AM=stage3 for low."""
    tt = _get_trade_time()
    with freeze_time("2026-03-02 01:00:00"):
        assert tt.get_close_stage_for_low() == 0
    with freeze_time("2026-03-02 02:00:00"):
        assert tt.get_close_stage_for_low() == 1
    with freeze_time("2026-03-02 03:00:00"):
        assert tt.get_close_stage_for_low() == 2
    with freeze_time("2026-03-02 04:00:00"):
        assert tt.get_close_stage_for_low() == 3
    with freeze_time("2026-03-02 05:00:00"):
        assert tt.get_close_stage_for_low() == 3  # still 3 after window


def test_trade_time_close_stage_tmr_high_5am_6am_7am():
    """5AM=stage1, 6AM=stage2, 7AM=stage3 for tmr high."""
    tt = _get_trade_time()
    with freeze_time("2026-03-02 04:00:00"):
        assert tt.get_close_stage_for_tmr_high() == 0
    with freeze_time("2026-03-02 05:00:00"):
        assert tt.get_close_stage_for_tmr_high() == 1
    with freeze_time("2026-03-02 06:00:00"):
        assert tt.get_close_stage_for_tmr_high() == 2
    with freeze_time("2026-03-02 07:00:00"):
        assert tt.get_close_stage_for_tmr_high() == 3


def test_trade_time_close_stage_today_high_9am_10am_11am():
    """9AM=stage1, 10AM=stage2, 11AM=stage3 for today high."""
    tt = _get_trade_time()
    with freeze_time("2026-03-02 08:00:00"):
        assert tt.get_close_stage_for_today_high() == 0
    with freeze_time("2026-03-02 09:00:00"):
        assert tt.get_close_stage_for_today_high() == 1
    with freeze_time("2026-03-02 10:00:00"):
        assert tt.get_close_stage_for_today_high() == 2
    with freeze_time("2026-03-02 11:00:00"):
        assert tt.get_close_stage_for_today_high() == 3


def test_trade_time_start_today_high_at_8am():
    """8AM: start today high."""
    tt = _get_trade_time()
    tt.reset_start_trade_time()
    with freeze_time("2026-03-02 08:00:00"):
        assert tt.is_today_high_start_trade_time() is True
    with freeze_time("2026-03-02 08:01:00"):
        assert tt.is_today_high_start_trade_time() is False


def test_trade_time_reset_on_date_roll():
    """When date rolls (midnight), reset start flags so we can start again."""
    tt = _get_trade_time()
    tt.update_dates("2026-03-01", "2026-03-02")
    with freeze_time("2026-03-01 10:00:00"):
        assert tt.is_tomorrow_low_start_trade_time() is True  # consumes one-shot
    with freeze_time("2026-03-01 10:01:00"):
        assert tt.is_tomorrow_low_start_trade_time() is False  # already started
    tt.update_dates("2026-03-02", "2026-03-03")  # date rolled -> reset
    with freeze_time("2026-03-02 10:00:00"):
        assert tt.is_tomorrow_low_start_trade_time() is True  # can start again


# --- fish_price_strategy tests ---

def test_price_strategy_stage1():
    """Stage 1: (open_sell_order_price + entry_price) / 2."""
    market_book = {"yes_dollars": [["0.15", 100]], "no_dollars": []}
    ps = FISH_PRICE_STRATEGY(entry_price=0.10, trade_price=0.20, side="yes")
    ps.update_price_strategy(market_book, 1)
    # stage1 = (0.20 + 0.10) / 2 = 0.15
    assert ps.trade_price == 0.15


def test_price_strategy_stage2():
    """Stage 2: (price_above + lowest_ask) / 2, where price_above = stage 1 result."""
    market_book = {
        "yes_dollars": [["0.10", 100], ["0.15", 150], ["0.20", 200]],
        "no_dollars": [],
    }
    ps = FISH_PRICE_STRATEGY(entry_price=0.08, trade_price=0.20, side="yes")
    ps.update_price_strategy(market_book, 1)  # stage 1 -> trade_price = 0.14
    ps.update_price_strategy(market_book, 2)  # stage 2 -> (0.14 + 0.10) / 2 = 0.12
    assert ps.trade_price == 0.12


def test_price_strategy_stage3():
    """Stage 3: lowest ask."""
    market_book = {
        "yes_dollars": [["0.05", 500], ["0.08", 300], ["0.12", 100]],
        "no_dollars": [],
    }
    ps = FISH_PRICE_STRATEGY(entry_price=0.10, trade_price=0.15, side="yes")
    ps.update_price_strategy(market_book, 3)
    assert ps.trade_price == 0.05


def test_price_strategy_stage3_clamps_to_min():
    """Stage 3 with very low ask: clamp to 0.01."""
    market_book = {
        "yes_dollars": [["0.005", 100]],
        "no_dollars": [],
    }
    ps = FISH_PRICE_STRATEGY(entry_price=0.10, trade_price=0.15, side="yes")
    ps.update_price_strategy(market_book, 3)
    assert ps.trade_price == 0.01


def test_price_strategy_no_update_for_stage0():
    """Stage 0 or invalid: no update."""
    market_book = {"yes_dollars": [["0.15", 100]], "no_dollars": []}
    ps = FISH_PRICE_STRATEGY(entry_price=0.10, trade_price=0.20, side="yes")
    ps.update_price_strategy(market_book, 0)
    assert ps.trade_price == 0.20  # unchanged


def test_price_strategy_get_buy_returns_valid_price():
    """get_buy_price_strategy returns valid resting price."""
    market_book = {
        "yes_dollars": [["0.05", 500], ["0.06", 300], ["0.07", 200]],
        "no_dollars": [["0.91", 100]],
    }
    ps = FISH_PRICE_STRATEGY()
    ps.volume_threshold = 100
    price = ps.get_buy_price_strategy(market_book)
    assert price is not None
    assert 0.01 < price <= 0.1


def test_price_strategy_get_buy_returns_none_when_best_ask_too_low():
    """get_buy_price_strategy returns None when best ask < 0.01 (cannot rest)."""
    market_book = {
        "yes_dollars": [["0.02", 500]],
        "no_dollars": [["0.995", 1000]],  # best_yes_ask = 1 - 0.995 = 0.005
    }
    ps = FISH_PRICE_STRATEGY()
    price = ps.get_buy_price_strategy(market_book)
    assert price is None


# --- Integration: no trades, then with trades ---

def test_full_schedule_no_trades():
    """Verify time schedule with no open trades (just time checks)."""
    tt = _get_trade_time()
    tt.reset_start_trade_time()
    tt.update_dates("2026-03-01", "2026-03-02")

    # Day 1, 10AM: can start tmr low/high
    with freeze_time("2026-03-01 10:00:00"):
        assert tt.is_tomorrow_low_start_trade_time() is True
        assert tt.is_tomorrow_high_start_trade_time() is True

    # Day 1, 4PM: stop tmr low
    with freeze_time("2026-03-01 16:00:00"):
        assert tt.is_tomorrow_low_stop_trade_time() is True

    # Day 1, 8PM: stop tmr high
    with freeze_time("2026-03-01 20:00:00"):
        assert tt.is_tomorrow_high_stop_trade_time() is True

    # Day 2, 0AM: start today low
    tt.reset_start_trade_time()
    with freeze_time("2026-03-02 00:00:00"):
        assert tt.is_today_low_start_trade_time() is True

    # Day 2, 2-4AM: low close stages
    with freeze_time("2026-03-02 02:00:00"):
        assert tt.get_close_stage_for_low() == 1
    with freeze_time("2026-03-02 04:00:00"):
        assert tt.get_close_stage_for_low() == 3

    # Day 2, 5-7AM: tmr high close stages
    with freeze_time("2026-03-02 05:00:00"):
        assert tt.get_close_stage_for_high() == 1
    with freeze_time("2026-03-02 07:00:00"):
        assert tt.get_close_stage_for_high() == 3

    # Day 2, 8AM: start today high
    tt.reset_start_trade_time()
    with freeze_time("2026-03-02 08:00:00"):
        assert tt.is_today_high_start_trade_time() is True

    # Day 2, 9-11AM: today high close stages
    with freeze_time("2026-03-02 09:00:00"):
        assert tt.get_close_stage_for_high() == 1
    with freeze_time("2026-03-02 11:00:00"):
        assert tt.get_close_stage_for_high() == 3


def test_price_strategy_with_trades_low_and_high():
    """Price strategy produces correct values for low and high stages."""
    # Low: entry=0.08, open_sell=0.20
    market_low = {
        "yes_dollars": [["0.10", 100], ["0.12", 150], ["0.15", 200]],
        "no_dollars": [],
    }
    ps_low = FISH_PRICE_STRATEGY(entry_price=0.08, trade_price=0.20, side="yes")
    ps_low.update_price_strategy(market_low, 1)
    assert ps_low.trade_price == round((0.20 + 0.08) / 2, 4)  # 0.14
    ps_low.update_price_strategy(market_low, 2)
    assert ps_low.trade_price == round((0.14 + 0.10) / 2, 4)  # 0.12
    ps_low.update_price_strategy(market_low, 3)
    assert ps_low.trade_price == 0.10  # lowest ask

    # High: entry=0.15, open_sell=0.30
    market_high = {
        "yes_dollars": [["0.20", 100], ["0.25", 150]],
        "no_dollars": [],
    }
    ps_high = FISH_PRICE_STRATEGY(entry_price=0.15, trade_price=0.30, side="yes")
    ps_high.update_price_strategy(market_high, 1)
    assert ps_high.trade_price == round((0.30 + 0.15) / 2, 4)  # 0.225
    ps_high.update_price_strategy(market_high, 3)
    assert ps_high.trade_price == 0.20


if __name__ == "__main__":
    import sys
    # Run as script
    tests = [
        test_trade_time_start_tomorrow_low_at_10am,
        test_trade_time_stop_tomorrow_low_at_4pm,
        test_trade_time_stop_tomorrow_high_at_8pm,
        test_trade_time_start_today_low_at_0am,
        test_trade_time_close_stage_low_2am_3am_4am,
        test_trade_time_close_stage_tmr_high_5am_6am_7am,
        test_trade_time_close_stage_today_high_9am_10am_11am,
        test_trade_time_start_today_high_at_8am,
        test_trade_time_reset_on_date_roll,
        test_price_strategy_stage1,
        test_price_strategy_stage2,
        test_price_strategy_stage3,
        test_price_strategy_stage3_clamps_to_min,
        test_price_strategy_no_update_for_stage0,
        test_price_strategy_get_buy_returns_valid_price,
        test_price_strategy_get_buy_returns_none_when_best_ask_too_low,
        test_full_schedule_no_trades,
        test_price_strategy_with_trades_low_and_high,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except Exception as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
    if failed:
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed.")
