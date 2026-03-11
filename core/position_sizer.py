"""
Fee Calculator + Position Sizer
Delta Exchange India — Fees, GST, Leverage, Lot Size
"""
import math
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.settings import (
    MAKER_FEE_PCT, TAKER_FEE_PCT, GST_PCT,
    LEVERAGE, RISK_PER_TRADE_PCT, RR_RATIO,
    PRODUCTS, CAPITAL_USDT
)


class FeeCalculator:
    """Calculate all costs: trading fees + GST"""

    @staticmethod
    def fee_per_side(notional_value: float, is_maker: bool = False) -> dict:
        """
        notional_value = entry_price × size (USDT value of position)
        Returns breakdown of fees for one side (entry OR exit)
        """
        fee_rate   = MAKER_FEE_PCT / 100 if is_maker else TAKER_FEE_PCT / 100
        base_fee   = notional_value * fee_rate
        gst        = base_fee * (GST_PCT / 100)
        total      = base_fee + gst
        return {
            "notional":   round(notional_value, 4),
            "fee_rate":   fee_rate * 100,
            "base_fee":   round(base_fee, 6),
            "gst_18pct":  round(gst, 6),
            "total":      round(total, 6),
        }

    @staticmethod
    def round_trip_fees(notional_value: float, is_maker: bool = False) -> dict:
        """Full round trip: entry + exit fees"""
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
        """
        Calculate ACTUAL RR after fees are deducted
        Returns real profit/loss in USDT after all costs
        """
        notional  = entry * size
        fees      = FeeCalculator.round_trip_fees(notional, is_maker)

        risk_raw  = abs(entry - sl)  * size
        reward_raw= abs(tp - entry)  * size
        net_loss  = -(risk_raw  + fees["total_fees"])
        net_profit=   reward_raw - fees["total_fees"]
        net_rr    = net_profit / max(abs(net_loss), 0.0001)

        return {
            "risk_usdt":   round(risk_raw, 4),
            "reward_usdt": round(reward_raw, 4),
            "fees_usdt":   round(fees["total_fees"], 4),
            "net_loss":    round(net_loss, 4),
            "net_profit":  round(net_profit, 4),
            "stated_rr":   round(reward_raw / max(risk_raw, 0.0001), 3),
            "net_rr":      round(net_rr, 3),
        }


