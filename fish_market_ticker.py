from datetime import datetime, timedelta


class FISH_MARKET_TICKER:

    __ticker_range = 1
    __instance = None
    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super(FISH_MARKET_TICKER, cls).__new__(cls)
        return cls.__instance


    def _format_date_for_ticker(self, date_str: str):
        """
        Convert date string from '2026-01-29' to '26JAN29' (uppercase)
        """
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
            year_short = dt.strftime("%y")
            month_short = month_names[dt.month - 1]
            day = dt.strftime("%d")
            return f"{year_short}{month_short}{day}"
        except (ValueError, AttributeError):
            return None

    def _get_markets_by_series(self, client, series_ticker: str):
        """
        Get all markets in a series from Kalshi API using the client.
        First tries with series_ticker parameter, then tries searching all markets.
        Returns list of market tickers, or empty list if series doesn't exist.
        
        Args:
            client: KalshiHttpClient instance
            series_ticker: Series ticker to search for
        """
        # Try 1: Search by series_ticker with status filter
        try:
            response = client.get_markets_by_series(series_ticker=series_ticker.upper(), status="open", limit=1000)
            if response and 'markets' in response:
                markets = [m["ticker"] for m in response['markets']]
                if markets:
                    return markets
        except Exception:
            pass
        
        # Try 2: Search without status filter
        try:
            response = client.get_markets_by_series(series_ticker=series_ticker.upper(), limit=1000)
            if response and 'markets' in response:
                markets = [m["ticker"] for m in response['markets']]
                if markets:
                    return markets
        except Exception:
            pass
        
        # Try 3: Search markets by ticker pattern (if series_ticker includes date)
        # Extract base series (e.g., "KXHIGHPHIL" from "KXHIGHPHIL-26JAN30")
        base_series = series_ticker.upper().split("-")[0]
        try:
            response = client.get_markets_by_series(series_ticker=base_series, limit=1000)
            if response and 'markets' in response:
                all_markets = [m["ticker"] for m in response['markets']]
                # Filter markets that start with our series ticker
                filtered = [t for t in all_markets if t.startswith(series_ticker.upper())]
                if filtered:
                    return filtered
        except Exception:
            pass
        
        # Try 3b: Try common city code variations (e.g., PHI -> PHIL)
        city_variations = [base_series]
        if base_series.endswith("PHI") and not base_series.endswith("PHIL"):
            base_series_variant = base_series + "L"
            city_variations.append(base_series_variant)
        elif base_series.endswith("NYC") and not base_series.endswith("NY"):
            base_series_variant = base_series[:-1]
            city_variations.append(base_series_variant)
        
        for variant in city_variations[1:]:
            try:
                response = client.get_markets_by_series(series_ticker=variant, limit=1000)
                if response and 'markets' in response:
                    all_markets = [m["ticker"] for m in response['markets']]
                    variant_series_ticker = series_ticker.upper().replace(base_series, variant)
                    filtered = [t for t in all_markets if t.startswith(variant_series_ticker)]
                    if filtered:
                        return filtered
            except Exception:
                pass
        
        # Try 4: Search all markets (no status filter) and filter by ticker prefix
        try:
            response = client.get_markets_by_series(limit=1000)
            if response and 'markets' in response:
                all_markets = [m["ticker"] for m in response['markets']]
                filtered = [t for t in all_markets if t.startswith(series_ticker.upper())]
                if filtered:
                    return filtered
                
                date_part = series_ticker.upper().split("-")[1] if "-" in series_ticker.upper() else None
                if date_part:
                    if base_series.endswith("PHI") and not base_series.endswith("PHIL"):
                        base_variant = base_series + "L"
                        filtered_base = [t for t in all_markets if t.startswith(base_variant)]
                        if filtered_base:
                            filtered = [t for t in filtered_base if date_part in t]
                            if filtered:
                                return filtered
                    
                    filtered_base = [t for t in all_markets if t.startswith(base_series)]
                    if filtered_base:
                        filtered = [t for t in filtered_base if date_part in t]
                        if filtered:
                            return filtered
        except Exception:
            pass
        
        return []

    def _order_book_total_volume(self, client, ticker: str) -> int:
        """Sum of all quantities in yes + no order book for this ticker. Returns 0 on error."""
        try:
            resp = client.get_market_ticker_order_book(ticker)
            ob = (resp or {}).get("orderbook") or {}
            total = 0
            for side in ("yes_dollars", "no_dollars", "yes", "no"):
                for pe in ob.get(side) or []:
                    if len(pe) >= 2:
                        try:
                            total += int(pe[1])
                        except (ValueError, TypeError):
                            pass
            return total
        except Exception:
            return 0

    def _top_n_by_volume(self, client, tickers: list, n: int) -> list:
        """Return top n tickers by order book total volume (descending). Ties keep order."""
        if not tickers or n <= 0:
            return []
        with_vol = [(t, self._order_book_total_volume(client, t)) for t in tickers]
        with_vol.sort(key=lambda x: -x[1])
        return [t for t, _ in with_vol[:n]]

    def get_tickers_for_date(self, client, city_code: str, date_str: str, weather_range: list = None, ticker_type: str = None):

        kalshi_city = city_code.upper()
        date_formatted = self._format_date_for_ticker(date_str)
        if not date_formatted:
            return []

        low_series_ticker = f"kxlowt{kalshi_city}-{date_formatted}"
        low_markets = self._get_markets_by_series(client, low_series_ticker)

        high_series_ticker = f"kxhigh{kalshi_city}-{date_formatted}"
        high_markets = self._get_markets_by_series(client, high_series_ticker)

        r = self.__ticker_range
        n_keep_first = 2 * r + 1  # first 3 tickers: closest to target

        def closest_n(tickers, target, n):
            with_temp = [(t, self._extract_temp_from_ticker(t)) for t in tickers]
            valid = [(t, temp) for t, temp in with_temp if temp is not None]
            sorted_by_dist = sorted(valid, key=lambda x: abs(x[1] - target))
            return [t for t, _ in sorted_by_dist[:n]]

        def median_target(tickers):
            temps = [self._extract_temp_from_ticker(t) for t in tickers]
            valid = [t for t in temps if t is not None]
            return sum(valid) / len(valid) if valid else None

        def fourth_by_volume(full_tickers, target, first_three, kalshi_client):
            """From full_tickers, pick one with temp in [target-2, target+2] not in first_three, with highest volume."""
            first_set = set(first_three)
            in_band = []
            for t in full_tickers:
                if t in first_set:
                    continue
                temp = self._extract_temp_from_ticker(t)
                if temp is None or temp < target - 2 or temp > target + 2:
                    continue
                vol = self._order_book_total_volume(kalshi_client, t)
                in_band.append((t, vol))
            if not in_band:
                return None
            in_band.sort(key=lambda x: -x[1])
            return in_band[0][0]

        if weather_range is not None and len(weather_range) >= 2:
            low_val, high_val = weather_range[0], weather_range[1]
            first_3_low = closest_n(low_markets, low_val, n_keep_first)
            first_3_high = closest_n(high_markets, high_val, n_keep_first)
            fourth_low = fourth_by_volume(low_markets, low_val, first_3_low, client)
            fourth_high = fourth_by_volume(high_markets, high_val, first_3_high, client)
            # Fallback: when no ticker in ±2 band, use 4th-closest so we return 4 when series has 4+
            if fourth_low is None and len(low_markets) >= 4:
                first_4 = closest_n(low_markets, low_val, 4)
                fourth_low = first_4[3] if len(first_4) > 3 else None
            if fourth_high is None and len(high_markets) >= 4:
                first_4 = closest_n(high_markets, high_val, 4)
                fourth_high = first_4[3] if len(first_4) > 3 else None
            low_markets = first_3_low + ([fourth_low] if fourth_low else [])
            high_markets = first_3_high + ([fourth_high] if fourth_high else [])
        else:
            if low_markets:
                t = median_target(low_markets)
                if t is not None:
                    first_3 = closest_n(low_markets, t, n_keep_first)
                    fourth = fourth_by_volume(low_markets, t, first_3, client)
                    if fourth is None and len(low_markets) >= 4:
                        first_4 = closest_n(low_markets, t, 4)
                        fourth = first_4[3] if len(first_4) > 3 else None
                    low_markets = first_3 + ([fourth] if fourth else [])
                else:
                    low_markets = low_markets[:n_keep_first]
            if high_markets:
                t = median_target(high_markets)
                if t is not None:
                    first_3 = closest_n(high_markets, t, n_keep_first)
                    fourth = fourth_by_volume(high_markets, t, first_3, client)
                    if fourth is None and len(high_markets) >= 4:
                        first_4 = closest_n(high_markets, t, 4)
                        fourth = first_4[3] if len(first_4) > 3 else None
                    high_markets = first_3 + ([fourth] if fourth else [])
                else:
                    high_markets = high_markets[:n_keep_first]

        if ticker_type == "low":
            return low_markets
        if ticker_type == "high":
            return high_markets
        return low_markets + high_markets

    def get_temperature_ticker(self, client, ticker_data: dict):
        """
        Get temperature tickers for given city codes and dates using the API client.
        
        Args:
            client: KalshiHttpClient instance
            ticker_data: Dictionary with city codes as keys and date lists as values
                        Format: {"CHI": ["2026-01-30", "2026-01-31"], ...}
        
        Returns:
            Dictionary with city codes as keys and nested dictionaries with dates and tickers
        """
        self.temperature_ticker_dict = {}
        
        for city_code, date_list in ticker_data.items():
            kalshi_city = city_code.lower()
            city_tickers = {}
            
            for date_str in date_list:
                date_formatted = self._format_date_for_ticker(date_str)
                if not date_formatted:
                    continue
                
                date_tickers = []
                
                # Get low tickers: search series kxlowt{city}-{date}
                low_series_ticker = f"kxlowt{kalshi_city}-{date_formatted}"
                low_markets = self._get_markets_by_series(client, low_series_ticker)
                date_tickers.extend(low_markets)
                
                # Get high tickers: search series kxhigh{city}-{date}
                high_series_ticker = f"kxhigh{kalshi_city}-{date_formatted}"
                high_markets = self._get_markets_by_series(client, high_series_ticker)
                date_tickers.extend(high_markets)
                
                if date_tickers:
                    city_tickers[date_str] = date_tickers
            
            self.temperature_ticker_dict[city_code] = city_tickers
        
        return self.temperature_ticker_dict

    def ticker_to_symbol(self, ticker: str) -> str:

        ticker_upper = ticker.upper()
        
        # Remove KXHIGH or KXLOWT prefix
        if ticker_upper.startswith("KXHIGH"):
            city_part = ticker_upper[6:]  # Remove "KXHIGH" (6 chars)
        elif ticker_upper.startswith("KXLOWT"):
            city_part = ticker_upper[6:]  # Remove "KXLOWT" (6 chars)
        else:
            # If it doesn't match expected format, try to extract anyway
            city_part = ticker_upper
        
        # Extract city code (everything before the first "-")
        city_symbol = city_part.split("-")[0]
        
        return city_symbol

    def _extract_temp_from_ticker(self, ticker: str):
        """
        Extract temperature value from ticker (e.g., "KXLOWTCHI-26MAR01-T27.5" -> 27.5)
        Returns float or None if temp cannot be extracted
        """
        try:
            parts = ticker.upper().split("-")
            if len(parts) >= 3:
                suffix = parts[-1]  # e.g. "T27.5" or "B32.5"
                if suffix.startswith("T") or suffix.startswith("B"):
                    return float(suffix[1:])
        except (ValueError, IndexError):
            pass
        return None

    def _extract_date_from_ticker(self, ticker: str) -> str:
        """
        Extract date part from ticker (e.g., "26JAN30" from "KXLOWTCHI-26JAN30-B32.5")
        Returns None if date cannot be extracted
        """
        try:
            ticker_upper = ticker.upper()
            if "-" in ticker_upper:
                parts = ticker_upper.split("-")
                if len(parts) >= 2:
                    date_part = parts[1]
                    if len(date_part) >= 6:
                        return date_part
        except Exception:
            pass
        return None

    def get_market_datetime_from_ticker(self, ticker: str, hour: int = 0) -> "datetime|None":
        """Parse ticker date (e.g. 26MAR02) to datetime for escape_time. Returns market date at given hour."""
        date_part = self._extract_date_from_ticker(ticker)
        if not date_part or len(date_part) < 6:
            return None
        try:
            month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
            yy, mm_str, dd = int(date_part[:2]), date_part[2:5], int(date_part[5:7])
            month = month_names.index(mm_str) + 1 if mm_str in month_names else 1
            year = 2000 + yy
            return datetime(year, month, int(dd), hour, 0, 0)
        except (ValueError, IndexError):
            return None

    __reference_trade_time = None

    def set_reference_trade_time(self, trade_time):
        """Set trade_time for reference dates (today/tomorrow). When set, is_today_* uses these."""
        self.__reference_trade_time = trade_time

    def _get_today_date_formatted(self) -> str:
        """Get today's date formatted for ticker (e.g., '26JAN30')"""
        if self.__reference_trade_time:
            return self.__reference_trade_time.get_today_date_formatted()
        today = datetime.now()
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        return f"{today.strftime('%y')}{month_names[today.month - 1]}{today.strftime('%d')}"

    def _get_tomorrow_date_formatted(self) -> str:
        """Get tomorrow's date formatted for ticker (e.g., '26JAN31')"""
        if self.__reference_trade_time:
            return self.__reference_trade_time.get_tomorrow_date_formatted()
        tomorrow = datetime.now() + timedelta(days=1)
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        return f"{tomorrow.strftime('%y')}{month_names[tomorrow.month - 1]}{tomorrow.strftime('%d')}"

    def is_today_low_ticker(self, ticker: str) -> bool:
        """
        Check if ticker is a low ticker for today's date.
        Returns True if ticker starts with 'KXLOWT' and date matches today.
        """
        if not ticker.upper().startswith("KXLOWT"):
            return False
        
        ticker_date = self._extract_date_from_ticker(ticker)
        today_date = self._get_today_date_formatted()
        
        return ticker_date == today_date
    
    def is_today_high_ticker(self, ticker: str) -> bool:
        """
        Check if ticker is a high ticker for today's date.
        Returns True if ticker starts with 'KXHIGH' and date matches today.
        """
        if not ticker.upper().startswith("KXHIGH"):
            return False
        
        ticker_date = self._extract_date_from_ticker(ticker)
        today_date = self._get_today_date_formatted()
        
        return ticker_date == today_date

    def is_tomorrow_low_ticker(self, ticker: str) -> bool:
        """
        Check if ticker is a low ticker for tomorrow's date.
        Returns True if ticker starts with 'KXLOWT' and date matches tomorrow.
        """
        if not ticker.upper().startswith("KXLOWT"):
            return False
        
        ticker_date = self._extract_date_from_ticker(ticker)
        tomorrow_date = self._get_tomorrow_date_formatted()
        
        return ticker_date == tomorrow_date

    def is_tomorrow_high_ticker(self, ticker: str) -> bool:
        """
        Check if ticker is a high ticker for tomorrow's date.
        Returns True if ticker starts with 'KXHIGH' and date matches tomorrow.
        """
        if not ticker.upper().startswith("KXHIGH"):
            return False
        
        ticker_date = self._extract_date_from_ticker(ticker)
        tomorrow_date = self._get_tomorrow_date_formatted()
        
        return ticker_date == tomorrow_date

    def is_ticker(self, ticker: str) -> bool:
        """Check if ticker is either a low or high ticker (any date)"""
        return ticker.upper().startswith("KXLOWT") or ticker.upper().startswith("KXHIGH")


