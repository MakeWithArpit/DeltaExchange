"""
Unified Trading Bot v3.0
========================
Crypto (Delta Exchange):  runs 24/7 — BTC/ETH futures on 4H candles
India  (Dhann NSE):       runs only during market hours (09:15 – 15:30)
                          intraday stocks + pairs arbitrage

Both bots share one main loop with a single CHECK_INTERVAL_SEC timer.
Each has its own SQLite database so P&L stays separate.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import time, logging, os, argparse, math
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from config.settings import (
    # Crypto
    PRODUCTS, CAPITAL_USDT, CAPITAL_FALLBACK,
    CRYPTO_RISK_PCT, CRYPTO_MAX_OPEN, LEVERAGE, RR_RATIO,
    CANDLE_TF, CANDLES_NEEDED,
    STRATEGY_MODE, GANN_REF_PRICES,
    USE_PARTIAL_TP, PARTIAL_TP_R, PARTIAL_TP_SIZE, USE_TRAILING_SL,
    USE_ML_FILTER, ML_MIN_CONFIDENCE,
    PAPER_TRADE, CHECK_INTERVAL_SEC,
    CRYPTO_DB_PATH, INDIA_DB_PATH, LOG_PATH,
    MAX_WEEKLY_LOSS_PCT, MONTHLY_TARGET_PCT,
    MONTHLY_TRAIL_PCT, MONTHLY_HARD_STOP_PCT,
    # India
    DHANN_CLIENT_ID, DHANN_ACCESS_TOKEN,
    INDIA_TOTAL_CAPITAL, INTRADAY_CAPITAL, PAIRS_CAPITAL,
    INDIA_RISK_PCT, INDIA_MAX_OPEN,
    INTRADAY_STOCKS, PAIRS,
    MARKET_OPEN, MARKET_CLOSE, NO_NEW_TRADE_AFTER, SQUARE_OFF_TIME,
    MAX_DAILY_LOSS_PCT, NSE_HOLIDAYS, TRADE_ON_MUHURAT,
)
from core.delta_client      import DeltaClient
from core.dhann_client      import DhannClient
from core.strategy          import StrategyEngine, Signal
from core.gann_strategy     import GannStrategyEngine, GannSignal
from core.intraday_strategy import IntradayEngine
from core.pairs_strategy    import PairsEngine
from core.position_sizer    import PositionSizer
from ml.signal_filter       import MLSignalFilter
from data.database          import Database, IndiaDatabase

# ── LOGGING ──────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# CRYPTO BOT SECTION
# ═══════════════════════════════════════════════════════════════════

class CryptoBot:
    """24/7 crypto futures trading on Delta Exchange."""

    def __init__(self):
        self.client      = DeltaClient()
        self.engine      = StrategyEngine()
        self.gann_engine = GannStrategyEngine()
        self.ml          = MLSignalFilter()
        self.db          = Database(CRYPTO_DB_PATH)
        self.capital     = float(CAPITAL_USDT)
        self.wallet      = {}
        # Monthly trailing state
        self.monthly_peak_pct   = 0.0
        self.monthly_protected  = False
        self.month_trading_band = False
        self._current_month     = None
        self._load_or_train_ml()

    # ── ML ───────────────────────────────────────────────────────
    def _load_or_train_ml(self):
        """Load pre-trained ML model or train on historical CSVs."""
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
                pass

        if self.ml.load():
            logger.info("ML model loaded.")
            return

        logger.info("Training ML model from historical CSVs...")
        csv_dir = os.path.join(os.path.dirname(__file__), "Historical CSVs")
        datasets = {}
        for fname, name in [
            ("bitcoin_30min.csv", "BTC"),
            ("eth_30min.csv",     "ETH"),
            ("sol_30min.csv",     "SOL"),
        ]:
            fpath = os.path.join(csv_dir, fname)
            if os.path.exists(fpath):
                df = pd.read_csv(fpath)
                df.columns = df.columns.str.strip().str.lower()
                df["time"] = pd.to_datetime(df["time"])
                datasets[name] = df
        if datasets:
            self.ml.train(datasets)
        else:
            logger.warning("No CSVs found — ML disabled.")

    # ── WALLET ───────────────────────────────────────────────────
    def fetch_wallet(self) -> dict:
        raw = self.client.get_balance()
        if not raw:
            logger.warning(f"Wallet fetch failed — using local capital: ${self.capital:,.2f}")
            return {}

        self.wallet = raw
        usdt = (raw.get("USD")  or raw.get("USDT") or
                raw.get("usdt") or raw.get("INR")  or
                raw.get("inr")  or {})
        asset_key = next((k for k in ["USD","USDT","usdt","INR","inr"] if raw.get(k)), "?")
        live_avail = float(usdt.get("available_balance", usdt.get("available", 0)))
        live_total = float(usdt.get("balance", live_avail))

        if live_total > 0:
            old = self.capital
            # FIX: Use total balance (available + locked in positions)
            # Agar manually trade open hai toh available near 0 hoti hai
            # lekin total balance actual capital reflect karta hai
            self.capital = live_total
            logger.info(f"  Capital (available):   ${live_avail:,.2f} {asset_key}  ← free margin")
            logger.info(f"  Capital (total):       ${live_total:,.2f} {asset_key}  ← available + in positions")
            if old > 0 and abs(old - live_total) > 0.01:
                logger.info(f"  Change since last fetch: {live_total - old:+.2f}")
        elif live_avail > 0:
            # Fallback: total missing, use available
            old = self.capital
            self.capital = live_avail
            logger.info(f"  Capital (available):   ${live_avail:,.2f} {asset_key}")
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
        print(f"  CRYPTO WALLET  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("─"*60)
        for asset, info in self.wallet.items():
            total = float(info.get("balance", info.get("total", 0)))
            avail = float(info.get("available_balance", info.get("available", 0)))
            if total < 0.000001:
                continue
            locked  = total - avail
            used_pct = locked / total * 100 if total > 0 else 0
            bar = ("█" * int(used_pct / 5)) + ("░" * (20 - int(used_pct / 5)))
            print(f"  {asset:<8} total : {total:>16,.6f}")
            print(f"           free  : {avail:>16,.6f}  [{bar}] {used_pct:.1f}% in orders")
        print("─"*60)
        print(f"  Active capital: ${self.capital:,.2f}")
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

    # ── CANDLES ──────────────────────────────────────────────────
    def fetch_candles(self, symbol: str, tf: str = None,
                      limit: int = None) -> pd.DataFrame:
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

    # ── CIRCUIT BREAKER ──────────────────────────────────────────
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

    # ── SIGNAL PIPELINE ──────────────────────────────────────────
    def process_symbol(self, symbol: str) -> dict:
        result = {"symbol": symbol, "action": "none", "signal": None}

        df = self.fetch_candles(symbol, tf=CANDLE_TF, limit=CANDLES_NEEDED)
        if df is None or len(df) < 50:
            result["action"] = "insufficient_data"
            return result

        bb_signal   = None
        gann_signal = None

        if STRATEGY_MODE in ("bb", "both", "confirm"):
            if len(df) >= 50:
                bb_signal = self.engine.check_signal(symbol, df)

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

        # ── ML Filter (BB signals only) — BUG FIX: predict returns dict ──
        if USE_ML_FILTER and STRATEGY_MODE in ("bb", "confirm", "both") and hasattr(sig, 'squeeze_dur'):
            ml_result = self.ml.predict(sig, df)
            if not ml_result.get("take_trade", True):
                result["action"] = "ml_filtered"
                result["reason"] = ml_result.get("reason", "ML filtered")
                return result

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
        if len(open_trades) >= CRYPTO_MAX_OPEN:
            result["action"] = "max_trades_reached"
            return result

        result.update({"action": "trade", "signal": sig, "calc": calc})
        return result

    def execute_trade(self, result: dict) -> bool:
        signal = result["signal"]
        calc   = result["calc"]
        tp1    = getattr(signal, 'tp1', signal.tp)

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

        side  = "buy" if signal.direction == "long" else "sell"
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
        for trade in self.db.get_open_trades():
            if not trade.get("is_paper"):
                continue

            df = self.fetch_candles(trade["symbol"])
            if df is None or df.empty:
                continue

            price        = float(df.iloc[-1]["close"])
            direction    = trade["direction"]
            sl           = float(trade.get("trail_sl") or trade["sl_price"])
            tp           = float(trade["tp_price"])
            tp1          = float(trade.get("tp1_price") or tp)
            partial_done = bool(trade.get("partial_done", False))

            trail_info = self.engine.update_trailing_sl(trade, price)
            new_sl     = trail_info['new_sl']

            if new_sl != sl:
                self.db.update_trade_trail(trade["id"], new_sl,
                                           trail_info['trail_active'])
                if trail_info['trail_active']:
                    logger.info(f"  [TRAIL] #{trade['id']} {trade['symbol']} "
                                f"SL moved: {sl:.2f} -> {new_sl:.2f} "
                                f"(price={price:.2f}, +{trail_info['profit_r']:.2f}R)")

            # Partial TP at 1R
            if trail_info['should_close_partial'] and not partial_done:
                partial_pnl = (trade["reward_usdt"] * PARTIAL_TP_SIZE
                               - trade["fees_usdt"] * PARTIAL_TP_SIZE)
                self.capital += partial_pnl
                self.db.mark_partial_tp(trade["id"], tp1, partial_pnl)
                logger.info(f"  [TP1] #{trade['id']} {trade['symbol']} "
                            f"Partial 50% @ {tp1:.2f} | +${partial_pnl:.2f} "
                            f"| SL → breakeven")
                continue

            sl_hit = (price <= new_sl) if direction == "long" else (price >= new_sl)
            if sl_hit:
                risk_remaining = trade["risk_usdt"] * (0.5 if partial_done else 1.0)
                pnl = -risk_remaining
                self.db.close_trade(trade["id"], new_sl, "stop_loss", -1.0, pnl)
                self.capital += pnl
                status = "trail_SL" if trail_info['trail_active'] else "SL"
                logger.info(f"  [{status}] #{trade['id']} {trade['symbol']} "
                            f"@ {new_sl:.2f} | ${pnl:.2f}")
                continue

            tp_hit = (price >= tp) if direction == "long" else (price <= tp)
            if tp_hit:
                reward_remaining = trade["reward_usdt"] * (0.5 if partial_done else 1.0)
                pnl = reward_remaining - trade["fees_usdt"] * (0.5 if partial_done else 1.0)
                self.db.close_trade(trade["id"], tp, "take_profit", RR_RATIO, pnl)
                self.capital += pnl
                logger.info(f"  [TP2] #{trade['id']} {trade['symbol']} "
                            f"Full TP @ {tp:.2f} | +${pnl:.2f}")

    # ── DISPLAY ──────────────────────────────────────────────────
    def _print_trade_alert(self, sig, calc, trade_id, paper):
        mode = "PAPER" if paper else "LIVE"
        icon = "LONG" if sig.direction == "long" else "SHORT"
        tp1  = getattr(sig, 'tp1', sig.tp)
        print(f"""
{'='*65}
[{mode}] CRYPTO TRADE #{trade_id}  {icon} {sig.symbol}
{'='*65}
  Signal   : {sig.reason[:75]}

  Entry    : ${sig.entry:>12,.4f}
  Stop Loss: ${sig.sl:>12,.4f}   risk   = ${calc['risk_usdt']:.2f}
  TP1 (1R) : ${tp1:>12,.4f}   partial = 50% close
  TP2 (full): ${sig.tp:>12,.4f}  reward = ${calc['reward_usdt']:.2f}
  Liq Price: ${calc['liq_price']:>12,.4f}   ⚠️  WARNING

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

        _t   = int(stats.get('total', 0) or 0)
        _w   = int(stats.get('wins',  0) or 0)
        _wr  = float(stats.get('wr',   0) or 0)
        _r   = float(stats.get('net_r', 0) or 0)
        _pnl = float(stats.get('pnl_usdt', 0) or 0)
        mo   = self.db.get_monthly_pnl_pct(self.capital)

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  CRYPTO BOT v3.0  {'[PAUSED]' if paused else '[RUNNING]':^18}  {datetime.now().strftime('%H:%M:%S'):>8}  ║
╠══════════════════════════════════════════════════════════════╣
║  MODE: {'PAPER' if PAPER_TRADE else 'LIVE ':<7}  TF: {CANDLE_TF:<4}  ML: {'ON' if USE_ML_FILTER else 'OFF'}  Lev:{LEVERAGE}x  RR:{RR_RATIO}  ║
╠══════════════════════════════════════════════════════════════╣
║  Capital: ${self.capital:>10,.2f}  USDT                              ║
╠══════════════════════════════════════════════════════════════╣
║  PERFORMANCE (30 days)                                       ║
║  Trades:{_t:<5}  Wins:{_w:<5}  WR:{_wr:>5.1f}%  Net R:{_r:>+7.1f}R      ║
║  PnL: ${_pnl:>+8.2f}  | Month: {mo['pnl_pct']:>+6.2f}% (${mo['pnl_usdt']:>+7.2f})  ║
╠══════════════════════════════════════════════════════════════╣
║  OPEN TRADES ({len(open_t)} paper / {len(live_pos)} live)                     ║""")
        for t in open_t:
            trail   = "[TRAIL]" if t.get('trail_active') else "      "
            partial = "[P1]"    if t.get('partial_done')  else "    "
            print(f"║  {'L' if t['direction']=='long' else 'S'} {t['symbol']:<8} "
                  f"@ ${t['entry_price']:>10,.2f} {trail}{partial}  ║")
        if paused:
            print(f"║  STOPPED: {pause_reason[:50]:<50} ║")
        print("╚══════════════════════════════════════════════════════════════╝")

    def run_cycle(self, cycle: int):
        """Run one full crypto trading cycle."""
        paused, reason = self._check_circuit_breaker()
        if paused:
            logger.warning(f"[CRYPTO STOP] CIRCUIT BREAKER: {reason}")
            return

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
                    if s.get("strategy") == "gann":
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
                    logger.info(f"  {symbol}: max crypto trades open ({CRYPTO_MAX_OPEN})")
                elif action == "insufficient_data":
                    logger.warning(f"  {symbol}: insufficient 4H data")

            except Exception as e:
                logger.error(f"Crypto error on {symbol}: {e}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════
# INDIA BOT SECTION
# ═══════════════════════════════════════════════════════════════════

class IndiaBot:
    """NSE intraday + pairs arbitrage — active only during market hours."""

    def __init__(self):
        self.client   = DhannClient(DHANN_CLIENT_ID, DHANN_ACCESS_TOKEN)
        self.intraday = IntradayEngine()
        self.pairs    = PairsEngine()
        self.db       = IndiaDatabase(INDIA_DB_PATH)
        saved = self.db.get_last_capital()
        self.capital = saved if saved is not None else float(INDIA_TOTAL_CAPITAL)
        if saved is not None:
            logger.info(f"  [India Capital] Restored from last session: Rs {self.capital:,.2f}")
        else:
            logger.info(f"  [India Capital] Starting fresh: Rs {self.capital:,.2f}")
        self._candle_cache       = {}
        self._squared_off_today  = False
        self._restore_pairs_state()

    # ── PAIRS STATE RESTORE (persists across restarts) ───────────
    def _restore_pairs_state(self):
        open_pairs = self.db.get_open_pairs()
        if open_pairs:
            logger.info(f"Restoring {len(open_pairs)} open pairs from DB...")
        for t in open_pairs:
            entry_p1 = round(t["notional1"] / t["shares1"], 4) if t["shares1"] else 0
            entry_p2 = round(t["notional2"] / t["shares2"], 4) if t["shares2"] else 0
            try:
                opened_dt = datetime.fromisoformat(t["opened_at"])
                elapsed_min = (datetime.now() - opened_dt).total_seconds() / 60
                bars_held = max(0, int(elapsed_min / (CHECK_INTERVAL_SEC / 60)))
            except Exception:
                bars_held = 0
            self.pairs.open_pairs[t["pair_name"]] = {
                "action":       t["action"],
                "entry_spread": float(t["entry_spread"]),
                "entry_zscore": float(t["entry_zscore"]),
                "shares1":      t["shares1"],
                "shares2":      t["shares2"],
                "bars_held":    bars_held,
                "timestamp":    t["opened_at"],
                "trade_id":     t["id"],
                "entry_price1": entry_p1,
                "entry_price2": entry_p2,
            }
            logger.info(f"  Restored pair: {t['pair_name']} (id={t['id']}, "
                        f"action={t['action']}, bars={bars_held})")

    # ── MARKET HOURS + HOLIDAY CHECK ────────────────────────────
    def _is_nse_holiday(self, date: datetime = None) -> bool:
        """
        Returns True if given date is an NSE holiday or weekend.
        Checks NSE_HOLIDAYS list from settings.py.
        Update NSE_HOLIDAYS every January for the new year's calendar.
        """
        date = date or datetime.now()
        if date.weekday() >= 5:          # Saturday=5, Sunday=6
            return True
        date_str = date.strftime("%Y-%m-%d")
        if date_str in NSE_HOLIDAYS:
            return True
        return False

    def _is_market_open(self) -> bool:
        now = datetime.now()
        if self._is_nse_holiday(now):
            return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= MARKET_CLOSE

    def _can_enter_new(self) -> bool:
        now = datetime.now()
        if self._is_nse_holiday(now):
            return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= NO_NEW_TRADE_AFTER

    def _should_squareoff(self) -> bool:
        """
        Squareoff trigger: 15:15 on normal days.
        On holidays, if somehow trades are open (e.g., holiday declared
        after bot started), squareoff immediately when market closes.
        """
        now = datetime.now()
        t   = now.strftime("%H:%M")
        if self._is_nse_holiday(now):
            # Holiday mein agar koi open trade hai to immediately squareoff
            open_it = self.db.get_open_intraday()
            open_pt = self.db.get_open_pairs()
            if open_it or open_pt:
                logger.warning(
                    f"  [India HOLIDAY] {now.strftime('%Y-%m-%d')} is NSE holiday "
                    f"but {len(open_it)} intraday + {len(open_pt)} pairs trades open! "
                    f"Force squaring off..."
                )
                return True
            return False
        return t >= SQUARE_OFF_TIME

    # ── DATA FETCH ───────────────────────────────────────────────
    def _get_candles(self, symbol: str, security_id: str = None) -> pd.DataFrame:
        if DHANN_CLIENT_ID != "YOUR_CLIENT_ID" and security_id:
            df = self.client.get_candles(security_id, interval="60", days=60)
            if not df.empty:
                self._candle_cache[symbol] = df
                return df
        cached = self._candle_cache.get(symbol)
        if cached is not None:
            last_dt = pd.to_datetime(cached["datetime"].iloc[-1])
            if (datetime.now() - last_dt).seconds < 300:
                return cached
        df = self.client.get_candles_yfinance(symbol, interval="1h", period="60d")
        if not df.empty:
            self._candle_cache[symbol] = df
        return df

    def fetch_balance(self) -> float:
        if DHANN_CLIENT_ID == "YOUR_CLIENT_ID":
            logger.info(f"  [India Paper] Capital: Rs {self.capital:,.2f}")
            return self.capital
        cap = self.client.get_available_capital()
        if cap > 0:
            self.capital = cap
            logger.info(f"  Dhann balance: Rs {cap:,.2f}")
        return cap

    # ── INTRADAY ─────────────────────────────────────────────────
    def _run_intraday(self):
        if not self._can_enter_new():
            return
        open_trades = self.db.get_open_intraday()
        if len(open_trades) >= INDIA_MAX_OPEN:
            logger.info(f"  [Intraday] Max trades open ({INDIA_MAX_OPEN})")
            return
        for name, stock in INTRADAY_STOCKS.items():
            if not stock.get("active"):
                continue
            df = self._get_candles(name, stock.get("security_id"))
            if df.empty or len(df) < 30:
                logger.warning(f"  [Intraday] No data for {name}")
                continue
            sig = self.intraday.check_signal(name, stock["strategy"], df)
            if sig is None:
                state = self.intraday.get_state(name, df)
                logger.info(
                    f"  {name}: Rs {state.get('price',0):,.2f} | "
                    f"RSI={state.get('rsi',0):.0f} | "
                    f"Vol={state.get('vol_ratio',0):.1f}x | "
                    f"Squeeze={'YES' if state.get('bb_squeeze') else 'no'} | "
                    f"Trend={'UP' if state.get('ema200_bull') else 'DOWN'}")
                continue
            calc = PositionSizer.calculate_india(sig.entry, sig.sl, INTRADAY_CAPITAL)
            if "error" in calc:
                logger.warning(f"  [Intraday] {name}: {calc['error']}")
                continue
            self._execute_intraday(sig, calc)

    def _execute_intraday(self, sig, calc):
        if PAPER_TRADE:
            trade_id = self.db.open_intraday(
                sig.symbol, sig.strategy, sig.direction,
                sig.entry, sig.sl, sig.tp,
                calc["shares"], calc["notional"],
                calc["risk_inr"], calc["fees_inr"], is_paper=True,
            )
            logger.info(f"""
{'='*60}
[PAPER] INTRADAY #{trade_id} | {sig.symbol} {sig.direction.upper()}
{'='*60}
  Strategy : {sig.strategy}
  Signal   : {sig.reason}
  Entry    : Rs {sig.entry:>10,.2f}
  Stop Loss: Rs {sig.sl:>10,.2f}   Risk = Rs {calc['risk_inr']:.2f}
  Target   : Rs {sig.tp:>10,.2f}
  Shares   : {calc['shares']:>10}
  Fees     : Rs {calc['fees_inr']:.2f}
{'='*60}""")
            return
        stock  = INTRADAY_STOCKS.get(sig.symbol, {})
        sec_id = stock.get("security_id", "")
        exch   = stock.get("exchange", "NSE")
        side   = "BUY" if sig.direction == "long" else "SELL"
        order  = self.client.place_order(sec_id, exch, side, calc["shares"], "MARKET")
        if not order:
            logger.error(f"  Order failed for {sig.symbol}")
            return
        sl_side = "SELL" if sig.direction == "long" else "BUY"
        self.client.place_sl_order(sec_id, exch, sl_side, calc["shares"], sig.sl)
        self.db.open_intraday(
            sig.symbol, sig.strategy, sig.direction,
            sig.entry, sig.sl, sig.tp,
            calc["shares"], calc["notional"],
            calc["risk_inr"], calc["fees_inr"], is_paper=False,
        )

    def _monitor_intraday(self):
        for trade in self.db.get_open_intraday():
            if not trade["is_paper"]:
                continue
            df = self._get_candles(trade["symbol"])
            if df is None or df.empty:
                continue
            price = float(df.iloc[-1]["close"])
            sl = trade["sl_price"]; tp = trade["tp_price"]
            d  = trade["direction"]
            sl_hit = price <= sl if d == "long" else price >= sl
            tp_hit = price >= tp if d == "long" else price <= tp
            if sl_hit:
                pnl = -trade["risk_inr"] - trade["fees_inr"]
                self.db.close_intraday(trade["id"], sl, "stop_loss", pnl)
                self.capital += pnl
                logger.info(f"  [SL] Intraday #{trade['id']} {trade['symbol']} "
                            f"Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")
            elif tp_hit:
                pnl = trade["risk_inr"] * 2 - trade["fees_inr"]
                self.db.close_intraday(trade["id"], tp, "take_profit", pnl)
                self.capital += pnl
                logger.info(f"  [TP] Intraday #{trade['id']} {trade['symbol']} "
                            f"+Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")

    def _squareoff_all_intraday(self):
        for trade in self.db.get_open_intraday():
            df = self._get_candles(trade["symbol"])
            price = float(df.iloc[-1]["close"]) if not df.empty else trade["entry_price"]
            raw = (price - trade["entry_price"]) * trade["shares"]
            if trade["direction"] == "short":
                raw = -raw
            pnl = raw - trade["fees_inr"]
            self.db.close_intraday(trade["id"], price, "eod_squareoff", pnl)
            self.capital += pnl
            logger.info(f"  [EOD-IT] {trade['symbol']} @ Rs {price:.2f} | "
                        f"PnL: Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")

    # ── PAIRS ARBITRAGE ──────────────────────────────────────────
    def _run_pairs(self):
        for pair in PAIRS:
            if not pair.get("active"):
                continue
            pname = pair["name"]
            df1   = self._get_candles(pair["stock1"], pair.get("sec_id1"))
            df2   = self._get_candles(pair["stock2"], pair.get("sec_id2"))
            if df1.empty or df2.empty:
                logger.warning(f"  [Pairs] No data for {pname}")
                continue
            sig   = self.pairs.check_signal(pname, pair["stock1"], pair["stock2"], df1, df2)
            state = self.pairs.get_state(pname, df1, df2)
            if sig is None:
                in_trade = state.get("in_trade", False)
                logger.info(f"  {pname}: zscore={state.get('zscore',0):+.2f} | "
                            f"{'IN TRADE' if in_trade else 'watching'}")
                continue
            if sig.action == "EXIT":
                self._execute_pairs_exit(sig, pname, df1, df2)
            elif sig.action in ("ENTER_SHORT_S1", "ENTER_LONG_S1") and self._can_enter_new():
                self._execute_pairs_entry(sig, df1, df2)

    def _execute_pairs_entry(self, sig, df1, df2):
        price1 = float(df1.iloc[-1]["close"])
        price2 = float(df2.iloc[-1]["close"])
        calc   = PositionSizer.calculate_pairs(PAIRS_CAPITAL, price1, price2)
        if PAPER_TRADE:
            trade_id = self.db.open_pairs(
                sig.pair_name, sig.stock1, sig.stock2, sig.action,
                sig.zscore, sig.spread,
                calc["shares1"], calc["shares2"],
                calc["notional1"], calc["notional2"],
                calc["fees_inr"], is_paper=True,
            )
            self.pairs.register_entry(sig.pair_name, sig, sig.spread)
            self.pairs.open_pairs[sig.pair_name]["trade_id"]     = trade_id
            self.pairs.open_pairs[sig.pair_name]["entry_price1"] = price1
            self.pairs.open_pairs[sig.pair_name]["entry_price2"] = price2
            logger.info(f"""
{'='*60}
[PAPER] PAIRS ARB #{trade_id} | {sig.pair_name}
{'='*60}
  Action   : {sig.action}
  Signal   : {sig.reason}
  Zscore   : {sig.zscore:+.3f}
  {sig.stock1}: {calc['shares1']} shares @ Rs {price1:,.2f}
  {sig.stock2}: {calc['shares2']} shares @ Rs {price2:,.2f}
  Total Notional: Rs {calc['total_notional']:,.2f}
  Fees     : Rs {calc['fees_inr']:.2f}
{'='*60}""")
            return
        pair_cfg = next((p for p in PAIRS if p["name"] == sig.pair_name), {})
        if sig.action == "ENTER_SHORT_S1":
            self.client.place_order(pair_cfg["sec_id1"], "NSE", "SELL", calc["shares1"], "MARKET")
            self.client.place_order(pair_cfg["sec_id2"], "NSE", "BUY",  calc["shares2"], "MARKET")
        else:
            self.client.place_order(pair_cfg["sec_id1"], "NSE", "BUY",  calc["shares1"], "MARKET")
            self.client.place_order(pair_cfg["sec_id2"], "NSE", "SELL", calc["shares2"], "MARKET")
        self.db.open_pairs(
            sig.pair_name, sig.stock1, sig.stock2, sig.action,
            sig.zscore, sig.spread,
            calc["shares1"], calc["shares2"],
            calc["notional1"], calc["notional2"],
            calc["fees_inr"], is_paper=False,
        )
        self.pairs.register_entry(sig.pair_name, sig, sig.spread)

    def _execute_pairs_exit(self, sig, pair_name, df1, df2):
        """Exit using actual prices for accurate P&L."""
        open_pairs = self.db.get_open_pairs()
        trade = next((t for t in open_pairs if t["pair_name"] == pair_name), None)
        if not trade:
            return

        exit_price1 = float(df1.iloc[-1]["close"]) if not df1.empty else 0
        exit_price2 = float(df2.iloc[-1]["close"]) if not df2.empty else 0

        mem_state    = self.pairs.open_pairs.get(pair_name, {})
        entry_price1 = mem_state.get("entry_price1", 0)
        entry_price2 = mem_state.get("entry_price2", 0)
        shares1 = trade["shares1"]
        shares2 = trade["shares2"]

        if entry_price1 > 0 and exit_price1 > 0:
            if trade["action"] == "ENTER_LONG_S1":
                pnl_gross = ((exit_price1 - entry_price1) * shares1 +
                             (entry_price2 - exit_price2) * shares2)
            else:
                pnl_gross = ((entry_price1 - exit_price1) * shares1 +
                             (exit_price2 - entry_price2) * shares2)
        else:
            spread_chg = sig.spread - trade["entry_spread"]
            pnl_gross  = (-spread_chg if trade["action"] == "ENTER_SHORT_S1"
                          else spread_chg) * PAIRS_CAPITAL

        pnl_net = pnl_gross - trade["fees_inr"]
        self.db.close_pairs(trade["id"], sig.zscore, sig.spread, sig.reason, pnl_net)
        self.pairs.register_exit(pair_name)
        self.capital += pnl_net
        status = "+Rs" if pnl_net >= 0 else "-Rs"
        logger.info(f"  [Pairs EXIT] {pair_name} | zscore={sig.zscore:+.2f} | "
                    f"PnL: {status}{abs(pnl_net):.2f} | Capital: Rs {self.capital:,.2f}")

    def _squareoff_all_pairs(self):
        open_pairs = self.db.get_open_pairs()
        if not open_pairs:
            return
        logger.info(f"  [EOD-Pairs] Squaring off {len(open_pairs)} open pairs...")
        for trade in open_pairs:
            pname    = trade["pair_name"]
            pair_cfg = next((p for p in PAIRS if p["name"] == pname), None)
            if not pair_cfg:
                continue
            df1 = self._get_candles(pair_cfg["stock1"])
            df2 = self._get_candles(pair_cfg["stock2"])
            if df1.empty or df2.empty:
                logger.warning(f"  [EOD-Pairs] No data to square off {pname}")
                continue
            exit_p1  = float(df1.iloc[-1]["close"])
            exit_p2  = float(df2.iloc[-1]["close"])
            mem      = self.pairs.open_pairs.get(pname, {})
            entry_p1 = mem.get("entry_price1", 0)
            entry_p2 = mem.get("entry_price2", 0)
            if entry_p1 > 0:
                if trade["action"] == "ENTER_LONG_S1":
                    pnl_gross = ((exit_p1 - entry_p1) * trade["shares1"] +
                                 (entry_p2 - exit_p2) * trade["shares2"])
                else:
                    pnl_gross = ((entry_p1 - exit_p1) * trade["shares1"] +
                                 (exit_p2 - entry_p2) * trade["shares2"])
            else:
                from numpy import log as nplog
                exit_spread = nplog(exit_p1 / exit_p2) if exit_p2 > 0 else trade["entry_spread"]
                spread_chg  = exit_spread - trade["entry_spread"]
                pnl_gross   = (-spread_chg if trade["action"] == "ENTER_SHORT_S1"
                               else spread_chg) * PAIRS_CAPITAL
            pnl_net = pnl_gross - trade["fees_inr"]
            self.db.close_pairs(trade["id"], None, None, "eod_squareoff", pnl_net)
            self.pairs.register_exit(pname)
            self.capital += pnl_net
            logger.info(f"  [EOD-Pairs] {pname} | PnL: Rs {pnl_net:.2f} | "
                        f"Capital: Rs {self.capital:,.2f}")

    def print_dashboard(self):
        stats     = self.db.get_stats()
        today_pnl = self.db.get_today_pnl()
        it_open   = self.db.get_open_intraday()
        pt_open   = self.db.get_open_pairs()
        it = stats["intraday"]; pt = stats["pairs"]
        today_pct = today_pnl["total"] / max(self.capital, 1) * 100
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  INDIA BOT v3.0  {'[PAPER]' if PAPER_TRADE else '[LIVE] '}   {datetime.now().strftime('%d-%m-%Y %H:%M')}  ║
╠══════════════════════════════════════════════════════════════╣
║  Capital: Rs {self.capital:>10,.2f}  |  Today: {today_pct:>+6.2f}%  (Rs {today_pnl['total']:>+8.2f})  ║
╠══════════════════════════════════════════════════════════════╣
║  INTRADAY  Trades:{it['total']:<4}  Wins:{it['wins']:<4}  WR:{it['wr']:>5.1f}%  PnL:Rs{it['pnl']:>+8.0f}  ║
║  PAIRS ARB Trades:{pt['total']:<4}  Wins:{pt['wins']:<4}  WR:{pt['wr']:>5.1f}%  PnL:Rs{pt['pnl']:>+8.0f}  ║
╠══════════════════════════════════════════════════════════════╣""")
        if it_open:
            print(f"║  OPEN INTRADAY ({len(it_open)})                                      ║")
            for t in it_open:
                print(f"║  {'L' if t['direction']=='long' else 'S'} {t['symbol']:<10} "
                      f"@ Rs {t['entry_price']:>8,.2f}  [{t['strategy']}]  ║")
        if pt_open:
            print(f"║  OPEN PAIRS ({len(pt_open)})                                         ║")
            for t in pt_open:
                print(f"║  {t['pair_name']:<22} zscore_entry={t['entry_zscore']:>+6.2f}  ║")
        print("╚══════════════════════════════════════════════════════════════╝")

    def run_cycle(self):
        """Run one India trading cycle. Only executes if market is open."""
        now = datetime.now()

        # ── Holiday / weekend check ──────────────────────────────
        if self._is_nse_holiday(now):
            date_str = now.strftime("%Y-%m-%d")
            # On holiday, if there are somehow open trades, squareoff
            if self._should_squareoff():
                pass   # _should_squareoff already logged + will squareoff below
            else:
                logger.info(f"  [India] {date_str} is NSE holiday — skipping")
                return

        if not self._is_market_open():
            logger.info("  [India] Market closed — skipping")
            return

        if self.db.check_daily_loss(self.capital, MAX_DAILY_LOSS_PCT):
            logger.warning("  [India STOP] Daily loss limit hit")
            return

        # Reset squareoff flag for new trading day
        if now.strftime("%H:%M") < SQUARE_OFF_TIME:
            self._squared_off_today = False

        # EOD squareoff — runs once at 15:15 (or immediately on holiday)
        if self._should_squareoff() and not self._squared_off_today:
            logger.info("  [India] EOD: Squaring off all positions...")
            self._squareoff_all_intraday()
            self._squareoff_all_pairs()
            self.db.save_daily_capital(self.capital)
            self._squared_off_today = True
            logger.info(f"  [India Capital] Saved: Rs {self.capital:,.2f}")
            return

        self._monitor_intraday()
        logger.info("  -- India Intraday Scan --")
        self._run_intraday()
        logger.info("  -- India Pairs Scan --")
        self._run_pairs()

    def save_and_stop(self):
        self.db.save_daily_capital(self.capital)
        logger.info(f"  [India Capital] Saved on stop: Rs {self.capital:,.2f}")


