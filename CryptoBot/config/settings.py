"""
╔══════════════════════════════════════════════════════════════╗
║         DELTA EXCHANGE TRADING BOT — CONFIGURATION          ║
║         v2.0 — 4H Strategy + Trailing SL + Partial TP       ║
╚══════════════════════════════════════════════════════════════╝
"""

# ── API CREDENTIALS ─────────────────────────────────────────────
API_KEY    = "7bidqvVgWDCuBOmrakyGecz4NBtgb8"
API_SECRET = "tepQPqi7Dul7tLR0ygFNUN8xKwjaM5TRdkL3cTvKdZzEDZIsqTSN9kIawQGj"

USE_TESTNET = True
BASE_URL = (
    "https://cdn-ind.testnet.deltaex.org"  if USE_TESTNET else
    "https://api.india.delta.exchange"
)

# ── TRADING PAIRS ────────────────────────────────────────────────
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
        "active":        False,        # SOL disabled — low WR from backtest
    },
}

# ── RISK MANAGEMENT ──────────────────────────────────────────────
CAPITAL_USDT       = 0.0
CAPITAL_FALLBACK   = 188.81
RISK_PER_TRADE_PCT = 0.75     # was 0.5% — raised for 4H (fewer trades, better quality)
MAX_OPEN_TRADES    = 2
LEVERAGE           = 5
RR_RATIO           = 2.5      # was 2.0 — 4H allows bigger targets
ATR_MULTIPLIER     = 1.0

# ── FEES ────────────────────────────────────────────────────────
MAKER_FEE_PCT      = 0.02
TAKER_FEE_PCT      = 0.05
GST_PCT            = 18.0

# ── STRATEGY PARAMS ─────────────────────────────────────────────
BB_PERIOD          = 20
BB_STD             = 2.0
BB_SQUEEZE_PCT     = 0.20
MACD_FAST          = 12
MACD_SLOW          = 26
MACD_SIGNAL        = 9
EMA_4H_PERIOD      = 21
ATR_PERIOD         = 14

# ── TIMEFRAMES ──────────────────────────────────────────────────
CANDLE_TF          = "4h"     # Primary — backtest best (42% WR vs 34% on 30m)
CANDLE_TF_CONFIRM  = "1h"     # Confirmation timeframe
CANDLES_NEEDED     = 200      # 200 x 4h = ~33 days history
CANDLES_CONFIRM    = 100      # 1H confirmation

# ── PARTIAL TAKE PROFIT ─────────────────────────────────────────
USE_PARTIAL_TP     = True
PARTIAL_TP_R       = 1.0      # First TP at 1R (50% close)
PARTIAL_TP_SIZE    = 0.5      # 50% position at TP1

# ── TRAILING STOP LOSS ──────────────────────────────────────────
USE_TRAILING_SL    = True
TRAIL_ACTIVATE_R   = 1.0      # Start trailing at 1R profit
TRAIL_DISTANCE_ATR = 0.8      # Trail = 0.8 x ATR below price

# ── ML MODEL ────────────────────────────────────────────────────
USE_ML_FILTER      = True
ML_MIN_CONFIDENCE  = 0.52     # Slightly relaxed for 4H higher-quality signals
ML_RETRAIN_DAYS    = 30

# ── DATABASE / LOGGING ──────────────────────────────────────────
DB_PATH            = "data/bot_trades.db"
LOG_PATH           = "logs/bot.log"

# ── BOT BEHAVIOR ────────────────────────────────────────────────
CHECK_INTERVAL_SEC = 300      # 5 min check (4H bot — no need for 60s)
PAPER_TRADE        = True
NOTIFY_ON_SIGNAL   = True

# ── CIRCUIT BREAKER ─────────────────────────────────────────────
MAX_DAILY_LOSS_PCT    = 3.0
MAX_WEEKLY_LOSS_PCT   = 8.0

# ── GANN STRATEGY ───────────────────────────────────────────────
GANN_REF_PRICES = {
    "BTCUSD": 70481,
    "ETHUSD": 2070,
    "SOLUSD": 0,
}
GANN_LEVEL_TOL    = 0.004
GANN_VOL_FILTER   = 1.5
GANN_RSI_LONG_MAX = 62.0
GANN_RSI_SHORT_MIN= 38.0

STRATEGY_MODE = "both"        # "bb" | "gann" | "both" | "confirm"

# ── ENHANCED FILTERS ────────────────────────────────────────────
BB_VOL_FILTER      = 1.2      # 4H naturally has lower vol spikes (was 1.5)
GANN_MIN_RR        = 1.0
BB_MIN_SQUEEZE_BARS= 2        # 4H: 2 bars sufficient (was 3)
SUPERTREND_MULT    = 3.0
SUPERTREND_PERIOD  = 10

# ── MONTHLY PROFIT TRAILING ─────────────────────────────────────
MONTHLY_TARGET_PCT   = 3.0
MONTHLY_TRAIL_PCT    = 1.5
MONTHLY_HARD_STOP_PCT= 5.0
