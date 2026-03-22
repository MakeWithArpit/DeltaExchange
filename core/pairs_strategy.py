"""
Pairs Arbitrage (Statistical Arbitrage) Strategy
Method: zscore of log spread → mean reversion
Best pairs from backtest:
  BAJFINANCE/KOTAKBANK → WR 64%, +1.71%/mo
  ICICIBANK/SBIN       → WR 65%, +1.14%/mo
  RELIANCE/WIPRO       → WR 59%, +1.08%/mo
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional, Tuple
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import *


@dataclass
class PairsSignal:
    pair_name:  str
    stock1:     str
    stock2:     str
    action:     str      # "ENTER_SHORT_S1" or "ENTER_LONG_S1" or "EXIT"
    zscore:     float
    spread:     float
    spread_mean:float
    spread_std: float
    reason:     str
    timestamp:  str


class PairsEngine:

    def __init__(self):
        self.open_pairs = {}   # pair_name → trade info

    def compute_spread(self, df1: pd.DataFrame,
                       df2: pd.DataFrame) -> pd.DataFrame:
        """
        Align two price series and compute log spread + zscore.
        df1, df2: must have columns [datetime, close]
        """
        d1 = df1[["datetime", "close"]].rename(columns={"close": "s1"})
        d2 = df2[["datetime", "close"]].rename(columns={"close": "s2"})

        # Round to nearest hour for alignment
        d1["datetime"] = pd.to_datetime(d1["datetime"]).dt.floor("1h")
        d2["datetime"] = pd.to_datetime(d2["datetime"]).dt.floor("1h")

        merged = pd.merge(d1, d2, on="datetime", how="inner").dropna()
        if len(merged) < PAIRS_LOOKBACK + 5:
            return pd.DataFrame()

        merged = merged.sort_values("datetime").reset_index(drop=True)
        merged["spread"]     = np.log(merged["s1"] / merged["s2"])
        merged["spread_ma"]  = merged["spread"].rolling(PAIRS_LOOKBACK).mean()
        merged["spread_std"] = merged["spread"].rolling(PAIRS_LOOKBACK).std()
        merged["zscore"]     = ((merged["spread"] - merged["spread_ma"]) /
                                merged["spread_std"].replace(0, np.nan))

        return merged.dropna()

    def check_signal(self, pair_name: str,
                     stock1: str, stock2: str,
                     df1: pd.DataFrame,
                     df2: pd.DataFrame) -> Optional[PairsSignal]:
        """
        Check for pairs entry/exit signal.

        ENTER_SHORT_S1: zscore > +2.0  (s1 too expensive vs s2)
          → Sell s1, Buy s2, wait for mean reversion

        ENTER_LONG_S1: zscore < -2.0   (s1 too cheap vs s2)
          → Buy s1, Sell s2, wait for mean reversion
        """
        df = self.compute_spread(df1, df2)
        if df.empty: return None

        # Market hours only
        latest = df.iloc[-1]
        hr = pd.to_datetime(latest["datetime"]).hour
        if hr < 9 or hr >= 15: return None

        z       = float(latest["zscore"])
        spread  = float(latest["spread"])
        sp_mean = float(latest["spread_ma"])
        sp_std  = float(latest["spread_std"])
        ts      = str(latest["datetime"])

        in_trade = pair_name in self.open_pairs

        # ── EXIT CHECK ────────────────────────────────────────────
        if in_trade:
            trade    = self.open_pairs[pair_name]
            hold     = trade.get("bars_held", 0) + 1
            self.open_pairs[pair_name]["bars_held"] = hold

            should_exit = (abs(z) < PAIRS_EXIT_ZSCORE or
                           hold >= PAIRS_MAX_HOLD_BARS)
            if should_exit:
                return PairsSignal(
                    pair_name  = pair_name,
                    stock1     = stock1,
                    stock2     = stock2,
                    action     = "EXIT",
                    zscore     = round(z, 3),
                    spread     = round(spread, 4),
                    spread_mean= round(sp_mean, 4),
                    spread_std = round(sp_std, 4),
                    reason     = (f"Exit: zscore={z:.2f} "
                                  f"({'mean-reverted' if abs(z)<PAIRS_EXIT_ZSCORE else 'max hold reached'}) "
                                  f"| held {hold} bars"),
                    timestamp  = ts,
                )
            return None   # Still holding, no new signal

        # ── ENTRY CHECK ───────────────────────────────────────────
        if z > PAIRS_ENTRY_ZSCORE:
            # s1 overpriced → SHORT s1, LONG s2
            return PairsSignal(
                pair_name  = pair_name,
                stock1     = stock1,
                stock2     = stock2,
                action     = "ENTER_SHORT_S1",
                zscore     = round(z, 3),
                spread     = round(spread, 4),
                spread_mean= round(sp_mean, 4),
                spread_std = round(sp_std, 4),
                reason     = (f"Zscore={z:.2f} > {PAIRS_ENTRY_ZSCORE} | "
                              f"{stock1} overpriced vs {stock2} | "
                              f"Short {stock1}, Long {stock2}"),
                timestamp  = ts,
            )
        elif z < -PAIRS_ENTRY_ZSCORE:
            # s1 underpriced → LONG s1, SHORT s2
            return PairsSignal(
                pair_name  = pair_name,
                stock1     = stock1,
                stock2     = stock2,
                action     = "ENTER_LONG_S1",
                zscore     = round(z, 3),
                spread     = round(spread, 4),
                spread_mean= round(sp_mean, 4),
                spread_std = round(sp_std, 4),
                reason     = (f"Zscore={z:.2f} < -{PAIRS_ENTRY_ZSCORE} | "
                              f"{stock1} underpriced vs {stock2} | "
                              f"Long {stock1}, Short {stock2}"),
                timestamp  = ts,
            )

        return None

    def register_entry(self, pair_name: str, signal: PairsSignal,
                       entry_spread: float):
        """Mark pair as open after entry."""
        self.open_pairs[pair_name] = {
            "action":       signal.action,
            "entry_spread": entry_spread,
            "entry_zscore": signal.zscore,
            "bars_held":    0,
            "timestamp":    signal.timestamp,
        }

    def register_exit(self, pair_name: str):
        """Mark pair as closed after exit."""
        self.open_pairs.pop(pair_name, None)

    def get_state(self, pair_name: str,
                  df1: pd.DataFrame,
                  df2: pd.DataFrame) -> dict:
        """Current zscore for dashboard."""
        df = self.compute_spread(df1, df2)
        if df.empty:
            return {"pair": pair_name, "zscore": 0}
        row = df.iloc[-1]
        return {
            "pair":       pair_name,
            "zscore":     round(float(row["zscore"]), 3),
            "spread":     round(float(row["spread"]), 4),
            "spread_ma":  round(float(row["spread_ma"]), 4),
            "in_trade":   pair_name in self.open_pairs,
            "timestamp":  str(row["datetime"]),
        }
