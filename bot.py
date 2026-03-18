"""
╔══════════════════════════════════════════════════════════════════╗
║         MAIN TRADING BOT — BB SQUEEZE STRATEGY                  ║
║         Delta Exchange India | BTC + ETH + SOL                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time, logging, os, sys, math
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config.settings import *
from core.delta_client   import DeltaClient
from core.strategy       import StrategyEngine
from core.gann_strategy  import GannStrategyEngine, GannSignal
from core.position_sizer import PositionSizer
from ml.signal_filter    import MLSignalFilter
from data.database       import Database

# -- LOGGING --────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


class TradingBot:

    def __init__(self):
        self.client  = DeltaClient()
        self.engine      = StrategyEngine()
        self.gann_engine = GannStrategyEngine()
        self.ml      = MLSignalFilter()
        self.db      = Database(DB_PATH)
        self.capital = CAPITAL_USDT   # 0 at start; set from wallet on first fetch
        self.wallet  = {}             # raw wallet response from API
        self.running           = False
        # Monthly trailing profit tracker
        self.monthly_peak_pct  = 0.0
        self.monthly_protected = False
        self.month_trading_band= False
        self._current_month    = None
        self._load_or_train_ml()

    # -- ML INIT --────────────────────────────────────────────────
    def _load_or_train_ml(self):
        if self.ml.load():
            logger.info("ML model loaded.")
            return
        logger.info("Training ML model from historical CSVs...")
        datasets = {}
        for fname, name in [("Historical CSVs/bitcoin_30min.csv","BTC"),
                             ("Historical CSVs/eth_30min.csv","ETH"),
                             ("Historical CSVs/sol_30min.csv","SOL")]:
            if os.path.exists(fname):
                df = pd.read_csv(fname)
                df.columns = df.columns.str.strip().str.lower()
                df["time"] = pd.to_datetime(df["time"])
                datasets[name] = df
        if datasets:
            self.ml.train(datasets)
        else:
            logger.warning("No CSVs found — ML disabled.")

    # ══════════════════════════════════════════════════════════════
    # WALLET — fully integrated fetch + display + capital sync
    # ══════════════════════════════════════════════════════════════
    def fetch_wallet(self) -> dict:
        """
        Fetch live wallet from Delta Exchange.
        - Called at startup
        - Called every WALLET_REFRESH_EVERY cycles in main loop
        - Called after every real order execution
        - Updates self.capital with live available USDT
        """
        raw = self.client.get_balance()

        if not raw:
            logger.warning("Wallet fetch failed — API unreachable or bad credentials")
            logger.warning(f"Using local capital: ${self.capital:,.2f}")
            return {}

        self.wallet = raw

        # Delta Exchange uses "USD" not "USDT" as the margin asset key
        # Priority: USD → USDT → usdt → INR → inr
        usdt = (raw.get("USD")  or raw.get("USDT") or
                raw.get("usdt") or raw.get("INR")  or
                raw.get("inr")  or {})
        asset_key = next((k for k in ["USD","USDT","usdt","INR","inr"] if raw.get(k)), "?")

        live_avail = float(usdt.get("available_balance",
                           usdt.get("available", 0)))
        live_total = float(usdt.get("balance", live_avail))

        if live_avail > 0:
            old = self.capital
            self.capital = live_avail
            logger.info(f"  Capital (live wallet): ${live_avail:,.2f} {asset_key} available")
            logger.info(f"  Capital (total     ): ${live_total:,.2f} {asset_key} total")
            if old > 0 and abs(old - live_avail) > 0.01:
                change = live_avail - old
                logger.info(f"  Change since last fetch: {change:+.2f}")
        else:
            logger.warning(f"  Balance = 0 — wallet keys received: {list(raw.keys())}")
            logger.warning("  Hint: Delta testnet uses 'USD', not 'USDT'")

        return raw

    def print_wallet(self):
        """Detailed wallet printout"""
        if not self.wallet:
            print("\n  [Wallet not fetched — check API keys in config/settings.py]\n")
            return

        print("\n" + "─"*58)
        print(f"  WALLET  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("─"*58)

        for asset, info in self.wallet.items():
            total  = float(info.get("balance",           info.get("total",     0)))
            avail  = float(info.get("available_balance", info.get("available", 0)))
            locked = total - avail
            if total < 0.000001:
                continue
            used_pct = locked / total * 100 if total > 0 else 0
            bar = ("█" * int(used_pct/5)) + ("░" * (20 - int(used_pct/5)))
            print(f"  {asset:<8} total : {total:>16,.6f}")
            print(f"           free  : {avail:>16,.6f}  [{bar}] {used_pct:.1f}% in orders")

        print("─"*58)
        print(f"  Active capital (free USDT): ${self.capital:,.2f}")
        print("─"*58 + "\n")

    def get_positions_summary(self) -> list:
        """Fetch open positions from exchange (live trades only)"""
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
                "rpnl":      float(p.get("realized_pnl",   0)),
            })
        return out

    # -- CANDLES --────────────────────────────────────────────────
    def fetch_candles(self, symbol: str) -> pd.DataFrame:
        raw = self.client.get_candles(symbol, resolution=CANDLE_TF, limit=CANDLES_NEEDED)
        if raw:
            df = self.engine.candles_to_df(raw)
            if len(df) > 0:
                self.db.upsert_candles(symbol, df.to_dict("records"))
                return df
        cached = self.db.get_candles(symbol, limit=CANDLES_NEEDED)
        if cached:
            return pd.DataFrame(cached)
        logger.error(f"No candle data available for {symbol}")
        return pd.DataFrame()

    # -- CIRCUIT BREAKER --────────────────────────────────────────
    def _check_circuit_breaker(self) -> tuple:
        """
        Multi-level protection:
        1. Daily loss limit
        2. Monthly hard stop (-5%)
        3. Monthly profit trailing (target 3%, trail 1.5%)
        """
        from datetime import datetime

        # ── Month rollover reset ──────────────────────────────────
        this_month = datetime.now().strftime("%Y-%m")
        if self._current_month != this_month:
            self._current_month     = this_month
            self.monthly_peak_pct   = 0.0
            self.monthly_protected  = False
            self.month_trading_band = False
            logger.info(f"[TRAIL] New month {this_month} — trailing reset")

        # ── Already banded this month? ────────────────────────────
        if self.month_trading_band:
            mo = self.db.get_monthly_pnl_pct(self.capital)
            return True, (f"[TRAIL] Month locked at {mo['pnl_pct']:+.2f}% — "
                          f"profit protected. Next month pe resume.")

        # ── Daily loss check ──────────────────────────────────────
        daily_loss = self.db.get_daily_loss_pct(self.capital)
        if daily_loss >= MAX_DAILY_LOSS_PCT:
            return True, f"Daily loss {daily_loss:.1f}% >= limit {MAX_DAILY_LOSS_PCT}%"

        # ── Monthly hard stop ─────────────────────────────────────
        mo = self.db.get_monthly_pnl_pct(self.capital)
        mo_pct = mo["pnl_pct"]

        monthly_loss = abs(min(0.0, mo_pct))
        if monthly_loss >= MONTHLY_HARD_STOP_PCT:
            self.month_trading_band = True
            return True, (f"[STOP] Monthly loss {monthly_loss:.1f}% >= "
                          f"hard stop {MONTHLY_HARD_STOP_PCT}%")

        # ── Monthly profit trailing ───────────────────────────────
        if mo_pct >= MONTHLY_TARGET_PCT:
            # Target hit — trailing mode on
            if not self.monthly_protected:
                self.monthly_protected = True
                self.monthly_peak_pct  = mo_pct
                logger.info(f"[TRAIL] Target {MONTHLY_TARGET_PCT}% HIT! "
                            f"Current: {mo_pct:+.2f}% — trailing on")

        if self.monthly_protected:
            # Update peak
            if mo_pct > self.monthly_peak_pct:
                old_peak = self.monthly_peak_pct
                self.monthly_peak_pct = mo_pct
                logger.info(f"[TRAIL] New peak: {mo_pct:+.2f}% "
                            f"(was {old_peak:+.2f}%) — floor = {mo_pct - MONTHLY_TRAIL_PCT:+.2f}%")

            # Check if dropped below floor
            floor = self.monthly_peak_pct - MONTHLY_TRAIL_PCT
            if mo_pct <= floor:
                self.month_trading_band = True
                return True, (f"[TRAIL] Profit protected at {floor:+.2f}% "
                              f"(peak was {self.monthly_peak_pct:+.2f}%, "
                              f"current {mo_pct:+.2f}%) — trading band for month")

        return False, ""

    # -- SIGNAL PIPELINE --────────────────────────────────────────
    def process_symbol(self, symbol: str) -> dict:
        """
        Run configured strategy (BB Squeeze / Gann / Both) on one symbol.
        Returns action dict.
        """
        result = {"symbol": symbol, "action": "none", "signal": None}

        df = self.fetch_candles(symbol)
        if df is None or len(df) < 60:
            result["action"] = "insufficient_data"
            return result

        bb_signal   = None
        gann_signal = None

        # ── BB Squeeze ────────────────────────────────────────────
        if STRATEGY_MODE in ("bb", "both", "confirm"):
            if len(df) >= 300:
                bb_signal = self.engine.check_signal(symbol, df)

        # ── Gann Strategy ─────────────────────────────────────────
        if STRATEGY_MODE in ("gann", "both", "confirm"):
            # Skip SOL for Gann (ref price 0 = not configured)
            if GANN_REF_PRICES.get(symbol, 0) != 0 or symbol not in GANN_REF_PRICES:
                gann_signal = self.gann_engine.check_signal(symbol, df)

        # ── Confirm mode: both must agree ─────────────────────────
        if STRATEGY_MODE == "confirm":
            if (bb_signal and gann_signal and
                    bb_signal.direction == gann_signal.direction):
                # Both agree — high confidence
                sig = gann_signal  # use Gann levels for SL/TP
                sig.confidence = min(0.99, gann_signal.confidence + 0.15)
                sig.reason = f"[BB+GANN CONFIRM] {gann_signal.reason}"
            else:
                sig = None
        elif STRATEGY_MODE == "gann":
            sig = gann_signal
        elif STRATEGY_MODE == "bb":
            sig = bb_signal
        else:  # "both" — first signal that comes
            sig = gann_signal or bb_signal

        if sig is None:
            # Show current state for dashboard
            if STRATEGY_MODE in ("gann", "both"):
                state = self.gann_engine.get_current_state(symbol, df)
                state["strategy"] = "gann"
            else:
                state = self.engine.get_current_state(symbol, df)
                state["strategy"] = "bb"
            result["action"] = "no_signal"
            result["state"]  = state
            return result

        # ── ML filter (only for BB signals — Gann has its own filters) ──
        if STRATEGY_MODE in ("bb", "confirm") and hasattr(sig, 'squeeze_dur'):
            ml_ok, ml_conf, ml_reason = self.ml.predict(sig, df)
            if not ml_ok:
                result["action"] = "ml_filtered"
                result["reason"] = ml_reason
                return result

        # ── Position sizing ───────────────────────────────────────
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

        # ── Open trade limit ──────────────────────────────────────
        open_trades = self.db.get_open_trades()
        if len(open_trades) >= MAX_OPEN_TRADES:
            result["action"] = "max_trades_reached"
            return result

        result.update({
            "action":  "trade",
            "signal":  sig,
            "calc":    calc,
            "ml":      None,
        })
        return result


    def execute_trade(self, result: dict) -> bool:
        signal = result["signal"]
        calc   = result["calc"]
        ml     = result.get("ml")

        # Log signal to DB — normalize fields for both BB and Gann signals
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
                                           order_id="PAPER", is_paper=True)
            self._print_trade_alert(signal, calc, ml, trade_id, paper=True)
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
        # Docs: stop loss = order_type:"market_order" + stop_order_type:"stop_loss_order"
        # "stop_market_order" is NOT a valid order_type in Delta API
        self.client.place_stop_loss(
            product_id=product_id, side=sl_side, size=calc["lots"],
            stop_price=signal.sl)
        # Take profit = regular limit order with reduce_only
        self.client.place_order(
            product_id=product_id, side=sl_side, size=calc["lots"],
            order_type="limit_order", limit_price=signal.tp, reduce_only=True)

        trade_id = self.db.open_trade(sig_id, signal, calc,
                                       order_id=order_id, is_paper=False)
        self._print_trade_alert(signal, calc, ml, trade_id, paper=False)

        # Always refresh wallet after placing real order
        self.fetch_wallet()
        return True

    # -- MONITOR PAPER TRADES --───────────────────────────────────
    def monitor_trades(self):
        for trade in self.db.get_open_trades():
            if not trade.get("is_paper"):
                continue
            df = self.fetch_candles(trade["symbol"])
            if df.empty:
                continue
            price = float(df.iloc[-1]["close"])
            sl = trade["sl_price"]; tp = trade["tp_price"]
            d  = trade["direction"]
            sl_hit = (price <= sl) if d=="long" else (price >= sl)
            tp_hit = (price >= tp) if d=="long" else (price <= tp)
            if sl_hit:
                pnl = -trade["risk_usdt"]
                self.db.close_trade(trade["id"], sl, "stop_loss", -1.0, pnl)
                self.capital += pnl
                logger.info(f"[SL] Trade #{trade['id']} SL | {trade['symbol']} ${pnl:.2f}")
            elif tp_hit:
                pnl = trade["reward_usdt"] - trade["fees_usdt"]
                self.db.close_trade(trade["id"], tp, "take_profit", RR_RATIO, pnl)
                self.capital += pnl
                logger.info(f"[TP] Trade #{trade['id']} TP | {trade['symbol']} +${pnl:.2f}")

    # -- DISPLAY --────────────────────────────────────────────────
    def _print_trade_alert(self, sig, calc, ml, trade_id, paper):
        mode = "📝 PAPER" if paper else "💰 LIVE"
        icon = "🟢" if sig.direction == "long" else "🔴"
        print(f"""
{'='*65}
{mode} TRADE #{trade_id}  {icon} {sig.symbol} {sig.direction.upper()}
{'='*65}
  Signal     : {sig.reason[:80]}
  ML         : {ml.get('reason','N/A') if ml else 'N/A (Gann signal)'}

  Entry      : ${sig.entry:>12,.4f}
  Stop Loss  : ${sig.sl:>12,.4f}   risk   = ${calc['risk_usdt']:.2f}
  Take Profit: ${sig.tp:>12,.4f}   reward = ${calc['reward_usdt']:.2f}
  Liq Price  : ${calc['liq_price']:>12,.4f}   ⚠️

  Lots       : {calc['lots']}  ({calc['contracts']:.4f} {sig.symbol[:3]})
  Notional   : ${calc['notional_usdt']:>10,.2f}   ({calc['leverage']}x leverage)
  Margin Req : ${calc['margin_req']:>10,.2f}   ({calc['capital_pct']:.1f}% of capital)
  Fees + GST : ${calc['fees_usdt']:>10,.4f}
  Net R:R    : {calc['net_rr']:.2f}x
  Wallet bal : ${self.capital:>10,.2f}  (available after this trade)
{'='*65}""")

    def print_dashboard(self):
        stats    = self.db.get_trade_stats(days=30)
        open_t   = self.db.get_open_trades()
        paused, pause_reason = self._check_circuit_breaker()
        live_pos = self.get_positions_summary() if not PAPER_TRADE else []

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  BB SQUEEZE BOT  {'[PAUSED ⛔]' if paused else '[RUNNING ✅]':^20}  {datetime.now().strftime('%H:%M:%S'):>8}  ║
╠══════════════════════════════════════════════════════════════╣
║  MODE: {'PAPER 📝' if PAPER_TRADE else 'LIVE  💰':<10}  ML: {'ON ✅' if USE_ML_FILTER else 'OFF ❌'}  Lev: {LEVERAGE}x  RR: {RR_RATIO}:1  ║
╠══════════════════════════════════════════════════════════════╣
║  WALLET (live from Delta Exchange)                           ║""")

        if self.wallet:
            for asset, info in self.wallet.items():
                avail = float(info.get("available_balance", info.get("available", 0)))
                total = float(info.get("balance", avail))
                if total < 0.000001: continue
                print(f"║  {asset:<6}: total={total:>14,.4f}  free={avail:>14,.4f}  ║")
        else:
            print("║  (no wallet data — add API keys to config/settings.py)      ║")

        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  Capital available : ${self.capital:>10,.2f}  USDT                  ║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  PERFORMANCE (last 30 days)                                  ║")
        _t   = int(stats.get('total',0)    or 0)
        _w   = int(stats.get('wins',0)     or 0)
        _wr  = float(stats.get('wr',0.0)   or 0.0)
        _r   = float(stats.get('net_r',0)  or 0.0)
        _pnl = float(stats.get('pnl_usdt',0) or 0.0)
        print(f"║  Trades: {_t:<5}  Wins: {_w:<5}  WR: {_wr:>5.1f}%           ║")
        print(f"║  Net R : {_r:>+7.1f}R   PnL: ${_pnl:>+8.2f}              ║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        # Monthly trailing profit system
        mo_stat  = self.db.get_monthly_pnl_pct(self.capital)
        mo_pct   = mo_stat["pnl_pct"]
        mo_usdt  = mo_stat["pnl_usdt"]
        if self.month_trading_band:
            mo_icon  = "[LOCKED]"
            floor    = self.monthly_peak_pct - MONTHLY_TRAIL_PCT
            mo_trail = f"Profit secured >= {floor:+.1f}% — next month pe resume"
        elif self.monthly_protected:
            mo_icon  = "[TRAILING]"
            floor    = self.monthly_peak_pct - MONTHLY_TRAIL_PCT
            mo_trail = f"Peak:{self.monthly_peak_pct:+.1f}%  Floor:{floor:+.1f}%  Riding profits"
        else:
            mo_icon  = "[ACTIVE]"
            mo_trail = f"Target:+{MONTHLY_TARGET_PCT}%  Trail:{MONTHLY_TRAIL_PCT}%  Stop:-{MONTHLY_HARD_STOP_PCT}%"
        print(f"║  MONTHLY TRAILING                                            ║")
        print(f"║  {mo_icon:<10} This month: {mo_pct:>+6.2f}%  (${mo_usdt:>+7.2f})          ║")
        print(f"║  {mo_trail:<60} ║")
        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  OPEN TRADES  ({len(open_t)} paper / {len(live_pos)} live on exchange)          ║")

        for t in open_t:
            icon = "🟢" if t["direction"]=="long" else "🔴"
            print(f"║  {icon} {t['symbol']:<10} {t['direction']:<6} @ ${t['entry_price']:>10,.2f}  [PAPER]  ║")
        for p in live_pos:
            icon = "🟢" if p["direction"]=="long" else "🔴"
            print(f"║  {icon} {p['symbol']:<10} {p['direction']:<6} @ ${p['entry']:>10,.2f}  uPnL={p['upnl']:>+8.2f} ║")

        if paused:
            print(f"║  ⛔ {pause_reason[:56]:<56} ║")
        print("╚══════════════════════════════════════════════════════════════╝")

    # -- MAIN LOOP --──────────────────────────────────────────────
    def run(self):
        logger.info("="*65)
        logger.info("  BB SQUEEZE TRADING BOT")
        logger.info(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'} | "
                    f"Leverage: {LEVERAGE}x | Risk: {RISK_PER_TRADE_PCT}%/trade")
        logger.info("="*65)

        # STARTUP: wallet fetch FIRST, before anything else
        logger.info("Step 1/3: Fetching wallet balance from Delta Exchange...")
        self.fetch_wallet()
        self.print_wallet()

        logger.info("Step 2/3: Testing API connection...")
        if not self.client.test_connection():
            logger.warning("API unreachable — running in offline/paper mode")

        logger.info(f"Step 3/3: Starting main loop. Capital: ${self.capital:,.2f}")

        if self.capital <= 0:
            logger.warning(f"Wallet fetch returned 0 — using fallback ${CAPITAL_FALLBACK}")
            self.capital = CAPITAL_FALLBACK

        self.running = True
        cycle = 0
        WALLET_REFRESH_EVERY = 5  # sync wallet every 5 cycles (~5 min)

        while self.running:
            cycle += 1
            logger.info(f"\n-- Cycle #{cycle} | {datetime.now().strftime('%H:%M:%S')} "
                        f"| Capital: ${self.capital:,.2f} --")

            # Periodic wallet refresh
            if cycle % WALLET_REFRESH_EVERY == 0:
                logger.info("Refreshing wallet...")
                self.fetch_wallet()

            paused, reason = self._check_circuit_breaker()
            if paused:
                logger.warning(f"[STOP] CIRCUIT BREAKER: {reason}")
                self.print_dashboard()
                time.sleep(CHECK_INTERVAL_SEC * 5)
                continue

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
                        logger.info(f"  [SKIP] {symbol}: ML skip — {result.get('reason','')}")
                    elif action == "no_signal":
                        s = result.get("state", {})
                        strat = s.get("strategy", "bb")
                        if strat == "gann":
                            r1     = float(s.get('gann_r1') or 0)
                            s1     = float(s.get('gann_s1') or 0)
                            dist_r = float(s.get('dist_r1') or 0)
                            dist_s = float(s.get('dist_s1') or 0)
                            logger.info(
                                f"  {symbol}: ${float(s.get('price',0)):,.2f} | "
                                f"Gann R1={r1:,.0f}(+{dist_r:.2f}%) "
                                f"S1={s1:,.0f}(-{dist_s:.2f}%) | "
                                f"RSI={float(s.get('rsi',0)):.0f} | "
                                f"4H: {s.get('trend_4h','?')} | "
                                f"Vol: {float(s.get('vol_ratio',1)):.1f}x")
                        else:
                            logger.info(
                                f"  {symbol}: ${s.get('price',0):,.2f} | "
                                f"Squeeze: {'[SQUEEZE]' if s.get('bb_squeeze') else 'no'} "
                                f"({s.get('squeeze_dur',0)} bars) | "
                                f"4H: {s.get('trend_4h','?')} | "
                                f"Vol: {s.get('vol_ratio',1):.1f}x")
                    elif action == "insufficient_margin":
                        logger.warning(f"  [WARN] {symbol}: {result.get('reason','')}")
                    elif action == "max_trades_reached":
                        logger.info(f"  {symbol}: max trades open")
                    elif action == "insufficient_data":
                        logger.warning(f"  {symbol}: insufficient data")

                except Exception as e:
                    logger.error(f"Error on {symbol}: {e}", exc_info=True)

            if cycle % 10 == 0:
                self.print_dashboard()

            time.sleep(CHECK_INTERVAL_SEC)

    def run_once(self):
        logger.info("--- Single scan ---")
        logger.info("Fetching wallet...")
        self.fetch_wallet()
        self.print_wallet()
        for symbol in PRODUCTS:
            if not PRODUCTS[symbol]["active"]: continue
            result = self.process_symbol(symbol)
            logger.info(f"{symbol}: {result['action']}")
            if result.get("signal"):
                PositionSizer.print_trade_card(result["calc"])
            elif result.get("state"):
                s = result["state"]
                logger.info(f"  ${s.get('price',0):,.2f} | "
                            f"Squeeze={s.get('bb_squeeze')} | "
                            f"Trend={s.get('trend_4h','?')}")
        if self.capital <= 0:
            logger.warning(f"Wallet returned 0 — using fallback ${CAPITAL_FALLBACK}")
            self.capital = CAPITAL_FALLBACK
        logger.info(f"30d stats: {self.db.get_trade_stats()}")

    def stop(self):
        self.running = False
        logger.info("Bot stopped.")


# -- ENTRYPOINT --──────────────────────────────────────────────────
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
        # Discover actual product IDs from the exchange
        print("Fetching available products from exchange...")
        found = bot.client.discover_product_ids()
        print(f"Found {len(found)} BTC/ETH/SOL products:")
        for sym, info in sorted(found.items()):
            print(f"  {sym:<15} id={info['product_id']:<8} ",
                  f"tick={info['tick_size']:<8} lot={info['contract_value']}")
        print("\nUpdate PRODUCTS in config/settings.py with correct product_ids!")