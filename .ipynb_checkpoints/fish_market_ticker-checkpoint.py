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

    def get_tickers_for_date(self, client, city_code: str, dates: list, weather_range: list = None):
        """
        Get tickers for a city code and list of dates using the API client.
        When weather_range is provided, only returns tickers around that low/high (within __ticker_range).

        Args:
            client: KalshiHttpClient instance
            city_code: City code (e.g., "CHI", "PHIL", "NYC")
            dates: List of date strings in format "YYYY-MM-DD" (e.g., ["2026-01-30", "2026-01-31"])
            weather_range: Optional [low, high] e.g. [65, 67]. Only tickers around low +/- __ticker_range
                and high +/- __ticker_range are kept. If None, returns all available tickers.

        Returns:
            Dictionary with city code as key and list of all tickers as value
            Format: {"CHI": {"2026-03-01": [...], "2026-03-02": [...]}, ...}
        """
        kalshi_city = city_code.upper()
        all_tickers = {}

        for date_str in dates:
            all_tickers[date_str] = []
            date_formatted = self._format_date_for_ticker(date_str)
            if not date_formatted:
                continue

            # Get low tickers: search series kxlowt{city}-{date}
            low_series_ticker = f"kxlowt{kalshi_city}-{date_formatted}"
            low_markets = self._get_markets_by_series(client, low_series_ticker)

            # Get high tickers: search series kxhigh{city}-{date}
            high_series_ticker = f"kxhigh{kalshi_city}-{date_formatted}"
            high_markets = self._get_markets_by_series(client, high_series_ticker)

            if weather_range is not None and len(weather_range) >= 2:
                low_val, high_val = weather_range[0], weather_range[1]
                r = self.__ticker_range
                n_keep = 2 * r + 1  # e.g. range=1 -> 3 tickers (target-1, target, target+1)

                def closest_n(tickers, target, n):
                    with_temp = [(t, self._extract_temp_from_ticker(t)) for t in tickers]
                    valid = [(t, temp) for t, temp in with_temp if temp is not None]
                    sorted_by_dist = sorted(valid, key=lambda x: abs(x[1] - target))
                    return [t for t, _ in sorted_by_dist[:n]]

                low_markets = closest_n(low_markets, low_val, n_keep)
                high_markets = closest_n(high_markets, high_val, n_keep)

            all_tickers[date_str].extend(low_markets)
            all_tickers[date_str].extend(high_markets)

        return {kalshi_city: all_tickers}

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
            # Ticker format: KXLOWTCHI-26JAN30-B32.5 or KXHIGHPHIL-26JAN30-T45
            # Date is between first and second "-"
            if "-" in ticker_upper:
                parts = ticker_upper.split("-")
                if len(parts) >= 2:
                    date_part = parts[1]
                    # Date format should be like "26JAN30" (6-7 chars: YYMMMDD)
                    if len(date_part) >= 6:
                        return date_part
        except Exception:
            pass
        return None

    def _get_today_date_formatted(self) -> str:
        """Get today's date formatted for ticker (e.g., '26JAN30')"""
        today = datetime.now()
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        year_short = today.strftime("%y")
        month_short = month_names[today.month - 1]
        day = today.strftime("%d")
        return f"{year_short}{month_short}{day}"

    def _get_tomorrow_date_formatted(self) -> str:
        """Get tomorrow's date formatted for ticker (e.g., '26JAN31')"""
        tomorrow = datetime.now() + timedelta(days=1)
        month_names = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
                      "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
        year_short = tomorrow.strftime("%y")
        month_short = month_names[tomorrow.month - 1]
        day = tomorrow.strftime("%d")
        return f"{year_short}{month_short}{day}"

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
    
    # Load environment variables
    load_dotenv()
    env = Environment.PROD  # toggle environment here
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

    # Initialize the HTTP client
    client = KalshiHttpClient(
        key_id=KEYID,
        private_key=private_key,
        environment=env
    )
    
    ticker_gen = FISH_MARKET_TICKER()
    tickers = ticker_gen.get_tickers_for_date(client, "CHI", ["2026-03-01", "2026-03-02"], [29, 33])
    print(f"output ticker is: {tickers}")