import requests
import base64
import time
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
import json

from requests.exceptions import HTTPError

from cryptography.hazmat.primitives import serialization, hashes


def _price_2dec(s: Optional[str]) -> Optional[str]:
    """Round price to 2 decimals for Kalshi API tick."""
    if s is None:
        return None
    try:
        return f"{round(float(s), 2):.2f}"
    except (ValueError, TypeError):
        return s
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature

import websockets

class Environment(Enum):
    DEMO = "demo"
    PROD = "prod"

class KalshiBaseClient:
    """Base client class for interacting with the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
    ):
        """Initializes the client with the provided API key and private key.

        Args:
            key_id (str): Your Kalshi API key ID.
            private_key (rsa.RSAPrivateKey): Your RSA private key.
            environment (Environment): The API environment to use (DEMO or PROD).
        """
        self.key_id = key_id
        self.private_key = private_key
        self.environment = environment
        self.last_api_call = datetime.now()

        if self.environment == Environment.DEMO:
            self.HTTP_BASE_URL = "https://demo-api.kalshi.co"
            self.WS_BASE_URL = "wss://demo-api.kalshi.co"
        elif self.environment == Environment.PROD:
            self.HTTP_BASE_URL = "https://api.elections.kalshi.com"
            self.WS_BASE_URL = "wss://api.elections.kalshi.com"
        else:
            raise ValueError("Invalid environment")

    def request_headers(self, method: str, path: str) -> Dict[str, Any]:
        """Generates the required authentication headers for API requests."""
        current_time_milliseconds = int(time.time() * 1000)
        timestamp_str = str(current_time_milliseconds)

        # Remove query params from path
        path_parts = path.split('?')

        msg_string = timestamp_str + method + path_parts[0]
        signature = self.sign_pss_text(msg_string)

        headers = {
            "Content-Type": "application/json",
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        }

        return headers

    def sign_pss_text(self, text: str) -> str:
        """Signs the text using RSA-PSS and returns the base64 encoded signature."""
        message = text.encode('utf-8')
        try:
            signature = self.private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256()
            )
            return base64.b64encode(signature).decode('utf-8')
        except InvalidSignature as e:
            raise ValueError("RSA sign PSS failed") from e

class KalshiHttpClient(KalshiBaseClient):
    """Client for handling HTTP connections to the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
    ):
        super().__init__(key_id, private_key, environment)
        self.host = self.HTTP_BASE_URL
        self.exchange_url = "/trade-api/v2/exchange"
        self.markets_url = "/trade-api/v2/markets"
        self.portfolio_url = "/trade-api/v2/portfolio"

    def get_positions(
        self,
        count_filter: Optional[str] = "position",
        limit: Optional[int] = 1000,
        cursor: Optional[str] = None,
        fetch_all: bool = True,
    ) -> Dict[str, Any]:
        """
        Retrieves account positions. Default count_filter='position' returns only
        positions with non-zero position_fp. API defaults to 100 per page (max 1000).
        If fetch_all is True, paginates until no cursor.
        See https://docs.kalshi.com/api-reference/portfolio/get-positions
        """
        all_market = []
        all_event = []
        next_cursor = cursor
        page_limit = min(1000, limit) if limit else 1000
        while True:
            params = {"limit": page_limit}
            if count_filter is not None:
                params["count_filter"] = count_filter
            if next_cursor:
                params["cursor"] = next_cursor
            resp = self.get(self.portfolio_url + '/positions', params=params)
            all_market.extend(resp.get("market_positions") or [])
            all_event.extend(resp.get("event_positions") or [])
            next_cursor = resp.get("cursor")
            if not fetch_all or not next_cursor:
                break
        return {
            "market_positions": all_market,
            "event_positions": all_event,
            "cursor": next_cursor,
        }

    def get_fills(self) -> Dict[str, Any]:
        """Retrieves the account fills."""
        return self.get(self.portfolio_url + '/fills')

    def rate_limit(self) -> None:
        """Built-in rate limiter to prevent exceeding API rate limits."""
        THRESHOLD_IN_MILLISECONDS = 100
        now = datetime.now()
        threshold_in_microseconds = 1000 * THRESHOLD_IN_MILLISECONDS
        threshold_in_seconds = THRESHOLD_IN_MILLISECONDS / 1000
        if now - self.last_api_call < timedelta(microseconds=threshold_in_microseconds):
            time.sleep(threshold_in_seconds)
        self.last_api_call = datetime.now()

    def raise_if_bad_response(self, response: requests.Response) -> None:
        """Raises an HTTPError if the response status code indicates an error."""
        if response.status_code not in range(200, 299):
            # Capture error details for debugging
            error_details = None
            try:
                error_details = response.json()
            except:
                error_details = {"error_text": response.text}
            
            # Create a more informative error message
            error_msg = f"API Error Response: {error_details}"
            print(error_msg)
            
            # Store error details in response for better error handling
            response._error_details = error_details
            
            response.raise_for_status()

    def post(self, path: str, body: dict) -> Any:
        """Performs an authenticated POST request to the Kalshi API."""
        self.rate_limit()
        response = requests.post(
            self.host + path,
            json=body,
            headers=self.request_headers("POST", path)
        )
        self.raise_if_bad_response(response)
        return response.json()

    def get(self, path: str, params: Dict[str, Any] = {}) -> Any:
        """Performs an authenticated GET request to the Kalshi API."""
        self.rate_limit()
        response = requests.get(
            self.host + path,
            headers=self.request_headers("GET", path),
            params=params
        )
        self.raise_if_bad_response(response)
        return response.json()

    def delete(self, path: str, params: Dict[str, Any] = {}) -> Any:
        """Performs an authenticated DELETE request to the Kalshi API."""
        self.rate_limit()
        response = requests.delete(
            self.host + path,
            headers=self.request_headers("DELETE", path),
            params=params
        )
        self.raise_if_bad_response(response)
        return response.json()

    def get_balance(self) -> Dict[str, Any]:
        """Retrieves the account balance."""
        return self.get(self.portfolio_url + '/balance')

    def get_exchange_status(self) -> Dict[str, Any]:
        """Retrieves the exchange status."""
        return self.get(self.exchange_url + "/status")

    def get_trades(
        self,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        max_ts: Optional[int] = None,
        min_ts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Retrieves trades based on provided filters."""
        params = {
            'ticker': ticker,
            'limit': limit,
            'cursor': cursor,
            'max_ts': max_ts,
            'min_ts': min_ts,
        }
        # Remove None values
        params = {k: v for k, v in params.items() if v is not None}
        return self.get(self.markets_url + '/trades', params=params)

    def get_fills(self, min_ts: Optional[int] = None):
        """Retrieves the fills."""
        return self.get(self.portfolio_url + '/fills?min_ts=' + str(min_ts))

    def get_market_incentive(self, status: str = "active") -> Dict[str, Any]:
        """
        Retrieves active incentive programs only.
        """
        params = {"status": status, "limit": 10000}
        resp = self.get("/trade-api/v2/incentive_programs", params=params)
        return resp


    def get_market_ticker(self, ticker: Optional[str] = None):
        """Retrieves tickers for all markets."""
        return self.get(self.markets_url + '/' + ticker)

    def get_markets_by_series(self, series_ticker: Optional[str] = None, status: Optional[str] = None, limit: int = 1000):
        """
        Get markets by series_ticker from Kalshi API.
        
        Args:
            series_ticker: The series ticker to search for (e.g., "KXLOWTCHI-26JAN30")
            status: Filter by status (e.g., "open", "closed"). If None, returns all statuses.
            limit: Maximum number of results to return (default: 1000)
        
        Returns:
            Dictionary with 'markets' list containing market data
        """
        params = {"limit": limit}
        if series_ticker:
            params["series_ticker"] = series_ticker.upper()
        if status:
            params["status"] = status
        
        return self.get(self.markets_url, params=params)

    def get_market_ticker_order_book(self, ticker: Optional[str] = None):
        """Retrieves order book for a given market."""
        return self.get(self.markets_url + '/' + ticker + '/orderbook')


    def get_open_orders(
        self,
        status: Optional[str] = "resting",
        limit: Optional[int] = 200,
        cursor: Optional[str] = None,
        fetch_all: bool = True,
    ) -> Dict[str, Any]:
        """
        Retrieves orders. Default status='resting' returns only open (resting) orders.
        API defaults to 100 per page (max 200). If fetch_all is True, paginates until no cursor.
        See https://docs.kalshi.com/api-reference/orders/get-orders
        """
        all_orders = []
        next_cursor = cursor
        page_limit = min(200, limit) if limit else 200
        while True:
            params = {"limit": page_limit}
            if status is not None:
                params["status"] = status
            if next_cursor:
                params["cursor"] = next_cursor
            resp = self.get(self.portfolio_url + '/orders', params=params)
            orders = resp.get("orders") or []
            all_orders.extend(orders)
            next_cursor = resp.get("cursor")
            if not fetch_all or not next_cursor or (limit and len(all_orders) >= limit):
                break
        return {"orders": all_orders, "cursor": next_cursor}

    def create_open_order(self, 
          ticker: Optional[str] = None, 
          side: Optional[str] = None, 
          action: Optional[str] = None,
          count: Optional[int] = None, 
          type: Optional[str] = None,
          yes_price_dollars: Optional[str] = None,  
          no_price_dollars: Optional[str] = None,  
          time_in_force: Optional[str] = None,
          reduce_only: Optional[bool] = None,
        #   expiration_ts: Optional[int] = None,
        #   status: Optional[str] = None,
        ):
        """Creates an open order for a given market. reduce_only=True prevents selling more than position (no short)."""
        # NEVER allow sell NO - would create short position
        if action == "sell" and side == "no":
            raise ValueError("BLOCKED: sell side=no would create short position. Only sell YES.")
        playload = {
            "ticker": ticker, 
            "side": side,    
            "action": action,
            "count": int(count) if count is not None else None,
            "type": type,
            "yes_price_dollars": _price_2dec(yes_price_dollars),  
            "no_price_dollars": _price_2dec(no_price_dollars),
            "time_in_force": time_in_force,
            "status": "resting",
            "reduce_only": reduce_only,
            # "expiration_ts": expiration_ts
        }
        # Remove None values to avoid API errors
        playload = {k: v for k, v in playload.items() if v is not None}
        return self.post(self.portfolio_url + '/orders', body=playload)


    def close_open_position_order(self, 
            ticker: Optional[str] = None, 
            side: Optional[str] = None, 
            action: Optional[str] = None,
            count: Optional[int] = None, 
            type: Optional[str] = None,
            yes_price_dollars: Optional[str] = None,
            no_price_dollars: Optional[str] = None,
            time_in_force: Optional[str] = None,
            reduce_only: Optional[bool] = None,
        ):
        """Closes an open position by creating an order."""
        playload = {
            "ticker": ticker, 
            "side": side,    
            "action": action,
            "count": int(count) if count is not None else None,
            "type": type,
            "yes_price_dollars": _price_2dec(yes_price_dollars),
            "no_price_dollars": _price_2dec(no_price_dollars),
            "time_in_force": time_in_force,
            "reduce_only": reduce_only,
        }
        playload = {k: v for k, v in playload.items() if v is not None}
        return self.post(self.portfolio_url + '/orders', body=playload)

    def cancel_open_order(self, order_id: Optional[str] = None):
        """Cancels an open order for a given market."""
        return self.delete(self.portfolio_url + '/orders/' + order_id)


class KalshiWebSocketClient(KalshiBaseClient):
    """Client for handling WebSocket connections to the Kalshi API."""
    def __init__(
        self,
        key_id: str,
        private_key: rsa.RSAPrivateKey,
        environment: Environment = Environment.DEMO,
    ):
        super().__init__(key_id, private_key, environment)
        self.ws = None
        self.url_suffix = "/trade-api/ws/v2"
        self.message_id = 1  # Add counter for message IDs

    async def connect(self):
        """Establishes a WebSocket connection using authentication."""
        host = self.WS_BASE_URL + self.url_suffix
        auth_headers = self.request_headers("GET", self.url_suffix)
        async with websockets.connect(host, additional_headers=auth_headers) as websocket:
            self.ws = websocket
            await self.on_open()
            await self.handler()

    async def on_open(self):
        """Callback when WebSocket connection is opened."""
        print("WebSocket connection opened.")
        await self.subscribe_to_tickers()

    async def subscribe_to_tickers(self):
        """Subscribe to ticker updates for all markets."""
        subscription_message = {
            "id": self.message_id,
            "cmd": "subscribe",
            "params": {
                "channels": ["ticker"]
            }
        }
        await self.ws.send(json.dumps(subscription_message))
        self.message_id += 1

    async def handler(self):
        """Handle incoming messages."""
        try:
            async for message in self.ws:
                await self.on_message(message)
        except websockets.ConnectionClosed as e:
            await self.on_close(e.code, e.reason)
        except Exception as e:
            await self.on_error(e)

    async def on_message(self, message):
        """Callback for handling incoming messages."""
        print("Received message:", message)

    async def on_error(self, error):
        """Callback for handling errors."""
        print("WebSocket error:", error)

    async def on_close(self, close_status_code, close_msg):
        """Callback when WebSocket connection is closed."""
        print("WebSocket connection closed with code:", close_status_code, "and message:", close_msg)

    