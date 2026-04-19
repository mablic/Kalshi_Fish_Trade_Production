# new fish strategy incentive program
import re

class FISH_INCENTIVE:

    __instance = None

    def __new__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super(FISH_INCENTIVE, cls).__new__(cls)
        return cls.__instance

    def __init__(self, fish_incentive_threshold: float = 0.02, fish_incentive_volume_threshold: int = 500):
        self.fish_incentive_dict = {}
        self.fish_ticker_market_orders = {}
        self.FISH_INCENTIVE_THRESHOLD = fish_incentive_threshold
        self.FISH_INCENTIVE_VOLUME_THRESHOLD = fish_incentive_volume_threshold

    def load_from_incentive_programs(self, incentive_response: dict, filter_ticker_list: list = []):
        """
        Take incentive program API response, filter for weather tickers (KXLOW or KXHIGH),
        and store them in self.fish_incentive_dict.
        incentive_response: dict from client.get_market_incentive(), e.g. {'incentive_programs': [...]}
        """
        self.fish_incentive_dict = {}
        programs = incentive_response.get('incentive_programs') or incentive_response
        if isinstance(programs, dict):
            programs = programs.get('incentive_programs') or []
        for incentive in programs:
            ticker = incentive.get('market_ticker') or ''
            ticker_upper = ticker.upper()
            for filter_ticker in filter_ticker_list:
                if filter_ticker in ticker:
                    continue
                if ticker_upper.startswith('KXLOW') or ticker_upper.startswith('KXHIGH'):
                    self.fish_incentive_dict[ticker] = incentive

    def load_fish_incentive_dict(self,
        fish_incentive_dict: dict,
        filter_dict: dict
    ):
        """Apply filter for the weather incentive only (legacy)."""
        for incentive in fish_incentive_dict:
            curr_incentive_ticker = incentive['market_ticker']
            for filter_regex in filter_dict.keys():
                if re.search(re.compile(f'{filter_regex}'), curr_incentive_ticker):
                    self.fish_incentive_dict[curr_incentive_ticker] = incentive
                    break

    def get_fish_incentive_tickers(self):
        return self.fish_incentive_dict.keys()

    def update_fish_incentive_market_ticker(self, ticker: str, market_orders: dict):
        """
        Process market_orders: order book for ticker.
        Reverse yes_book by price (highest first, top down).
        - If >= 300 volume above 0.02 from top down: do nothing.
        - If < 300 above 0.02: check 0.01.
          - If < 300 above 0.01: save ticker -> 0.01
          - Else: save ticker -> 0.02
        """
        if ticker not in self.fish_incentive_dict.keys():
            return

        ob = market_orders.get('orderbook') or market_orders.get('orderbook_fp') or market_orders
        yes_book = ob.get('yes_dollars') or ob.get('yes') or []
        if not yes_book:
            self.fish_ticker_market_orders[ticker] = None
            return

        entries = []
        for pe in yes_book:
            try:
                p = float(pe[0])
                if p > 1:
                    p = p / 100
                qty = float(pe[1]) if len(pe) >= 2 else 0
                entries.append((p, qty))
            except (ValueError, TypeError, IndexError):
                continue

        # Reverse by price: highest first (top down)
        entries.sort(key=lambda x: x[0], reverse=True)

        volume_above_003 = sum(qty for p, qty in entries if p >= 0.03)
        volume_above_002 = sum(qty for p, qty in entries if p >= 0.02)

        if volume_above_003 >= self.FISH_INCENTIVE_VOLUME_THRESHOLD:
            return  # 300+ above 0.02, do nothing

        if volume_above_003 < self.FISH_INCENTIVE_VOLUME_THRESHOLD:
            self.fish_ticker_market_orders[ticker] = 0.03
        elif volume_above_002 < self.FISH_INCENTIVE_VOLUME_THRESHOLD:
            self.fish_ticker_market_orders[ticker] = 0.02
        else:
            self.fish_ticker_market_orders[ticker] = 0.01


    def get_fish_ticker_market_orders(self):
        """Return the filtered tickers dict (highest yes bid <= 0.05, volume 0.02-0.05 < 300)."""
        return self.fish_ticker_market_orders

