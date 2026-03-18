"""
╔══════════════════════════════════════════════════════════════════╗
║         MAIN TRADING BOT — v2.0 (4H + Trailing SL)              ║
║         Delta Exchange India | BTC + ETH                         ║
║                                                                   ║
║  Upgrades:                                                        ║
║    - Primary TF: 4H (42% WR vs 34% on 30m)                       ║
║    - Partial TP at 1R (50% position secured)                      ║
║    - Trailing SL after 1R profit                                  ║
║    - SOL disabled (low WR from backtest)                          ║
║    - CHECK_INTERVAL: 5min (was 60s — 4H doesnt need 60s)         ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import time, logging, os, sys, math
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config.settings import *
from core.delta_client   import DeltaClient
from core.strategy       import StrategyEngine, Signal
from core.gann_strategy  import GannStrategyEngine, GannSignal
from core.position_sizer import PositionSizer
from ml.signal_filter    import MLSignalFilter
from data.database       import Database

# ── LOGGING ─────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


class TradingBot:

    def __init__(self):
        self.client      = DeltaClient()
        self.engine      = StrategyEngine()
        self.gann_engine = GannStrategyEngine()
        self.ml          = MLSignalFilter()
        self.db          = Database(DB_PATH)
        self.capital     = CAPITAL_USDT
        self.wallet      = {}
        self.running     = False
        # Monthly trailing
        self.monthly_peak_pct   = 0.0
        self.monthly_protected  = False
        self.month_trading_band = False
        self._current_month     = None
        self._load_or_train_ml()

    # ── ML ───────────────────────────────────────────────────────
    def _load_or_train_ml(self):
        # Delete stale pkl if sklearn version mismatch detected
        import pickle, sklearn
        pkl_path = "data/ml_model.pkl"
        if os.path.exists(pkl_path):
            try:
                with open(pkl_path, 'rb') as f:
                    meta = pickle.load(f)
                trained_ver = getattr(meta, '_sklearn_version', None)
                current_ver = sklearn.__version__
                if trained_ver and trained_ver != current_ver:
                    logger.warning(f"sklearn version mismatch ({trained_ver} vs {current_ver}) — retraining ML")
                    os.remove(pkl_path)
            except Exception:
                pass  # Let load() handle it

        if self.ml.load():
            logger.info("ML model loaded.")
            return
        logger.info("Training ML model from historical CSVs...")
        datasets = {}
        for fname, name in [
            ("Historical CSVs/bitcoin_30min.csv", "BTC"),
            ("Historical CSVs/eth_30min.csv",     "ETH"),
            ("Historical CSVs/sol_30min.csv",     "SOL"),
        ]:
            if os.path.exists(fname):
                df = pd.read_csv(fname)
                df.columns = df.columns.str.strip().str.lower()
                df["time"] = pd.to_datetime(df["time"])
                datasets[name] = df
        if datasets:
            self.ml.train(datasets)
        else:
            logger.warning("No CSVs found — ML disabled.")

    # ── WALLET ──────────────────────────────────────────────────
    def fetch_wallet(self) -> dict:
        raw = self.client.get_balance()
        if not raw:
            logger.warning("Wallet fetch failed — API unreachable or bad credentials")
            logger.warning(f"Using local capital: ${self.capital:,.2f}")
            return {}

        self.wallet = raw
        usdt = (raw.get("USD")  or raw.get("USDT") or
                raw.get("usdt") or raw.get("INR")  or
                raw.get("inr")  or {})
        asset_key = next((k for k in ["USD","USDT","usdt","INR","inr"] if raw.get(k)), "?")

        live_avail = float(usdt.get("available_balance", usdt.get("available", 0)))
        live_total = float(usdt.get("balance", live_avail))

        if live_avail > 0:
            old = self.capital
            self.capital = live_avail
            logger.info(f"  Capital (live wallet): ${live_avail:,.2f} {asset_key} available")
            logger.info(f"  Capital (total     ): ${live_total:,.2f} {asset_key} total")
            if old > 0 and abs(old - live_avail) > 0.01:
                logger.info(f"  Change since last fetch: {live_avail - old:+.2f}")
        else:
            logger.warning(f"  Balance = 0 — wallet keys: {list(raw.keys())}")

        return raw

    def print_wallet(self):
        if not self.wallet:
            print("\n  [Wallet not fetched — check API keys]\n")
            return
        print("\n" + "─"*60)
        print(f"  WALLET  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("─"*60)
        for asset, info in self.wallet.items():
            total  = float(info.get("balance",           info.get("total", 0)))
            avail  = float(info.get("available_balance", info.get("available", 0)))
            locked = total - avail
            if total < 0.000001:
                continue
            used_pct = locked / total * 100 if total > 0 else 0
            bar = ("█" * int(used_pct/5)) + ("░" * (20 - int(used_pct/5)))
            print(f"  {asset:<8} total : {total:>16,.6f}")
            print(f"           free  : {avail:>16,.6f}  [{bar}] {used_pct:.1f}% in orders")
        print("─"*60)
        print(f"  Active capital (free): ${self.capital:,.2f}")
        print("─"*60 + "\n")

    def get_positions_summary(self) -> list:
        raw = self.client.get_positions()
        out = []
        for p in (raw or []):
            size = float(p.get("size", 0))
            if size == 0:
                continue
            out.append({
                "symbol":    p.get("product", {}).get("symbol", "?"),
                "direction": "long" if size > 0 else "short",
                "size":      abs(size),
                "entry":     float(p.get("entry_price", 0)),
                "mark":      float(p.get("mark_price",  0)),
                "upnl":      float(p.get("unrealized_pnl", 0)),
            })
        return out

    # ── CANDLES ─────────────────────────────────────────────────
    def fetch_candles(self, symbol: str, tf: str = None, limit: int = None) -> pd.DataFrame:
        """Fetch candles for given timeframe. Falls back to DB cache."""
        tf    = tf    or CANDLE_TF
        limit = limit or CANDLES_NEEDED

        raw = self.client.get_candles(symbol, resolution=tf, limit=limit)
        if raw:
            df = self.engine.candles_to_df(raw)
            if len(df) > 0:
                self.db.upsert_candles(symbol, df.to_dict("records"))
                return df

        cached = self.db.get_candles(symbol, limit=limit)
        if cached:
            return pd.DataFrame(cached)

        logger.error(f"No candle data for {symbol}")
        return pd.DataFrame()

    # ── CIRCUIT BREAKER ─────────────────────────────────────────
    def _check_circuit_breaker(self) -> tuple:
        this_month = datetime.now().strftime("%Y-%m")
        if self._current_month != this_month:
            self._current_month     = this_month
            self.monthly_peak_pct   = 0.0
            self.monthly_protected  = False
            self.month_trading_band = False
            logger.info(f"[TRAIL] New month {this_month} — trailing reset")

        if self.month_trading_band:
            mo = self.db.get_monthly_pnl_pct(self.capital)
            return True, (f"[TRAIL] Month locked at {mo['pnl_pct']:+.2f}% — "
                          f"profit protected.")

        daily_loss = self.db.get_daily_loss_pct(self.capital)
        if daily_loss >= MAX_DAILY_LOSS_PCT:
            return True, f"Daily loss {daily_loss:.1f}% >= limit {MAX_DAILY_LOSS_PCT}%"

        mo     = self.db.get_monthly_pnl_pct(self.capital)
        mo_pct = mo["pnl_pct"]

        if abs(min(0.0, mo_pct)) >= MONTHLY_HARD_STOP_PCT:
            self.month_trading_band = True
            return True, f"[STOP] Monthly loss {abs(min(0,mo_pct)):.1f}% >= hard stop"

        if mo_pct >= MONTHLY_TARGET_PCT:
            if not self.monthly_protected:
                self.monthly_protected = True
                self.monthly_peak_pct  = mo_pct
                logger.info(f"[TRAIL] Target {MONTHLY_TARGET_PCT}% HIT! Trailing on")

        if self.monthly_protected:
            if mo_pct > self.monthly_peak_pct:
                self.monthly_peak_pct = mo_pct
            floor = self.monthly_peak_pct - MONTHLY_TRAIL_PCT
            if mo_pct <= floor:
                self.month_trading_band = True
                return True, f"[TRAIL] Profit protected at {floor:+.2f}%"

        return False, ""

    # ── SIGNAL PIPELINE ─────────────────────────────────────────
    def process_symbol(self, symbol: str) -> dict:
        result = {"symbol": symbol, "action": "none", "signal": None}

        # Fetch 4H candles (primary)
        df = self.fetch_candles(symbol, tf=CANDLE_TF, limit=CANDLES_NEEDED)
        if df is None or len(df) < 50:
            result["action"] = "insufficient_data"
            return result

        bb_signal   = None
        gann_signal = None

        # BB Squeeze on 4H
        if STRATEGY_MODE in ("bb", "both", "confirm"):
            if len(df) >= 50:
                bb_signal = self.engine.check_signal(symbol, df)

        # Gann on 4H
        if STRATEGY_MODE in ("gann", "both", "confirm"):
            if GANN_REF_PRICES.get(symbol, 0) != 0 or symbol not in GANN_REF_PRICES:
                gann_signal = self.gann_engine.check_signal(symbol, df)

        if STRATEGY_MODE == "confirm":
            if (bb_signal and gann_signal and
                    bb_signal.direction == gann_signal.direction):
                sig = gann_signal
                sig.confidence = min(0.99, gann_signal.confidence + 0.15)
                sig.reason = f"[BB+GANN CONFIRM] {gann_signal.reason}"
            else:
                sig = None
        elif STRATEGY_MODE == "gann":
            sig = gann_signal
        elif STRATEGY_MODE == "bb":
            sig = bb_signal
        else:
            sig = gann_signal or bb_signal

        if sig is None:
            if STRATEGY_MODE in ("gann", "both"):
                state = self.gann_engine.get_current_state(symbol, df)
                state["strategy"] = "gann"
            else:
                state = self.engine.get_current_state(symbol, df)
                state["strategy"] = "bb_4h"
            result["action"] = "no_signal"
            result["state"]  = state
            return result

        # ML filter (BB signals only)
        if STRATEGY_MODE in ("bb", "confirm", "both") and hasattr(sig, 'squeeze_dur'):
            ml_ok, ml_conf, ml_reason = self.ml.predict(sig, df)
            if not ml_ok:
                result["action"] = "ml_filtered"
                result["reason"] = ml_reason
                return result

        # Position sizing
        prod    = PRODUCTS.get(symbol, {})
        prod_id = prod.get("product_id", 0)
        if not prod_id:
            result["action"] = "no_product"
            return result

        calc = PositionSizer.calculate(
            symbol      = symbol,
            entry_price = sig.entry,
            sl_price    = sig.sl,
            capital     = self.capital,
        )
        if calc.get("error"):
            result["action"] = "insufficient_margin"
            result["reason"] = calc["error"]
            return result

        open_trades = self.db.get_open_trades()
        if len(open_trades) >= MAX_OPEN_TRADES:
            result["action"] = "max_trades_reached"
            return result

        result.update({
            "action": "trade",
            "signal": sig,
            "calc":   calc,
        })
        return result

    def execute_trade(self, result: dict) -> bool:
        signal = result["signal"]
        calc   = result["calc"]

        # Add tp1 to calc for DB storage
        tp1 = getattr(signal, 'tp1', signal.tp)

        sig_meta = {
            "symbol":       signal.symbol,
            "direction":    signal.direction,
            "confidence":   getattr(signal, "confidence", 0.5),
            "reason":       getattr(signal, "reason", ""),
            "squeeze_dur":  getattr(signal, "squeeze_dur", 0),
            "breakout_str": getattr(signal, "breakout_str", 0),
            "vol_ratio":    getattr(signal, "vol_ratio", 1.0),
            "trend_4h":     getattr(signal, "trend_4h", "unknown"),
            "timestamp":    getattr(signal, "timestamp", ""),
        }
        try:
            sig_id = self.db.log_signal(sig_meta)
        except Exception:
            sig_id = 0

        product_id = PRODUCTS.get(signal.symbol, {}).get("product_id", 0)

        if PAPER_TRADE:
            trade_id = self.db.open_trade(sig_id, signal, calc,
                                          order_id="PAPER", is_paper=True,
                                          tp1_price=tp1,
                                          atr=getattr(signal, 'atr', 0))
            self._print_trade_alert(signal, calc, trade_id, paper=True)
            return True

        side = "buy" if signal.direction == "long" else "sell"
        order = self.client.place_order(
            product_id=product_id, side=side, size=calc["lots"],
            order_type="limit_order", limit_price=signal.entry,
        )
        if not order.get("success"):
            logger.error(f"Order failed: {order}")
            return False

        order_id = str(order.get("result", {}).get("id", ""))
        sl_side  = "sell" if signal.direction == "long" else "buy"
        self.client.place_stop_loss(
            product_id=product_id, side=sl_side, size=calc["lots"],
            stop_price=signal.sl)
        self.client.place_order(
            product_id=product_id, side=sl_side, size=calc["lots"],
            order_type="limit_order", limit_price=signal.tp, reduce_only=True)

        trade_id = self.db.open_trade(sig_id, signal, calc,
                                      order_id=order_id, is_paper=False,
                                      tp1_price=tp1,
                                      atr=getattr(signal, 'atr', 0))
        self._print_trade_alert(signal, calc, trade_id, paper=False)
        self.fetch_wallet()
        return True

    # ── MONITOR TRADES WITH TRAILING SL ─────────────────────────
    def monitor_trades(self):
        """
        Monitor open trades:
          1. Check SL hit
          2. Check TP hit
          3. Partial TP at 1R (50% close)
          4. Update trailing SL
        """
        for trade in self.db.get_open_trades():
            if not trade.get("is_paper"):
                continue

            df = self.fetch_candles(trade["symbol"])
            if df is None or df.empty:
                continue

            price     = float(df.iloc[-1]["close"])
            direction = trade["direction"]
            sl        = float(trade.get("trail_sl") or trade["sl_price"])
            tp        = float(trade["tp_price"])
            tp1       = float(trade.get("tp1_price") or tp)
            partial_done = bool(trade.get("partial_done", False))

            # ── Trailing SL update ───────────────────────────────
            trail_info = self.engine.update_trailing_sl(trade, price)
            new_sl     = trail_info['new_sl']

            if new_sl != sl:
                self.db.update_trade_trail(trade["id"], new_sl,
                                           trail_info['trail_active'])
                if trail_info['trail_active']:
                    logger.info(f"  [TRAIL] #{trade['id']} {trade['symbol']} "
                                f"SL moved: {sl:.2f} -> {new_sl:.2f} "
                                f"(price={price:.2f}, +{trail_info['profit_r']:.2f}R)")

            # ── Partial TP (50% at 1R) ───────────────────────────
            if trail_info['should_close_partial'] and not partial_done:
                partial_pnl = trade["reward_usdt"] * PARTIAL_TP_SIZE - trade["fees_usdt"] * PARTIAL_TP_SIZE
                self.capital += partial_pnl
                self.db.mark_partial_tp(trade["id"], tp1, partial_pnl)
                logger.info(f"  [TP1] #{trade['id']} {trade['symbol']} "
                            f"Partial close 50% @ {tp1:.2f} | +${partial_pnl:.2f} "
                            f"| SL moved to breakeven")
                continue   # Don't check full TP same candle

            # ── SL hit ──────────────────────────────────────────
            sl_hit = (price <= new_sl) if direction=="long" else (price >= new_sl)
            if sl_hit:
                # If partial was done, remaining risk is 50%
                risk_remaining = trade["risk_usdt"] * (0.5 if partial_done else 1.0)
                pnl = -risk_remaining
                self.db.close_trade(trade["id"], new_sl, "stop_loss", -1.0, pnl)
                self.capital += pnl
                status = "trail_SL" if trail_info['trail_active'] else "SL"
                logger.info(f"  [{status}] #{trade['id']} {trade['symbol']} "
                            f"@ {new_sl:.2f} | ${pnl:.2f}")
                continue

            # ── Full TP ──────────────────────────────────────────
            tp_hit = (price >= tp) if direction=="long" else (price <= tp)
            if tp_hit:
                reward_remaining = trade["reward_usdt"] * (0.5 if partial_done else 1.0)
                pnl = reward_remaining - trade["fees_usdt"] * (0.5 if partial_done else 1.0)
                self.db.close_trade(trade["id"], tp, "take_profit", RR_RATIO, pnl)
                self.capital += pnl
                logger.info(f"  [TP2] #{trade['id']} {trade['symbol']} "
                            f"Full TP @ {tp:.2f} | +${pnl:.2f}")

    # ── DISPLAY ─────────────────────────────────────────────────
    def _print_trade_alert(self, sig, calc, trade_id, paper):
        mode = "PAPER" if paper else "LIVE"
        icon = "LONG" if sig.direction == "long" else "SHORT"
        tp1  = getattr(sig, 'tp1', sig.tp)
        print(f"""
{'='*65}
[{mode}] TRADE #{trade_id}  {icon} {sig.symbol}
{'='*65}
  Signal   : {sig.reason[:75]}

  Entry    : ${sig.entry:>12,.4f}
  Stop Loss: ${sig.sl:>12,.4f}   risk   = ${calc['risk_usdt']:.2f}
  TP1 (1R) : ${tp1:>12,.4f}   partial = 50% close
  TP2 (full): ${sig.tp:>12,.4f}  reward = ${calc['reward_usdt']:.2f}
  Liq Price: ${calc['liq_price']:>12,.4f}   WARNING

  Lots     : {calc['lots']}  ({calc['contracts']:.4f} {sig.symbol[:3]})
  Notional : ${calc['notional_usdt']:>10,.2f}   ({calc['leverage']}x leverage)
  Margin   : ${calc['margin_req']:>10,.2f}   ({calc['capital_pct']:.1f}% of capital)
  Fees+GST : ${calc['fees_usdt']:>10,.4f}
  Net R:R  : {calc['net_rr']:.2f}x
  Capital  : ${self.capital:>10,.2f}  after this trade
{'='*65}""")

    def print_dashboard(self):
        stats    = self.db.get_trade_stats(days=30)
        open_t   = self.db.get_open_trades()
        paused, pause_reason = self._check_circuit_breaker()
        live_pos = self.get_positions_summary() if not PAPER_TRADE else []

        _t   = int(stats.get('total',    0) or 0)
        _w   = int(stats.get('wins',     0) or 0)
        _wr  = float(stats.get('wr',     0) or 0)
        _r   = float(stats.get('net_r',  0) or 0)
        _pnl = float(stats.get('pnl_usdt', 0) or 0)
        mo   = self.db.get_monthly_pnl_pct(self.capital)

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  BB SQUEEZE BOT v2.0  {'[PAUSED]' if paused else '[RUNNING]':^18}  {datetime.now().strftime('%H:%M:%S'):>8}  ║
╠══════════════════════════════════════════════════════════════╣
║  MODE: {'PAPER' if PAPER_TRADE else 'LIVE ':<7}  TF: {CANDLE_TF:<4}  ML: {'ON' if USE_ML_FILTER else 'OFF'}  Lev:{LEVERAGE}x  RR:{RR_RATIO}  ║
╠══════════════════════════════════════════════════════════════╣""")

        if self.wallet:
            for asset, info in self.wallet.items():
                avail = float(info.get("available_balance", info.get("available", 0)))
                total = float(info.get("balance", avail))
                if total < 0.000001: continue
                print(f"║  {asset:<6}: total={total:>14,.4f}  free={avail:>14,.4f}  ║")
        else:
            print("║  (no wallet data — add API keys to config/settings.py)      ║")

        print(f"""╠══════════════════════════════════════════════════════════════╣
║  Capital: ${self.capital:>10,.2f}  USDT                              ║
╠══════════════════════════════════════════════════════════════╣
║  PERFORMANCE (30 days)                                       ║
║  Trades:{_t:<5}  Wins:{_w:<5}  WR:{_wr:>5.1f}%  Net R:{_r:>+7.1f}R      ║
║  PnL: ${_pnl:>+8.2f}  | Month: {mo['pnl_pct']:>+6.2f}% (${mo['pnl_usdt']:>+7.2f})  ║
╠══════════════════════════════════════════════════════════════╣
║  OPEN TRADES ({len(open_t)} paper / {len(live_pos)} live)                     ║""")

        for t in open_t:
            trail = "[TRAIL]" if t.get('trail_active') else "      "
            partial = "[P1]" if t.get('partial_done') else "    "
            print(f"║  {'L' if t['direction']=='long' else 'S'} {t['symbol']:<8} "
                  f"@ ${t['entry_price']:>10,.2f} {trail}{partial}  ║")

        if paused:
            print(f"║  STOPPED: {pause_reason[:50]:<50} ║")
        print("╚══════════════════════════════════════════════════════════════╝")

    # ── MAIN LOOP ───────────────────────────────────────────────
    def run(self):
        logger.info("="*65)
        logger.info("  BB SQUEEZE BOT v2.0 — 4H Timeframe")
        logger.info(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'} | "
                    f"TF: {CANDLE_TF} | RR: {RR_RATIO} | Risk: {RISK_PER_TRADE_PCT}%/trade")
        logger.info(f"  Partial TP: {'ON' if USE_PARTIAL_TP else 'OFF'} @ {PARTIAL_TP_R}R | "
                    f"Trailing SL: {'ON' if USE_TRAILING_SL else 'OFF'}")
        logger.info("="*65)

        logger.info("Step 1/3: Fetching wallet...")
        self.fetch_wallet()
        self.print_wallet()

        logger.info("Step 2/3: Testing connection...")
        if not self.client.test_connection():
            logger.warning("API unreachable — running in offline/paper mode")

        logger.info(f"Step 3/3: Starting main loop. Capital: ${self.capital:,.2f}")
        if self.capital <= 0:
            logger.warning(f"Wallet returned 0 — using fallback ${CAPITAL_FALLBACK}")
            self.capital = CAPITAL_FALLBACK

        self.running = True
        cycle = 0
        WALLET_REFRESH_EVERY = 6  # refresh every 6 cycles (~30 min)

        while self.running:
            cycle += 1
            logger.info(f"\n-- Cycle #{cycle} | {datetime.now().strftime('%H:%M:%S')} "
                        f"| TF:{CANDLE_TF} | Capital: ${self.capital:,.2f} --")

            if cycle % WALLET_REFRESH_EVERY == 0:
                logger.info("Refreshing wallet...")
                self.fetch_wallet()

            paused, reason = self._check_circuit_breaker()
            if paused:
                logger.warning(f"[STOP] CIRCUIT BREAKER: {reason}")
                self.print_dashboard()
                time.sleep(CHECK_INTERVAL_SEC * 3)
                continue

            # Monitor + update trailing SLs
            self.monitor_trades()

            for symbol, product in PRODUCTS.items():
                if not product.get("active"):
                    continue
                try:
                    result = self.process_symbol(symbol)
                    action = result.get("action")

                    if action == "trade":
                        self.execute_trade(result)

                    elif action == "ml_filtered":
                        logger.info(f"  [SKIP] {symbol}: ML — {result.get('reason','')}")

                    elif action == "no_signal":
                        s = result.get("state", {})
                        strat = s.get("strategy", "bb_4h")
                        if strat == "gann":
                            logger.info(
                                f"  {symbol}: ${float(s.get('price',0)):,.2f} | "
                                f"Gann R1={float(s.get('gann_r1',0)):,.0f} "
                                f"S1={float(s.get('gann_s1',0)):,.0f} | "
                                f"RSI={float(s.get('rsi',0)):.0f} | "
                                f"4H:{s.get('trend_4h','?')} | "
                                f"Vol:{float(s.get('vol_ratio',1)):.1f}x")
                        else:
                            sq = s.get('squeeze_dur', 0)
                            vr = s.get('vol_ratio', 0)
                            logger.info(
                                f"  {symbol}: ${s.get('price',0):,.2f} | "
                                f"4H BB Squeeze:{'YES' if s.get('bb_squeeze') else 'no'} "
                                f"({sq}bars) | "
                                f"4H:{s.get('trend_4h','?')} | "
                                f"Vol:{vr:.1f}x | RSI:{s.get('rsi',0):.0f}")

                    elif action == "insufficient_margin":
                        logger.warning(f"  [WARN] {symbol}: {result.get('reason','')}")
                    elif action == "max_trades_reached":
                        logger.info(f"  {symbol}: max trades open ({MAX_OPEN_TRADES})")
                    elif action == "insufficient_data":
                        logger.warning(f"  {symbol}: insufficient 4H data")

                except Exception as e:
                    logger.error(f"Error on {symbol}: {e}", exc_info=True)

            if cycle % 12 == 0:  # dashboard every 12 cycles (~1 hour)
                self.print_dashboard()

            time.sleep(CHECK_INTERVAL_SEC)

    def run_once(self):
        logger.info("--- Single scan (4H mode) ---")
        self.fetch_wallet()
        self.print_wallet()
        for symbol in PRODUCTS:
            if not PRODUCTS[symbol]["active"]:
                continue
            result = self.process_symbol(symbol)
            logger.info(f"{symbol}: {result['action']}")
            if result.get("signal"):
                PositionSizer.print_trade_card(result["calc"])
                sig = result["signal"]
                logger.info(f"  TP1 (50%@1R): ${getattr(sig,'tp1',sig.tp):,.2f}")
                logger.info(f"  TP2 (full)  : ${sig.tp:,.2f}")
            elif result.get("state"):
                s = result["state"]
                logger.info(f"  ${s.get('price',0):,.2f} | "
                            f"Squeeze={s.get('bb_squeeze')} | "
                            f"4H trend={s.get('trend_4h','?')}")
        if self.capital <= 0:
            self.capital = CAPITAL_FALLBACK
        logger.info(f"30d stats: {self.db.get_trade_stats()}")

    def stop(self):
        self.running = False
        logger.info("Bot stopped.")


