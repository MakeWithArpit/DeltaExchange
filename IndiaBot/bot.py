"""
╔══════════════════════════════════════════════════════════════════╗
║   INDIA STOCK TRADING BOT                                        ║
║   Strategy 1: Intraday — TCS BB Squeeze, MARUTI SR Breakout      ║
║   Strategy 2: Pairs Arb — BAJFIN/KOTAK, ICICI/SBIN               ║
║   Broker: Dhann API | Mode: Paper Trading                        ║
║                                                                  ║
║   Commands:                                                      ║
║     python bot.py --mode run       → Start bot                   ║
║     python bot.py --mode once      → Single scan                 ║
║     python bot.py --mode dashboard → Show stats                  ║
║     python bot.py --mode balance   → Check account balance       ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import time, logging, os, argparse
from datetime import datetime
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from config.settings import *
from core.dhann_client     import DhannClient
from core.intraday_strategy import IntradayEngine
from core.pairs_strategy    import PairsEngine
from core.position_sizer    import PositionSizer
from data.database          import Database

# ── LOGGING ──────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger(__name__)


class IndiaBot:

    def __init__(self):
        self.client   = DhannClient(DHANN_CLIENT_ID, DHANN_ACCESS_TOKEN)
        self.intraday = IntradayEngine()
        self.pairs    = PairsEngine()
        self.db       = Database(DB_PATH)
        self.capital  = float(TOTAL_CAPITAL)
        self.running  = False
        self._candle_cache = {}    # symbol → df

    # ── MARKET HOURS CHECK ────────────────────────────────────────
    def _is_market_open(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5: return False   # Weekend
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= MARKET_CLOSE

    def _can_enter_new(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5: return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= NO_NEW_TRADE_AFTER

    def _should_squareoff(self) -> bool:
        t = datetime.now().strftime("%H:%M")
        return t >= SQUARE_OFF_TIME

    # ── CAPITAL CHECK ─────────────────────────────────────────────
    def _check_daily_loss(self) -> bool:
        """Returns True if daily loss limit hit → stop trading."""
        today_pnl = self.db.get_today_pnl()
        loss = abs(min(0, today_pnl["total"]))
        loss_pct = loss / self.capital * 100
        if loss_pct >= MAX_DAILY_LOSS_PCT:
            logger.warning(f"[STOP] Daily loss {loss_pct:.1f}% >= {MAX_DAILY_LOSS_PCT}% limit")
            return True
        return False

    # ── DATA FETCHING ─────────────────────────────────────────────
    def _get_candles(self, symbol: str,
                     security_id: str = None) -> pd.DataFrame:
        """
        Fetch 1H candles. Uses Dhann API first, falls back to yfinance.
        Caches per symbol.
        """
        # Try Dhann first (if API keys configured)
        if DHANN_CLIENT_ID != "YOUR_CLIENT_ID" and security_id:
            df = self.client.get_candles(security_id, interval="60", days=60)
            if not df.empty:
                self._candle_cache[symbol] = df
                return df

        # Fallback: yfinance
        cached = self._candle_cache.get(symbol)
        if cached is not None and len(cached) > 0:
            # Refresh if older than 5 min
            last_dt = pd.to_datetime(cached["datetime"].iloc[-1])
            if (datetime.now() - last_dt).seconds < 300:
                return cached

        df = self.client.get_candles_yfinance(symbol, interval="1h", period="60d")
        if not df.empty:
            self._candle_cache[symbol] = df
        return df

    # ── BALANCE ───────────────────────────────────────────────────
    def fetch_balance(self) -> float:
        if DHANN_CLIENT_ID == "YOUR_CLIENT_ID":
            logger.info(f"  [Paper] Capital: Rs {self.capital:,.2f}")
            return self.capital
        cap = self.client.get_available_capital()
        if cap > 0:
            self.capital = cap
            logger.info(f"  Dhann balance: Rs {cap:,.2f}")
        return cap

    # ──────────────────────────────────────────────────────────────
    # INTRADAY LOGIC
    # ──────────────────────────────────────────────────────────────

    def _run_intraday(self):
        """Scan all intraday stocks for signals."""
        if not self._can_enter_new():
            logger.info("  [Intraday] No new entries after 15:00")
            return

        open_trades = self.db.get_open_intraday()
        if len(open_trades) >= MAX_OPEN_TRADES:
            logger.info(f"  [Intraday] Max trades open ({MAX_OPEN_TRADES})")
            return

        for name, stock in INTRADAY_STOCKS.items():
            if not stock.get("active"): continue

            df = self._get_candles(name, stock.get("security_id"))
            if df.empty or len(df) < 30:
                logger.warning(f"  [Intraday] No data for {name}")
                continue

            sig = self.intraday.check_signal(name, stock["strategy"], df)

            if sig is None:
                state = self.intraday.get_state(name, df)
                logger.info(
                    f"  {name}: Rs {state.get('price', 0):,.2f} | "
                    f"RSI={state.get('rsi', 0):.0f} | "
                    f"Vol={state.get('vol_ratio', 0):.1f}x | "
                    f"Squeeze={'YES' if state.get('bb_squeeze') else 'no'} | "
                    f"Trend={'UP' if state.get('ema200_bull') else 'DOWN'}"
                )
                continue

            # Position size
            calc = PositionSizer.calculate(
                entry   = sig.entry,
                sl      = sig.sl,
                capital = INTRADAY_CAPITAL,
            )
            if "error" in calc:
                logger.warning(f"  [Intraday] {name}: {calc['error']}")
                continue

            self._execute_intraday(sig, calc)

    def _execute_intraday(self, sig, calc):
        if PAPER_TRADE:
            trade_id = self.db.open_intraday(
                symbol=sig.symbol, strategy=sig.strategy,
                direction=sig.direction,
                entry=sig.entry, sl=sig.sl, tp=sig.tp,
                shares=calc["shares"], notional=calc["notional"],
                risk=calc["risk_inr"], fees=calc["fees_inr"],
                is_paper=True,
            )
            self._print_trade(sig, calc, trade_id)
            return

        # Live order via Dhann
        stock    = INTRADAY_STOCKS.get(sig.symbol, {})
        sec_id   = stock.get("security_id", "")
        exchange = stock.get("exchange", "NSE")
        side     = "BUY" if sig.direction == "long" else "SELL"

        order = self.client.place_order(
            security_id=sec_id, exchange=exchange,
            transaction_type=side,
            quantity=calc["shares"],
            order_type="MARKET",
        )
        if not order:
            logger.error(f"  Order failed for {sig.symbol}")
            return

        # Place SL order
        sl_side = "SELL" if sig.direction == "long" else "BUY"
        self.client.place_sl_order(
            security_id=sec_id, exchange=exchange,
            transaction_type=sl_side,
            quantity=calc["shares"],
            trigger_price=sig.sl,
        )

        trade_id = self.db.open_intraday(
            symbol=sig.symbol, strategy=sig.strategy,
            direction=sig.direction,
            entry=sig.entry, sl=sig.sl, tp=sig.tp,
            shares=calc["shares"], notional=calc["notional"],
            risk=calc["risk_inr"], fees=calc["fees_inr"],
            is_paper=False,
        )
        self._print_trade(sig, calc, trade_id)

    def _print_trade(self, sig, calc, trade_id):
        mode = "PAPER" if PAPER_TRADE else "LIVE"
        logger.info(f"""
{'='*60}
[{mode}] INTRADAY #{trade_id} | {sig.symbol} {sig.direction.upper()}
{'='*60}
  Strategy : {sig.strategy}
  Signal   : {sig.reason}
  Entry    : Rs {sig.entry:>10,.2f}
  Stop Loss: Rs {sig.sl:>10,.2f}   Risk = Rs {calc['risk_inr']:.2f}
  Target   : Rs {sig.tp:>10,.2f}   Reward = Rs {calc['risk_inr']*2:.2f}
  Shares   : {calc['shares']:>10}
  Notional : Rs {calc['notional']:>10,.2f}
  Fees     : Rs {calc['fees_inr']:>10,.2f}
{'='*60}""")

    # ── INTRADAY MONITOR ──────────────────────────────────────────
    def _monitor_intraday(self):
        """Check SL/TP for open paper intraday trades."""
        for trade in self.db.get_open_intraday():
            if not trade["is_paper"]: continue

            df = self._get_candles(trade["symbol"])
            if df is None or df.empty: continue

            price = float(df.iloc[-1]["close"])
            sl = trade["sl_price"]; tp = trade["tp_price"]
            d  = trade["direction"]

            sl_hit = price <= sl if d == "long" else price >= sl
            tp_hit = price >= tp if d == "long" else price <= tp

            if sl_hit:
                pnl = -trade["risk_inr"] - trade["fees_inr"]
                self.db.close_intraday(trade["id"], sl, "stop_loss", pnl)
                logger.info(f"  [SL] Intraday #{trade['id']} {trade['symbol']} Rs {pnl:.2f}")

            elif tp_hit:
                pnl = trade["risk_inr"] * 2 - trade["fees_inr"]
                self.db.close_intraday(trade["id"], tp, "take_profit", pnl)
                logger.info(f"  [TP] Intraday #{trade['id']} {trade['symbol']} +Rs {pnl:.2f}")

    def _squareoff_all_intraday(self):
        """Force close all open intraday positions at EOD."""
        for trade in self.db.get_open_intraday():
            df = self._get_candles(trade["symbol"])
            price = float(df.iloc[-1]["close"]) if not df.empty else trade["entry_price"]
            direction = trade["direction"]
            raw = (price - trade["entry_price"]) * trade["shares"]
            if direction == "short": raw = -raw
            pnl = raw - trade["fees_inr"]
            self.db.close_intraday(trade["id"], price, "eod_squareoff", pnl)
            logger.info(f"  [EOD] Squared off {trade['symbol']} @ Rs {price:.2f} | PnL: Rs {pnl:.2f}")

    # ──────────────────────────────────────────────────────────────
    # PAIRS ARBITRAGE LOGIC
    # ──────────────────────────────────────────────────────────────

    def _run_pairs(self):
        """Scan all pairs for arbitrage signals."""
        for pair in PAIRS:
            if not pair.get("active"): continue

            pname  = pair["name"]
            stock1 = pair["stock1"]
            stock2 = pair["stock2"]

            df1 = self._get_candles(stock1, pair.get("sec_id1"))
            df2 = self._get_candles(stock2, pair.get("sec_id2"))

            if df1.empty or df2.empty:
                logger.warning(f"  [Pairs] No data for {pname}")
                continue

            sig = self.pairs.check_signal(pname, stock1, stock2, df1, df2)

            # Dashboard state
            state = self.pairs.get_state(pname, df1, df2)
            in_trade = state.get("in_trade", False)

            if sig is None:
                logger.info(
                    f"  {pname}: zscore={state.get('zscore',0):+.2f} | "
                    f"{'IN TRADE' if in_trade else 'watching'}"
                )
                continue

            if sig.action == "EXIT":
                self._execute_pairs_exit(sig, pname)
            elif sig.action in ("ENTER_SHORT_S1", "ENTER_LONG_S1"):
                if self._can_enter_new():
                    self._execute_pairs_entry(sig, df1, df2)

    def _execute_pairs_entry(self, sig, df1, df2):
        price1 = float(df1.iloc[-1]["close"])
        price2 = float(df2.iloc[-1]["close"])
        calc   = PositionSizer.calculate_pairs(PAIRS_CAPITAL, price1, price2)

        if PAPER_TRADE:
            trade_id = self.db.open_pairs(
                pair_name=sig.pair_name, stock1=sig.stock1, stock2=sig.stock2,
                action=sig.action,
                entry_zscore=sig.zscore, entry_spread=sig.spread,
                shares1=calc["shares1"], shares2=calc["shares2"],
                notional1=calc["notional1"], notional2=calc["notional2"],
                fees=calc["fees_inr"], is_paper=True,
            )
            self.pairs.register_entry(sig.pair_name, sig, sig.spread)
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

        # Live: place two orders simultaneously
        pair_cfg = next((p for p in PAIRS if p["name"] == sig.pair_name), {})
        if sig.action == "ENTER_SHORT_S1":
            # Short stock1, Long stock2
            self.client.place_order(pair_cfg["sec_id1"], "NSE", "SELL",
                                    calc["shares1"], "MARKET")
            self.client.place_order(pair_cfg["sec_id2"], "NSE", "BUY",
                                    calc["shares2"], "MARKET")
        else:
            # Long stock1, Short stock2
            self.client.place_order(pair_cfg["sec_id1"], "NSE", "BUY",
                                    calc["shares1"], "MARKET")
            self.client.place_order(pair_cfg["sec_id2"], "NSE", "SELL",
                                    calc["shares2"], "MARKET")

        self.db.open_pairs(
            pair_name=sig.pair_name, stock1=sig.stock1, stock2=sig.stock2,
            action=sig.action,
            entry_zscore=sig.zscore, entry_spread=sig.spread,
            shares1=calc["shares1"], shares2=calc["shares2"],
            notional1=calc["notional1"], notional2=calc["notional2"],
            fees=calc["fees_inr"], is_paper=False,
        )
        self.pairs.register_entry(sig.pair_name, sig, sig.spread)

    def _execute_pairs_exit(self, sig, pair_name):
        """Close pairs trade and calculate P&L."""
        open_pairs = self.db.get_open_pairs()
        trade = next((t for t in open_pairs
                      if t["pair_name"] == pair_name), None)
        if not trade: return

        entry_spread = trade["entry_spread"]
        exit_spread  = sig.spread
        spread_chg   = exit_spread - entry_spread

        if trade["action"] == "ENTER_SHORT_S1":
            pnl_spread = -spread_chg   # profit when spread falls
        else:
            pnl_spread = spread_chg    # profit when spread rises

        pnl_inr = pnl_spread * PAIRS_CAPITAL - trade["fees_inr"]

        self.db.close_pairs(
            trade["id"], sig.zscore, sig.spread,
            sig.reason, pnl_inr
        )
        self.pairs.register_exit(pair_name)
        status = "+Rs" if pnl_inr >= 0 else "-Rs"
        logger.info(f"  [Pairs EXIT] {pair_name} | zscore={sig.zscore:+.2f} | "
                    f"PnL: {status}{abs(pnl_inr):.2f}")

    # ──────────────────────────────────────────────────────────────
    # DASHBOARD
    # ──────────────────────────────────────────────────────────────

    def print_dashboard(self):
        stats     = self.db.get_stats()
        today_pnl = self.db.get_today_pnl()
        it_open   = self.db.get_open_intraday()
        pt_open   = self.db.get_open_pairs()
        it = stats["intraday"]; pt = stats["pairs"]
        today_pct = today_pnl["total"] / self.capital * 100

        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  INDIA TRADING BOT  {'[PAPER]' if PAPER_TRADE else '[LIVE] '}   {datetime.now().strftime('%d-%m-%Y %H:%M')}               ║
╠══════════════════════════════════════════════════════════════╣
║  Capital: Rs {self.capital:>10,.2f}  |  Today: {today_pct:>+6.2f}% (Rs {today_pnl['total']:>+8.2f})     ║
╠══════════════════════════════════════════════════════════════╣
║  INTRADAY PERFORMANCE                                        ║
║  Trades:{it['total']:<5}  Wins:{it['wins']:<5}  WR:{it['wr']:>5.1f}%                         ║
║  PnL: Rs{it['pnl']:>+10,.2f}  Fees: Rs{it['fees']:>8,.2f}                         ║
╠══════════════════════════════════════════════════════════════╣
║  PAIRS ARBITRAGE PERFORMANCE                                 ║
║  Trades:{pt['total']:<5}  Wins:{pt['wins']:<5}  WR:{pt['wr']:>5.1f}%                         ║
║  PnL: Rs{pt['pnl']:>+10,.2f}  Fees: Rs{pt['fees']:>8,.2f}                         ║
╠══════════════════════════════════════════════════════════════╣
║  OPEN INTRADAY ({len(it_open)})                                           ║""")

        for t in it_open:
            print(f"║  {'L' if t['direction']=='long' else 'S'} {t['symbol']:<10} "
                  f"@ Rs {t['entry_price']:>8,.2f}  [{t['strategy']}]                   ║")

        print(f"╠══════════════════════════════════════════════════════════════╣")
        print(f"║  OPEN PAIRS ({len(pt_open)})                                              ║")
        for t in pt_open:
            print(f"║  {t['pair_name']:<20} zscore_entry={t['entry_zscore']:>+6.2f}                     ║")

        print("╚══════════════════════════════════════════════════════════════╝")

    # ──────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────

    def run(self):
        logger.info("=" * 60)
        logger.info("  INDIA TRADING BOT STARTED")
        logger.info(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'}")
        logger.info(f"  Capital: Rs {self.capital:,.2f}")
        logger.info(f"  Intraday: {[s for s in INTRADAY_STOCKS if INTRADAY_STOCKS[s]['active']]}")
        logger.info(f"  Pairs:    {[p['name'] for p in PAIRS if p.get('active')]}")
        logger.info("=" * 60)

        self.fetch_balance()

        if DHANN_CLIENT_ID != "YOUR_CLIENT_ID":
            self.client.test_connection()
        else:
            logger.info("API keys not set — using yfinance for data (paper mode only)")

        self.running = True
        cycle = 0

        while self.running:
            cycle += 1
            now_str = datetime.now().strftime("%H:%M:%S")
            logger.info(f"\n-- Cycle #{cycle} | {now_str} | "
                        f"Rs {self.capital:,.2f} --")

            if not self._is_market_open():
                logger.info("  Market closed — waiting...")
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # Daily loss circuit breaker
            if self._check_daily_loss():
                logger.warning("  Daily loss limit hit — no new trades today")
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # EOD square off
            if self._should_squareoff():
                logger.info("  EOD: Squaring off all intraday positions")
                self._squareoff_all_intraday()
                time.sleep(CHECK_INTERVAL_SEC)
                continue

            # Monitor existing trades
            self._monitor_intraday()

            # Scan for new signals
            logger.info("  -- Intraday Scan --")
            self._run_intraday()

            logger.info("  -- Pairs Arbitrage Scan --")
            self._run_pairs()

            # Dashboard every 6 cycles (~30 min)
            if cycle % 6 == 0:
                self.print_dashboard()

            time.sleep(CHECK_INTERVAL_SEC)

    def run_once(self):
        """Single scan — useful for testing."""
        logger.info("--- Single scan ---")
        self.fetch_balance()
        logger.info("Intraday scan:")
        self._run_intraday()
        logger.info("Pairs scan:")
        self._run_pairs()
        self.print_dashboard()

    def stop(self):
        self.running = False
        logger.info("Bot stopped.")


# ── ENTRYPOINT ───────────────────────────────────────────────────
if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode",
                   choices=["run", "once", "dashboard", "balance"],
                   default="once")
    args = p.parse_args()

    bot = IndiaBot()

    if args.mode == "run":
        try:
            bot.run()
        except KeyboardInterrupt:
            bot.stop()
    elif args.mode == "once":
        bot.run_once()
    elif args.mode == "dashboard":
        bot.fetch_balance()
        bot.print_dashboard()
    elif args.mode == "balance":
        bal = bot.fetch_balance()
        print(f"Available balance: Rs {bal:,.2f}")
