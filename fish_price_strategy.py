"""
fish_price_strategy.py - Price logic for fish trades.

Sell price stages (triggered by fish_trade_time):
  Stage 1: (entry_price + best_ask) / 2
  Stage 2: (price_stage_1 + lowest_ask) / 2
  Stage 3: lowest_ask (lowest of market ask)

Lowest price = min of all asks in the order book.
"""

from datetime import datetime, timedelta


class FISH_PRICE_STRATEGY:

    __VOLUME_THRESHOLD = 100
    __MIN_PRICE = 0.01
    __MIN_ENTRY_PRICE = 0.03
    __MAX_ENTRY_PRICE = 0.13

    def __init__(self,
        entry_price: float = None,
        trade_price: float = None,
        action: str = None,
        side: str = None,
    ):
        self.entry_price = entry_price
        self.trade_price = trade_price
        self.action = action
        self.side = side

    @property
    def volume_threshold(self):
        return self.__VOLUME_THRESHOLD

    @volume_threshold.setter
    def volume_threshold(self, value: int):
        self.__VOLUME_THRESHOLD = value

    def _clamp_price(self, price: float) -> float:
        if price is None or price < self.__MIN_PRICE:
            return self.__MIN_PRICE
        return round(price, 4)

    def _parse_order_book_prices(self, market_book: dict) -> list:
        """Extract YES prices as floats (0.01-0.99). API may use cents (1-99)."""
        if self.side == 'yes':
            order_book = market_book.get('no_dollars', market_book.get('no', []))
        else:
            order_book = market_book.get('yes_dollars', market_book.get('yes', []))
        prices = []
        for pe in order_book or []:
            try:
                p = 1 - float(pe[0])
                if p > 1:
                    p = p / 100
                prices.append(round(float(p), 4))
            except (ValueError, TypeError, IndexError):
                continue
        return prices

    def _get_best_ask(self, order_book_prices: list) -> float | None:
        """Best ask = lowest price at which we can sell (top of book for YES)."""
        if not order_book_prices:
            return None
        return min(order_book_prices)

    def _get_lowest_ask(self, order_book_prices: list) -> float | None:
        """Lowest ask = minimum of all asks in the book (same as best for single-side)."""
        if not order_book_prices:
            return None
        return min(order_book_prices)

    def get_best_ask(self, market_book: dict) -> float | None:
        """Best ask = lowest price at which we can sell (top of book for YES)."""
        order_book = market_book.get('no_dollars', market_book.get('no', []))
        results = []
        if not order_book:
            return self.__MIN_PRICE
        if order_book:
            for pe in order_book:
                try:
                    p = float(pe[0])
                    if p > 1:
                        p = p / 100
                    results.append(1 - round(float(p), 2))
                except (ValueError, TypeError, IndexError):
                    continue
        return min(results)


    def get_best_bid(self, market_book: dict) -> float | None:
        """Best bid = highest price at which we can buy (top of book for YES)."""
        order_book = market_book.get('yes_dollars', market_book.get('yes', []))
        results = [0.01]
        if order_book:
            for pe in order_book:
                try:
                    p = float(pe[0])
                    if p > 1:
                        p = p / 100
                    results.append(round(float(p), 2))
                except (ValueError, TypeError, IndexError):
                    continue
        return max(results)

    def get_buy_price_strategy(self, market_book: dict):
        """
        Find buy price for a RESTING order (maker).
        Best YES ask = 1 - max(no_dollars). Our bid must be < best_yes_ask to rest.
        Pick lowest price <= 0.15 and > 0.01 with cumulative volume >= threshold.
        """
        yes_book = market_book.get('yes_dollars', market_book.get('yes', []))
        no_book = market_book.get('no_dollars', market_book.get('no', []))
        if not yes_book:
            return None

        best_yes_ask = None
        if no_book:
            no_prices = []
            for pe in no_book:
                try:
                    p = float(pe[0])
                    if p > 1:
                        p = p / 100
                    no_prices.append(round(float(p), 4))
                except (ValueError, TypeError, IndexError):
                    continue
            if no_prices:
                best_yes_ask = 1 - max(no_prices)

        entries = []
        for pe in yes_book:
            try:
                p, v = float(pe[0]), float(pe[1])
                if p > 1:
                    p = p / 100
                entries.append((p, v))
            except (ValueError, TypeError, IndexError):
                print(f"Error parsing price: {pe}")
                continue
        if not entries:
            return None

        if best_yes_ask is not None and best_yes_ask <= self.__MIN_PRICE:
            return None

        entries.sort(key=lambda x: -x[0])
        cum = 0
        result_price = None
        for p, v in entries:
            cum += v
            if 0.01 < p <= self.__MAX_ENTRY_PRICE and cum >= self.__VOLUME_THRESHOLD:
                if best_yes_ask is None or p < best_yes_ask:
                    result_price = p
                    break
            elif p > self.__MAX_ENTRY_PRICE:
                if best_yes_ask is None or p < best_yes_ask:
                    result_price = self.__MAX_ENTRY_PRICE
                    break

        if result_price is not None:
            p = round(result_price, 4)
            return p if p >= self.__MIN_ENTRY_PRICE else None
        for p, v in sorted(entries, key=lambda x: x[0]):
            if p > 0.01 and p <= self.__MAX_ENTRY_PRICE and v >= self.__VOLUME_THRESHOLD:
                if best_yes_ask is None or p < best_yes_ask:
                    r = round(p, 4)
                    return r if r >= self.__MIN_ENTRY_PRICE else None
        above = [p for p, _ in entries if 0.01 < p <= self.__MAX_ENTRY_PRICE and (best_yes_ask is None or p < best_yes_ask)]
        if not above:
            return None
        r = round(min(above), 4)
        return r if r >= self.__MIN_ENTRY_PRICE else None

    def update_price_strategy(self, market_book: dict, stage: int):
        """
        Update trade_price based on close stage.

        Stage 1: (open_sell_order_price + entry_price) / 2  [open_sell_order_price = current trade_price]
        Stage 2: (price_stage_1 + lowest_ask) / 2
        Stage 3: lowest_ask
        """
        if stage not in (1, 2, 3):
            return

        order_book_prices = self._parse_order_book_prices(market_book)

        lowest_ask = self._get_lowest_ask(order_book_prices)
        if not order_book_prices or lowest_ask is None or round(float(lowest_ask), 4) <= round(float(self.__MIN_PRICE), 4):
            self.trade_price = round(self.__MIN_PRICE, 4)
            return

        entry = self.entry_price if self.entry_price is not None else self.__MIN_PRICE

        if stage == 1:
            self.trade_price = self._clamp_price(round((3 * entry) / 2, 4))
        elif stage == 2:
            # price_above = result of stage 1 = current trade_price (already updated)
            self.trade_price = self._clamp_price(round((3 * entry) / 4, 4))
        elif stage == 3:
            self.trade_price = self._clamp_price(round(lowest_ask, 4))
        
        self.trade_price = self._clamp_price(round(max(self.trade_price, lowest_ask), 4))