if __name__ == "__main__":
    import os
    from clients import KalshiHttpClient, Environment
    from dotenv import load_dotenv
    from cryptography.hazmat.primitives import serialization
    from fish_parse_weather import FISH_PARSE_WEATHER

    load_dotenv()
    env = Environment.PROD
    KEYID = os.getenv('DEMO_KEYID') if env == Environment.DEMO else os.getenv('PROD_KEYID')
    KEYFILE = os.getenv('DEMO_KEYFILE') if env == Environment.DEMO else os.getenv('PROD_KEYFILE')

    try:
        with open(KEYFILE, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None
            )
    except FileNotFoundError:
        raise FileNotFoundError(f"Private key file not found at {KEYFILE}")
    except Exception as e:
        raise Exception(f"Error loading private key: {str(e)}")

    client = KalshiHttpClient(
        key_id=KEYID,
        private_key=private_key,
        environment=env
    )

    # CHImi only: get weather ranges (today = forecast+historical, tomorrow = forecast)
    site_dict_CHI = {
        "CHI": [
            "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW",
            "https://forecast.weather.gov/MapClick.php?lat=41.7885&lon=-87.7417&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMDW",
        ],
    }
    parse_weather = FISH_PARSE_WEATHER(site_dict_CHI)
    all_weather = parse_weather.get_all_weather()
    today_str = datetime.now().strftime("%Y-%m-%d")
    tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    CHI_weather = all_weather.get("CHI") or {}
    today_forecast = CHI_weather.get(today_str, {}).get("forecast")
    today_report = CHI_weather.get(today_str, {}).get("report")
    # Today: use report when available (e.g. [74, 84]); else forecast. Tomorrow: forecast only (same as fish_trade).
    today_range = today_report if today_report else today_forecast
    tomorrow_range = CHI_weather.get(tomorrow_str, {}).get("forecast")

    print("=" * 60)
    print("CHIMI (CHI) – Weather ranges")
    print("=" * 60)
    print(f"  Today    ({today_str}):  used range = {today_range}   (report = {today_report}, forecast = {today_forecast})")
    print(f"  Tomorrow ({tomorrow_str}): forecast range = {tomorrow_range}")
    print()

    mt = FISH_MARKET_TICKER()
    date_fmt_today = mt._format_date_for_ticker(today_str)
    date_fmt_tomorrow = mt._format_date_for_ticker(tomorrow_str)
    if not date_fmt_today or not date_fmt_tomorrow:
        print("Could not format dates for ticker")
    else:
        # All tickers in series (before volume filter)
        low_series_today = f"kxlowtCHI-{date_fmt_today}"
        high_series_today = f"kxhighCHI-{date_fmt_today}"
        low_series_tomorrow = f"kxlowtCHI-{date_fmt_tomorrow}"
        high_series_tomorrow = f"kxhighCHI-{date_fmt_tomorrow}"

        for label, series_ticker in [
            ("TODAY LOW", low_series_today),
            ("TODAY HIGH", high_series_today),
            ("TOMORROW LOW", low_series_tomorrow),
            ("TOMORROW HIGH", high_series_tomorrow),
        ]:
            tickers_all = mt._get_markets_by_series(client, series_ticker)
            print("-" * 60)
            print(f"  {label}  series={series_ticker}  ({len(tickers_all)} tickers)")
            print("-" * 60)
            with_vol = []
            for t in tickers_all:
                vol = mt._order_book_total_volume(client, t)
                temp = mt._extract_temp_from_ticker(t)
                with_vol.append((t, vol, temp))
            with_vol.sort(key=lambda x: -x[1])
            for t, vol, temp in with_vol:
                temp_str = f"  temp={temp}" if temp is not None else ""
                print(f"    {t}  order_book_volume={vol}{temp_str}")
            print()

        # Selected tickers (closest to weather range only; volume not used for selection)
        print("=" * 60)
        print("SELECTED TICKERS (closest to weather range)")
        print("=" * 60)
        for date_label, date_str, weather_range in [
            ("Today", today_str, today_range),      # today_range = report [74,84] when available
            ("Tomorrow", tomorrow_str, tomorrow_range),
        ]:
            print(f"  --- {date_label} ({date_str}), weather_range={weather_range} ---")
            for kind, ticker_type in [("low", "low"), ("high", "high")]:
                selected = mt.get_tickers_for_date(client, "CHI", date_str, weather_range, ticker_type=ticker_type)
                print(f"    {kind}: {selected}")
            print()