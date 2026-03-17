import os
import time
import traceback
import requests
from requests.exceptions import HTTPError
from fish_orders import FISH_ORDERS, FISH_ORDERS_MANAGER, ensure_pnl_csv_exists, ensure_state_file_exists
from fish_incentive import FISH_INCENTIVE
from fish_market_ticker import FISH_MARKET_TICKER
from fish_parse_weather import FISH_PARSE_WEATHER
from fish_trade_time import FISH_TRADE_TIME
from clients import KalshiHttpClient, Environment
from fish_price_strategy import FISH_PRICE_STRATEGY
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fish_incentive import FISH_INCENTIVE
from cryptography.hazmat.primitives import serialization

TRADE_SIZE = 100
VOLUME_THRESHOLD = 100
FISH_INCENTIVE_THRESHOLD = 0.02
FISH_INCENTIVE_VOLUME_THRESHOLD = 500
FISH_INCENTIVE_TRADE_SIZE = 1

class FISH_TRADE:
    # Resolve log path from this file so it works regardless of cwd
    _base_dir = os.path.dirname(os.path.abspath(__file__))
    log_file = os.path.join(_base_dir, "logs", "fish_trade.log")

    def __init__(self, client: KalshiHttpClient, site_dict: dict, test_mode: bool = False,
                 test_client=None, test_parse_weather=None, test_market_ticker=None, test_trade_time=None):
        self.test_mode = test_mode
        self.incentive = FISH_INCENTIVE()
        self.market_ticker = test_market_ticker if test_mode and test_market_ticker else FISH_MARKET_TICKER()
        self.parse_weather = test_parse_weather if test_mode and test_parse_weather else FISH_PARSE_WEATHER(site_dict)
        self.trade_time = test_trade_time if test_mode and test_trade_time else FISH_TRADE_TIME()
        self.client = test_client if test_mode and test_client else client
        self.orders_manager = FISH_ORDERS_MANAGER()
        self.market_ticker.set_reference_trade_time(self.trade_time)
        self._last_fill_time = int(datetime.strptime((datetime.now()- timedelta(days=1)).strftime("%Y-%m-%d"), "%Y-%m-%d").timestamp())
        self.site_dict = site_dict
        self.orders_manager.fish_order_quantity = TRADE_SIZE
        self.fish_incentive = FISH_INCENTIVE(fish_incentive_threshold=FISH_INCENTIVE_THRESHOLD, fish_incentive_volume_threshold=FISH_INCENTIVE_VOLUME_THRESHOLD)

    def get_datetime(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def log(self, message: str):
        print(message)
        try:
            log_dir = os.path.dirname(self.log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(self.log_file, 'a') as f:
                f.write(message + '\n')
                f.flush()
        except Exception as e:
            print(f"Error writing to log file: {e}")
    

    def _update_sell_order_price_strategy(self, ticker: str, order, stage: int):
        """
        Update sell order price based on close stage (1, 2, or 3).
        Stage comes from fish_trade_time.get_close_stage_for_*().
        """
        if stage <= 0:
            return
        try:
            price_strategy = FISH_PRICE_STRATEGY(
                entry_price=order.entry_price,
                trade_price=order.price,
                action=order.action,
                side=order.side,
            )
            resp = self.client.get_market_ticker_order_book(ticker)
            market_book = resp.get('orderbook') or resp.get('orderbook_fp')
            if not market_book:
                raise ValueError(f"no orderbook in response for {ticker}")
            price_strategy.update_price_strategy(market_book, stage)
            if ticker in self.orders_manager.open_sell_orders:
                old_price = self.orders_manager.open_sell_orders[ticker].price
                new_price = price_strategy.trade_price
                self.orders_manager.open_sell_orders[ticker].price = new_price
                self.orders_manager.open_sell_orders[ticker].order_execution_type = 'update'
                self.orders_manager.open_sell_orders[ticker].last_updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self.log(f"{self.get_datetime()} [SELL PRICE UPDATE] {ticker} stage={stage} {old_price} -> {new_price} (will cancel+replace on API)")
        except Exception as e:
            self.log(f"{self.get_datetime()} [ERROR] Failed to update sell order price strategy for ticker {ticker}: {str(e)}")

    def _cancel_order_safe(self, order_id: str, ticker: str):
        """Cancel order; on 404 (already gone), log and continue."""
        if not order_id:
            return
        try:
            self.client.cancel_open_order(order_id)
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                self.log(f"{self.get_datetime()} [CANCEL] {ticker} already gone (404, may have filled), removing from local state")
            else:
                raise

    def check_outstanding_orders(self):
        for ticker, order in list(self.orders_manager.get_open_buy_orders().items()):
            if self.trade_time.is_today_low_stop_trade_time() and self.market_ticker.is_today_low_ticker(order.ticker):
                self.log(f"{self.get_datetime()} [CANCEL OPEN BUY] {order.ticker} qty={order.remaining_quantity} (today low stop)")
                self._cancel_order_safe(order.order_id, order.ticker)
                self.orders_manager.open_buy_orders.pop(order.ticker)
            if self.trade_time.is_today_high_stop_trade_time() and self.market_ticker.is_today_high_ticker(order.ticker):
                self.log(f"{self.get_datetime()} [CANCEL OPEN BUY] {order.ticker} qty={order.remaining_quantity} (today high stop)")
                self._cancel_order_safe(order.order_id, order.ticker)
                self.orders_manager.open_buy_orders.pop(order.ticker)
            if self.trade_time.is_tomorrow_low_stop_trade_time() and self.market_ticker.is_tomorrow_low_ticker(order.ticker):
                self.log(f"{self.get_datetime()} [CANCEL OPEN BUY] {order.ticker} qty={order.remaining_quantity} (tomorrow low stop)")
                self._cancel_order_safe(order.order_id, order.ticker)
                self.orders_manager.open_buy_orders.pop(order.ticker)
            if self.trade_time.is_tomorrow_high_stop_trade_time() and self.market_ticker.is_tomorrow_high_ticker(order.ticker):
                self.log(f"{self.get_datetime()} [CANCEL OPEN BUY] {order.ticker} qty={order.remaining_quantity} (tomorrow high stop)")
                self._cancel_order_safe(order.order_id, order.ticker)
                self.orders_manager.open_buy_orders.pop(order.ticker)
            if self.trade_time.is_fish_incentive_stop_trade_time() and order.trade_type == 'incentive_trade':
                self.log(f"{self.get_datetime()} [CANCEL INCENTIVE BUY] {order.ticker} qty={order.remaining_quantity} (fish incentive stop)")
                self._cancel_order_safe(order.order_id, order.ticker)
                self.orders_manager.open_buy_orders.pop(order.ticker)

        for ticker, order in list(self.orders_manager.get_open_sell_orders().items()):
            stage = 0
            if order.trade_type == 'incentive_trade':
                stage = 3
            elif self.market_ticker.is_today_low_ticker(order.ticker):
                stage = self.trade_time.get_close_stage_for_today_low()
            elif self.market_ticker.is_tomorrow_low_ticker(order.ticker):
                stage = self.trade_time.get_close_stage_for_tmr_low()
            elif self.market_ticker.is_today_high_ticker(order.ticker):
                stage = self.trade_time.get_close_stage_for_today_high()
            elif self.market_ticker.is_tomorrow_high_ticker(order.ticker):
                stage = self.trade_time.get_close_stage_for_tmr_high()
            if stage > 0:
                self._update_sell_order_price_strategy(order.ticker, order, stage)

    def get_ticker_orders_for_date(self, date: str):
        print("=== Searching for Available Temperature Tickers ===")
        for site in self.site_dict:
            ticker_data = {site: [date]}
        tickers = self.market_ticker.get_temperature_ticker(ticker_data)
        return tickers

    def get_fills(self):
        fills = self.client.get_fills(min_ts=self._last_fill_time)['fills']
        # Advance _last_fill_time so next run only gets NEW fills (avoid same fill every loop → 200 qty)
        max_fill_ts = self._last_fill_time
        for fill in fills:
            try:
                ts_str = fill.get('created_time') or fill.get('ts') or ''
                if ts_str:
                    if 'T' in ts_str:
                        dt = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    else:
                        dt = datetime.utcfromtimestamp(int(ts_str))
                    fill_ts = int(dt.timestamp())
                    if fill_ts > max_fill_ts:
                        max_fill_ts = fill_ts
            except (ValueError, TypeError):
                pass
        self._last_fill_time = max_fill_ts + 1 if fills else int(datetime.now().timestamp())
        for fill in fills:
            # Dedupe: Kalshi may return fill_id or trade_id
            fill_id = fill.get('fill_id') or fill.get('trade_id') or ''
            self.orders_manager.add_filled_order(FISH_ORDERS(
                order_id=fill['order_id'],
                ticker=fill['ticker'],
                symbol=self.market_ticker.ticker_to_symbol(fill['ticker']),
                order_date=fill['created_time'].split('T')[0],
                order_type='fill',
                order_execution_type='',  # Will be set in create_sell_order() if needed
                action=fill['action'],
                side=fill['side'],
                quantity=float(fill['count_fp']),
                remaining_quantity=float(fill['count_fp']),
                entry_price=float(fill['yes_price_fixed']) if fill['side'] == 'yes' else float(fill['no_price_fixed']),
                price=float(fill['yes_price_fixed']) if fill['side'] == 'yes' else float(fill['no_price_fixed']),
                created_at=fill['created_time'],
                last_updated_at=None,
                trade_type='market_order',
                fill_id=fill_id or None,
            ))
    
    def _order_remaining(self, o: dict) -> int:
        """API may return remaining_count (int) or remaining_count_fp (str e.g. '100.00')."""
        r = o.get('remaining_count')
        if r is not None:
            return int(r)
        fp = o.get('remaining_count_fp')
        if fp is not None:
            try:
                return int(float(fp))
            except (ValueError, TypeError):
                pass
        return 0

    def get_open_orders(self):
        open_orders = self.client.get_open_orders()['orders']
        # Tickers with resting buy orders on API (not filled)
        resting_buy_tickers = {
            o['ticker'] for o in open_orders
            if o.get('action') == 'buy' and o.get('status') == 'resting' and self._order_remaining(o) > 0
        }
        # Resting sell orders: ticker -> (order_id, remaining_count)
        api_resting_sells = {
            o['ticker']: (o.get('order_id'), self._order_remaining(o))
            for o in open_orders
            if o.get('action') == 'sell' and o.get('status') == 'resting' and self._order_remaining(o) > 0
        }
        # Remove from open_buy_orders any ticker no longer resting (buy was filled)
        for ticker in list(self.orders_manager.open_buy_orders.keys()):
            if ticker not in resting_buy_tickers:
                self.orders_manager.open_buy_orders.pop(ticker, None)
        # Sync open_sell_orders with API: update remaining_quantity, remove if order was filled
        for ticker in list(self.orders_manager.open_sell_orders.keys()):
            order = self.orders_manager.open_sell_orders[ticker]
            if ticker in api_resting_sells:
                oid, rem = api_resting_sells[ticker]
                order.order_id = oid or ''
                order.remaining_quantity = rem
            elif order.order_id:
                self.orders_manager.open_sell_orders.pop(ticker, None)
        for order in open_orders:
            rem = self._order_remaining(order)
            if order.get('status') == 'resting' and rem > 0:
                if order.get('action') == 'buy':
                    self.orders_manager.record_placed_order_id(order.get('order_id'))
                    ticker = order.get('ticker')
                    # Only update if this ticker already in our manager (from state). Do not add from API.
                    existing = self.orders_manager.open_buy_orders.get(ticker)
                    if existing is not None:
                        existing.order_id = order.get('order_id', '')
                        existing.remaining_quantity = rem
                        existing.trade_type = 'fish_order'
                else:
                    # Resting sell: only sync existing from state (above loop). Do not add from API.
                    pass
            # Track executed sell orders (status='executed' and action='sell')
            elif order.get('status') == 'executed' and order.get('action') == 'sell':
                self.orders_manager.mark_executed_sell_order(order.get('ticker'))

    def check_over_sell(self):
        open_orders = self.client.get_open_orders()['orders']
        open_positions = self.client.get_positions()['market_positions']
        open_positions_qty = {}
        for p in open_positions:
            ticker = p.get("ticker")
            pos = p.get("position")
            if pos is None and p.get("position_fp") is not None:
                try:
                    pos = int(float(p.get("position_fp", 0)))
                except (ValueError, TypeError):
                    pos = 0
            open_positions_qty[ticker] = pos
        open_sell_orders = {o['ticker']: o for o in open_orders if o.get('action') == 'sell' and o.get('status') == 'resting'}
        for ticker, order in open_sell_orders.items():
            rem = self._order_remaining(order)
            if rem > open_positions_qty.get(ticker, 0):
                if open_positions_qty.get(ticker, 0) > 0:
                    self.log(f"{self.get_datetime()} [OVER SELL] {ticker} qty={rem} > {open_positions_qty.get(ticker, 0)} - cancelling order")
                    self._cancel_order_safe(order.get('order_id'), ticker)
                    self.orders_manager.open_sell_orders[ticker].remaining_quantity = open_positions_qty.get(ticker, 0)
                    self.orders_manager.open_sell_orders[ticker].order_execution_type = 'update'
                    self.orders_manager.open_sell_orders[ticker].last_updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    self.log(f"{self.get_datetime()} [UPDATE SELL] {ticker} qty={rem} -> {open_positions_qty.get(ticker, 0)} (cancel+replace applied on API)")
                    sell_side = 'yes'
                    sell_qty = open_positions_qty.get(ticker, 0)
                    # order is from API (dict); price in yes_price_dollars or no_price_dollars
                    raw_price = order.get('yes_price_dollars') or order.get('no_price_dollars') or '0'
                    yes_price = f"{float(raw_price):.4f}"
                    no_price = None
                    try:
                        response = self.client.create_open_order(
                            ticker, sell_side, 'sell', sell_qty, 'limit',
                            yes_price_dollars=yes_price, no_price_dollars=no_price,
                        )
                        if response and 'order' in response:
                            managed = self.orders_manager.open_sell_orders.get(ticker)
                            if managed:
                                managed.order_id = response['order'].get('order_id', '')
                                managed.order_execution_type = 'pending'
                            self.log(f"{self.get_datetime()} [CREATE SELL] {ticker} side={sell_side} qty={sell_qty} @ {yes_price} (filled buy position)")
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] Failed to place sell order {ticker}: {e}")
                else:
                    self.log(f"{self.get_datetime()} [OVER SELL] {ticker} qty={rem} > {open_positions_qty.get(ticker, 0)} - cancelling order")
                    self._cancel_order_safe(order.get('order_id'), ticker)
                    self.orders_manager.open_sell_orders[ticker].remaining_quantity = 0
                    self.orders_manager.open_sell_orders.pop(ticker, None)
            else:
                self.log(f"{self.get_datetime()} [OK SELL] {ticker} qty={rem} <= {open_positions_qty.get(ticker, 0)} - keeping order")

    def create_fish_sell_order(self):
        # Fetch actual positions - only create sells for tickers where we have position > 0
        actual_positions = {}
        tickers_open_qty = {}
        try:
            resp = self.client.get_positions()
            for p in resp.get("market_positions", []):
                ticker = p.get("ticker")
                pos = p.get("position")
                if pos is None and p.get("position_fp") is not None:
                    try:
                        pos = int(float(p.get("position_fp", 0)))
                    except (ValueError, TypeError):
                        pos = 0
                else:
                    pos = int(pos or 0)
                tickers_open_qty[ticker] = pos
                if ticker:
                    actual_positions[ticker] = pos
        except Exception as e:
            self.log(f"{self.get_datetime()} [WARNING] Could not fetch positions: {e} - proceeding without position check")

        # Cancel orphaned sell orders (we have no position but have open sell on API)
        if actual_positions:
            for ticker, order in list(self.orders_manager.get_open_sell_orders().items()):
                if actual_positions.get(ticker, 0) <= 0 and order.order_id:
                    self.log(f"{self.get_datetime()} [CANCEL ORPHAN] {ticker} - no position, cancelling stale sell order")
                    self._cancel_order_safe(order.order_id, ticker)

        self.orders_manager.create_fish_sell_order(actual_positions=actual_positions or None)
        open_sell_orders = self.orders_manager.get_open_sell_orders()
        for ticker, order in list(open_sell_orders.items()):
            # CRITICAL: We only ever sell YES (close long YES). NEVER sell NO (would go short).
            sell_side = 'yes'
            assert order.side == 'yes', f"BLOCKED: sell order for {ticker} side={order.side} - would go short"

            # Cap sell qty to actual position so we NEVER oversell (oversell = short YES = NO position).
            actual_pos = tickers_open_qty.get(ticker, 0)
            if ticker in tickers_open_qty and order.remaining_quantity != actual_pos:
                order.remaining_quantity = actual_pos
            sell_qty = min(order.remaining_quantity, actual_pos)
            if sell_qty <= 0:
                self.log(f"{self.get_datetime()} [SKIP SELL] {ticker} position={actual_pos} qty=0 - not placing sell (prevents oversell)")
                if order.order_id:
                    self._cancel_order_safe(order.order_id, ticker)
                    self.orders_manager.open_sell_orders.pop(ticker, None)
                continue
            order.remaining_quantity = sell_qty

            yes_price = f"{float(order.price):.4f}"
            no_price = None
            if order.order_execution_type == 'new':
                try:
                    response = self.client.create_open_order(
                        ticker, sell_side, 'sell', sell_qty, 'limit',
                        yes_price_dollars=yes_price, no_price_dollars=no_price,
                    )
                    if response and 'order' in response:
                        order.order_id = response['order'].get('order_id', '')
                        order.order_execution_type = 'pending'
                    self.log(f"{self.get_datetime()} [CREATE SELL] {ticker} side={sell_side} qty={sell_qty} @ {order.price} (filled buy position)")
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] Failed to place sell order {ticker}: {e}")
            elif order.order_execution_type == 'update':
                # Price changed: cancel old order and replace at new price
                try:
                    self._cancel_order_safe(order.order_id, ticker)
                    response = self.client.create_open_order(
                        ticker, sell_side, 'sell', sell_qty, 'limit',
                        yes_price_dollars=yes_price, no_price_dollars=no_price,
                    )
                    if response and 'order' in response:
                        order.order_id = response['order'].get('order_id', '')
                        order.order_execution_type = 'pending'
                        self.log(f"{self.get_datetime()} [UPDATE SELL] {ticker} side={sell_side} qty={sell_qty} @ {order.price} (cancel+replace applied on API)")
                    else:
                        self.log(f"{self.get_datetime()} [WARNING] Price adjustment for {ticker} not applied on API: create_open_order returned no order")
                        # Cancel likely succeeded; remove stale open_sell so next cycle we reconcile with actual position
                        self.orders_manager.open_sell_orders.pop(ticker, None)
                except Exception as e:
                    self.log(f"{self.get_datetime()} [WARNING] Price adjustment for {ticker} not applied on API: {e}")
                    # Cancel may have succeeded; remove stale open_sell so next cycle we reconcile with actual position
                    self.orders_manager.open_sell_orders.pop(ticker, None)

    def _create_fish_buy_orders_for_date(self, parsed_weather, city, date_str, log_label):
        day_data = parsed_weather.get(city, {}).get(date_str, {})
        today_str = datetime.now().strftime("%Y-%m-%d")
        # Today: lowest and highest across report and forecast so range spans both. Tomorrow: forecast only.
        if date_str == today_str:
            report_range = day_data.get('report')
            forecast_range = day_data.get('forecast')
            vals = []
            if report_range and len(report_range) >= 2:
                vals.extend([report_range[0], report_range[1]])
            if forecast_range and len(forecast_range) >= 2:
                vals.extend([forecast_range[0], forecast_range[1]])
            weather_range = [min(vals), max(vals)] if vals else forecast_range or report_range
        else:
            weather_range = day_data.get('forecast')
        if weather_range is None:
            weather_range = day_data.get('forecast') or day_data.get('report')
        ticker_type = "low" if "LOW" in log_label else "high" if "HIGH" in log_label else None
        tickers = self.market_ticker.get_tickers_for_date(self.client, city, date_str, weather_range, ticker_type=ticker_type)
        for ticker in tickers:
            try:
                # Skip if we already have an open buy order for this ticker (e.g. from before restart)
                existing = self.orders_manager.open_buy_orders.get(ticker)
                if existing and existing.order_id:
                    self.log(f"{self.get_datetime()} [SKIP {log_label}] {city} {ticker} - already have open buy order_id={existing.order_id} remaining={existing.remaining_quantity}")
                    continue
                resp = self.client.get_market_ticker_order_book(ticker)
                market_book = resp.get('orderbook_fp') or resp.get('orderbook')
                if not market_book:
                    self.log(f"{self.get_datetime()} [ERROR] {log_label} {city} {ticker} - no orderbook in response")
                    continue
                price_strategy = FISH_PRICE_STRATEGY()
                price = price_strategy.get_buy_price_strategy(market_book)
                if price is not None:
                    site_list = self.site_dict.get(city, [])
                    quantity = site_list[3] if len(site_list) > 3 else 100
                    self.orders_manager.create_fish_buy_order(ticker, price, quantity=quantity)
                    self.log(f"{self.get_datetime()} [CREATE {log_label}] {city} {ticker} qty={quantity} @ {price}")
                else:
                    self.log(f"{self.get_datetime()} [SKIP {log_label}] {city} {ticker} - no resting buy price (best ask <= 0.01, would be taker)")
            except Exception as e:
                self.log(f"{self.get_datetime()} [ERROR] {log_label} {city} {ticker}: {e}\n{traceback.format_exc()}")
        self.log(f"{self.get_datetime()} [START {log_label}] {city}")

    def create_fish_incentive_program(self):
        incentive_response = self.client.get_market_incentive()
        self.fish_incentive.load_from_incentive_programs(incentive_response)
        incentive_tickers = self.fish_incentive.get_fish_incentive_tickers()
        incentive_market_orders = {}

        for ticker in incentive_tickers:
            market_orders = self.client.get_market_ticker_order_book(ticker)
            self.fish_incentive.update_fish_incentive_market_ticker(ticker,market_orders)
        
        fish_ticker_market_orders = self.fish_incentive.get_fish_ticker_market_orders()
        log_label = "incentive_trade"
        for ticker in fish_ticker_market_orders.keys():
            try:
                # Skip if we already have an open buy order for this ticker (e.g. from before restart)
                existing = self.orders_manager.open_buy_orders.get(ticker)
                if existing and existing.order_id:
                    self.log(f"{self.get_datetime()} [SKIP {log_label}] {ticker} - already have open buy order_id={existing.order_id} remaining={existing.remaining_quantity}")
                    continue
                price = fish_ticker_market_orders[ticker]
                if price is not None:
                    quantity = FISH_INCENTIVE_TRADE_SIZE
                    self.orders_manager.create_fish_buy_order(ticker, price, quantity=quantity, trade_type=log_label)
                    self.log(f"{self.get_datetime()} [CREATE {log_label}] {ticker} qty={quantity} @ {price}")
                else:
                    self.log(f"{self.get_datetime()} [SKIP {log_label}] {ticker} - no resting buy price (best ask <= 0.01, would be taker)")
            except Exception as e:
                self.log(f"{self.get_datetime()} [ERROR] {log_label} {ticker}: {e}\n{traceback.format_exc()}")


    def create_fish_buy_order(self):
        parsed_weather = self.parse_weather.get_all_weather()
        today_date = datetime.now().strftime("%Y-%m-%d")
        tomorrow_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        # Check time once per trade type, then iterate ALL cities (flag is one-shot per type)
        # if self.trade_time.is_today_low_start_trade_time():
        #     for city in parsed_weather:
        #         self._create_fish_buy_orders_for_date(parsed_weather, city, today_date, "TODAY'S LOW TRADE")
        if self.trade_time.is_today_high_start_trade_time():
            for city in parsed_weather:
                try:
                    self._create_fish_buy_orders_for_date(parsed_weather, city, today_date, "TODAY'S HIGH TRADE")
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] create_fish_buy_order city={city} today_high: {e}\n{traceback.format_exc()}")
        if self.trade_time.is_tomorrow_low_start_trade_time():
            for city in parsed_weather:
                try:
                    self._create_fish_buy_orders_for_date(parsed_weather, city, tomorrow_date, "TOMORROW'S LOW TRADE")
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] create_fish_buy_order city={city} tomorrow_low: {e}\n{traceback.format_exc()}")
        if self.trade_time.is_tomorrow_high_start_trade_time():
            for city in parsed_weather:
                try:
                    self._create_fish_buy_orders_for_date(parsed_weather, city, tomorrow_date, "TOMORROW'S HIGH TRADE")
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] create_fish_buy_order city={city} tomorrow_high: {e}\n{traceback.format_exc()}")
        if self.trade_time.is_fish_incentive_start_trade_time():
            try:
                self.create_fish_incentive_program()
            except Exception as e:
                self.log(f"{self.get_datetime()} [ERROR] create_fish_incentive_program: {e}\n{traceback.format_exc()}")
        # for city in parsed_weather:
        #     self._create_fish_buy_orders_for_date(parsed_weather, city, tomorrow_date, "TOMORROW'S HIGH TRADE")
        
        all_open_buy_orders = self.orders_manager.get_open_buy_orders()
        for ticker, order in all_open_buy_orders.items():
            if order.order_execution_type != 'new':
                continue
            try:
                response = self.client.create_open_order(ticker, 'yes', 'buy', order.quantity, 'limit', f"{float(order.price):.4f}")
                if response and 'order' in response:
                    order_id = response['order'].get('order_id', '')
                    order.order_id = order_id
                    order.order_execution_type = 'pending'
                    self.orders_manager.record_placed_order_id(order_id)
                    self.log(f"{self.get_datetime()} [PLACE BUY] {ticker} qty={order.quantity} @ {order.price} order_id={order_id}")
            except Exception as e:
                self.log(f"{self.get_datetime()} [ERROR] Failed to place buy order {ticker}: {e}")

    def start_trade(self):
        ensure_pnl_csv_exists()
        ensure_state_file_exists()
        self.orders_manager.load_state()

        max_retries = 3
        retry_delay = 10

        while True:
            for attempt in range(max_retries):
                try:
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
                    self.trade_time.update_dates(today_str, tomorrow_str)
                    if datetime.now().hour == 0:
                        self.orders_manager.process_midnight_expiry(today_str)
                    try:
                        self.get_open_orders()  # First: sync placed_order_ids from API
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] get_open_orders: {e}\n{traceback.format_exc()}")
                    try:
                        self.get_fills()
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] get_fills: {e}\n{traceback.format_exc()}")
                    try:
                        self.check_outstanding_orders()
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] check_outstanding_orders: {e}\n{traceback.format_exc()}")
                    try:
                        self.create_fish_sell_order()
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] create_fish_sell_order: {e}\n{traceback.format_exc()}")
                    try:
                        self.check_over_sell()
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] check_over_sell: {e}\n{traceback.format_exc()}")
                    try:
                        self.create_fish_buy_order()
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] create_fish_buy_order: {e}\n{traceback.format_exc()}")
                    break
                except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ReadTimeout) as e:
                    self.log(f"{self.get_datetime()} [RETRY {attempt + 1}/{max_retries}] Transient error: {e}")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
                    else:
                        self.log(f"{self.get_datetime()} [SKIP] Max retries reached, continuing to next cycle")
            self.orders_manager.save_state()
            self.log(f"{self.get_datetime()} [==================WAIT 30 MIN==================]")
            target = time.time() + 1800
            while time.time() < target:
                time.sleep(60)


