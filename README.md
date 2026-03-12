# Kalshi Market Making Bot

An automated trading bot for market making on the Kalshi prediction market exchange. The bot participates in incentive programs by placing liquidity orders on selected markets.

## Overview

This bot automates the process of:

- Monitoring and participating in Kalshi incentive programs
- Placing limit orders to provide liquidity on selected markets
- Managing open positions and orders
- Logging all trading activities for monitoring and analysis

## Features

- **Automated Trading**: Automatically places limit orders based on incentive programs
- **Position Management**: Closes open positions and cancels existing orders before new trading sessions
- **Incentive Tracking**: Monitors and tracks incentive programs, updating trading strategies accordingly
- **Error Handling**: Robust error handling with automatic retry on transient failures
- **Comprehensive Logging**: Detailed logs of all trading activities written to `logs/trade.log`
- **Dual Environment Support**: Works with both DEMO and PROD environments

## Requirements

- Python 3.8+
- Kalshi API credentials (API Key ID and Private Key)
- Virtual environment (recommended)

## Installation

1. **Clone the repository** (if applicable):

```bash
git clone <repository-url>
cd KalshiModel
```

2. **Create and activate a virtual environment**:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

3. **Install dependencies**:

```bash
pip install -r requirements.txt
```

## Configuration

### Environment Variables

Create a `.env` file in the project root with the following variables:

```env
# Demo Environment
DEMO_KEYID=your_demo_api_key_id
DEMO_KEYFILE=/path/to/demo_private_key.pem

# Production Environment
PROD_KEYID=your_prod_api_key_id
PROD_KEYFILE=/path/to/prod_private_key.pem
```

### Trading Parameters

Edit the constants in `market_bot.py` to customize trading behavior:

```python
TRADE_SIZE = 1              # Number of contracts per order
TRADE_DELTA = 0.01          # Price delta for order placement
WAIT_TIME = 60              # Seconds between trading cycles
EXPIRATION_TS = 1           # Order expiration time in minutes
TRADE_TICKER_SIZE = 2       # Number of tickers to trade simultaneously
INCENTIVE_SIZE = 300        # Incentive size parameter
```

### Environment Selection

In `market_bot.py`, change the environment:

```python
env = Environment.PROD  # Use Environment.DEMO for testing
```

## Usage

### Running the Market-Making Bot

```bash
python market_bot.py
```

### Running the Fish Trade (Temperature) Bot

```bash
python fish_trade.py
```

