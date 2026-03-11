"""
╔══════════════════════════════════════════════════════════════╗
║         DELTA EXCHANGE TRADING BOT — CONFIGURATION          ║
╚══════════════════════════════════════════════════════════════╝
Apni API keys yahan fill karo.
Demo account ke liye: https://testnet.delta.exchange
"""

# ── API CREDENTIALS ─────────────────────────────────────────────
API_KEY    = "7bidqvVgWDCuBOmrakyGecz4NBtgb8"
API_SECRET = "tepQPqi7Dul7tLR0ygFNUN8xKwjaM5TRdkL3cTvKdZzEDZIsqTSN9kIawQGj"

# Demo (testnet) ya Live
USE_TESTNET = True   # True = demo account, False = real money ⚠️

BASE_URL = (
    "https://cdn-ind.testnet.deltaex.org"  if USE_TESTNET else
    "https://api.india.delta.exchange"
)

# ── TRADING PAIRS ────────────────────────────────────────────────
# Delta Exchange India product IDs (perpetual futures)
# NOTE: Delta Exchange India uses BTCUSD (not BTCUSDT), ETHUSD, SOLUSD
# Product IDs below are for production India (api.india.delta.exchange)
# For testnet (cdn-ind.testnet.deltaex.org): run bot.py --mode discover
# to get the actual testnet product IDs, then update here.
PRODUCTS = {
    "BTCUSD": {
        "symbol":        "BTCUSD",
        "product_id":    27,           # production India product ID
        "lot_size":      0.001,        # 1 lot = 0.001 BTC
        "min_lots":      1,
        "tick_size":     0.5,
        "contract_type": "perpetual_futures",
        "active":        True,
    },
    "ETHUSD": {
        "symbol":        "ETHUSD",
        "product_id":    3136,         # production India product ID
        "lot_size":      0.01,         # 1 lot = 0.01 ETH
        "min_lots":      1,
        "tick_size":     0.05,
        "contract_type": "perpetual_futures",
        "active":        True,
    },
    "SOLUSD": {
        "symbol":        "SOLUSD",
        "product_id":    9376,         # production India product ID
        "lot_size":      0.1,          # 1 lot = 0.1 SOL
        "min_lots":      1,
        "tick_size":     0.01,
        "contract_type": "perpetual_futures",
        "active":        True,
    },
}

# ── RISK MANAGEMENT ──────────────────────────────────────────────
# CAPITAL_USDT = 0 means auto-fetch from Delta wallet
# Testnet gives ~100,000 demo USDT automatically
# CAPITAL_FALLBACK used only if wallet API fails completely
CAPITAL_USDT       = 0.0      # 0 = auto-fetch from wallet
CAPITAL_FALLBACK   = 800.0    # fallback if API unreachable
RISK_PER_TRADE_PCT = 1.0      # % of live capital risked per trade
MAX_OPEN_TRADES    = 2        # Maximum simultaneous open trades
LEVERAGE           = 5        # Leverage multiplier (5x recommended for beginners)
RR_RATIO           = 2.0      # Risk:Reward = 1:2
ATR_MULTIPLIER     = 1.0      # SL = entry ± ATR × this

# ── FEES (Delta Exchange India) ──────────────────────────────────
MAKER_FEE_PCT      = 0.02     # 0.02% maker fee
TAKER_FEE_PCT      = 0.05     # 0.05% taker fee
GST_PCT            = 18.0     # 18% GST on brokerage (India)
# Net fees per side (taker order):
# 0.05% × 1.18 (GST) = 0.059% per trade entry/exit
# Round trip (entry + exit) = ~0.118%

# ── STRATEGY PARAMS ──────────────────────────────────────────────
BB_PERIOD          = 20
BB_STD             = 2.0
BB_SQUEEZE_PCT     = 0.20     # Bottom 20% = squeeze
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
EMA_4H_PERIOD      = 21
ATR_PERIOD         = 14

# ── TIMEFRAMES ───────────────────────────────────────────────────
CANDLE_TF          = "30m"    # Primary timeframe
CANDLES_NEEDED     = 350      # How many candles to fetch for indicators
TREND_TF           = "4h"     # Higher timeframe for trend

# ── ML MODEL ─────────────────────────────────────────────────────
USE_ML_FILTER      = True     # Enable ML confidence filter
ML_MIN_CONFIDENCE  = 0.55     # Only trade if ML says ≥55% confidence
ML_RETRAIN_DAYS    = 30       # Retrain every 30 days

# ── DATABASE ─────────────────────────────────────────────────────
DB_PATH            = "data/bot_trades.db"
LOG_PATH           = "logs/bot.log"

# ── BOT BEHAVIOR ─────────────────────────────────────────────────
CHECK_INTERVAL_SEC = 60       # Check for signals every 60 seconds
PAPER_TRADE        = True     # True = simulate only (no real orders)
NOTIFY_ON_SIGNAL   = True     # Print/log every signal

# ── CIRCUIT BREAKER ──────────────────────────────────────────────
MAX_DAILY_LOSS_PCT = 3.0      # Stop trading if daily loss > 3%
MAX_WEEKLY_LOSS_PCT= 8.0      # Stop trading if weekly loss > 8%