# ═══════════════════════════════════════════════════════════════════
# UNIFIED MAIN LOOP
# ═══════════════════════════════════════════════════════════════════

class UnifiedBot:
    """
    Single process running both bots.
    Crypto runs every cycle.
    India runs only during NSE market hours.
    """

    def __init__(self):
        logger.info("=" * 65)
        logger.info("  UNIFIED BOT v3.0 — Crypto 24/7 + India Market Hours")
        logger.info(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")
        logger.info("=" * 65)
        self.crypto = CryptoBot()
        self.india  = IndiaBot()
        self.running = False

    def startup(self):
        logger.info("Step 1/3: Fetching crypto wallet...")
        self.crypto.fetch_wallet()
        self.crypto.print_wallet()

        logger.info("Step 2/3: Testing crypto connection...")
        if not self.crypto.client.test_connection():
            logger.warning("Delta API unreachable — running crypto in paper/offline mode")

        logger.info("Step 3/3: Fetching India balance...")
        self.india.fetch_balance()

        if self.crypto.capital <= 0:
            logger.warning(f"Wallet returned 0 — using fallback ${CAPITAL_FALLBACK}")
            self.crypto.capital = CAPITAL_FALLBACK

    def run(self):
        self.startup()
        self.running = True
        cycle = 0
        WALLET_REFRESH_EVERY = 6  # refresh crypto wallet every 6 cycles (~30 min)

        while self.running:
            cycle += 1
            now_str = datetime.now().strftime('%H:%M:%S')
            logger.info(f"\n{'='*65}")
            logger.info(f"  CYCLE #{cycle} | {now_str} | "
                        f"Crypto: ${self.crypto.capital:,.2f} | "
                        f"India: Rs {self.india.capital:,.2f}")
            logger.info(f"{'='*65}")

            if cycle % WALLET_REFRESH_EVERY == 0:
                self.crypto.fetch_wallet()

            # ── CRYPTO (always) ──────────────────────────────────
            logger.info("  [Crypto] Running cycle...")
            self.crypto.run_cycle(cycle)

            # ── INDIA (market hours only) ────────────────────────
            if self.india._is_nse_holiday():
                logger.info(f"  [India] NSE HOLIDAY — market closed today")
            elif self.india._is_market_open():
                logger.info("  [India] Market OPEN — running cycle...")
                self.india.run_cycle()
            else:
                logger.info(f"  [India] Market closed ({datetime.now().strftime('%H:%M')})")

            # Dashboard every 12 cycles (~1 hour)
            if cycle % 12 == 0:
                self.crypto.print_dashboard()
                self.india.print_dashboard()

            time.sleep(CHECK_INTERVAL_SEC)

    def stop(self):
        self.running = False
        self.india.save_and_stop()
        logger.info("Unified bot stopped.")


# ── ENTRYPOINT ───────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Unified Trading Bot v3.0")
    p.add_argument("--mode",
                   choices=["run", "once", "wallet", "train",
                            "dashboard", "discover", "india_once"],
                   default="once",
                   help="run=main loop | once=single scan | wallet=show balances | "
                        "train=retrain ML | dashboard=show stats | "
                        "discover=find product IDs | india_once=India single scan")
    args = p.parse_args()

    bot = UnifiedBot()

    if args.mode == "run":
        try:
            bot.run()
        except KeyboardInterrupt:
            bot.stop()

    elif args.mode == "once":
        bot.startup()
        logger.info("--- Single scan (crypto) ---")
        for symbol in PRODUCTS:
            if not PRODUCTS[symbol]["active"]:
                continue
            result = bot.crypto.process_symbol(symbol)
            logger.info(f"{symbol}: {result['action']}")
            if result.get("signal"):
                PositionSizer.print_trade_card(result["calc"])
        logger.info(f"30d stats: {bot.crypto.db.get_trade_stats()}")

    elif args.mode == "india_once":
        bot.startup()
        logger.info("--- Single scan (India) ---")
        bot.india._run_intraday()
        bot.india._run_pairs()
        bot.india.print_dashboard()

    elif args.mode == "wallet":
        bot.startup()
        bot.crypto.print_wallet()
        positions = bot.crypto.get_positions_summary()
        if positions:
            print("Open Crypto Positions:")
            for pos in positions:
                print(f"  {pos['symbol']} {pos['direction']} x{pos['size']} "
                      f"@ {pos['entry']} | uPnL: {pos['upnl']:+.4f}")
        print(f"\nIndia Capital: Rs {bot.india.capital:,.2f}")

    elif args.mode == "train":
        bot.crypto._load_or_train_ml()

    elif args.mode == "dashboard":
        bot.startup()
        bot.crypto.print_dashboard()
        bot.india.print_dashboard()

    elif args.mode == "discover":
        bot.startup()
        print("Fetching products from Delta Exchange...")
        found = bot.crypto.client.discover_product_ids()
        print(f"Found {len(found)} BTC/ETH/SOL products:")
        for sym, info in sorted(found.items()):
            print(f"  {sym:<15} id={info['product_id']:<8} "
                  f"tick={info['tick_size']:<8} lot={info['contract_value']}")
        print("\nUpdate PRODUCTS in config/settings.py with correct product_ids!")