Uses NWS weather to trade Kalshi temperature markets (see [Fish Trade](#fish-trade-fish_tradepy) below). Set `PROD_KEYID`/`PROD_KEYFILE` (or `DEMO_*`) in `.env`.

**Market-making bot** (market_bot.py) will:

1. Cancel any existing open orders
2. Close any open positions
3. Check for available incentive programs
4. Place new orders based on incentives
5. Wait `WAIT_TIME` seconds before repeating

### Stopping the Bot

Press `Ctrl+C` to stop the bot gracefully. The bot will log a shutdown message and exit.

## Project Structure

```
KalshiModel/
├── market_bot.py        # Main market-making bot and trading loop
├── fish_trade.py        # Temperature (weather) trading bot — see Fish Trade below
├── clients.py           # Kalshi API client implementation
├── incentive.py         # Incentive program tracking and management
├── trade.py             # Order creation and trading logic (market bot)
├── fish_orders.py       # Fish trade order/position state and PnL
├── fish_parse_weather.py # NWS weather parsing (report, forecast, historical)
├── fish_market_ticker.py # Kalshi temp ticker selection (3–4 per city/date)
├── fish_trade_time.py   # Trade windows (today/tomorrow high/low)
├── fish_price_strategy.py # Buy/sell price logic for fish markets
├── fish_incentive.py    # Fish incentive (if used)
├── main.py              # Alternative entry point (if used)
├── requirements.txt     # Python dependencies
├── scripts/             # Analysis and backtest scripts
│   ├── analyze_pnl.py   # PnL from API or CSV
│   └── weather_winner_past_7days.py # Weather vs Kalshi outcome backtest
├── logs/                 # Log files (auto-generated)
│   ├── trade.log        # Market bot logs
│   ├── fish_trade.log   # Fish trade logs
│   └── fish_pnl.csv     # Fish PnL log
└── .env                  # Environment variables (not in repo)
```

## Key Components

### MARKET_BOT (`market_bot.py`)

Main bot class that orchestrates the trading workflow:

- `start_trading()`: Main trading cycle
- `place_order()`: Places orders for incentive tickers
- `run()`: Main loop with error handling
- `log()`: Unified logging to console and file

### KalshiHttpClient (`clients.py`)

API client for interacting with Kalshi:

- Authentication with RSA signatures
- Rate limiting
- Order management (create, cancel, get orders)
- Position management
- Market data retrieval

### INCENTIVE_PROGRAM (`incentive.py`)

Manages incentive program tracking:

- Loads market incentives
- Filters and selects tradable incentives
- Updates incentive status
- Maintains trade incentive dictionary

### TRADE (`trade.py`)

Order creation logic for the market bot:

- Calculates order prices based on order book
- Creates limit orders with proper pricing
- Manages trade size and balance

---

## Fish Trade (`fish_trade.py`)

A separate automated strategy that trades Kalshi **temperature (weather) markets** using NWS weather data. It buys YES on a small set of temperature tickers around the predicted high/low for each city and date, then places resting sell orders to close positions.

### What it does

- **Weather input**: Uses `fish_parse_weather` to get min/max forecasts and historical data from NWS (daily report, DWML forecast, obhistory) per city in `site_dict`.
- **Ticker selection**: For each city and date (today/tomorrow), `fish_market_ticker` picks **3–4** temperature tickers: the 3 closest to the predicted high (or low), plus optionally a 4th by highest order-book volume within ±2° of the target.
- **Buy orders**: At configured start times (today high, tomorrow low, tomorrow high), places limit **buy YES** orders on those tickers (one order per ticker, size `TRADE_SIZE`).
- **Sell orders**: When buys fill, it creates **sell YES** (limit) orders to close. Sell quantity is **capped to actual position** from `get_positions()` so it never oversells (no short YES / NO position). Sell prices can be updated in stages via `fish_price_strategy` as market close approaches.
- **Oversell check**: `check_over_sell()` compares resting sell order size to current position; if a sell order is larger than position, it cancels and replaces with a sell sized to position.
- **State**: Open buy/sell orders and filled positions are tracked in `fish_orders` (FISH_ORDERS_MANAGER), with state and PnL written to `logs/` (e.g. `fish_pnl.csv`).

### Running the Fish Trade bot

```bash
python fish_trade.py
```

- Requires `.env` with `PROD_KEYID` / `PROD_KEYFILE` (or `DEMO_*`). Toggle environment at the bottom of `fish_trade.py`: `env = Environment.PROD` or `Environment.DEMO`.
- Main loop: every cycle it syncs open orders and fills, cancels/updates as needed, creates/updates sell orders (capped to position), then creates buy orders at the appropriate time windows. It then waits **30 minutes** before the next cycle.

### Key parameters

- **`TRADE_SIZE`** (default 100): Contracts per buy order per ticker.
- **`site_dict`**: City code → list of 3 NWS URLs (daily report, forecast DWML, timeseries/obhistory). Defines which cities are traded (e.g. PHIL, CHI, AUS, LAX, DEN, TOKC, TMIN, TATL, TNOLA, TPHX, TSATX, TDAL, TSFO, TSEA, THOU, TBOS).

### Related modules

- **`fish_parse_weather`**: FISH_PARSE_WEATHER — NWS report/forecast/historical by city.
- **`fish_market_ticker`**: FISH_MARKET_TICKER — get_tickers_for_date() returns 3–4 tickers per city/date/type (low/high).
- **`fish_orders`**: FISH_ORDERS, FISH_ORDERS_MANAGER — in-memory state, state file, PnL CSV.
- **`fish_trade_time`**: FISH_TRADE_TIME — today/tomorrow dates and time windows for when to start/stop each trade type.
- **`fish_price_strategy`**: FISH_PRICE_STRATEGY — buy and sell price from order book.

### Logging

- **`logs/fish_trade.log`**: All actions (create/cancel/update orders, over-sell fixes, errors). Path is resolved from the script directory so it works regardless of cwd.
- **`logs/fish_pnl.csv`**: PnL entries (e.g. expired unfilled sells).

---

## Logging

All trading activities are logged to `logs/trade.log` with timestamps. Log entries include:

- **Order Operations**: Cancel, open, and close orders with details
- **Position Management**: Open positions, closing positions
- **Order Books**: Top 5 best prices for Yes/No sides
- **API Responses**: Order status, fills, and remaining quantities
- **Errors**: Detailed error messages with tracebacks
- **Trading Sessions**: New/updated incentives and tickers

Example log entries:

```
2026-01-16 18:05:57 [CANCEL ORDER] Ticker: KXHIGHCHI-26JAN16-B35.5 | Side: yes | Price: 0.4600
2026-01-16 18:05:57 [CLOSE POSITION] Ticker: KXHIGHCHI-26JAN16-B35.5 | Side: no | Price: 0.4600 | Count: 1
2026-01-16 18:05:58 [OPEN ORDER] Ticker: KXLOWTMIA-26JAN17-B60.5 | Side: yes | Action: buy | Count: 1 | Type: limit | Price: 0.5300
```

## Error Handling

The bot includes comprehensive error handling:

- **Transient Errors**: 503 Service Unavailable errors are logged and retried on next cycle
- **Client Errors**: 400 Bad Request errors are logged with detailed API error messages
- **Critical Errors**: All exceptions are caught, logged with full tracebacks, and the bot continues running
- **Graceful Shutdown**: KeyboardInterrupt is handled for clean shutdown

## Important Notes

⚠️ **Risk Warning**:

- This bot trades real money when using PROD environment
- Always test thoroughly in DEMO environment first
- Monitor `logs/trade.log` regularly for errors
- Ensure sufficient account balance for trading

📝 **Best Practices**:

- Start with `WAIT_TIME=60` or higher to avoid rate limiting
- Monitor `logs/trade.log` for the first few hours of operation
- Keep API keys secure and never commit them to version control
- Use `.env` file for credentials (already in `.gitignore`)

🔧 **Troubleshooting**:

- **503 Errors**: API server temporarily unavailable - bot will retry automatically
- **400 Errors**: Check order parameters in logs (price format, required fields)
- **401 Errors**: Verify API credentials in `.env` file
- **429 Errors**: Reduce trading frequency (increase `WAIT_TIME`)

## API Documentation

For detailed API documentation, refer to:

- [Kalshi API Documentation](https://docs.kalshi.com/api-reference/)

## Author

Mai He

## Disclaimer

This software is provided as-is for educational and research purposes. Trading involves risk of loss. Use at your own risk.
