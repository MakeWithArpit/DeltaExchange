"""
Unified Bot — Configuration
Crypto (Delta Exchange) + India (Dhann / NSE) trading settings.
"""

# ── DELTA EXCHANGE (CRYPTO) API ──────────────────────────────────
API_KEY    = "7bidqvVgWDCuBOmrakyGecz4NBtgb8"
API_SECRET = "tepQPqi7Dul7tLR0ygFNUN8xKwjaM5TRdkL3cTvKdZzEDZIsqTSN9kIawQGj"

USE_TESTNET = True
BASE_URL = (
    "https://cdn-ind.testnet.deltaex.org" if USE_TESTNET else
    "https://api.india.delta.exchange"
)

# ── DHANN API (INDIA) ────────────────────────────────────────────
# Get from: https://api.dhan.co → My Account → API Keys
DHANN_CLIENT_ID    = "YOUR_CLIENT_ID"
DHANN_ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# ── CRYPTO PRODUCTS ──────────────────────────────────────────────
PRODUCTS = {
    "BTCUSD": {
        "symbol":        "BTCUSD",
        "product_id":    84,
        "lot_size":      0.001,
        "min_lots":      1,
        "tick_size":     0.1,
        "contract_type": "perpetual_futures",
        "active":        True,
    },
    "ETHUSD": {
        "symbol":        "ETHUSD",
        "product_id":    1699,
        "lot_size":      0.01,
        "min_lots":      1,
        "tick_size":     0.05,
        "contract_type": "perpetual_futures",
        "active":        True,
    },
    "SOLUSD": {
        "symbol":        "SOLUSD",
        "product_id":    92572,
        "lot_size":      1,
        "min_lots":      1,
        "tick_size":     0.0001,
        "contract_type": "perpetual_futures",
        "active":        False,  # SOL disabled — low WR from backtest
    },
}

# ── CRYPTO CAPITAL & RISK ─────────────────────────────────────────
CAPITAL_USDT          = 0.0        # loaded from live wallet; 0 = fetch at startup
CAPITAL_FALLBACK      = 188.81     # used if wallet returns 0
CRYPTO_RISK_PCT       = 0.75       # % of capital at risk per crypto trade
CRYPTO_MAX_OPEN       = 2
LEVERAGE              = 5
RR_RATIO              = 2.5
ATR_MULTIPLIER        = 1.0

# ── CRYPTO FEES ───────────────────────────────────────────────────
MAKER_FEE_PCT = 0.02
TAKER_FEE_PCT = 0.05
GST_PCT       = 18.0

# ── INDIA CAPITAL & RISK ──────────────────────────────────────────
INDIA_TOTAL_CAPITAL  = 50000
INTRADAY_CAPITAL     = 25000
PAIRS_CAPITAL        = 25000
INDIA_RISK_PCT       = 1.0         # % of capital at risk per India trade
INDIA_MAX_OPEN       = 4
INDIA_ROUND_TRIP_FEE = 0.00102     # ~0.102% total intraday round trip

# RISK_PER_TRADE_PCT is referenced by India strategy files via `from config.settings import *`
RISK_PER_TRADE_PCT   = INDIA_RISK_PCT
INTRADAY_ROUND_TRIP  = INDIA_ROUND_TRIP_FEE
MAX_OPEN_TRADES      = INDIA_MAX_OPEN
MAX_DAILY_LOSS_PCT   = 3.0

# ── INDIA STOCKS (NSE INTRADAY) ───────────────────────────────────
# security_id: run bot.py --mode discover to get correct IDs
INTRADAY_STOCKS = {
    "TCS": {
        "symbol":      "TCS",
        "exchange":    "NSE",
        "security_id": "11536",
        "strategy":    "BB_SQUEEZE",
        "active":      True,
    },
    "MARUTI": {
        "symbol":      "MARUTI",
        "exchange":    "NSE",
        "security_id": "10999",
        "strategy":    "SR_BREAKOUT",
        "active":      True,
    },
    "RELIANCE": {
        "symbol":      "RELIANCE",
        "exchange":    "NSE",
        "security_id": "2885",
        "strategy":    "BB_SQUEEZE",
        "active":      True,
    },
    "HDFCBANK": {
        "symbol":      "HDFCBANK",
        "exchange":    "NSE",
        "security_id": "1333",
        "strategy":    "EMA_CROSS",
        "active":      True,
    },
}

# ── PAIRS ARBITRAGE ───────────────────────────────────────────────
PAIRS = [
    {
        "name":    "BAJFIN_KOTAK",
        "stock1":  "BAJFINANCE",
        "stock2":  "KOTAKBANK",
        "sec_id1": "317",
        "sec_id2": "1922",
        "active":  True,
    },
    {
        "name":    "ICICI_SBIN",
        "stock1":  "ICICIBANK",
        "stock2":  "SBIN",
        "sec_id1": "4963",
        "sec_id2": "3045",
        "active":  True,
    },
    {
        "name":    "RELIANCE_WIPRO",
        "stock1":  "RELIANCE",
        "stock2":  "WIPRO",
        "sec_id1": "2885",
        "sec_id2": "3787",
        "active":  True,
    },
]

