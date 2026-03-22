# Unified Trading Bot v3.0

## Bots
- **CryptoBot**: Delta Exchange — BTC/ETH perpetual futures, 24/7
- **IndiaBot**: Dhann NSE — Intraday stocks + Pairs arbitrage, market hours only

## Run
```bash
# Install dependencies
pip install -r requirements.txt

# Paper trade (default)
python bot.py --mode run

# Single scan test
python bot.py --mode once

# India only
python bot.py --mode india_once

# Show dashboard
python bot.py --mode dashboard

# Retrain ML
python bot.py --mode train

# Discover product IDs
python bot.py --mode discover
```

## Config
Edit `config/settings.py`:
- `API_KEY` / `API_SECRET` — Delta Exchange keys
- `DHANN_CLIENT_ID` / `DHANN_ACCESS_TOKEN` — Dhann keys
- `PAPER_TRADE = False` — switch to live trading

## ML Model
Used only for CryptoBot to filter BB Squeeze signals.
Random Forest trained on BTC/ETH/SOL historical data.
Gann strategy signals bypass ML filter.
