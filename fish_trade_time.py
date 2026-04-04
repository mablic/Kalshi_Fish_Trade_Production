"""
fish_trade_time.py - All trade times as configurable constants.
Returns time windows to trigger fish_price_strategy for sell order updates.

Strategy:
  Today:
    10AM: start tmr low + high (place buy orders)
    4PM:  stop tmr low  (cancel unfilled buy orders)
    8PM:  stop tmr high (cancel unfilled buy orders)

  Next day (tmr becomes today at midnight):
    0AM:  start today low
    2AM:  close low 1st  (entry + best_ask) / 2
    3AM:  close low 2nd  (price_1st + lowest_ask) / 2
    4AM:  close low 3rd  lowest ask
    5AM:  stop today low (cancel unfilled buy orders)

    5AM:  close tmr-high 1st
    6AM:  close tmr-high 2nd
    7AM:  close tmr-high 3rd

    8AM:  start today high
    9AM:  stop today high (cancel unfilled buy orders) + close today-high 1st
    10AM: close today-high 2nd
    11AM: close today-high 3rd
"""

from datetime import datetime, timedelta


class FISH_TRADE_TIME:

    __instance = None

    # --- Today: tomorrow low/high ---
    TOMORROW_LOW_START_HOUR = 9
    TOMORROW_LOW_STOP_HOUR = 16   # 4PM
    TOMORROW_HIGH_START_HOUR = 9
    TOMORROW_HIGH_STOP_HOUR = 20  # 8PM

    # --- Next day: today low ---
    TODAY_LOW_START_HOUR = 0
    TODAY_LOW_CLOSE_HOURS = (2, 3, 4)   # 2AM=1st, 3AM=2nd, 4AM=3rd
    TODAY_LOW_STOP_HOUR = 5             # 5AM: cancel remaining unfilled buy orders

    # --- Next day: tmr high (bought yesterday, close 5-7AM) ---
    TMR_LOW_CLOSE_HOURS = (17, 18, 19)
    TMR_HIGH_CLOSE_HOURS = (23, 30, 50)

    # --- Next day: today high ---
    TODAY_HIGH_START_HOUR = 8
    TODAY_HIGH_CLOSE_HOURS = (10, 11, 12)
    TODAY_HIGH_STOP_HOUR = 9            # 9AM: cancel remaining unfilled buy orders

    # --- Fish incentive ---
    FISH_INCENTIVE_START_HOUR = 10
    FISH_INCENTIVE_STOP_HOUR = 16

    # One-shot flags (reset when date rolls)
    __START_TODAY_LOW = False
    __START_TODAY_HIGH = False
    __START_TOMORROW_LOW = False
    __START_TOMORROW_HIGH = False

    __today_date_str = None
    __tomorrow_date_str = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super(FISH_TRADE_TIME, cls).__new__(cls)
        return cls.__instance

    def _hour(self) -> int:
        return datetime.now().hour

    # --- Start trade times (one-shot per date) ---
    def is_today_low_start_trade_time(self) -> bool:
        """Start any time within 0-2AM (before close 2-4AM)."""
        h = self._hour()
        if h >= self.TODAY_LOW_CLOSE_HOURS[0] or self.__START_TODAY_LOW:
            return False
        if h >= self.TODAY_LOW_START_HOUR:
            self.__START_TODAY_LOW = True
            return True
        return False

    def is_today_high_start_trade_time(self) -> bool:
        """Start any time within 8AM hour (8:00-8:59, before close 9-11AM)."""
        h = self._hour()
        if h >= self.TODAY_HIGH_CLOSE_HOURS[0] or self.__START_TODAY_HIGH:
            return False
        if h >= self.TODAY_HIGH_START_HOUR:
            self.__START_TODAY_HIGH = True
            return True
        return False

    def is_tomorrow_low_start_trade_time(self) -> bool:
        """Start any time within 10AM hour (10:00-10:59)."""
        h = self._hour()
        if h >= self.TOMORROW_LOW_START_HOUR + 3 or self.__START_TOMORROW_LOW:
            return False
        if h >= self.TOMORROW_LOW_START_HOUR:
            self.__START_TOMORROW_LOW = True
            return True
        return False

    def is_tomorrow_high_start_trade_time(self) -> bool:
        """Start any time within 10AM hour (10:00-10:59)."""
        h = self._hour()
        if h >= self.TOMORROW_HIGH_START_HOUR + 3 or self.__START_TOMORROW_HIGH:
            return False
        if h >= self.TOMORROW_HIGH_START_HOUR:
            self.__START_TOMORROW_HIGH = True
            return True
        return False

    # --- Stop trade times (cancel unfilled buy orders) ---
    def is_today_low_stop_trade_time(self) -> bool:
        """5AM: cancel remaining unfilled buy orders for today's low."""
        return self._hour() >= self.TODAY_LOW_STOP_HOUR

    def is_today_high_stop_trade_time(self) -> bool:
        """9AM: cancel remaining unfilled buy orders for today's high."""
        return self._hour() >= self.TODAY_HIGH_STOP_HOUR

    def is_tomorrow_low_stop_trade_time(self) -> bool:
        """4PM: cancel remaining unfilled buy orders for tomorrow's low."""
        return self._hour() >= self.TOMORROW_LOW_STOP_HOUR

    def is_tomorrow_high_stop_trade_time(self) -> bool:
        """8PM: cancel remaining unfilled buy orders for tomorrow's high."""
        return self._hour() >= self.TOMORROW_HIGH_STOP_HOUR

    # --- Close stages for price strategy (1, 2, 3 or 0 = no update) ---
    def get_close_stage_for_today_low(self) -> int:
        """Return 1, 2, 3 for close hours 2-4AM; 0 otherwise."""
        h = self._hour()
        stage = 0
        for i, close_hour in enumerate(self.TODAY_LOW_CLOSE_HOURS, 1):
            if h >= close_hour:
                stage = i
        return stage

    def get_close_stage_for_tmr_low(self) -> int:
        """Return 1, 2, 3 for close hours 7-9AM; 0 otherwise."""
        h = self._hour()
        stage = 0
        for i, close_hour in enumerate(self.TMR_LOW_CLOSE_HOURS, 1):
            if h >= close_hour:
                stage = i
        return stage

    def get_close_stage_for_tmr_high(self) -> int:
        """Return 1, 2, 3 for close hours 5-7AM; 0 otherwise."""
        h = self._hour()
        stage = 0
        for i, close_hour in enumerate(self.TMR_HIGH_CLOSE_HOURS, 1):
            if h >= close_hour:
                stage = i
        return stage

    def get_close_stage_for_today_high(self) -> int:
        """Return 1, 2, 3 for close hours 9-11AM; 0 otherwise."""
        h = self._hour()
        stage = 0
        for i, close_hour in enumerate(self.TODAY_HIGH_CLOSE_HOURS, 1):
            if h >= close_hour:
                stage = i
        return stage

    def get_close_stage_for_high(self) -> int:
        """
        Return close stage for high tickers.
        Uses tmr_high window (5-7AM) or today_high window (9-11AM) based on current hour.
        """
        if self._hour() in self.TMR_HIGH_CLOSE_HOURS:
            return self.get_close_stage_for_tmr_high()
        if self._hour() in self.TODAY_HIGH_CLOSE_HOURS:
            return self.get_close_stage_for_today_high()
        return 0

    def is_fish_incentive_start_trade_time(self) -> bool:
        """10AM: start fish incentive program."""
        return self._hour() >= self.FISH_INCENTIVE_START_HOUR and self._hour() < self.FISH_INCENTIVE_STOP_HOUR

    def is_fish_incentive_stop_trade_time(self) -> bool:
        """13PM: stop fish incentive program."""
        return self._hour() >= self.FISH_INCENTIVE_STOP_HOUR

    # --- Legacy property names for fish_trade compatibility ---
    @property
    def today_low_determine_time(self) -> int:
        return self.TODAY_LOW_CLOSE_HOURS[-1]  # 4AM

    @property
    def today_high_determine_time(self) -> int:
        return self.TODAY_HIGH_CLOSE_HOURS[-1]  # 11AM

    @property
    def tomorrow_low_determine_time(self) -> int:
        return self.TODAY_LOW_CLOSE_HOURS[-1]  # 4AM (on market date)

    @property
    def tomorrow_high_determine_time(self) -> int:
        return self.TMR_HIGH_CLOSE_HOURS[-1]  # 7AM (on market date)

    def reset_start_trade_time(self):
        self.__START_TODAY_LOW = False
        self.__START_TODAY_HIGH = False
        self.__START_TOMORROW_LOW = False
        self.__START_TOMORROW_HIGH = False

    def update_dates(self, today_str: str, tomorrow_str: str):
        prev_today = self.__today_date_str
        self.__today_date_str = today_str
        self.__tomorrow_date_str = tomorrow_str
        if prev_today is not None and today_str != prev_today:
            self.reset_start_trade_time()

    def get_today_date_formatted(self) -> str:
        if self.__today_date_str:
            return self._format_date_str(self.__today_date_str)
        return self._format_date_str(datetime.now().strftime("%Y-%m-%d"))

    def get_tomorrow_date_formatted(self) -> str:
        if self.__tomorrow_date_str:
            return self._format_date_str(self.__tomorrow_date_str)
        return self._format_date_str((datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d"))

    def _format_date_str(self, date_str: str) -> str:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
            return f"{dt.strftime('%y')}{month_names[dt.month - 1]}{dt.strftime('%d')}"
        except (ValueError, AttributeError):
            return ""

    @property
    def today_date_str(self):
        return self.__today_date_str

    @property
    def tomorrow_date_str(self):
        return self.__tomorrow_date_str
