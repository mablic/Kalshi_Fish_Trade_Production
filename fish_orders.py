import json
import os
from datetime import datetime, timedelta

PNL_LOG_FILE = "logs/fish_pnl.csv"
STATE_FILE = "logs/fish_open_trade_ticker.json"
MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

CSV_HEADER = "datetime,city,ticker,qty,entry_price,exit_price,pnl"


def _extract_city_from_ticker(ticker: str) -> str:
    """Extract city code from ticker e.g. KXLOWTNYC-26MAR03-B25.5 -> NYC, KXHIGHNY -> NYC."""
    t = ticker.upper()
    if t.startswith("KXLOWT"):
        city = t[6:].split("-")[0]
    elif t.startswith("KXHIGH"):
        city = t[6:].split("-")[0]
    else:
        return ""
    return "NYC" if city == "NY" else city


def _extract_market_date_from_ticker(ticker: str) -> str | None:
    """Extract YYYY-MM-DD from ticker e.g. KXLOWTCHI-26MAR02-B21.5 -> 2026-03-02."""
    try:
        parts = ticker.upper().split("-")
        if len(parts) >= 2 and len(parts[1]) >= 7:
            date_part = parts[1]
            yy, mm_str, dd = int(date_part[:2]), date_part[2:5], int(date_part[5:7])
            if mm_str in MONTH_NAMES:
                return f"20{yy:02d}-{MONTH_NAMES.index(mm_str) + 1:02d}-{dd:02d}"
    except (ValueError, IndexError):
        pass
    return None


