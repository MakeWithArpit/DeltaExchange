"""
Position Sizer — Crypto + India
Crypto: leveraged futures (Delta Exchange) with GST fees
India:  intraday MIS shares (Dhann NSE) + pairs dollar-neutral sizing
"""
import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    MAKER_FEE_PCT, TAKER_FEE_PCT, GST_PCT,
    LEVERAGE, CRYPTO_RISK_PCT, RR_RATIO, PRODUCTS,
    INDIA_RISK_PCT, INTRADAY_ROUND_TRIP,
)


# ── CRYPTO FEE CALCULATOR ────────────────────────────────────────

class FeeCalculator:
    """Calculate Delta Exchange fees + GST for one round trip."""

    @staticmethod
    def fee_per_side(notional_value: float, is_maker: bool = False) -> dict:
        fee_rate = MAKER_FEE_PCT / 100 if is_maker else TAKER_FEE_PCT / 100
        base_fee = notional_value * fee_rate
        gst      = base_fee * (GST_PCT / 100)
        total    = base_fee + gst
        return {
            "notional":  round(notional_value, 4),
            "fee_rate":  fee_rate * 100,
            "base_fee":  round(base_fee, 6),
            "gst_18pct": round(gst, 6),
            "total":     round(total, 6),
        }

    @staticmethod
    def round_trip_fees(notional_value: float, is_maker: bool = False) -> dict:
        entry = FeeCalculator.fee_per_side(notional_value, is_maker)
        exit_ = FeeCalculator.fee_per_side(notional_value, is_maker)
        total_fees = round(entry["total"] + exit_["total"], 6)
        return {
            "entry_fee":      entry["total"],
            "exit_fee":       exit_["total"],
            "total_fees":     total_fees,
            "fee_pct_of_cap": round(total_fees / max(notional_value, 0.0001) * 100, 4),
        }

    @staticmethod
    def net_rr_after_fees(entry: float, sl: float, tp: float,
                           size: float, is_maker: bool = False) -> dict:
        notional   = entry * size
        fees       = FeeCalculator.round_trip_fees(notional, is_maker)
        risk_raw   = abs(entry - sl)  * size
        reward_raw = abs(tp - entry)  * size
        net_loss   = -(risk_raw  + fees["total_fees"])
        net_profit =   reward_raw - fees["total_fees"]
        net_rr     = net_profit / max(abs(net_loss), 0.0001)
        return {
            "risk_usdt":   round(risk_raw, 4),
            "reward_usdt": round(reward_raw, 4),
            "fees_usdt":   round(fees["total_fees"], 4),
            "net_loss":    round(net_loss, 4),
            "net_profit":  round(net_profit, 4),
            "stated_rr":   round(reward_raw / max(risk_raw, 0.0001), 3),
            "net_rr":      round(net_rr, 3),
        }


# ── POSITION SIZER ────────────────────────────────────────────────

