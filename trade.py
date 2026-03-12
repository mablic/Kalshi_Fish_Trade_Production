from datetime import datetime, timedelta
import time

class TRADE:

    __instance = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super(TRADE, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        self.open_trade_orders = {}

    @property
    def trade_size(self):
        return self.__trade_size
    
    @trade_size.setter
    def trade_size(self, value: int):
        self.__trade_size = value
    
    @property
    def expiration_ts(self):
        return self.__expiration_ts
    
    @expiration_ts.setter
    def expiration_ts(self, value: int):
        self.__expiration_ts = value

    @property
    def trade_price_range(self):
        return self.__trade_price_range
    
    @trade_price_range.setter
    def trade_price_range(self, value: float):
        self.__trade_price_range = value

    @property
    def open_position_max(self):
        return self.__open_position_max
    
    @open_position_max.setter
    def open_position_max(self, value: int):
        self.__open_position_max = value

    @property
    def minimum_market_price_delta(self):
        return self.__minimum_market_price_delta
    
    @minimum_market_price_delta.setter
    def minimum_market_price_delta(self, value: float):
        self.__minimum_market_price_delta = value

    def get_balance(self, balance: int):
        self.balance = balance

    def _reverse_cum(self,yes_dollars: list):
        reverse_cum = []
        current_sum = 0
        for price, qty in reversed(yes_dollars):
            current_sum += qty
            reverse_cum.append([price, current_sum])
        return reverse_cum

    def _find_the_last_price_and_qty(self, reverse_cum: list, incentive_size: int):
        for i in range(len(reverse_cum)):
            if float(reverse_cum[i][1]) >= float(incentive_size):
                # Found match - return i-1 (previous index before threshold)
                return max(0, i - 1)
        # No match found - return last valid index
        return max(0, len(reverse_cum) - 1)


    def prepare_open_order(self,
            order_book: dict,
            order_market_book: dict,
        ):

        for ticker in order_book:
            if ticker in order_market_book:
                
                if order_market_book[ticker]['yes_dollars'] is None or order_market_book[ticker]['no_dollars'] is None:
                    continue
                yes_book = self._reverse_cum(order_market_book[ticker]['yes_dollars'])
                no_book = self._reverse_cum(order_market_book[ticker]['no_dollars'])
                yes_idx = self._find_the_last_price_and_qty(yes_book, order_book[ticker]['target_size'])
                no_idx = self._find_the_last_price_and_qty(no_book, order_book[ticker]['target_size'])
                market_yes_price = float(yes_book[0][0])
                market_no_price = float(no_book[0][0])
                yes_price = float(yes_book[yes_idx][0])
                yes_qty = float(yes_book[yes_idx][1])
                no_price = float(no_book[no_idx][0])
                no_qty = float(no_book[no_idx][1])
                price = min(yes_price, no_price)
                market_yes_price_delta = abs(float(market_yes_price) - float(yes_price))
                market_no_price_delta = abs(float(market_no_price) - float(no_price))

                if yes_qty > order_book[ticker]['target_size'] and no_qty > order_book[ticker]['target_size']:
                    continue

                price_name = 'yes_price_dollars' if yes_price < no_price else 'no_price_dollars'

                if price_name == 'yes_price_dollars' and yes_qty > order_book[ticker]['target_size']:
                    continue
                if price_name == 'no_price_dollars' and no_qty > order_book[ticker]['target_size']:
                    continue
                if price_name == 'yes_price_dollars' and (yes_price < self.trade_price_range[0] or yes_price > self.trade_price_range[1] or market_yes_price_delta < float(self.minimum_market_price_delta)):
                    continue
                if price_name == 'no_price_dollars' and (no_price < self.trade_price_range[0] or no_price > self.trade_price_range[1] or market_no_price_delta < float(self.minimum_market_price_delta)):
                    continue

                order = {
                    'ticker': ticker,
                    'side': 'yes' if yes_price < no_price else 'no',
                    price_name: f"{price:.4f}",
                    'price': price,
                    'price_name': price_name,
                    'target_size': order_book[ticker]['target_size'],
                    'title': order_book[ticker]['title'],
                    'rules_primary': order_book[ticker]['rules_primary'],
                    'yes_qty': yes_qty,
                    'no_qty': no_qty,
                    'yes_price': yes_price,
                    'no_price': no_price,
                    'market_yes_price': market_yes_price,
                    'market_no_price': market_no_price,
                    'market_yes_price_delta': market_yes_price_delta,
                    'market_no_price_delta': market_no_price_delta,
                    'yes_order_book': yes_book,
                    'no_order_book': no_book,
                }
                self.open_trade_orders[ticker] = order
        
        sorted_items = sorted(self.open_trade_orders.items(), key=lambda x: x[1]['price'])[:self.open_position_max]
        self.open_trade_orders = dict(sorted_items)

    def get_open_trade_orders(self):
        return self.open_trade_orders

    def check_open_order_expiration(self, incentive_dict: dict):
        for ticker, _ in self.open_trade_orders.items():
            if ticker not in incentive_dict:
                self.open_trade_orders.pop(ticker)

    def has_open_position(self):
        return len(self.open_trade_orders) > 0

    def create_open_order(self):
        if self.balance <= 0:
            raise ValueError("Insufficient balance")
            return

        market_orders = []
        for key, order in self.open_trade_orders.items():
            if order['price'] * float(self.trade_size) < self.balance:
                # no incentive for the no bid price
                # API expects no_price_dollars as a string with 4 decimal places
                # When expiration_ts is provided, time_in_force should be omitted
                expiration_ts = int((datetime.now() + timedelta(seconds=self.expiration_ts)).timestamp())
                tmp_market_order = {
                    'ticker': key,
                    'side': order['side'],
                    'action': "buy",
                    'count': self.trade_size,
                    # 'expiration_ts': expiration_ts,
                    'time_in_force': "good_till_canceled",
                    'type': "limit",
                    order['price_name']: f"{order['price']:.4f}",
                    'title': order['title'],
                    'rules_primary': order['rules_primary'],
                    'yes_qty': order['yes_qty'],
                    'no_qty': order['no_qty'],
                    'yes_price': order['yes_price'],
                    'no_price': order['no_price'],
                    'market_yes_price': order['market_yes_price'],
                    'market_no_price': order['market_no_price'],
                    'market_yes_price_delta': order['market_yes_price_delta'],
                    'market_no_price_delta': order['market_no_price_delta'],
                    'yes_order_book': order['yes_order_book'],
                    'no_order_book': order['no_order_book'],
                }
                market_orders.append(tmp_market_order)
        
        return market_orders

