# new fish strategy incentive program
import re


class FISH_INCENTIVE:

    __instance = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super(FISH_INCENTIVE, cls).__new__(cls)
        return cls.__instance

    def __init__(self):
        self.fish_incentive_dict = {}
    
    def load_fish_incentive_dict(self, 
        fish_incentive_dict: dict, 
        filter_dict: dict
    ):

        # apply filter for the weather incentive only
        for incentive in fish_incentive_dict:
            curr_incentive_ticker = incentive['market_ticker']
            for filter_regex in filter_dict.keys():
                if re.search(re.compile(f'{filter_regex}'), curr_incentive_ticker):
                    self.fish_incentive_dict[curr_incentive_ticker] = incentive
                    break

    def get_fish_incentive_tickers(self):
        return self.fish_incentive_dict.keys()

