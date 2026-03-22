"""
Intraday Strategy Engine
Strategies: BB Squeeze, SR Breakout, EMA Cross
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import *


@dataclass
class IntradaySignal:
    symbol:    str
    strategy:  str
    direction: str      # "long" or "short"
    entry:     float
    sl:        float
    tp:        float
    atr:       float
    vol_ratio: float
    confidence:float
    reason:    str
    timestamp: str


class IntradayEngine:

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy().sort_values("datetime").reset_index(drop=True)

        # ATR
        df["pc"]  = df["close"].shift(1)
        df["tr"]  = np.maximum(df["high"] - df["low"],
                    np.maximum(abs(df["high"] - df["pc"]),
                               abs(df["low"]  - df["pc"])))
        df["atr"] = df["tr"].rolling(14).mean()

        # Volume
        df["vm"]  = df["volume"].rolling(20).mean()
        df["vr"]  = df["volume"] / df["vm"].replace(0, np.nan)

        # BB
        df["bm"]   = df["close"].rolling(BB_PERIOD).mean()
        df["bs"]   = df["close"].rolling(BB_PERIOD).std()
        df["bu"]   = df["bm"] + BB_STD * df["bs"]
        df["bl"]   = df["bm"] - BB_STD * df["bs"]
        df["bw"]   = (df["bu"] - df["bl"]) / df["bm"]
        df["bsq"]  = df["bw"].rolling(60).rank(pct=True) < BB_SQUEEZE_PCT
        sq = 0; sqs = []
        for s in df["bsq"]: sq = sq+1 if s else 0; sqs.append(sq)
        df["sqd"]  = sqs

        # MACD
        df["macd"] = df["close"].ewm(span=12).mean() - df["close"].ewm(span=26).mean()
        df["ms"]   = df["macd"].ewm(span=9).mean()
        df["mb"]   = df["macd"] > df["ms"]

        # RSI
        d = df["close"].diff()
        df["rsi"]  = 100 - (100 / (1 + d.clip(lower=0).rolling(14).mean() /
                             (-d.clip(upper=0)).rolling(14).mean().replace(0, np.nan)))

        # EMAs
        df["ema21"]  = df["close"].ewm(span=EMA_FAST,  adjust=False).mean()
        df["ema55"]  = df["close"].ewm(span=EMA_SLOW,  adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=EMA_TREND, adjust=False).mean()

        # Supertrend
        atr_s = df["atr"].rolling(10).mean()
        hl2   = (df["high"] + df["low"]) / 2
        u = hl2 + 3 * atr_s; l = hl2 - 3 * atr_s
        st = [l.iloc[0] if not pd.isna(l.iloc[0]) else 0]
        for i in range(1, len(df)):
            c  = df["close"].iloc[i]
            li = l.iloc[i] if not pd.isna(l.iloc[i]) else st[-1]
            ui = u.iloc[i] if not pd.isna(u.iloc[i]) else st[-1]
            st.append(max(li, st[-1]) if c > st[-1] else min(ui, st[-1]))
        df["stb"] = df["close"] > pd.Series(st, index=df.index)

        # SR levels
        df["hh20"] = df["high"].rolling(SR_PERIOD).max().shift(1)
        df["ll20"]  = df["low"].rolling(SR_PERIOD).min().shift(1)

        return df

    # ── BB SQUEEZE ────────────────────────────────────────────────
    def check_bb_squeeze(self, symbol: str,
                         df: pd.DataFrame) -> Optional[IntradaySignal]:
        if len(df) < 50: return None
        df = self.prepare(df)
        row  = df.iloc[-2]   # last completed candle
        prev = df.iloc[-3]

        if not prev["bsq"] or prev["sqd"] < BB_MIN_SQUEEZE_BARS:
            return None

        lb = row["close"] > row["bu"]
        sb = row["close"] < row["bl"]
        if not (lb or sb): return None

        if lb and not row["mb"]:  return None
        if sb and row["mb"]:      return None

        trend = 1 if row["close"] > row["ema200"] else -1
        if lb and trend != 1:  return None
        if sb and trend != -1: return None

        v = row["vr"]
        if pd.isna(v) or v < BB_VOL_FILTER: return None

        if lb and not row["stb"]: return None
        if sb and row["stb"]:     return None

        # Market hours only
        hr = pd.to_datetime(row["datetime"]).hour
        if hr < 9 or hr >= 15: return None

        atr = row["atr"]; e = row["close"]
        if pd.isna(atr) or atr <= 0: return None

        d   = "long" if lb else "short"
        sl  = e - atr if d == "long" else e + atr
        tp  = e + abs(e - sl) * RR_BB if d == "long" else e - abs(sl - e) * RR_BB

        score = 0.5
        if v > 1.5: score += 0.1
        if v > 2.0: score += 0.1
        if prev["sqd"] > 4: score += 0.1
        score = round(min(0.95, score), 3)

        return IntradaySignal(
            symbol    = symbol,
            strategy  = "BB_SQUEEZE",
            direction = d,
            entry     = round(e, 2),
            sl        = round(sl, 2),
            tp        = round(tp, 2),
            atr       = round(atr, 2),
            vol_ratio = round(v, 2),
            confidence= score,
            reason    = (f"BB Squeeze ({int(prev['sqd'])}b) breakout | "
                         f"Vol {v:.1f}x | {'Bull' if lb else 'Bear'} | "
                         f"RSI={row['rsi']:.0f}"),
            timestamp = str(row["datetime"]),
        )

    # ── SR BREAKOUT ───────────────────────────────────────────────
    def check_sr_breakout(self, symbol: str,
                          df: pd.DataFrame) -> Optional[IntradaySignal]:
        if len(df) < 30: return None
        df = self.prepare(df)
        row = df.iloc[-2]

        if pd.isna(row["hh20"]) or pd.isna(row["atr"]): return None

        v = row["vr"]
        if pd.isna(v) or v < SR_VOL_FILTER: return None

        atr = row["atr"]; e = row["close"]
        if pd.isna(atr) or atr <= 0: return None

        hr = pd.to_datetime(row["datetime"]).hour
        if hr < 9 or hr >= 15: return None

        if row["close"] > row["hh20"] and row["close"] > row["ema200"]:
            d  = "long"
            sl = e - atr
            tp = e + abs(e - sl) * RR_SR
        elif row["close"] < row["ll20"] and row["close"] < row["ema200"]:
            d  = "short"
            sl = e + atr
            tp = e - abs(sl - e) * RR_SR
        else:
            return None

        return IntradaySignal(
            symbol    = symbol,
            strategy  = "SR_BREAKOUT",
            direction = d,
            entry     = round(e, 2),
            sl        = round(sl, 2),
            tp        = round(tp, 2),
            atr       = round(atr, 2),
            vol_ratio = round(v, 2),
            confidence= 0.6 if v > 2.0 else 0.5,
            reason    = (f"{'20d High' if d=='long' else '20d Low'} breakout | "
                         f"Vol {v:.1f}x | Trend {'Up' if d=='long' else 'Down'}"),
            timestamp = str(row["datetime"]),
        )

    # ── EMA CROSS ─────────────────────────────────────────────────
    def check_ema_cross(self, symbol: str,
                        df: pd.DataFrame) -> Optional[IntradaySignal]:
        if len(df) < 60: return None
        df = self.prepare(df)
        row  = df.iloc[-2]
        prev = df.iloc[-3]

        if pd.isna(row["ema55"]) or pd.isna(row["atr"]): return None

        v = row["vr"]
        if pd.isna(v) or v < EMA_VOL_FILTER: return None

        hr = pd.to_datetime(row["datetime"]).hour
        if hr < 9 or hr >= 15: return None

        atr = row["atr"]; e = row["close"]
        if pd.isna(atr) or atr <= 0: return None

        if (prev["ema21"] <= prev["ema55"] and row["ema21"] > row["ema55"]
                and row["close"] > row["ema200"]):
            d  = "long"
            sl = e - atr
            tp = e + abs(e - sl) * RR_EMA
        elif (prev["ema21"] >= prev["ema55"] and row["ema21"] < row["ema55"]
                and row["close"] < row["ema200"]):
            d  = "short"
            sl = e + atr
            tp = e - abs(sl - e) * RR_EMA
        else:
            return None

        return IntradaySignal(
            symbol    = symbol,
            strategy  = "EMA_CROSS",
            direction = d,
            entry     = round(e, 2),
            sl        = round(sl, 2),
            tp        = round(tp, 2),
            atr       = round(atr, 2),
            vol_ratio = round(v, 2),
            confidence= 0.55,
            reason    = (f"EMA21 {'Golden' if d=='long' else 'Death'} Cross | "
                         f"Vol {v:.1f}x | Trend {'Up' if d=='long' else 'Down'}"),
            timestamp = str(row["datetime"]),
        )

    def check_signal(self, symbol: str,
                     strategy: str,
                     df: pd.DataFrame) -> Optional[IntradaySignal]:
        """Route to correct strategy."""
        if strategy == "BB_SQUEEZE":
            return self.check_bb_squeeze(symbol, df)
        elif strategy == "SR_BREAKOUT":
            return self.check_sr_breakout(symbol, df)
        elif strategy == "EMA_CROSS":
            return self.check_ema_cross(symbol, df)
        return None

    def get_state(self, symbol: str, df: pd.DataFrame) -> dict:
        """Current market state for dashboard."""
        if len(df) < 30: return {}
        df = self.prepare(df)
        row = df.iloc[-1]
        return {
            "symbol":    symbol,
            "price":     round(float(row["close"]), 2),
            "rsi":       round(float(row.get("rsi", 50)), 1),
            "bb_squeeze":bool(row["bsq"]),
            "squeeze_dur":int(row["sqd"]),
            "vol_ratio": round(float(row.get("vr", 1)), 2),
            "ema200_bull": bool(row["close"] > row["ema200"]),
            "supertrend_bull": bool(row["stb"]),
            "atr":       round(float(row["atr"]), 2),
            "timestamp": str(row["datetime"]),
        }
