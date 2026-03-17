"""
Gann Square Root Strategy — Signal Engine
==========================================
Method:
  1. Ek reference price lo
  2. sqrt(ref) nikalo, square karte jao (n, n+1, n+2...)
  3. Upar aur niche dono direction mein jab tak
     poora historical price range cover na ho
  4. Hamesha ODD values milti hain (even pe +1)
  5. In levels pe candle pattern dekhte hain
  6. Entry: level touch + pattern confirm
  7. SL: entry candle ka low/high
  8. TP: next Gann level

Backtest results (8.5 yr data):
  BTC: 41.0% WR | +21.9R net | 4 trades/mo
  ETH: 23.2% WR | +76.2R net | 1.6 trades/mo (avg win +5.32R)
  SOL: 10.3% WR — avoid
"""

import math
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, List
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    GANN_REF_PRICES,
    GANN_LEVEL_TOL,
    GANN_VOL_FILTER,
    GANN_RSI_LONG_MAX,
    GANN_RSI_SHORT_MIN,
    GANN_MIN_RR,
)


# ── SIGNAL DATACLASS ──────────────────────────────────────────────
@dataclass
class GannSignal:
    symbol:      str
    direction:   str        # "long" or "short"
    entry:       float
    sl:          float
    tp:          float
    gann_level:  float      # level that was touched
    pattern:     str        # which candle pattern triggered
    rr:          float      # risk:reward ratio
    confidence:  float      # 0.0 - 1.0
    vol_ratio:   float
    rsi:         float
    trend_4h:    str
    timestamp:   str
    reason:      str


# ── GANN LEVEL GENERATOR ─────────────────────────────────────────
def generate_gann_levels(ref_price: float,
                          low_limit: float,
                          high_limit: float) -> List[float]:
    """
    Teri exact method:
    sqrt(ref) → n → n² (odd) ya n²+1 (even→odd)
    Upar bhi niche bhi jab tak range cover na ho
    Returns: sorted list of ODD Gann levels
    """
    sq = int(math.sqrt(ref_price))
    levels = set()

    # Base level
    s = sq ** 2
    levels.add(s + 1 if s % 2 == 0 else s)

    # Upar: jab tak high_limit cover na ho
    n = sq + 1
    while n < sq + 500:
        s = n ** 2
        val = s + 1 if s % 2 == 0 else s
        levels.add(val)
        if val >= high_limit:
            break
        n += 1

    # Niche: jab tak low_limit cover na ho
    n = sq - 1
    while n > 0:
        s = n ** 2
        val = s + 1 if s % 2 == 0 else s
        levels.add(val)
        if val <= low_limit:
            break
        n -= 1

    result = sorted(levels)
    return result


# ── CANDLE PATTERN DETECTION ─────────────────────────────────────
def detect_candle_pattern(df: pd.DataFrame, i: int) -> Optional[str]:
    """
    Patterns:
    LONG  → bullish engulfing, hammer, morning star
    SHORT → bearish engulfing, shooting star, evening star
    """
    if i < 2 or i >= len(df):
        return None

    c   = df.iloc[i]
    p   = df.iloc[i-1]
    pp  = df.iloc[i-2]

    c_o, c_c, c_h, c_l = c['open'], c['close'], c['high'], c['low']
    p_o, p_c            = p['open'], p['close']
    pp_o, pp_c          = pp['open'], pp['close']

    body_c = abs(c_c - c_o)
    lw     = min(c_o, c_c) - c_l          # lower wick
    uw     = c_h - max(c_o, c_c)          # upper wick

    if body_c == 0:
        return None

    # ── BULLISH PATTERNS ─────────────────────────────────────────
    # Bullish engulfing
    if (p_c < p_o and c_c > c_o
            and c_c > p_o and c_o < p_c):
        return 'bullish_engulfing'

    # Hammer (lower wick >= 2x body, small upper wick, green candle)
    if (lw >= 2 * body_c and uw <= body_c * 0.5 and c_c > c_o):
        return 'hammer'

    # Morning star (3 candle)
    if (pp_c < pp_o
            and abs(p_c - p_o) < abs(pp_c - pp_o) * 0.5
            and c_c > c_o
            and c_c > (pp_o + pp_c) / 2):
        return 'morning_star'

    # ── BEARISH PATTERNS ─────────────────────────────────────────
    # Bearish engulfing
    if (p_c > p_o and c_c < c_o
            and c_c < p_o and c_o > p_c):
        return 'bearish_engulfing'

    # Shooting star (upper wick >= 2x body, small lower wick, red candle)
    if (uw >= 2 * body_c and lw <= body_c * 0.5 and c_c < c_o):
        return 'shooting_star'

    # Evening star (3 candle)
    if (pp_c > pp_o
            and abs(p_c - p_o) < abs(pp_c - pp_o) * 0.5
            and c_c < c_o
            and c_c < (pp_o + pp_c) / 2):
        return 'evening_star'

    return None


