"""
BB Squeeze Breakout Strategy — Signal Engine
Processes candle data and generates BUY/SELL signals
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
    EMA_4H_PERIOD, ATR_PERIOD, ATR_MULTIPLIER, RR_RATIO
)


@dataclass
class Signal:
    """Trading signal with all required info"""
    symbol:      str
    direction:   str          # "long" or "short"
    entry:       float
    sl:          float
    tp:          float
    atr:         float
    confidence:  float        # 0.0 - 1.0
    squeeze_dur: int          # how many bars squeeze lasted
    breakout_str:float        # how strong is the breakout
    vol_ratio:   float        # volume vs average
    trend_4h:    str          # "bullish" / "bearish"
    timestamp:   str
    reason:      str          # human readable signal reason


class StrategyEngine:
    """
    BB Squeeze Breakout Strategy
    Rules:
      1. BB squeeze detected (width in bottom 20% of last 100 bars)
      2. Price breaks OUT of upper/lower BB
      3. 4H trend aligned (price > 4H EMA21)
      4. MACD confirms direction
      5. Not weekend
    """

    def __init__(self):
        self.last_signal_cache = {}

    def prepare_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all indicator columns to dataframe"""
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)

        # ── ATR ──────────────────────────────────────────────────
        df["tr"]  = np.maximum(df["high"] - df["low"],
                    np.maximum(abs(df["high"] - df["close"].shift(1)),
                               abs(df["low"]  - df["close"].shift(1))))
        df["atr"] = df["tr"].rolling(ATR_PERIOD).mean()

        # ── Bollinger Bands ───────────────────────────────────────
        df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
        df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
        df["bb_up"]    = df["bb_mid"] + BB_STD * df["bb_std"]
        df["bb_lo"]    = df["bb_mid"] - BB_STD * df["bb_std"]
        df["bb_width"] = (df["bb_up"] - df["bb_lo"]) / df["bb_mid"]

        # Squeeze = BB width in bottom 20% of last 100 bars
        df["bb_squeeze"] = df["bb_width"].rolling(100).rank(pct=True) < BB_SQUEEZE_PCT

        # Squeeze duration (consecutive squeezed candles)
        sq = 0; sq_list = []
        for s in df["bb_squeeze"]:
            sq = sq + 1 if s else 0
            sq_list.append(sq)
        df["squeeze_dur"] = sq_list

        # Breakout strength = how far price broke past band / ATR
        atr_safe = df["atr"].replace(0, np.nan).ffill().bfill()
        df["breakout_str"] = np.where(
            df["close"] > df["bb_up"],
            (df["close"] - df["bb_up"]) / atr_safe,
            np.where(df["close"] < df["bb_lo"],
                     (df["bb_lo"] - df["close"]) / atr_safe, 0.0))

        # ── MACD ──────────────────────────────────────────────────
        df["macd"]      = (df["close"].ewm(span=MACD_FAST).mean() -
                           df["close"].ewm(span=MACD_SLOW).mean())
        df["macd_sig"]  = df["macd"].ewm(span=MACD_SIGNAL).mean()
        df["macd_hist"] = df["macd"] - df["macd_sig"]
        df["macd_bull"] = df["macd"] > df["macd_sig"]

        # ── Volume ───────────────────────────────────────────────
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

        # ── EMA200 ───────────────────────────────────────────────
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        # ── 4H Trend (resampled from 30min data) ─────────────────
        df_4h = df.set_index("time").resample("4h").agg({
            "open":"first","high":"max","low":"min","close":"last"
        }).dropna()
        df_4h["ema_4h"] = df_4h["close"].ewm(span=EMA_4H_PERIOD, adjust=False).mean()
        df_4h["trend_4h"] = np.where(df_4h["close"] > df_4h["ema_4h"], 1, -1)
        df["time_4h"] = df["time"].dt.floor("4h")
        df = df.merge(
            df_4h[["trend_4h"]].reset_index().rename(columns={"time": "time_4h"}),
            on="time_4h", how="left"
        )

        # ── Weekend flag ─────────────────────────────────────────
        df["is_weekend"] = df["time"].dt.dayofweek >= 5

        return df

    def candles_to_df(self, raw_candles: list) -> pd.DataFrame:
        """Convert Delta Exchange API candle format to DataFrame"""
        if not raw_candles:
            return pd.DataFrame()
        records = []
        for c in raw_candles:
            # Delta API returns: [time, open, high, low, close, volume]
            if isinstance(c, (list, tuple)) and len(c) >= 6:
                records.append({
                    "time":   pd.to_datetime(c[0], unit="s"),
                    "open":   float(c[1]),
                    "high":   float(c[2]),
                    "low":    float(c[3]),
                    "close":  float(c[4]),
                    "volume": float(c[5]),
                })
            elif isinstance(c, dict):
                records.append({
                    "time":   pd.to_datetime(c.get("time", c.get("t", 0)), unit="s"),
                    "open":   float(c.get("open",  c.get("o", 0))),
                    "high":   float(c.get("high",  c.get("h", 0))),
                    "low":    float(c.get("low",   c.get("l", 0))),
                    "close":  float(c.get("close", c.get("c", 0))),
                    "volume": float(c.get("volume",c.get("v", 0))),
                })
        return pd.DataFrame(records)

    def check_signal(self, symbol: str, df: pd.DataFrame) -> Optional[Signal]:
        """
        Check latest candle for BB Squeeze signal
        Returns Signal object if valid signal found, else None
        """
        if len(df) < 300:
            return None

        df = self.prepare_indicators(df)
        if len(df) < 10:
            return None

        # Look at the LAST COMPLETED candle (index -2, not -1 which is forming)
        row  = df.iloc[-2]
        prev = df.iloc[-3]  # candle before signal

        # ── ENTRY CONDITIONS ─────────────────────────────────────
        # Condition 1: Squeeze was active on previous candle
        squeeze_was_active = bool(prev["bb_squeeze"])
        if not squeeze_was_active:
            return None

        # Condition 2: Price broke out of BB
        long_breakout  = float(row["close"]) > float(row["bb_up"])
        short_breakout = float(row["close"]) < float(row["bb_lo"])
        if not (long_breakout or short_breakout):
            return None

        # Condition 3: MACD confirmation
        macd_bull = bool(row["macd_bull"])
        if long_breakout  and not macd_bull:  return None
        if short_breakout and     macd_bull:  return None

        # Condition 4: 4H trend aligned
        trend_4h = int(row.get("trend_4h", 0))
        if long_breakout  and trend_4h != 1:  return None
        if short_breakout and trend_4h != -1: return None

        # Condition 5: Not weekend
        if bool(row["is_weekend"]):
            return None

        # ── CALCULATE LEVELS ─────────────────────────────────────
        atr       = float(row["atr"])
        entry     = float(row["close"])
        direction = "long" if long_breakout else "short"

        if direction == "long":
            sl = entry - atr * ATR_MULTIPLIER
            tp = entry + abs(entry - sl) * RR_RATIO
        else:
            sl = entry + atr * ATR_MULTIPLIER
            tp = entry - abs(entry - sl) * RR_RATIO

        # ── CONFIDENCE SCORE (0.0 - 1.0) ─────────────────────────
        # Used by ML layer but also useful standalone
        score = 0.5  # base
        if float(row.get("vol_ratio", 1.0)) > 1.5:   score += 0.1
        if float(row.get("breakout_str", 0)) > 0.3:   score += 0.1
        if int(row.get("squeeze_dur", 0)) > 5:        score += 0.1
        if float(row.get("macd_hist", 0)) > 0:        score += 0.1 if direction=="long" else -0.1
        score = max(0.1, min(1.0, score))

        reason = (
            f"BB Squeeze ({int(row.get('squeeze_dur',0))} bars) → "
            f"{'Upper' if direction=='long' else 'Lower'} band break | "
            f"4H {'Bullish' if trend_4h==1 else 'Bearish'} | "
            f"MACD {'↑' if macd_bull else '↓'} | "
            f"Vol {float(row.get('vol_ratio',1)):.1f}x avg"
        )

        return Signal(
            symbol       = symbol,
            direction    = direction,
            entry        = round(entry, 4),
            sl           = round(sl, 4),
            tp           = round(tp, 4),
            atr          = round(atr, 4),
            confidence   = round(score, 3),
            squeeze_dur  = int(row.get("squeeze_dur", 0)),
            breakout_str = round(float(row.get("breakout_str", 0)), 4),
            vol_ratio    = round(float(row.get("vol_ratio", 1.0)), 3),
            trend_4h     = "bullish" if trend_4h == 1 else "bearish",
            timestamp    = str(row["time"]),
            reason       = reason,
        )

    def get_current_state(self, symbol: str, df: pd.DataFrame) -> dict:
        """Get current market state (for dashboard display)"""
        if len(df) < 100:
            return {}
        df = self.prepare_indicators(df)
        row = df.iloc[-1]
        return {
            "symbol":       symbol,
            "price":        float(row["close"]),
            "bb_squeeze":   bool(row["bb_squeeze"]),
            "squeeze_dur":  int(row.get("squeeze_dur", 0)),
            "macd_bull":    bool(row["macd_bull"]),
            "trend_4h":     "bullish" if int(row.get("trend_4h",0))==1 else "bearish",
            "bb_width":     round(float(row["bb_width"]), 6),
            "vol_ratio":    round(float(row.get("vol_ratio",1)), 3),
            "atr":          round(float(row["atr"]), 4),
            "is_weekend":   bool(row["is_weekend"]),
            "timestamp":    str(row["time"]),
        }
