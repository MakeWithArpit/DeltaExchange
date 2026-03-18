"""
Position Sizer — Indian Market
Fees: STT + Brokerage + Exchange + GST (intraday MIS)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import RISK_PER_TRADE_PCT, INTRADAY_ROUND_TRIP


class PositionSizer:

    @staticmethod
    def calculate(entry: float, sl: float, capital: float,
                  risk_pct: float = RISK_PER_TRADE_PCT) -> dict:
        """
        Calculate shares, notional, fees for intraday trade.
        No leverage factor needed — MIS gives 5x but we size by risk.
        """
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return {"error": "SL distance is zero"}

        risk_inr = capital * (risk_pct / 100)
        shares   = max(1, int(risk_inr / sl_dist))
        notional = shares * entry
        fees     = notional * INTRADAY_ROUND_TRIP

        # Cap: notional shouldn't exceed 95% of capital
        if notional > capital * 0.95:
            shares   = max(1, int(capital * 0.95 / entry))
            notional = shares * entry
            fees     = notional * INTRADAY_ROUND_TRIP

        actual_risk = shares * sl_dist

        return {
            "shares":       shares,
            "notional":     round(notional, 2),
            "margin_req":   round(notional / 5, 2),   # ~5x MIS leverage
            "risk_inr":     round(actual_risk, 2),
            "risk_pct":     round(actual_risk / capital * 100, 3),
            "fees_inr":     round(fees, 2),
            "capital_used": round(notional / 5, 2),
            "capital_pct":  round(notional / 5 / capital * 100, 2),
        }

    @staticmethod
    def calculate_pairs(capital: float,
                        price1: float, price2: float) -> dict:
        """
        Position size for pairs trade.
        Split capital equally, dollar-neutral.
        """
        half = capital / 2
        shares1 = max(1, int(half / price1))
        shares2 = max(1, int(half / price2))
        notional1 = shares1 * price1
        notional2 = shares2 * price2
        total_notional = notional1 + notional2
        fees = total_notional * INTRADAY_ROUND_TRIP

        return {
            "shares1":      shares1,
            "shares2":      shares2,
            "notional1":    round(notional1, 2),
            "notional2":    round(notional2, 2),
            "total_notional": round(total_notional, 2),
            "fees_inr":     round(fees, 2),
            "capital_used": round(total_notional / 5, 2),
        }