if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    env = Environment.PROD # toggle environment here
    KEYID = os.getenv('DEMO_KEYID') if env == Environment.DEMO else os.getenv('PROD_KEYID')
    KEYFILE = os.getenv('DEMO_KEYFILE') if env == Environment.DEMO else os.getenv('PROD_KEYFILE')

    try:
        with open(KEYFILE, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None  # Provide the password if your key is encrypted
            )
    except FileNotFoundError:
        raise FileNotFoundError(f"Private key file not found at {KEYFILE}")
    except Exception as e:
        raise Exception(f"Error loading private key: {str(e)}")

    # Initialize the HTTP client
    client = KalshiHttpClient(
        key_id=KEYID,
        private_key=private_key,
        environment=env
    )

    site_dict = {
        "PHIL": [
            "https://forecast.weather.gov/product.php?site=PHI&product=CLI&issuedby=PHL",
            "https://forecast.weather.gov/MapClick.php?lat=39.8764&lon=-75.2422&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KPHL",
            10,
        ],
        "CHI": [
            "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW",
            "https://forecast.weather.gov/MapClick.php?lat=41.7885&lon=-87.7417&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMDW",
            10,
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
            100,
        ],
        "LAX": [
            "https://forecast.weather.gov/product.php?site=LOX&product=CLI&issuedby=LAX",
            "https://forecast.weather.gov/MapClick.php?lat=33.9435&lon=-118.4086&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KLAX",
            100,
        ],
        "MIA": [
            "https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MIA",
            "https://forecast.weather.gov/MapClick.php?lat=25.795&lon=-80.2798&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMIA",
            10,
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
        "TMIN": [
            "https://forecast.weather.gov/product.php?site=FSD&product=CLI&issuedby=MSP",
            "https://forecast.weather.gov/MapClick.php?lat=44.882&lon=-93.2218&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMSP",
            10,
        ],
        "TATL": [
            "https://forecast.weather.gov/product.php?site=FFC&product=CLI&issuedby=ATL",
            "https://forecast.weather.gov/MapClick.php?lat=33.7485&lon=-84.3915&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KATL",
            10,
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
            10,
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
            100,
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
            10,
        ],
        "TBOS": [
            "https://forecast.weather.gov/product.php?site=PVD&product=CLI&issuedby=BOS",
            "https://forecast.weather.gov/MapClick.php?lat=42.359&lon=-71.0586&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KBOS",
            100,
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
            10,
        ],
    }

    fish_trade = FISH_TRADE(client, site_dict)
    fish_trade.start_trade()