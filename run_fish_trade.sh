#!/bin/bash
# Keeps Mac awake while fish_trade runs (screen lock, lid closed when plugged in, etc.)
# Usage: ./run_fish_trade.sh
cd "$(dirname "$0")"
caffeinate -i .venv/bin/python fish_trade.py
