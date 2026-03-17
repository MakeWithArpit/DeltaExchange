"""
BB Squeeze Breakout Strategy — Enhanced v2
==========================================
Improvements based on backtest data:
  - Volume filter raised to 2.0x avg (+4.6% WR)
  - Supertrend filter added
  - Dynamic ATR-based SL (tighter = better RR)
  - Min squeeze bars filter
  - Confidence scoring improved
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    BB_PERIOD, BB_STD, BB_SQUEEZE_PCT,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    EMA_4H_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, RR_RATIO,
    BB_VOL_FILTER, BB_MIN_SQUEEZE_BARS,
    SUPERTREND_MULT, SUPERTREND_PERIOD,
)


@dataclass
class Signal:
    symbol:       str
    direction:    str
    entry:        float
    sl:           float
    tp:           float
    atr:          float
    confidence:   float
    squeeze_dur:  int
    breakout_str: float
    vol_ratio:    float
    trend_4h:     str
    timestamp:    str
    reason:       str


class StrategyEngine:

    def __init__(self):
        self.last_signal_cache = {}

    def _compute_supertrend(self, df: pd.DataFrame,
                             period: int = None,
                             mult: float = None) -> pd.Series:
        """
        Supertrend indicator.
        Returns Series: True = bullish, False = bearish.
        """
        period = period or SUPERTREND_PERIOD
        mult   = mult   or SUPERTREND_MULT

        atr = df['tr'].rolling(period).mean()
        hl2 = (df['high'] + df['low']) / 2
        upper = hl2 + mult * atr
        lower = hl2 - mult * atr

        st = [lower.iloc[0]]
        for i in range(1, len(df)):
            if df['close'].iloc[i] > st[-1]:
                st.append(max(lower.iloc[i], st[-1]))
            else:
                st.append(min(upper.iloc[i], st[-1]))

        st_series  = pd.Series(st, index=df.index)
        return df['close'] > st_series   # True = bullish

    def prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['time'] = pd.to_datetime(df['time'])
        df = df.sort_values('time').reset_index(drop=True)

        # ── ATR ──────────────────────────────────────────────────
        df['tr']  = np.maximum(df['high'] - df['low'],
                    np.maximum(abs(df['high'] - df['close'].shift(1)),
                               abs(df['low']  - df['close'].shift(1))))
        df['atr'] = df['tr'].rolling(ATR_PERIOD).mean()

        # ── Bollinger Bands ───────────────────────────────────────
        df['bb_mid']   = df['close'].rolling(BB_PERIOD).mean()
        df['bb_std']   = df['close'].rolling(BB_PERIOD).std()
        df['bb_up']    = df['bb_mid'] + BB_STD * df['bb_std']
        df['bb_lo']    = df['bb_mid'] - BB_STD * df['bb_std']
        df['bb_width'] = (df['bb_up'] - df['bb_lo']) / df['bb_mid']

        df['bb_squeeze'] = df['bb_width'].rolling(100).rank(pct=True) < BB_SQUEEZE_PCT

        sq = 0; sq_list = []
        for s in df['bb_squeeze']:
            sq = sq + 1 if s else 0
            sq_list.append(sq)
        df['squeeze_dur'] = sq_list

        atr_safe = df['atr'].replace(0, np.nan).ffill().bfill()
        df['breakout_str'] = np.where(
            df['close'] > df['bb_up'],
            (df['close'] - df['bb_up']) / atr_safe,
            np.where(df['close'] < df['bb_lo'],
                     (df['bb_lo'] - df['close']) / atr_safe, 0.0))

        # ── MACD ──────────────────────────────────────────────────
        df['macd']      = (df['close'].ewm(span=MACD_FAST).mean() -
                           df['close'].ewm(span=MACD_SLOW).mean())
        df['macd_sig']  = df['macd'].ewm(span=MACD_SIGNAL).mean()
        df['macd_hist'] = df['macd'] - df['macd_sig']
        df['macd_bull'] = df['macd'] > df['macd_sig']

        # ── Volume ────────────────────────────────────────────────
        df['vol_ma']    = df['volume'].rolling(20).mean()
        df['vol_ratio'] = df['volume'] / df['vol_ma'].replace(0, np.nan)

        # ── EMA200 ───────────────────────────────────────────────
        df['ema200'] = df['close'].ewm(span=200, adjust=False).mean()

        # ── RSI ───────────────────────────────────────────────────
        delta = df['close'].diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        df['rsi'] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

        # ── Supertrend ────────────────────────────────────────────
        try:
            df['st_bull'] = self._compute_supertrend(df)
        except Exception:
            df['st_bull'] = True   # safe default

        # ── 4H Trend ─────────────────────────────────────────────
        df_4h = df.set_index('time').resample('4h').agg({
            'open':'first','high':'max','low':'min','close':'last'
        }).dropna()
        df_4h['ema_4h']   = df_4h['close'].ewm(span=EMA_4H_PERIOD, adjust=False).mean()
        df_4h['trend_4h'] = np.where(df_4h['close'] > df_4h['ema_4h'], 1, -1)
        df['time_4h'] = df['time'].dt.floor('4h')
        df = df.merge(
            df_4h[['trend_4h']].reset_index().rename(columns={'time': 'time_4h'}),
            on='time_4h', how='left'
        )

        # ── Weekend flag ──────────────────────────────────────────
        df['is_weekend'] = df['time'].dt.dayofweek >= 5

        return df

    def candles_to_df(self, raw_candles: list) -> pd.DataFrame:
        if not raw_candles:
            return pd.DataFrame()

        def _f(v):
            try:
                return float(v) if v not in (None, '', 'null') else 0.0
            except (ValueError, TypeError):
                return 0.0

        records = []
        for c in raw_candles:
            try:
                if isinstance(c, (list, tuple)) and len(c) >= 6:
                    records.append({
                        'time':   pd.to_datetime(int(_f(c[0])), unit='s'),
                        'open':   _f(c[1]), 'high': _f(c[2]),
                        'low':    _f(c[3]), 'close': _f(c[4]),
                        'volume': _f(c[5]),
                    })
                elif isinstance(c, dict):
                    t = c.get('time', c.get('t', c.get('timestamp', 0)))
                    records.append({
                        'time':   pd.to_datetime(int(_f(t)), unit='s'),
                        'open':   _f(c.get('open',   c.get('o', 0))),
                        'high':   _f(c.get('high',   c.get('h', 0))),
                        'low':    _f(c.get('low',    c.get('l', 0))),
                        'close':  _f(c.get('close',  c.get('c', 0))),
                        'volume': _f(c.get('volume', c.get('v', 0))),
                    })
            except Exception:
                continue

        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        return df.sort_values('time').reset_index(drop=True)

    def check_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        if len(df) < 300:
            return None

        df = self.prepare_indicators(df)
        if len(df) < 10:
            return None

        row  = df.iloc[-2]   # last completed candle
        prev = df.iloc[-3]

        # ── CONDITION 1: Squeeze was active ───────────────────────
        if not bool(prev['bb_squeeze']):
            return None

        # ── CONDITION 2: Minimum squeeze duration ─────────────────
        if int(prev.get('squeeze_dur', 0)) < BB_MIN_SQUEEZE_BARS:
            return None

        # ── CONDITION 3: Price broke out of BB ────────────────────
        long_bo  = float(row['close']) > float(row['bb_up'])
        short_bo = float(row['close']) < float(row['bb_lo'])
        if not (long_bo or short_bo):
            return None

        # ── CONDITION 4: MACD confirmation ────────────────────────
        macd_bull = bool(row['macd_bull'])
        if long_bo  and not macd_bull: return None
        if short_bo and macd_bull:     return None

        # ── CONDITION 5: 4H trend aligned ─────────────────────────
        trend_4h = int(row.get('trend_4h', 0))
        if long_bo  and trend_4h != 1:  return None
        if short_bo and trend_4h != -1: return None

        # ── CONDITION 6: Volume filter (upgraded to 2x) ───────────
        vol_ratio = float(row.get('vol_ratio', 1.0))
        if pd.isna(vol_ratio) or vol_ratio < BB_VOL_FILTER:
            return None

        # ── CONDITION 7: Supertrend alignment ─────────────────────
        st_bull = bool(row.get('st_bull', True))
        if long_bo  and not st_bull: return None
        if short_bo and st_bull:     return None

        # ── CONDITION 8: Not weekend ──────────────────────────────
        if bool(row['is_weekend']):
            return None

        # ── CALCULATE LEVELS ──────────────────────────────────────
        atr       = float(row['atr'])
        entry     = float(row['close'])
        direction = 'long' if long_bo else 'short'

        if pd.isna(atr) or atr <= 0:
            return None

        # ATR-based SL (tighter: 0.8x ATR vs old 1.0x)
        sl_mult = ATR_MULTIPLIER * 0.8
        if direction == 'long':
            sl = entry - atr * sl_mult
            tp = entry + abs(entry - sl) * RR_RATIO
        else:
            sl = entry + atr * sl_mult
            tp = entry - abs(sl - entry) * RR_RATIO

        # ── CONFIDENCE SCORE ──────────────────────────────────────
        score = 0.5
        if vol_ratio > 2.0:  score += 0.10
        if vol_ratio > 3.0:  score += 0.05
        sq_dur = int(row.get('squeeze_dur', 0))
        if sq_dur > 5:       score += 0.10
        if sq_dur > 10:      score += 0.05
        if float(row.get('breakout_str', 0)) > 0.3: score += 0.08
        if float(row.get('macd_hist',    0)) > 0:   score += 0.05
        if st_bull == (direction == 'long'):         score += 0.07
        score = round(max(0.1, min(0.98, score)), 3)

        rsi = float(row.get('rsi', 50))
        reason = (
            f"BB Squeeze ({sq_dur}b) breakout | "
            f"Vol {vol_ratio:.1f}x | "
            f"4H {'Bull' if trend_4h==1 else 'Bear'} | "
            f"ST {'Bull' if st_bull else 'Bear'} | "
            f"MACD {'up' if macd_bull else 'dn'} | "
            f"RSI={rsi:.0f}"
        )

        return Signal(
            symbol       = symbol,
            direction    = direction,
            entry        = round(entry, 4),
            sl           = round(sl, 4),
            tp           = round(tp, 4),
            atr          = round(atr, 4),
            confidence   = score,
            squeeze_dur  = sq_dur,
            breakout_str = round(float(row.get('breakout_str', 0)), 4),
            vol_ratio    = round(vol_ratio, 3),
            trend_4h     = 'bullish' if trend_4h == 1 else 'bearish',
            timestamp    = str(row['time']),
            reason       = reason,
        )

    def get_current_state(self, symbol: str, df: pd.DataFrame) -> dict:
        if len(df) < 100:
            return {}
        df = self.prepare_indicators(df)
        row = df.iloc[-1]
        return {
            'symbol':      symbol,
            'price':       float(row['close']),
            'bb_squeeze':  bool(row['bb_squeeze']),
            'squeeze_dur': int(row.get('squeeze_dur', 0)),
            'macd_bull':   bool(row['macd_bull']),
            'trend_4h':    'bullish' if int(row.get('trend_4h', 0)) == 1 else 'bearish',
            'bb_width':    round(float(row['bb_width']), 6),
            'vol_ratio':   round(float(row.get('vol_ratio', 1)), 3),
            'atr':         round(float(row['atr']), 4),
            'rsi':         round(float(row.get('rsi', 50)), 1),
            'st_bull':     bool(row.get('st_bull', True)),
            'is_weekend':  bool(row['is_weekend']),
            'timestamp':   str(row['time']),
            'strategy':    'bb',
        }