# ── SHARED STRATEGY PARAMS (used by both bots) ───────────────────
BB_PERIOD          = 20
BB_STD             = 2.0
BB_SQUEEZE_PCT     = 0.20
BB_VOL_FILTER      = 1.2
BB_MIN_SQUEEZE_BARS= 2
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
EMA_4H_PERIOD      = 21
ATR_PERIOD         = 14
SUPERTREND_MULT    = 3.0
SUPERTREND_PERIOD  = 10

# ── INDIA-ONLY STRATEGY PARAMS ────────────────────────────────────
SR_PERIOD          = 20
SR_VOL_FILTER      = 1.5
RR_BB              = 2.0
RR_SR              = 2.5
EMA_FAST           = 21
EMA_SLOW           = 55
EMA_TREND          = 200
EMA_VOL_FILTER     = 1.2
RR_EMA             = 2.5
PAIRS_ENTRY_ZSCORE = 2.0
PAIRS_EXIT_ZSCORE  = 0.3
PAIRS_LOOKBACK     = 20
PAIRS_MAX_HOLD_BARS= 20

# ── CRYPTO TIMEFRAMES ─────────────────────────────────────────────
CANDLE_TF       = "4h"   # 42% WR vs 34% on 30m
CANDLES_NEEDED  = 200    # 200 x 4h ≈ 33 days history

# ── CRYPTO PARTIAL TP + TRAILING SL ──────────────────────────────
USE_PARTIAL_TP     = True
PARTIAL_TP_R       = 1.0   # close 50% at 1R profit
PARTIAL_TP_SIZE    = 0.5
USE_TRAILING_SL    = True
TRAIL_ACTIVATE_R   = 1.0   # start trailing at 1R profit
TRAIL_DISTANCE_ATR = 0.8   # trail = 0.8 × ATR below price

# ── ML MODEL (crypto only) ────────────────────────────────────────
USE_ML_FILTER      = True
ML_MIN_CONFIDENCE  = 0.52
ML_RETRAIN_DAYS    = 30

# ── GANN STRATEGY ─────────────────────────────────────────────────
GANN_REF_PRICES = {
    "BTCUSD": 70481,
    "ETHUSD": 2070,
    "SOLUSD": 0,
}
GANN_LEVEL_TOL     = 0.004
GANN_VOL_FILTER    = 1.5
GANN_RSI_LONG_MAX  = 62.0
GANN_RSI_SHORT_MIN = 38.0
GANN_MIN_RR        = 1.0

STRATEGY_MODE = "both"  # "bb" | "gann" | "both" | "confirm"

# ── INDIA MARKET HOURS ────────────────────────────────────────────
MARKET_OPEN       = "09:15"
MARKET_CLOSE      = "15:30"
NO_NEW_TRADE_AFTER= "15:00"
SQUARE_OFF_TIME   = "15:15"

# ── NSE HOLIDAYS 2025–2026 ────────────────────────────────────────
# Source: NSE official calendar (update every Jan for new year)
# Format: "YYYY-MM-DD"
NSE_HOLIDAYS = {
    # 2025
    "2025-01-26",  # Republic Day
    "2025-02-26",  # Mahashivratri
    "2025-03-14",  # Holi
    "2025-03-31",  # Id-Ul-Fitr (Ramzan Eid)
    "2025-04-10",  # Shri Ram Navami
    "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2025-04-18",  # Good Friday
    "2025-05-01",  # Maharashtra Day
    "2025-08-15",  # Independence Day
    "2025-08-27",  # Ganesh Chaturthi
    "2025-10-02",  # Mahatma Gandhi Jayanti
    "2025-10-02",  # Dussehra
    "2025-10-20",  # Diwali Laxmi Pujan (Muhurat Trading — partial)
    "2025-10-21",  # Diwali Balipratipada
    "2025-11-05",  # Prakash Gurpurb Sri Guru Nanak Dev Ji
    "2025-12-25",  # Christmas
    # 2026
    "2026-01-26",  # Republic Day
    "2026-03-20",  # Holi
    "2026-04-02",  # Shri Ram Navami
    "2026-04-03",  # Good Friday
    "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
    "2026-04-15",  # Id-Ul-Fitr (Ramzan Eid)  [tentative]
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-09-17",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-11-14",  # Diwali Laxmi Pujan  [tentative]
    "2026-12-25",  # Christmas
}
# Muhurat trading day: market opens briefly ~6:15 PM on Diwali
# Set to True if you want to trade during Muhurat session
TRADE_ON_MUHURAT = False

# ── CRYPTO CIRCUIT BREAKER ────────────────────────────────────────
MAX_WEEKLY_LOSS_PCT    = 8.0
MONTHLY_TARGET_PCT     = 3.0
MONTHLY_TRAIL_PCT      = 1.5
MONTHLY_HARD_STOP_PCT  = 5.0

# ── SHARED SETTINGS ───────────────────────────────────────────────
CHECK_INTERVAL_SEC = 300   # 5-minute main loop
PAPER_TRADE        = True
NOTIFY_ON_SIGNAL   = True
CRYPTO_DB_PATH     = "data/crypto_trades.db"
INDIA_DB_PATH      = "data/india_trades.db"
LOG_PATH           = "logs/bot.log"

# Legacy aliases kept for backward compat with strategy files
DB_PATH = CRYPTO_DB_PATH