# ── ENTRYPOINT ──────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mode",
                   choices=["run","once","wallet","train","dashboard","discover"],
                   default="once")
    args = p.parse_args()
    bot = TradingBot()

    if args.mode == "run":
        try:
            bot.run()
        except KeyboardInterrupt:
            bot.stop()
    elif args.mode == "once":
        bot.run_once()
    elif args.mode == "wallet":
        bot.fetch_wallet()
        bot.print_wallet()
        positions = bot.get_positions_summary()
        if positions:
            print("Open Positions:")
            for pos in positions:
                print(f"  {pos['symbol']} {pos['direction']} x{pos['size']} "
                      f"@ {pos['entry']} | uPnL: {pos['upnl']:+.4f}")
        else:
            print("No open positions.")
    elif args.mode == "train":
        bot._load_or_train_ml()
    elif args.mode == "dashboard":
        bot.fetch_wallet()
        bot.print_dashboard()
    elif args.mode == "discover":
        print("Fetching products from exchange...")
        found = bot.client.discover_product_ids()
        print(f"Found {len(found)} BTC/ETH/SOL products:")
        for sym, info in sorted(found.items()):
            print(f"  {sym:<15} id={info['product_id']:<8} "
                  f"tick={info['tick_size']:<8} lot={info['contract_value']}")
        print("\nUpdate PRODUCTS in config/settings.py with correct product_ids!")
