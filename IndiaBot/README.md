# India Trading Bot
## Intraday + Pairs Arbitrage | Dhann API | Paper Trading

### Setup

```bash
pip install -r requirements.txt
```

### Step 1 — Dhann API Keys
1. Dhann broker pe account kholo: https://dhan.co
2. API keys lo: My Account → API Keys
3. `config/settings.py` mein fill karo:
   ```python
   DHANN_CLIENT_ID    = "your_client_id"
   DHANN_ACCESS_TOKEN = "your_token"
   ```

### Step 2 — Paper Trading (Pehle yeh karo!)
```bash
python bot.py --mode once      # Single test scan
python bot.py --mode run       # Continuous paper trading
python bot.py --mode dashboard # Stats dekho
```

### Step 3 — Live Trading (Baad mein)
`config/settings.py` mein:
```python
PAPER_TRADE = False  # Tab change karo jab paper mein results acha ho
```

---

## Strategies

### 1. Intraday (Rs 25,000)
| Stock     | Strategy    | WR (backtest) | Mo Avg |
|-----------|-------------|---------------|--------|
| TCS       | BB Squeeze  | 75%           | +0.51% |
| MARUTI    | SR Breakout | 44%           | +0.61% |
| RELIANCE  | BB Squeeze  | 56%           | +0.35% |
| HDFCBANK  | EMA Cross   | 47%           | +0.31% |

### 2. Pairs Arbitrage (Rs 25,000)
| Pair                  | WR (backtest) | Mo Avg  |
|-----------------------|---------------|---------|
| BAJFINANCE/KOTAKBANK  | 64%           | +1.71%  |
| ICICIBANK/SBIN        | 65%           | +1.14%  |
| RELIANCE/WIPRO        | 59%           | +1.08%  |

**Combined expected: ~3% net monthly (after all fees)**

---

## Files
```
IndiaBot/
├── bot.py                    # Main bot — entry point
├── requirements.txt
├── config/
│   └── settings.py           # API keys, capital, params
├── core/
│   ├── dhann_client.py       # Dhann API + yfinance fallback
│   ├── intraday_strategy.py  # BB Squeeze, SR Breakout, EMA Cross
│   ├── pairs_strategy.py     # Statistical arbitrage
│   └── position_sizer.py     # Shares, fees calculation
├── data/
│   └── database.py           # SQLite trades DB
└── logs/
    └── bot.log               # Auto-created
```

## Risk Management
- Max 1% risk per trade
- Daily loss limit: 3% → auto-stop
- Square off all intraday at 15:15
- Paper trading mode default

## Tax (Student)
- Intraday business income → 0% (below Rs 2.5L/year)
- Round trip fees: ~0.102% per trade