class PositionSizer:

    # ── CRYPTO ──────────────────────────────────────────────────
    @staticmethod
    def calculate(symbol: str, entry_price: float, sl_price: float,
                  capital: float,
                  risk_pct: float = CRYPTO_RISK_PCT,
                  leverage: int = LEVERAGE) -> dict:
        """
        Crypto futures position size for Delta Exchange.
        Returns lots, contracts, margin_req, risk_usdt, etc.
        """
        product   = PRODUCTS.get(symbol, {})
        lot_size  = product.get("lot_size", 0.001)
        min_lots  = product.get("min_lots", 1)
        tick_size = product.get("tick_size", 0.5)

        risk_usdt   = capital * (risk_pct / 100)
        sl_distance = abs(entry_price - sl_price)
        if sl_distance < tick_size:
            sl_distance = tick_size

        contracts_raw = risk_usdt / sl_distance
        lots          = max(min_lots, math.floor(contracts_raw / lot_size))
        contracts     = lots * lot_size
        notional      = contracts * entry_price
        margin_req    = notional / leverage

        if margin_req > capital * 0.95:
            return {
                "error":      f"Insufficient margin: need ${margin_req:.2f}, have ${capital:.2f}",
                "margin_req": round(margin_req, 2),
                "capital":    round(capital, 2),
            }

        actual_risk = contracts * sl_distance

        if entry_price > sl_price:  # long
            tp_price = entry_price + sl_distance * RR_RATIO
        else:                        # short
            tp_price = entry_price - sl_distance * RR_RATIO

        fees = FeeCalculator.round_trip_fees(notional)
        rr   = FeeCalculator.net_rr_after_fees(entry_price, sl_price, tp_price, contracts)

        liq_buffer = notional / leverage * 0.8
        if entry_price > sl_price:
            liq_price = entry_price - (liq_buffer / contracts)
        else:
            liq_price = entry_price + (liq_buffer / contracts)

        return {
            "symbol":        symbol,
            "entry_price":   round(entry_price, 4),
            "sl_price":      round(sl_price,    4),
            "tp_price":      round(tp_price,    4),
            "lot_size":      lot_size,
            "lots":          lots,
            "contracts":     round(contracts,   6),
            "notional_usdt": round(notional,    2),
            "leverage":      leverage,
            "margin_req":    round(margin_req,  2),
            "risk_usdt":     round(actual_risk, 4),
            "risk_pct":      round(actual_risk / capital * 100, 3),
            "reward_usdt":   round(actual_risk * RR_RATIO, 4),
            "fees_usdt":     round(fees["total_fees"], 4),
            "net_profit":    round(rr["net_profit"], 4),
            "net_loss":      round(rr["net_loss"],   4),
            "net_rr":        rr["net_rr"],
            "liq_price":     round(liq_price, 4),
            "capital_used":  round(margin_req, 2),
            "capital_pct":   round(margin_req / capital * 100, 2),
        }

    # ── INDIA INTRADAY ───────────────────────────────────────────
    @staticmethod
    def calculate_india(entry: float, sl: float, capital: float,
                        risk_pct: float = INDIA_RISK_PCT) -> dict:
        """
        India intraday shares sizing.
        MIS gives ~5x leverage but we size purely by risk amount.
        """
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return {"error": "SL distance is zero"}

        risk_inr = capital * (risk_pct / 100)
        shares   = max(1, int(risk_inr / sl_dist))
        notional = shares * entry
        fees     = notional * INTRADAY_ROUND_TRIP

        # Cap at 95% of capital
        if notional > capital * 0.95:
            shares   = max(1, int(capital * 0.95 / entry))
            notional = shares * entry
            fees     = notional * INTRADAY_ROUND_TRIP

        actual_risk = shares * sl_dist
        return {
            "shares":       shares,
            "notional":     round(notional, 2),
            "margin_req":   round(notional / 5, 2),  # ~5x MIS
            "risk_inr":     round(actual_risk, 2),
            "risk_pct":     round(actual_risk / capital * 100, 3),
            "fees_inr":     round(fees, 2),
            "capital_used": round(notional / 5, 2),
            "capital_pct":  round(notional / 5 / capital * 100, 2),
        }

    # ── PAIRS ARBITRAGE ──────────────────────────────────────────
    @staticmethod
    def calculate_pairs(capital: float,
                        price1: float, price2: float) -> dict:
        """Dollar-neutral pairs sizing: split capital equally."""
        half      = capital / 2
        shares1   = max(1, int(half / price1))
        shares2   = max(1, int(half / price2))
        notional1 = shares1 * price1
        notional2 = shares2 * price2
        total     = notional1 + notional2
        fees      = total * INTRADAY_ROUND_TRIP
        return {
            "shares1":        shares1,
            "shares2":        shares2,
            "notional1":      round(notional1, 2),
            "notional2":      round(notional2, 2),
            "total_notional": round(total, 2),
            "fees_inr":       round(fees, 2),
            "capital_used":   round(total / 5, 2),
        }

    @staticmethod
    def print_trade_card(calc: dict):
        direction = "LONG  ↑" if calc["entry_price"] > calc["sl_price"] else "SHORT ↓"
        print(f"""
╔══════════════════════════════════════════════════════════╗
║  TRADE CARD — {calc['symbol']:<10} {direction:<10}                ║
╠══════════════════════════════════════════════════════════╣
║  Entry       : ${calc['entry_price']:>12,.4f}                      ║
║  Stop Loss   : ${calc['sl_price']:>12,.4f}                      ║
║  Take Profit : ${calc['tp_price']:>12,.4f}                      ║
║  Liquidation : ${calc['liq_price']:>12,.4f}  ⚠️                 ║
╠══════════════════════════════════════════════════════════╣
║  Lots        : {calc['lots']:>12}                            ║
║  Contracts   : {calc['contracts']:>12.4f}                        ║
║  Notional    : ${calc['notional_usdt']:>12,.2f}  ({calc['leverage']}x leverage)    ║
║  Margin Req  : ${calc['margin_req']:>12,.2f}  ({calc['capital_pct']:.1f}% of capital)  ║
╠══════════════════════════════════════════════════════════╣
║  Risk        : ${calc['risk_usdt']:>12,.4f}  ({calc['risk_pct']:.2f}% of capital)   ║
║  Reward gross: ${calc['reward_usdt']:>12,.4f}                        ║
║  Fees (in+out): ${calc['fees_usdt']:>11,.4f}                        ║
║  Net Profit  : ${calc['net_profit']:>12,.4f}                        ║
║  Net Loss    : ${calc['net_loss']:>12,.4f}                        ║
║  Net R:R     : {calc['net_rr']:>12.3f}x                          ║
╚══════════════════════════════════════════════════════════╝""")