def ensure_pnl_csv_exists(log_file: str = PNL_LOG_FILE):
    """Create fish_pnl.csv with header if it doesn't exist or is blank."""
    try:
        needs_header = False
        if not os.path.exists(log_file):
            needs_header = True
        elif os.path.getsize(log_file) == 0:
            needs_header = True
        else:
            with open(log_file, "r") as f:
                first_line = f.readline().strip()
            if not first_line or not first_line.startswith("datetime"):
                needs_header = True
        if needs_header:
            log_dir = os.path.dirname(log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(log_file, "w") as f:
                f.write(CSV_HEADER + "\n")
    except Exception as e:
        print(f"[PnL] Error ensuring {log_file} exists: {e}")


def ensure_state_file_exists(state_file: str = STATE_FILE):
    """Create fish_open_trade_ticker.json with base structure if it doesn't exist or is blank/invalid."""
    base_structure = {
        "open": [],
        "filled": [],
        "processed_fill_ids": [],
        "placed_order_ids": [],
        "traded_tickers": [],
    }
    try:
        needs_init = False
        if not os.path.exists(state_file):
            needs_init = True
        elif os.path.getsize(state_file) == 0:
            needs_init = True
        else:
            try:
                with open(state_file) as f:
                    data = json.load(f)
                if not isinstance(data, dict) or "open" not in data:
                    needs_init = True
            except (json.JSONDecodeError, TypeError):
                needs_init = True
        if needs_init:
            state_dir = os.path.dirname(state_file)
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
            with open(state_file, "w") as f:
                json.dump(base_structure, f, indent=2)
    except Exception as e:
        print(f"[State] Error ensuring {state_file} exists: {e}")


def _log_pnl_csv(log_file: str, datetime_str: str, city: str, ticker: str, qty: int, entry_price: float, exit_price: float, pnl: float):
    """Append one CSV row to PnL log. Writes header if file is new."""
    try:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        write_header = not os.path.exists(log_file) or os.path.getsize(log_file) == 0
        with open(log_file, "a") as f:
            if write_header:
                f.write(CSV_HEADER + "\n")
            row = f"{datetime_str},{city},{ticker},{qty},{entry_price},{exit_price},{pnl}\n"
            f.write(row)
    except Exception as e:
        print(f"[PnL] Error writing to {log_file}: {e}")


class FISH_ORDERS:

    def __init__(self, 
        order_id: str, 
        ticker: str,
        symbol: str,
        order_date: str,
        order_type: str,
        order_execution_type: str,
        action: str,
        side: str,
        quantity: int,
        remaining_quantity: int,
        entry_price: float,
        price: float,
        created_at: str,
        last_updated_at: str,
        trade_type: str,
        fill_id: str = None,
    ):
        self.order_id = order_id
        self.ticker = ticker
        self.symbol = symbol
        self.order_date = order_date
        self.order_type = order_type
        self.order_execution_type = order_execution_type
        self.action = action
        self.side = side
        self.quantity = quantity
        self.remaining_quantity = remaining_quantity
        self.entry_price = round(float(entry_price), 2) if entry_price is not None else entry_price
        self.price = round(float(price), 2) if price is not None else price
        self.created_at = created_at
        self.last_updated_at = last_updated_at
        self.trade_type = trade_type
        self.fill_id = fill_id

    def time_escape(self):
        if self.last_updated_at is not None:
            last_updated_at = datetime.strptime(self.last_updated_at, '%Y-%m-%d %H:%M:%S')
            time_difference = datetime.now() - last_updated_at
        else:
            time_difference = datetime.now() - datetime.strptime(self.created_at, '%Y-%m-%d %H:%M:%S')
        return time_difference.total_seconds()

    def to_dict(self):
        return {
            "order_id": self.order_id or "",
            "ticker": self.ticker,
            "symbol": self.symbol,
            "order_date": self.order_date,
            "order_type": self.order_type,
            "order_execution_type": self.order_execution_type or "",
            "action": self.action,
            "side": self.side,
            "quantity": self.quantity,
            "remaining_quantity": self.remaining_quantity,
            "entry_price": float(self.entry_price) if self.entry_price is not None else 0,
            "price": float(self.price) if self.price is not None else 0,
            "created_at": self.created_at or "",
            "last_updated_at": self.last_updated_at or "",
            "trade_type": self.trade_type or "",
            "fill_id": self.fill_id or "",
        }


def _order_from_dict(d: dict) -> FISH_ORDERS:
    """Create FISH_ORDERS from dict (for load_state)."""
    return FISH_ORDERS(
        order_id=d.get("order_id", ""),
        ticker=d.get("ticker", ""),
        symbol=d.get("symbol", d.get("ticker", "")),
        order_date=d.get("order_date", ""),
        order_type=d.get("order_type", "open"),
        order_execution_type=d.get("order_execution_type", ""),
        action=d.get("action", "buy"),
        side=d.get("side", "yes"),
        quantity=int(d.get("quantity", 0)),
        remaining_quantity=int(d.get("remaining_quantity", 0)),
        entry_price=float(d.get("entry_price", 0)),
        price=float(d.get("price", 0)),
        created_at=d.get("created_at", ""),
        last_updated_at=d.get("last_updated_at") or None,
        trade_type=d.get("trade_type", "fish_order"),
        fill_id=d.get("fill_id") or None,
    )


class FISH_ORDERS_MANAGER:
    
    __instance = None
    __FISH_ORDER_QUANTITY = 1

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super(FISH_ORDERS_MANAGER, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        self.fish_orders = {}
        self.open_buy_orders = {}
        self.open_sell_orders = {}
        self.filled_orders = {}
        self.settled_orders = {}
        self.executed_sell_orders = set()
        self.pnl_log_file = PNL_LOG_FILE
        self.placed_order_ids = set()  # Only add fills for orders we placed
        self.processed_fill_ids = set()  # Dedupe: skip fills we've already processed
        self.state_file = STATE_FILE

    def save_state(self):
        """Persist open/filled orders and IDs to JSON for restart recovery."""
        try:
            open_list = []
            for order in list(self.open_buy_orders.values()) + list(self.open_sell_orders.values()):
                open_list.append(order.to_dict())
            filled_list = [o.to_dict() for o in self.filled_orders.values()]
            data = {
                "open": open_list,
                "filled": filled_list,
                "processed_fill_ids": list(self.processed_fill_ids),
                "placed_order_ids": list(self.placed_order_ids),
            }
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[State] Error saving to {self.state_file}: {e}")

    def load_state(self):
        """Restore open/filled orders and IDs from JSON. Call before main loop on startup."""
        try:
            if not os.path.exists(self.state_file):
                return
            with open(self.state_file) as f:
                data = json.load(f)
            for d in data.get("open", []):
                if not d.get("ticker"):
                    continue
                order = _order_from_dict(d)
                if order.action == "buy":
                    self.open_buy_orders[order.ticker] = order
                else:
                    self.open_sell_orders[order.ticker] = order
                self.fish_orders[order.ticker] = order
            for d in data.get("filled", []):
                if not d.get("ticker"):
                    continue
                order = _order_from_dict(d)
                order.trade_type = "fish_order"
                self.filled_orders[order.ticker] = order
                self.fish_orders[order.ticker] = order
            self.processed_fill_ids = set(data.get("processed_fill_ids", []))
            self.placed_order_ids = set(data.get("placed_order_ids", []))
        except Exception as e:
            print(f"[State] Error loading from {self.state_file}: {e}")

    @property
    def fish_order_quantity(self):
        return self.__FISH_ORDER_QUANTITY

    @fish_order_quantity.setter
    def fish_order_quantity(self, value: int):
        self.__FISH_ORDER_QUANTITY = value

    def add_open_order(self, order: FISH_ORDERS):
        if order.trade_type != 'fish_order':
            return
        if order.action == 'buy':
            if order.ticker in self.open_buy_orders:
                self.open_buy_orders.pop(order.ticker)
            self.open_buy_orders[order.ticker] = order
        else:
            if order.ticker in self.open_sell_orders:
                self.open_sell_orders.pop(order.ticker)
            self.open_sell_orders[order.ticker] = order
        if order.ticker not in self.fish_orders:
            self.fish_orders[order.ticker] = order
    
    def record_placed_order_id(self, order_id: str):
        """Track order IDs we placed - only add fills for these."""
        if order_id:
            self.placed_order_ids.add(order_id)

    def add_filled_order(self, order: FISH_ORDERS):
        if order.fill_id and order.fill_id in self.processed_fill_ids:
            return  # Already processed this fill - skip
        if order.ticker not in self.fish_orders:
            return
        # For BUY fills: only add if order_id is from an order we placed
        # For SELL fills: we're reducing existing position - allow (we created the sell)
        if order.action == 'buy' and (not order.order_id or order.order_id not in self.placed_order_ids):
            return  # Ignore buy fills from orders we didn't place
        if order.fill_id:
            self.processed_fill_ids.add(order.fill_id)
        order.trade_type = 'fish_order'
        if order.ticker not in self.filled_orders:
            # always using the "yes"
            if order.side == 'no':
                order.side = 'yes'
                order.price = round(1 - float(order.price), 2)
                order.action = 'sell' if order.action == 'buy' else 'buy'
                if order.action == 'sell':
                    raise ValueError(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [ERROR] Cannot sell a filled order")
            else:
                order.price = round(float(order.price), 2)
            self.filled_orders[order.ticker] = order
        else:
            current_order = self.filled_orders[order.ticker]
            if current_order.side == order.side and current_order.action == order.action:
                self.filled_orders[order.ticker].quantity += order.quantity
                self.filled_orders[order.ticker].price = round((current_order.price * current_order.quantity + order.price * order.quantity) / (current_order.quantity + order.quantity), 2)
            else:
                if current_order.side != order.side:
                    order.side = current_order.side
                    order.price = round(1 - float(order.price), 2)
                    order.action = 'sell' if current_order.action == 'buy' else 'buy'
                else:
                    order.price = round(float(order.price), 2)
                if current_order.action != order.action:
                    # Sell fill: log realized PnL before updating
                    cost_basis = current_order.price
                    sell_price = order.price
                    qty_sold = order.quantity
                    pnl = round((sell_price - cost_basis) * qty_sold, 4)
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    city = _extract_city_from_ticker(order.ticker)
                    _log_pnl_csv(self.pnl_log_file, ts, city, order.ticker, qty_sold, cost_basis, sell_price, pnl)
                    self.filled_orders[order.ticker].quantity -= order.quantity
                    if current_order.quantity - order.quantity > 0:
                        self.filled_orders[order.ticker].price = round((current_order.price * current_order.quantity - order.price * order.quantity) / (current_order.quantity - order.quantity), 2)
                else:
                    self.filled_orders[order.ticker].quantity += order.quantity
                    self.filled_orders[order.ticker].price = round((current_order.price * current_order.quantity + order.price * order.quantity) / (current_order.quantity + order.quantity), 2)
        self.filled_orders[order.ticker].last_updated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if self.filled_orders[order.ticker].quantity == 0:
            self.settled_orders[order.ticker] = self.filled_orders[order.ticker]
            self.filled_orders.pop(order.ticker)

    def mark_executed_sell_order(self, ticker: str):
        """Mark a ticker as having an executed sell order in API"""
        self.executed_sell_orders.add(ticker)
    
    def create_fish_buy_order(self, ticker, price: float):
        """
        Create a buy order for a ticker.
        """
        ORDER = FISH_ORDERS(
            order_id = '',
            ticker = ticker,
            symbol = ticker,
            order_date = datetime.now().strftime('%Y-%m-%d'),
            order_type = 'open',
            order_execution_type = 'new',
            action = 'buy',
            side = 'yes',
            quantity = self.__FISH_ORDER_QUANTITY,
            remaining_quantity = self.__FISH_ORDER_QUANTITY,
            entry_price = price,
            price = price,
            created_at = datetime.now().isoformat(),
            last_updated_at = None,
            trade_type = 'fish_order',
        )
        self.add_open_order(ORDER)

    def create_fish_sell_order(self, actual_positions: dict = None):
        """
        Reconcile filled buy orders with open sell orders.
        For each ticker with filled buys, ensure there's a corresponding sell order.
        Only add sell orders for the net unmatched quantity (filled buys - filled sells - open sells).
        Skip tickers that have executed sell orders in API (they're already handled).

        actual_positions: {ticker: position} from API. position>0 = long YES. If provided, we ONLY
        create sells for tickers where we actually have position>0. Removes stale state for position<=0.
        """
        if actual_positions is not None:
            # Remove stale state: we have no position, clear filled_orders and open_sell_orders
            for ticker in list(self.filled_orders.keys()):
                if actual_positions.get(ticker, 0) <= 0:
                    self.filled_orders.pop(ticker, None)
                    self.open_sell_orders.pop(ticker, None)
                    self.fish_orders.pop(ticker, None)
            # Reconcile: if our tracked "to sell" exceeds actual position (e.g. sell fill not in get_fills yet),
            # cap filled_orders to actual position so we never open a second sell for the same contracts.
            for ticker, filled_order in list(self.filled_orders.items()):
                pos = actual_positions.get(ticker, 0)
                if pos > 0 and filled_order.quantity > pos:
                    filled_order.quantity = pos
                    filled_order.remaining_quantity = pos

        for ticker, filled_order in list(self.filled_orders.items()):
            # Only process filled buy orders for auto fish trade
            # If actual_positions provided, only create sells where we have position > 0
            if actual_positions is not None and actual_positions.get(ticker, 0) <= 0:
                continue
            if filled_order.action == 'buy' and filled_order.quantity > 0 and filled_order.trade_type == 'fish_order':
                # Skip if this ticker has an executed sell order in API
                # (it means the sell order was already executed, so we don't need to add it)
                if ticker in self.executed_sell_orders:
                    continue
                
                # Get open sell quantity for this ticker
                open_sell_qty = 0
                if ticker in self.open_sell_orders:
                    open_sell_qty = self.open_sell_orders[ticker].remaining_quantity
                
                # Calculate net unmatched filled buys
                # filled_order.quantity is already net (filled buys - filled sells from add_filled_order logic)
                # net_unmatched = net_filled_position - open_sells
                net_unmatched = filled_order.quantity - open_sell_qty
                # Cap by actual position so we never place a sell for more than we have (prevents oversell
                # when state is out of sync after e.g. cancel+replace failure or fill not yet in get_fills).
                if actual_positions is not None:
                    net_unmatched = min(net_unmatched, actual_positions.get(ticker, 0))
                
                if net_unmatched > 0:
                    if ticker not in self.open_sell_orders:
                        # Ticker doesn't exist in open_sell_orders, so this is a 'new' order to be created
                        order_execution_type = 'new'
                        
                        # Create a new sell order. ALWAYS side='yes' - never sell NO (short).
                        sell_order = FISH_ORDERS(
                            order_id='',  # No order ID since it's not yet created
                            ticker=ticker,
                            symbol=filled_order.symbol,
                            order_date=filled_order.order_date,
                            order_type='open',
                            order_execution_type=order_execution_type,
                            action='sell',
                            side='yes',
                            quantity=net_unmatched,
                            remaining_quantity=net_unmatched,
                            price=round(2 * float(filled_order.price), 2), 
                            created_at=datetime.now().isoformat(),
                            last_updated_at=None,
                            trade_type='fish_order',
                            entry_price=filled_order.entry_price,
                        )
                        self.open_sell_orders[ticker] = sell_order
                    else:
                        # Ticker exists: API has resting sell. If API qty < position, cancel+replace with full qty.
                        existing_order = self.open_sell_orders[ticker]
                        api_resting_qty = existing_order.remaining_quantity  # synced from API in get_open_orders
                        target_qty = filled_order.quantity
                        if actual_positions is not None:
                            target_qty = min(target_qty, actual_positions.get(ticker, 0))
                        if api_resting_qty < target_qty and target_qty > 0:
                            # Need more: cancel old order, place new for full position (capped by actual)
                            existing_order.quantity = target_qty
                            existing_order.remaining_quantity = target_qty
                            existing_order.order_execution_type = 'update'
                        # else: api_resting_qty >= position, nothing to do (or some filled - filled_order tracks that)

    def get_open_buy_orders(self):
        return self.open_buy_orders

    def get_open_sell_orders(self):
        return self.open_sell_orders

    def process_midnight_expiry(self, today_str: str):
        """
        At midnight: all open sell orders for markets that expired (market date < today)
        are treated as lost. Log negative PnL = -entry_price * remaining_quantity.
        """
        expired = []
        for ticker, order in list(self.open_sell_orders.items()):
            market_date = _extract_market_date_from_ticker(ticker)
            if market_date and market_date < today_str:
                qty_unfilled = order.remaining_quantity
                cost = order.entry_price * qty_unfilled
                pnl = -round(cost, 4)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                city = _extract_city_from_ticker(ticker)
                _log_pnl_csv(self.pnl_log_file, ts, city, ticker, qty_unfilled, order.entry_price, 0, pnl)
                expired.append(ticker)
        for ticker in expired:
            self.open_sell_orders.pop(ticker)
