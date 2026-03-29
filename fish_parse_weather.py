import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup


def _empty_forecast():
    """Returns empty dict for forecast"""
    return {}


def _parse_date_from_cli_string(date_time_str):
    """
    Parse date string like "1242 AM EST THU JAN 29 2026" to "2026-01-29"
    """
    if not date_time_str:
        return None
    # Extract date part: "JAN 29 2026" or similar
    # Pattern: MONTH_NAME DAY YEAR
    month_map = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
    }
    # Match: 3-letter month, day, 4-digit year
    match = re.search(r"([A-Z]{3})\s+(\d{1,2})\s+(\d{4})", date_time_str)
    if match:
        month_str, day_str, year_str = match.groups()
        month = month_map.get(month_str)
        if month:
            try:
                day = int(day_str)
                year = int(year_str)
                return f"{year}-{month:02d}-{day:02d}"
            except ValueError:
                pass
    return None


class FISH_PARSE_WEATHER:

    __instance = None
    __initialized = False

    def __new__(cls, *args, **kwargs):
        if cls.__instance is None:
            cls.__instance = super(FISH_PARSE_WEATHER, cls).__new__(cls)
        return cls.__instance

    def __init__(self, site_dict: dict):
        if not self.__initialized:
            self.weather_sites = site_dict
            self.__initialized = True

    def _parse_single_site(self, url: str):
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        pre = soup.find("pre", class_="glossaryProduct")
        if pre is None:
            return {
                "url": url,
                "date_time": None,
                "maximum": None,
                "minimum": None,
            }

        text = pre.get_text("\n")

        # Example line to capture (tokens):
        # 1250 AM EST WED JAN 28 2026
        date_time_match = re.search(
            r"\b\d{3,4}\s+[AP]M\s+[A-Z]{3}\s+[A-Z]{3}\s+[A-Z]{3}\s+\d{1,2}\s+\d{4}\b",
            text,
        )
        date_time_str = date_time_match.group(0) if date_time_match else None

        # Example lines:
        #   MAXIMUM         25   8:50 PM ...
        #   MINIMUM         15   6:16 AM ...
        max_match = re.search(r"MAXIMUM\s+(-?\d+)", text)
        min_match = re.search(r"MINIMUM\s+(-?\d+)", text)

        maximum = int(max_match.group(1)) if max_match else None
        minimum = int(min_match.group(1)) if min_match else None

        return {
            "url": url,
            "date_time": date_time_str,
            "maximum": maximum,
            "minimum": minimum,
        }

    def get_daily_report_weather(self):
        """
        Parse daily report (position 0) for each city.

        Returns a dict keyed by city, e.g.:
        {"PHI": {"2026-01-29": [14, 23]}, "CHI": {"2026-01-29": [1, 18]}, ...}
        Format: {date_string: [minimum, maximum]}
        """
        results = {}
        for code, urls in self.weather_sites.items():
            if not urls:
                results[code] = {}
                continue
            daily_url = urls[0]
            try:
                parsed = self._parse_single_site(daily_url)
                date_time_str = parsed.get("date_time")
                date_str = _parse_date_from_cli_string(date_time_str)
                minimum = parsed.get("minimum")
                maximum = parsed.get("maximum")
                if date_str:
                    results[code] = {date_str: [minimum, maximum]}
                else:
                    results[code] = {}
            except Exception:
                results[code] = {}
        return results


    def _parse_forecast_dwml(self, url: str):
        """
        Fetch one 3-day DWML URL; group hourly temps by calendar day.
        Returns [[today_low, today_high], [tomorrow_low, tomorrow_high]]
        """
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        data = root.find("data")
        if data is None:
            return _empty_forecast()

        layout_key = "k-p1h-n1-0"
        start_times = []
        for tl in data.findall("time-layout"):
            key_el = tl.find("layout-key")
            if key_el is not None and (key_el.text or "").strip() == layout_key:
                start_times = [el.text for el in tl.findall("start-valid-time") if el.text]
                break

        temp_el = None
        for t in data.findall("parameters/temperature"):
            if t.get("type") == "hourly" and (t.get("time-layout") or "").strip() == layout_key:
                temp_el = t
                break
        if temp_el is None:
            return _empty_forecast()

        temps = []
        for v in temp_el.findall("value"):
            raw = (v.text or "").strip()
            if raw and "nil" not in v.attrib:
                try:
                    temps.append(int(raw))
                except ValueError:
                    temps.append(None)
            else:
                temps.append(None)

        n = min(len(start_times), len(temps))
        pairs = []
        for i in range(n):
            t = temps[i]
            if t is not None:
                pairs.append((start_times[i], t))

        if not pairs:
            return _empty_forecast()

        # Group by calendar date (local date from start_valid_time)
        by_date = {}
        for st, t in pairs:
            try:
                date = st.split("T")[0]
            except IndexError:
                continue
            by_date.setdefault(date, []).append(t)

        # Get available dates from forecast data (sorted)
        dates_sorted = sorted(by_date.keys())
        if not dates_sorted:
            return _empty_forecast()

        # "Today" = report date from DWML creation-date (in forecast timezone)
        # Navigate: head -> product -> creation-date
        head_el = root.find("head")
        today_str = None
        if head_el is not None:
            product_el = head_el.find("product")
            if product_el is not None:
                creation_el = product_el.find("creation-date")
                if creation_el is not None and creation_el.text:
                    # e.g. "2026-01-29T04:11:29-05:00" -> "2026-01-29"
                    today_str = creation_el.text.strip()[:10]
        
        # If creation-date is valid and exists in forecast data, use it as today
        # Otherwise use first date in forecast
        if today_str and today_str in by_date:
            # Find tomorrow relative to creation-date
            try:
                today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
                tomorrow_dt = today_dt + timedelta(days=1)
                tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
            except ValueError:
                # Fallback: use first 2 dates from forecast
                today_str = dates_sorted[0] if len(dates_sorted) > 0 else None
                tomorrow_str = dates_sorted[1] if len(dates_sorted) > 1 else None
        else:
            # Use first 2 dates from forecast data
            today_str = dates_sorted[0] if len(dates_sorted) > 0 else None
            tomorrow_str = dates_sorted[1] if len(dates_sorted) > 1 else None

        # Build result: {date_string: [low, high]} for today and tomorrow
        result = {}
        today_vals = by_date.get(today_str) if today_str else None
        tomorrow_vals = by_date.get(tomorrow_str) if tomorrow_str else None
        
        if today_vals:
            today_low = min(today_vals)
            today_high = max(today_vals)
            result[today_str] = [today_low, today_high]
        
        if tomorrow_vals:
            tomorrow_low = min(tomorrow_vals)
            tomorrow_high = max(tomorrow_vals)
            result[tomorrow_str] = [tomorrow_low, tomorrow_high]
        
        return result if result else {}

    def get_hourly_forcast_weather(self):
        """
        Parse 3-day forecast (position 1) for each city. Returns today's and
        tomorrow's high/low per city.

        Returns a dict keyed by city, e.g.:
        {"PHI": {"2026-01-29": [10, 19], "2026-01-30": [2, 18]}, ...}
        Format: {date_string: [low, high]} for today and tomorrow
        """
        results = {}
        for code, urls in self.weather_sites.items():
            if len(urls) < 2:
                results[code] = {}
                continue
            forecast_url = urls[1]
            try:
                results[code] = self._parse_forecast_dwml(forecast_url)
            except Exception:
                results[code] = {}
        return results

    # Obhistory uses different station IDs than WRH timeseries (KPHI->KPHL, KCHI->KMDW, KNYC->KLGA)
    _OBHISTORY_STATION = {
        "PHIL": "KPHL",
        "CHI": "KMDW",
        "NYC": "KLGA",
        "AUS": "KAUS",
        "LAX": "KLAX",
        "MIA": "KMIA",
        "DEN": "KDEN",
        "OKC": "KOKC",
        "TOKC": "KOKC",
        "MIN": "KMSP",
        "TMIN": "KMSP",
        "TATL": "KATL",
        "TNOLA": "KMSY",
        "TPHX": "KPHX",
        "TSATX": "KSAT",
        "TDAL": "KDFW",
        "TSFO": "KSFO",
        "TSEA": "KSEA",
        "THOU": "KHOU",
        "TBOS": "KBOS",
        "TLV": "KLAS",
        "TMSP": "KMSP",
    }

    def _parse_historical_table(self, url: str, city_code: str = None):
        """
        Parse historical weather from forecast.weather.gov obhistory page.
        Fetches the obhistory HTML, parses the table, extracts Air temp (column 7)
        and returns the lowest and highest.

        Returns a dict with:
        {
            "url": url,
            "minimum": int or None,
            "maximum": int or None,
        }
        """
        # Use obhistory URL - static HTML, not JS-loaded timeseries
        station = self._OBHISTORY_STATION.get(city_code) if city_code else None
        if not station:
            return {"url": url, "minimum": None, "maximum": None}

        obhistory_url = f"https://forecast.weather.gov/data/obhistory/{station}.html"
        resp = requests.get(obhistory_url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        temperatures = []
        # Parse table: Date | Time | Wind | Vis | Weather | Sky Cond | Temp(Air) | Dew Point | 6hr Max | 6hr Min | Relative Humidity(%)
        # Temp (Air) is column index 6. Skip any cell with "%" (humidity).
        # Only use TODAY's rows (first date in table = most recent = today)
        today_date = None
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 8:
                    continue
                date_str = cells[0].get_text(strip=True)
                if not re.match(r"^\d{1,2}$", date_str):
                    continue
                if today_date is None:
                    today_date = date_str
                if date_str != today_date:
                    continue
                air_str = cells[6].get_text(strip=True)
                if "%" in air_str:
                    continue
                match = re.match(r"^(-?\d{1,3}(?:\.\d)?)$", air_str)
                if match:
                    try:
                        t = float(match.group(1))
                        if -50 <= t <= 150:
                            temperatures.append(int(round(t)))
                    except ValueError:
                        pass

        if temperatures:
            return {
                "url": obhistory_url,
                "minimum": min(temperatures),
                "maximum": max(temperatures),
            }
        return {"url": obhistory_url, "minimum": None, "maximum": None}

    def _parse_historical_table_by_date(self, url: str, city_code: str = None, reference_date: datetime = None):
        """
        Parse historical weather from obhistory page for ALL dates in the table.
        Table shows day-of-month only; we map to full dates using reference_date (default today).
        Returns dict: { "YYYY-MM-DD": [min_temp, max_temp], ... } for each date found (newest first).
        """
        station = self._OBHISTORY_STATION.get(city_code) if city_code else None
        if not station:
            return {}
        ref = reference_date or datetime.now()
        obhistory_url = f"https://forecast.weather.gov/data/obhistory/{station}.html"
        try:
            resp = requests.get(obhistory_url, timeout=10)
            resp.raise_for_status()
        except Exception:
            return {}
        soup = BeautifulSoup(resp.text, "html.parser")
        # Collect (day_of_month, temp) in table order (newest first)
        rows_by_dom = []  # list of (day_dom_str, temp_int)
        for table in soup.find_all("table"):
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                if len(cells) < 8:
                    continue
                date_str = cells[0].get_text(strip=True)
                if not re.match(r"^\d{1,2}$", date_str):
                    continue
                air_str = cells[6].get_text(strip=True)
                if "%" in air_str:
                    continue
                match = re.match(r"^(-?\d{1,3}(?:\.\d)?)$", air_str)
                if match:
                    try:
                        t = float(match.group(1))
                        if -50 <= t <= 150:
                            rows_by_dom.append((date_str, int(round(t))))
                    except ValueError:
                        pass
        if not rows_by_dom:
            return {}
        # Order of first occurrence of each day_dom = newest to oldest
        seen_dom = []
        for dom, _ in rows_by_dom:
            if dom not in seen_dom:
                seen_dom.append(dom)
        # Map day_dom index to full date: seen_dom[0] = ref, seen_dom[1] = ref-1, ...
        dom_to_date = {}
        for i, dom in enumerate(seen_dom):
            d = ref - timedelta(days=i)
            dom_to_date[dom] = d.strftime("%Y-%m-%d")
        # Group temps by day_dom
        by_dom = {}
        for dom, temp in rows_by_dom:
            by_dom.setdefault(dom, []).append(temp)
        result = {}
        for dom, temps in by_dom.items():
            if dom in dom_to_date:
                result[dom_to_date[dom]] = [min(temps), max(temps)]
        return result

    def get_historical_weather_past_n_days(self, n_days: int = 7, reference_date: datetime = None):
        """
        For each city with obhistory station, fetch historical table and parse all dates.
        Returns: { city_code: { "YYYY-MM-DD": [min_temp, max_temp], ... } }
        Typically obhistory has ~3 days; we return whatever dates are present (up to n_days).
        """
        ref = reference_date or datetime.now()
        results = {}
        for code in self.weather_sites:
            if len(self.weather_sites[code]) < 3:
                results[code] = {}
                continue
            station = self._OBHISTORY_STATION.get(code)
            if not station:
                results[code] = {}
                continue
            try:
                by_date = self._parse_historical_table_by_date(
                    self.weather_sites[code][2], city_code=code, reference_date=ref
                )
                # Optionally limit to last n_days
                dates_sorted = sorted(by_date.keys(), reverse=True)[:n_days]
                results[code] = {d: by_date[d] for d in dates_sorted}
            except Exception:
                results[code] = {}
        return results

    def get_historical_weather(self):
        """
        Parse historical weather table (position 2) for each city that has a third URL.
        Returns the lowest and highest temperatures from the historical data table.

        Returns a dict keyed by city, e.g.:
        {"LAX": {"minimum": 15, "maximum": 25}, ...}
        Format: {"minimum": int, "maximum": int}
        """
        results = {}
        for code, urls in self.weather_sites.items():
            if len(urls) < 3:
                results[code] = {}
                continue
            historical_url = urls[2]
            try:
                parsed = self._parse_historical_table(historical_url, city_code=code)
                minimum = parsed.get("minimum")
                maximum = parsed.get("maximum")
                if minimum is not None and maximum is not None:
                    results[code] = {"minimum": minimum, "maximum": maximum}
                else:
                    results[code] = {}
            except Exception as e:
                # Debug: print error for troubleshooting
                # print(f"Error parsing historical weather for {code}: {e}")
                results[code] = {}
        return results

    def get_all_weather(self):
        """
        Call report, forecast, and historical together. Returns unified format:

        {city: {date: {'report': [min, max], 'forecast': [low, high], 'historical': [min, max]}}}

        Historical is today only. Report and forecast may have today and tomorrow.
        """
        report = self.get_daily_report_weather()
        forecast = self.get_hourly_forcast_weather()
        historical = self.get_historical_weather()

        cities = set(report.keys()) | set(forecast.keys()) | set(historical.keys())
        dates = set()
        for c, d in report.items():
            dates.update(d.keys())
        for c, d in forecast.items():
            dates.update(d.keys())
        # Use calendar today for merging historical (report is delayed, so dates[0] may be yesterday)
        calendar_today = datetime.now().strftime("%Y-%m-%d")
        dates.add(calendar_today)
        dates = sorted(dates)

        result = {}
        for city in cities:
            result[city] = {}
            for date in dates:
                entry = {}
                if city in report and date in report[city]:
                    entry["report"] = report[city][date]
                # For today: merge #2 forecast and #3 historical so range covers "top 3 possible" (NWS resolves on report, but report is delayed)
                # For tomorrow: use forecast only
                f_vals = forecast.get(city, {}).get(date)
                h_vals = [historical[city]["minimum"], historical[city]["maximum"]] if (
                    city in historical and historical[city] and date == calendar_today
                ) else None
                merged = []
                if f_vals:
                    merged.extend(f_vals)
                if h_vals:
                    merged.extend(h_vals)
                if merged:
                    entry["forecast"] = [min(merged), max(merged)]
                elif f_vals:
                    entry["forecast"] = f_vals
                if entry:
                    result[city][date] = entry
        return result


if __name__ == "__main__":
    # city -> [daily_report_url, 3_day_forecast_dwml_url]
    site_dict = {
        "PHIL": [
            "https://forecast.weather.gov/product.php?site=PHI&product=CLI&issuedby=PHL",
            "https://forecast.weather.gov/MapClick.php?lat=39.8764&lon=-75.2422&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KPHL",
        ],
        "CHI": [
            "https://forecast.weather.gov/product.php?site=LOT&product=CLI&issuedby=MDW",
            "https://forecast.weather.gov/MapClick.php?lat=41.7885&lon=-87.7417&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMDW",
        ],
        "NYC": [
            "https://forecast.weather.gov/product.php?site=OKX&product=CLI&issuedby=NYC",
            "https://forecast.weather.gov/MapClick.php?lat=40.6849&lon=-73.8444&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KNYC",
        ],
        "AUS": [
            "https://forecast.weather.gov/product.php?site=EWX&product=CLI&issuedby=AUS",
            "https://forecast.weather.gov/MapClick.php?lat=30.1945&lon=-97.6699&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KAUS",
        ],
        "LAX": [
            "https://forecast.weather.gov/product.php?site=LOX&product=CLI&issuedby=LAX",
            "https://forecast.weather.gov/MapClick.php?lat=33.9435&lon=-118.4086&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KLAX",
        ],
        "MIA": [
            "https://forecast.weather.gov/product.php?site=MFL&product=CLI&issuedby=MIA",
            "https://forecast.weather.gov/MapClick.php?lat=25.795&lon=-80.2798&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMIA",
        ],
        "DEN": [
            "https://forecast.weather.gov/product.php?site=BOU&product=CLI&issuedby=DEN",
            "https://forecast.weather.gov/MapClick.php?lat=39.8482&lon=-104.6738&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KDEN",
        ],
        "OKC": [
            "https://forecast.weather.gov/product.php?site=OUN&product=CLI&issuedby=OKC",
            "https://forecast.weather.gov/MapClick.php?lat=35.3931&lon=-97.6009&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KOKC",
        ],
        "MIN": [
            "https://forecast.weather.gov/product.php?site=FSD&product=CLI&issuedby=MSP",
            "https://forecast.weather.gov/MapClick.php?lat=44.882&lon=-93.2218&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMSP",
        ],
        "TATL": [
            "https://forecast.weather.gov/product.php?site=FFC&product=CLI&issuedby=ATL",
            "https://forecast.weather.gov/MapClick.php?lat=33.7485&lon=-84.3915&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KATL",
        ],
        "TNOLA": [
            "https://forecast.weather.gov/product.php?site=LIX&product=CLI&issuedby=MSY",
            "https://forecast.weather.gov/MapClick.php?lat=29.9933&lon=-90.259&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KMSY",
        ],
        "TPHX": [
            "https://forecast.weather.gov/product.php?site=TUC&product=CLI&issuedby=PHX",
            "https://forecast.weather.gov/MapClick.php?lat=33.4355&lon=-111.998&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KPHX",
        ],
        "TSATX": [
            "https://forecast.weather.gov/product.php?site=CRP&product=CLI&issuedby=SAT",
            "https://forecast.weather.gov/MapClick.php?lat=29.5338&lon=-98.47&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KSAT",
        ],
        "TDAL": [
            "https://forecast.weather.gov/product.php?site=FWD&product=CLI&issuedby=DFW",
            "https://forecast.weather.gov/MapClick.php?lat=32.8975&lon=-97.0444&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KDFW",
        ],
        "TSFO": [
            "https://forecast.weather.gov/product.php?site=MTR&product=CLI&issuedby=SFO",
            "https://forecast.weather.gov/MapClick.php?lat=37.7801&lon=-122.4202&FcstType=digitalDWML",
            "https://www.weather.gov/wrh/timeseries?site=KSFO",
        ],
    }

    fish_parse_weather = FISH_PARSE_WEATHER(site_dict)
    print(fish_parse_weather.get_all_weather())