class PositionSizer:
    """Calculate lot size based on risk management rules"""

    @staticmethod
    def calculate(symbol: str, entry_price: float, sl_price: float,
                  capital: float = CAPITAL_USDT,
                  risk_pct: float = RISK_PER_TRADE_PCT,
                  leverage: int = LEVERAGE) -> dict:
        """
        Calculate position size, leverage, margin required
        
        Returns:
            lots        - number of lots to buy
            contracts   - total contract size
            notional    - USDT value of position
            margin_req  - USDT margin required (notional / leverage)
            risk_usdt   - actual $ at risk
            risk_pct    - % of capital at risk
        """
        product   = PRODUCTS.get(symbol, {})
        lot_size  = product.get("lot_size", 0.001)
        min_lots  = product.get("min_lots", 1)
        tick_size = product.get("tick_size", 0.5)

        # Risk per trade in USDT
        risk_usdt     = capital * (risk_pct / 100)

        # Price distance to SL
        sl_distance   = abs(entry_price - sl_price)
        if sl_distance < tick_size:
            sl_distance = tick_size  # minimum

        # Contract size = risk_usdt / sl_distance
        contracts_raw = risk_usdt / sl_distance

        # Round to lot size
        lots          = max(min_lots, math.floor(contracts_raw / lot_size))
        contracts     = lots * lot_size

        # Position value
        notional      = contracts * entry_price
        margin_req    = notional / leverage

        # Actual risk (after rounding to lots)
        actual_risk   = contracts * sl_distance

        # TP price based on RR
        if entry_price > sl_price:  # long
            tp_price = entry_price + sl_distance * RR_RATIO
        else:                        # short
            tp_price = entry_price - sl_distance * RR_RATIO

        # Fees
        fees = FeeCalculator.round_trip_fees(notional)
        rr   = FeeCalculator.net_rr_after_fees(entry_price, sl_price, tp_price,
                                                 contracts)
        # Liquidation estimate
        liq_buffer = notional / leverage * 0.8  # 80% of margin
        if entry_price > sl_price:  # long
            liq_price = entry_price - (liq_buffer / contracts)
        else:
            liq_price = entry_price + (liq_buffer / contracts)

        return {
            "symbol":        symbol,
            "entry_price":   round(entry_price,  4),
            "sl_price":      round(sl_price,     4),
            "tp_price":      round(tp_price,     4),
            "lot_size":      lot_size,
            "lots":          lots,
            "contracts":     round(contracts,    6),
            "notional_usdt": round(notional,     2),
            "leverage":      leverage,
            "margin_req":    round(margin_req,   2),
            "risk_usdt":     round(actual_risk,  4),
            "risk_pct":      round(actual_risk / capital * 100, 3),
            "reward_usdt":   round(actual_risk * RR_RATIO, 4),
            "fees_usdt":     round(fees["total_fees"], 4),
            "net_profit":    round(rr["net_profit"], 4),
            "net_loss":      round(rr["net_loss"],   4),
            "net_rr":        rr["net_rr"],
            "liq_price":     round(liq_price,    4),
            "breakeven_fee": round(entry_price + fees["total_fees"]/contracts
                                   if entry_price > sl_price
                                   else entry_price - fees["total_fees"]/contracts, 4),
            "capital_used":  round(margin_req, 2),
            "capital_pct":   round(margin_req / capital * 100, 2),
        }

    @staticmethod
    def print_trade_card(calc: dict):
        """Pretty print trade details"""
        direction = "LONG  ↑" if calc["entry_price"] > calc["sl_price"] else "SHORT ↓"
        print(f"""
╔══════════════════════════════════════════════════════════╗
║  TRADE CARD — {calc['symbol']:<10} {direction:<10}                ║
╠══════════════════════════════════════════════════════════╣
║  Entry          : ${calc['entry_price']:>12,.4f}                      ║
║  Stop Loss      : ${calc['sl_price']:>12,.4f}                      ║
║  Take Profit    : ${calc['tp_price']:>12,.4f}                      ║
║  Liquidation    : ${calc['liq_price']:>12,.4f}  ⚠️                 ║
╠══════════════════════════════════════════════════════════╣
║  Lots           : {calc['lots']:>12}                            ║
║  Contracts      : {calc['contracts']:>12.4f}                        ║
║  Notional       : ${calc['notional_usdt']:>12,.2f}  ({calc['leverage']}x leverage)    ║
║  Margin Req     : ${calc['margin_req']:>12,.2f}  ({calc['capital_pct']:.1f}% of capital)  ║
╠══════════════════════════════════════════════════════════╣
║  Risk           : ${calc['risk_usdt']:>12,.4f}  ({calc['risk_pct']:.2f}% of capital)   ║
║  Reward (gross) : ${calc['reward_usdt']:>12,.4f}                        ║
║  Fees (in+out)  : ${calc['fees_usdt']:>12,.4f}                        ║
║  Net Profit     : ${calc['net_profit']:>12,.4f}                        ║
║  Net Loss       : ${calc['net_loss']:>12,.4f}                        ║
║  Net R:R        : {calc['net_rr']:>12.3f}x                          ║
╚══════════════════════════════════════════════════════════╝""")


# ── QUICK TEST ───────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== Position Sizer Test ===\n")

    # BTC Long example
    calc = PositionSizer.calculate(
        symbol="BTCUSDT",
        entry_price=85000,
        sl_price=84150,   # ~1% SL
        capital=800,
        risk_pct=1.0,
        leverage=5
    )
    PositionSizer.print_trade_card(calc)

    print("\n=== Fee Breakdown ===")
    fees = FeeCalculator.round_trip_fees(notional_value=calc["notional_usdt"])
    print(f"  Notional value  : ${calc['notional_usdt']:,.2f}")
    print(f"  Entry fee       : ${fees['entry_fee']:.4f}")
    print(f"  Exit fee        : ${fees['exit_fee']:.4f}")
    print(f"  Total fees      : ${fees['total_fees']:.4f}")
    print(f"  Fee % of trade  : {fees['fee_pct_of_cap']:.4f}%")