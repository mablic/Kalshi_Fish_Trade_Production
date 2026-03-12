import os
import asyncio
import json
import time
import traceback
from requests.exceptions import HTTPError
from incentive import INCENTIVE_PROGRAM
from trade import TRADE
from clients import KalshiHttpClient, Environment
from dotenv import load_dotenv
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timedelta

TRADE_SIZE = 300
WAIT_TIME = 0
EXPIRATION_TS = 30

TRADE_PRICE_RANGE = [0.05, 0.3]
STOP_TRADE_TIME = 300
OPEN_POSITIONS_MAX = 1

MINIMUM_MARKET_PRICE_DELTA = 0.15

LOG_FILE = "logs/trade.log"


class MARKET_BOT:

    def __init__(self, incentive_program: INCENTIVE_PROGRAM, trade: TRADE, client: KalshiHttpClient):
        self.incentive_program = incentive_program
        self.incentive_program.stop_trade_time = STOP_TRADE_TIME
        self.trade = trade
        self.client = client
        self.trade.trade_size = TRADE_SIZE
        self.trade.expiration_ts = EXPIRATION_TS
        self.wait_time = WAIT_TIME
        self.log_file = LOG_FILE
        self.trade.trade_price_range = TRADE_PRICE_RANGE
        self.trade.open_position_max = OPEN_POSITIONS_MAX
        self.trade.minimum_market_price_delta = MINIMUM_MARKET_PRICE_DELTA

        self.historical_trade_list = []

    def get_datetime(self):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def log(self, message: str):
        """Print message to console and write to log file."""
        print(message)
        try:
            log_dir = os.path.dirname(self.log_file)
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
            with open(self.log_file, 'a') as f:
                f.write(message + '\n')
        except Exception as e:
            print(f"Error writing to log file: {e}")

    def _retry_on_timestamp_error(self, func, max_retries=3, retry_delay=1):
        """Retry a function call if it fails with timestamp expiration error."""
        for attempt in range(max_retries):
            try:
                return func()
            except HTTPError as e:
                # Check if it's a timestamp expiration error
                if e.response.status_code == 401:
                    error_details = getattr(e.response, '_error_details', {})
                    error_code = error_details.get('error', {}).get('code', '')
                    if error_code == 'header_timestamp_expired' and attempt < max_retries - 1:
                        self.log(f"{self.get_datetime()} [RETRY] Timestamp expired, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(retry_delay)
                        continue
                # If it's not a timestamp error or we've exhausted retries, raise
                raise
            except Exception as e:
                # For other exceptions, raise immediately
                raise
        # Should never reach here, but just in case
        raise Exception("Max retries exceeded")

    def start_trading(self):
        try:
            curr_open_orders = self.client.get_open_orders()['orders']
            if curr_open_orders:
                for order in curr_open_orders:
                    try:
                        if order['status'] not in ['canceled', 'filled', 'executed']:
                            ticker = order.get('ticker', 'N/A')
                            side = order.get('side', 'N/A')
                            yes_price = order.get('yes_price_dollars', order.get('yes_price', 'N/A'))
                            no_price = order.get('no_price_dollars', order.get('no_price', 'N/A'))
                            price = yes_price if side == 'yes' else no_price
                            self.log(f"{self.get_datetime()} [CANCEL ORDER] Ticker: {ticker} | Side: {side} | Price: {price}")
                            self.client.cancel_open_order(order['order_id'])
                    except Exception as e:
                        self.log(f"{self.get_datetime()} [ERROR] Failed to cancel order: {str(e)}")
                        self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")
        
            curr_open_positions = self.client.get_positions()['market_positions']
            if curr_open_positions:
                for position in curr_open_positions:
                    try:
                        position_count = position.get('position', 0)
                        # Skip positions where position count is 0
                        if position_count == 0:
                            continue
                        
                        # Determine side: positive position = long yes, negative = long no
                        side = 'yes' if position_count > 0 else 'no'
                        count = abs(position_count)
                        
                        # Get order book to find current market price
                        order_book = self.client.get_market_ticker_order_book(position['ticker'])['orderbook']
                
                        # For market orders, use best bid price (highest price someone will pay)
                        # yes_dollars are bid prices for yes, no_dollars are bid prices for no
                        if side == 'yes':
                            # Get best bid price for yes (highest price)
                            yes_dollars = order_book.get('yes_dollars', [])
                            if yes_dollars:
                                best_bid = max(yes_dollars, key=lambda x: float(x[0]))
                                yes_price_dollars = f"{float(best_bid[0]):.4f}"
                            else:
                                yes_price_dollars = None
                            no_price_dollars = None
                        else:
                            # Get best bid price for no (highest price)
                            no_dollars = order_book.get('no_dollars', [])
                            if no_dollars:
                                best_bid = max(no_dollars, key=lambda x: float(x[0]))
                                no_price_dollars = f"{float(best_bid[0]):.4f}"
                            else:
                                no_price_dollars = None
                            yes_price_dollars = None
                        
                        if (side == 'yes' and yes_price_dollars is None) or (side == 'no' and no_price_dollars is None):
                            ticker = position.get('ticker', 'N/A')
                            position_count = position.get('position', 0)
                            self.log(f"{self.get_datetime()} [SKIP POSITION] Ticker: {ticker} | Position: {position_count} | Reason: No bid price available")
                            continue
                        
                        self.historical_trade_list.append({
                            'ticker': position.get('ticker', 'N/A'),
                            'side': side,
                            'price': yes_price_dollars if side == 'yes' else no_price_dollars,
                            'count': count,
                            'datetime': self.get_datetime(),
                        })
                        
                        ticker = position.get('ticker', 'N/A')
                        price = yes_price_dollars if side == 'yes' else no_price_dollars
                        self.log(f"{self.get_datetime()} [CLOSE POSITION] Ticker: {ticker} | Side: {side} | Price: {price} | Count: {count}")
                        self.client.close_open_position_order(
                            ticker=position['ticker'],
                            side=side,
                            action='sell',
                            count=count,
                            type="limit",
                            yes_price_dollars=yes_price_dollars,
                            no_price_dollars=no_price_dollars,
                            time_in_force="immediate_or_cancel",
                            reduce_only=True,
                        )
                    except Exception as e:
                        ticker = position.get('ticker', 'N/A')
                        self.log(f"{self.get_datetime()} [ERROR] Failed to close position {ticker}: {str(e)}")
                        self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")

            curr_open_positions = self.client.get_positions()['market_positions']
            
            # Check if there are any actual positions (non-zero position counts)
            actual_positions = [p for p in curr_open_positions if p.get('position', 0) != 0] if curr_open_positions else []
            if actual_positions:
                position_info = []
                for pos in actual_positions:
                    ticker = pos.get('ticker', 'N/A')
                    position_count = pos.get('position', 0)
                    side = 'yes' if position_count > 0 else 'no'
                    position_info.append(f"{ticker}({side}:{abs(position_count)})")
                self.log(f"{self.get_datetime()} [SKIP TRADING] Open positions: {', '.join(position_info)}")
                return

            curr_market_incentive = self.client.get_market_incentive()
            self.incentive_program.load_market_incentive(curr_market_incentive['incentive_programs'])
            incentive_tickers = self.incentive_program.get_open_incentive_tickers()
            ticker_dict = {}
            for ticker in incentive_tickers:
                curr_market_ticker = self.client.get_market_ticker(ticker)
                ticker_dict[ticker] = curr_market_ticker['market']
            self.incentive_program.fill_incentive_tickers(ticker_dict)
            curr_traded_incentive = self.incentive_program.get_trade_incentive_dict()

            if self.trade.has_open_position():
                try:
                    self.log(f"{self.get_datetime()} [EXISTING TRADING] open positions")
                    self.trade.check_open_order_expiration(curr_traded_incentive)
                    if self.trade.has_open_position():
                        curr_traded_orders = self.trade.get_open_trade_orders()
                        self.place_order(curr_traded_orders)
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] Failed to update incentive: {str(e)}")
                    self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")

            if not self.trade.has_open_position():            
                try:
                    self.log(f"{self.get_datetime()} [NEW SESSION] Starting new trading session")
                    self.place_order(curr_traded_incentive)
                except Exception as e:
                    self.log(f"{self.get_datetime()} [ERROR] Failed to start new trading session: {str(e)}")
                    self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")

        except Exception as e:
            self.log(f"{self.get_datetime()} [ERROR] Critical error in start_trading: {str(e)}")
            self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")
    
    def place_order(self, curr_traded_incentive: dict):
        trade_book_dict = {}
        for trade_ticker in curr_traded_incentive:
            trade_book_dict[trade_ticker] = self._retry_on_timestamp_error(
                lambda ticker=trade_ticker: self.client.get_market_ticker_order_book(ticker)['orderbook']
            )

        self.trade.prepare_open_order(curr_traded_incentive, trade_book_dict)

        if self.trade.has_open_position():
            self.trade.balance = self._retry_on_timestamp_error(
                lambda: self.client.get_balance()['balance']
            )
            market_orders = self.trade.create_open_order()
            for order in market_orders:
                try:
                    ticker = order.get('ticker', 'N/A')
                    side = order.get('side', 'N/A')
                    action = order.get('action', 'N/A')
                    count = order.get('count', 0)
                    order_type = order.get('type', 'N/A')
                    yes_price = order.get('yes_price_dollars', None)
                    no_price = order.get('no_price_dollars', None)
                    price = yes_price if side == 'yes' else no_price

                    title = order.get('title', 'N/A')
                    rules_primary = order.get('rules_primary', 'N/A')
                    yes_qty = order.get('yes_qty', 0)
                    no_qty = order.get('no_qty', 0)
                    yes_price = order.get('yes_price', 0)
                    no_price = order.get('no_price', 0)

                    self.log(f"{self.get_datetime()} [OPEN ORDER] Ticker: {ticker} | Title: {title} | Rules Primary: {rules_primary}")
                    self.log(f"  └─ {action}: {side} {count} {order_type} {price}")
                    self.log(f"  └─ Yes Order Book: {order['yes_order_book']}")
                    self.log(f"  └─ No Order Book: {order['no_order_book']}")
                    
                    # Extract price values for API call
                    yes_price_dollars = order.get('yes_price_dollars', None)
                    no_price_dollars = order.get('no_price_dollars', None)
                    
                    # Log what we're sending to API for debugging
                    # self.log(f"  └─ API Payload: yes_price_dollars={yes_price_dollars}, no_price_dollars={no_price_dollars}, expiration_ts={order.get('expiration_ts')}")
                    
                    response = self.client.create_open_order(
                        ticker=order['ticker'],
                        side=order['side'],
                        action=order['action'],
                        count=order['count'],
                        type=order['type'],
                        yes_price_dollars=yes_price_dollars,
                        no_price_dollars=no_price_dollars,
                        # expiration_ts=order['expiration_ts'],
                        time_in_force=order['time_in_force'],
                    )
                    
                    # Format response
                    if response and 'order' in response:
                        resp_order = response['order']
                        order_id = resp_order.get('order_id', 'N/A')
                        status = resp_order.get('status', 'N/A')
                        fill_count = resp_order.get('fill_count', 0)
                        remaining = resp_order.get('remaining_count', 0)
                        self.log(f"{self.get_datetime()} [ORDER RESPONSE] OrderID: {order_id[:8]}... | Status: {status} | Filled: {fill_count} | Remaining: {remaining}")
                    else:
                        self.log(f"{self.get_datetime()} [ORDER RESPONSE] {response}")
                except Exception as e:
                    ticker = order.get('ticker', 'N/A')
                    error_msg = str(e)
                    
                    # Try to extract API error details if it's an HTTPError
                    if hasattr(e, 'response') and hasattr(e.response, '_error_details'):
                        api_error = e.response._error_details
                        error_msg += f" | API Error: {api_error}"
                    elif "API Error Response" in str(e):
                        # Fallback: extract from error message if available
                        pass
                    
                    self.log(f"{self.get_datetime()} [ERROR] Failed to place order for {ticker}: {error_msg}")
                    
                    # Log order details for debugging
                    self.log(f"{self.get_datetime()} [ERROR] Order details: Ticker={ticker}, Side={side}, Action={action}, Count={count}, Type={order_type}, Price={price}")
                    self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")
        else:
            self.log(f"{self.get_datetime()} [NO OPEN POSITIONS] No open positions")

    def run(self):
        """Main trading loop with error handling - keeps running even if errors occur."""

        while True:
            try:
                self.start_trading()
                self.log(f"{self.get_datetime()} [HISTORICAL TRADE count] {len(self.historical_trade_list)}")
            except KeyboardInterrupt:
                self.log(f"{self.get_datetime()} [SHUTDOWN] Received interrupt signal, shutting down gracefully")
                raise  # Re-raise to allow clean shutdown
            except Exception as e:
                self.log(f"{self.get_datetime()} [ERROR] Unhandled error in main loop: {str(e)}")
                self.log(f"{self.get_datetime()} [ERROR] Traceback: {traceback.format_exc()}")
                self.log(f"{self.get_datetime()} [INFO] Continuing to next iteration in {self.wait_time} seconds...")
            finally:
                time.sleep(self.wait_time)

if __name__ == "__main__":
    # Load environment variables
    load_dotenv()
    env = Environment.PROD # toggle environment here
    KEYID = os.getenv('DEMO_KEYID') if env == Environment.DEMO else os.getenv('PROD_KEYID')
    KEYFILE = os.getenv('DEMO_KEYFILE') if env == Environment.DEMO else os.getenv('PROD_KEYFILE')

    try:
        with open(KEYFILE, "rb") as key_file:
            private_key = serialization.load_pem_private_key(
                key_file.read(),
                password=None  # Provide the password if your key is encrypted
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

    incentive_program = INCENTIVE_PROGRAM()
    trade = TRADE()
    market_bot = MARKET_BOT(incentive_program, trade, client)
    market_bot.run()


