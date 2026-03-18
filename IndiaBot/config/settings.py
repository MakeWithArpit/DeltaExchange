"""
╔══════════════════════════════════════════════════════════════╗
║       INDIA STOCK TRADING BOT — CONFIGURATION               ║
║       Intraday + Pairs Arbitrage | Dhann API                ║
╚══════════════════════════════════════════════════════════════╝
"""

# ── DHANN API ────────────────────────────────────────────────────
# Get from: https://api.dhan.co → My Account → API Keys
DHANN_CLIENT_ID    = "YOUR_CLIENT_ID"
DHANN_ACCESS_TOKEN = "YOUR_ACCESS_TOKEN"

# ── CAPITAL ──────────────────────────────────────────────────────
TOTAL_CAPITAL     = 50000
INTRADAY_CAPITAL  = 25000    # Regular intraday
PAIRS_CAPITAL     = 25000    # Pairs arbitrage

# ── RISK ─────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT = 1.0
MAX_DAILY_LOSS_PCT = 3.0
MAX_OPEN_TRADES    = 4

# ── INTRADAY STOCKS ───────────────────────────────────────────────
# Dhann security_id: run bot.py --mode discover to get correct IDs
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

# ── STRATEGY PARAMS ───────────────────────────────────────────────
BB_PERIOD           = 20
BB_STD              = 2.0
BB_SQUEEZE_PCT      = 0.20
BB_VOL_FILTER       = 1.3
BB_MIN_SQUEEZE_BARS = 2
RR_BB               = 2.0

SR_PERIOD           = 20
SR_VOL_FILTER       = 1.5
RR_SR               = 2.5

EMA_FAST            = 21
EMA_SLOW            = 55
EMA_TREND           = 200
EMA_VOL_FILTER      = 1.2
RR_EMA              = 2.5

PAIRS_ENTRY_ZSCORE  = 2.0
PAIRS_EXIT_ZSCORE   = 0.3
PAIRS_LOOKBACK      = 20
PAIRS_MAX_HOLD_BARS = 20

# ── FEES ─────────────────────────────────────────────────────────
INTRADAY_ROUND_TRIP = 0.00102   # ~0.102% total intraday round trip

# ── TIMING ───────────────────────────────────────────────────────
MARKET_OPEN         = "09:15"
MARKET_CLOSE        = "15:30"
NO_NEW_TRADE_AFTER  = "15:00"
SQUARE_OFF_TIME     = "15:15"
CHECK_INTERVAL_SEC  = 300

# ── MISC ─────────────────────────────────────────────────────────
PAPER_TRADE = True
DB_PATH     = "data/bot_trades.db"
LOG_PATH    = "logs/bot.log"