def pattern_direction(pattern: str) -> str:
    """Returns 'long' or 'short' based on pattern"""
    bullish = {'bullish_engulfing', 'hammer', 'morning_star'}
    if pattern in bullish:
        return 'long'
    return 'short'


# ── GANN STRATEGY ENGINE ─────────────────────────────────────────
class GannStrategyEngine:

    def __init__(self):
        # Cache levels per symbol so we don't regenerate every cycle
        self._levels_cache: dict = {}          # {symbol: [levels]}
        self._levels_ref:   dict = {}          # {symbol: ref_price}

    # ── PUBLIC API ────────────────────────────────────────────────
    def get_levels(self, symbol: str, current_price: float) -> List[float]:
        """
        Get Gann levels for a symbol.
        Uses GANN_REF_PRICES from settings if set to non-zero,
        otherwise uses current_price as reference.
        """
        ref_setting = GANN_REF_PRICES.get(symbol, 0)
        # 0 means "use current price"
        ref = ref_setting if ref_setting > 0 else current_price

        # Regenerate only if not cached or ref changed > 10%
        cached_ref = self._levels_ref.get(symbol, 0)
        if (symbol not in self._levels_cache or
                (cached_ref > 0 and abs(ref - cached_ref) / cached_ref > 0.10)):
            lo = max(ref * 0.01, 1.0)   # floor at $1
            hi = ref * 8.0              # ceiling at 8x
            self._levels_cache[symbol] = generate_gann_levels(ref, lo, hi)
            self._levels_ref[symbol]   = ref

        return self._levels_cache[symbol]

    def prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add RSI, EMA trend, volume ratio"""
        df = df.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time').reset_index(drop=True)

        # RSI (14)
        delta = df['close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        df['rsi'] = 100 - (100 / (1 + rs))

        # EMA 21 (for trend filter)
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

        # Volume ratio vs 20-bar avg
        df['vol_ratio'] = df['volume'] / df['volume'].rolling(20).mean()

        # 4H trend via resample
        try:
            df_4h = df.set_index('time').resample('4h').agg({
                'close': 'last'
            }).dropna()
            df_4h['ema_4h'] = df_4h['close'].ewm(span=21, adjust=False).mean()
            df_4h['trend_4h'] = np.where(df_4h['close'] > df_4h['ema_4h'], 1, -1)
            df['time_4h'] = df['time'].dt.floor('4h')
            df = df.merge(
                df_4h[['trend_4h']].reset_index().rename(columns={'time': 'time_4h'}),
                on='time_4h', how='left'
            )
        except Exception:
            df['trend_4h'] = 0

        return df

    def find_touched_level(self, high: float, low: float,
                           levels: List[float],
                           tol: float = None) -> Optional[tuple]:
        """
        Check if candle's HIGH or LOW touched any Gann level.
        Returns (level_value, touch_type) or None.
        touch_type: 'support' (low touched) or 'resistance' (high touched)
        """
        tol = tol or GANN_LEVEL_TOL

        for lvl in levels:
            # LOW touched a support level
            if abs(low - lvl) / max(lvl, 1) <= tol:
                return (lvl, 'support')
            # HIGH touched a resistance level
            if abs(high - lvl) / max(lvl, 1) <= tol:
                return (lvl, 'resistance')
        return None

    def check_signal(self, symbol: str,
                     df: pd.DataFrame) -> Optional[GannSignal]:
        """
        Main signal check.
        Looks at last completed candle for Gann level touch + pattern.
        Returns GannSignal or None.
        """
        if len(df) < 60:
            return None

        df = self.prepare_indicators(df)
        if len(df) < 10:
            return None

        # Current price for level generation
        current_price = float(df.iloc[-1]['close'])

        # Get fixed Gann levels
        levels = self.get_levels(symbol, current_price)
        if not levels:
            return None

        # Check last 3 candles (in case we missed one)
        for offset in [2, 3, 4]:
            if len(df) < offset + 1:
                continue
            idx = len(df) - offset   # last completed candle
            row = df.iloc[idx]

            hi    = float(row['high'])
            lo    = float(row['low'])
            price = float(row['close'])

            # ── Step 1: Level touch? ──────────────────────────────
            touched = self.find_touched_level(hi, lo, levels)
            if not touched:
                continue
            lvl, touch_type = touched

            # ── Step 2: Candle pattern? ───────────────────────────
            pattern = detect_candle_pattern(df, idx)
            if not pattern:
                continue

            pat_dir = pattern_direction(pattern)

            # Pattern must match touch type
            if touch_type == 'support'    and pat_dir != 'long':  continue
            if touch_type == 'resistance' and pat_dir != 'short': continue

            # ── Step 3: Trend filter ──────────────────────────────
            ema_now  = float(row.get('ema21', price))
            ema_prev = float(df.iloc[max(0, idx-5)].get('ema21', price))
            ema_bull = ema_now > ema_prev

            if pat_dir == 'long'  and not ema_bull: continue
            if pat_dir == 'short' and ema_bull:     continue

            # ── Step 4: Volume filter ─────────────────────────────
            vol_ratio = float(row.get('vol_ratio', 1.0))
            if pd.isna(vol_ratio) or vol_ratio < GANN_VOL_FILTER:
                continue

            # ── Step 5: RSI filter ────────────────────────────────
            rsi = float(row.get('rsi', 50.0))
            if pd.isna(rsi):
                continue
            if pat_dir == 'long'  and rsi > GANN_RSI_LONG_MAX:  continue
            if pat_dir == 'short' and rsi < GANN_RSI_SHORT_MIN: continue

            # ── Step 6: SL and TP ─────────────────────────────────
            if pat_dir == 'long':
                sl_price = float(row['low'])  * 0.9995   # candle low
                above    = [l for l in levels if l > price]
                if not above:
                    continue
                tp_price = above[0]                        # next Gann level
            else:
                sl_price = float(row['high']) * 1.0005    # candle high
                below    = [l for l in levels if l < price]
                if not below:
                    continue
                tp_price = below[-1]                       # next Gann level down

            risk   = abs(price - sl_price)
            reward = abs(tp_price - price)
            if risk <= 0 or reward / risk < GANN_MIN_RR:
                continue

            rr     = round(reward / risk, 2)
            trend_4h_val = int(row.get('trend_4h', 0))
            trend_str    = 'bullish' if trend_4h_val == 1 else 'bearish'

            # ── Confidence Score ──────────────────────────────────
            conf = 0.5
            if vol_ratio > 1.5:  conf += 0.1
            if vol_ratio > 2.0:  conf += 0.05
            if rr > 2.0:         conf += 0.1
            if pattern in ('bullish_engulfing', 'bearish_engulfing'):
                conf += 0.1
            if pattern in ('morning_star', 'evening_star'):
                conf += 0.15
            conf = round(min(0.95, max(0.3, conf)), 3)

            reason = (
                f"Gann level {lvl:,.0f} touched | "
                f"{pattern.replace('_',' ').title()} | "
                f"{'Support' if touch_type=='support' else 'Resistance'} | "
                f"RSI={rsi:.0f} | Vol={vol_ratio:.1f}x | "
                f"4H {trend_str} | RR={rr}"
            )

            return GannSignal(
                symbol     = symbol,
                direction  = pat_dir,
                entry      = round(price, 4),
                sl         = round(sl_price, 4),
                tp         = round(tp_price, 4),
                gann_level = lvl,
                pattern    = pattern,
                rr         = rr,
                confidence = conf,
                vol_ratio  = round(vol_ratio, 3),
                rsi        = round(rsi, 1),
                trend_4h   = trend_str,
                timestamp  = str(row['time']),
                reason     = reason,
            )

        return None

    def get_current_state(self, symbol: str,
                          df: pd.DataFrame) -> dict:
        """For dashboard — show nearest Gann levels"""
        if len(df) < 20:
            return {}
        df = self.prepare_indicators(df)
        row   = df.iloc[-1]
        price = float(row['close'])

        levels = self.get_levels(symbol, price)
        above  = [l for l in levels if l > price]
        below  = [l for l in levels if l < price]

        nearest_r = above[0] if above else None
        nearest_s = below[-1] if below else None

        return {
            'symbol':    symbol,
            'price':     price,
            'gann_r1':   nearest_r,
            'gann_s1':   nearest_s,
            'dist_r1':   round((nearest_r - price) / price * 100, 2) if nearest_r else None,
            'dist_s1':   round((price - nearest_s) / price * 100, 2) if nearest_s else None,
            'rsi':       round(float(row.get('rsi', 50)), 1),
            'vol_ratio': round(float(row.get('vol_ratio', 1)), 2),
            'trend_4h':  'bullish' if int(row.get('trend_4h', 0)) == 1 else 'bearish',
            'total_levels': len(levels),
            'timestamp': str(row['time']),
        }
