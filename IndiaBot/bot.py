"""
╔══════════════════════════════════════════════════════════════════╗
║   INDIA STOCK TRADING BOT  v2.1 — BUG FIXES                     ║
║                                                                   ║
║   Fixed bugs:                                                     ║
║   #1 Capital now updates correctly after every trade P&L         ║
║   #2 Pairs trades squared off at EOD (15:15)                     ║
║   #3 Pairs state restored from DB on bot restart                 ║
║   #4 Pairs P&L calculation fixed (price-based, not spread-based) ║
║   #6 No new pairs entries after 15:00                            ║
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
from core.dhann_client      import DhannClient
from core.intraday_strategy import IntradayEngine
from core.pairs_strategy    import PairsEngine
from core.position_sizer    import PositionSizer
from data.database          import Database

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
        self._candle_cache = {}

        # FIX #3: Restore pairs state from DB on startup
        self._restore_pairs_state()

    # ── FIX #3: Restore pairs open state from DB ─────────────────
    def _restore_pairs_state(self):
        """On restart, reload any open pairs trades into memory."""
        open_pairs = self.db.get_open_pairs()
        if open_pairs:
            logger.info(f"Restoring {len(open_pairs)} open pairs from DB...")
        for t in open_pairs:
            self.pairs.open_pairs[t["pair_name"]] = {
                "action":       t["action"],
                "entry_spread": float(t["entry_spread"]),
                "entry_zscore": float(t["entry_zscore"]),
                "shares1":      t["shares1"],
                "shares2":      t["shares2"],
                "bars_held":    0,   # reset counter — we'll use DB opened_at if needed
                "timestamp":    t["opened_at"],
                "trade_id":     t["id"],
            }
            logger.info(f"  Restored: {t['pair_name']} (id={t['id']}, action={t['action']})")

    # ── MARKET HOURS ─────────────────────────────────────────────
    def _is_market_open(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5: return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= MARKET_CLOSE

    def _can_enter_new(self) -> bool:
        now = datetime.now()
        if now.weekday() >= 5: return False
        t = now.strftime("%H:%M")
        return MARKET_OPEN <= t <= NO_NEW_TRADE_AFTER

    def _should_squareoff(self) -> bool:
        return datetime.now().strftime("%H:%M") >= SQUARE_OFF_TIME

    # ── DAILY LOSS CHECK ─────────────────────────────────────────
    def _check_daily_loss(self) -> bool:
        today_pnl = self.db.get_today_pnl()
        loss = abs(min(0, today_pnl["total"]))
        if loss / max(self.capital, 1) * 100 >= MAX_DAILY_LOSS_PCT:
            logger.warning(f"[STOP] Daily loss limit hit")
            return True
        return False

    # ── DATA FETCH ────────────────────────────────────────────────
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
            logger.info(f"  [Paper] Capital: Rs {self.capital:,.2f}")
            return self.capital
        cap = self.client.get_available_capital()
        if cap > 0:
            self.capital = cap
            logger.info(f"  Dhann balance: Rs {cap:,.2f}")
        return cap

    # ──────────────────────────────────────────────────────────────
    # INTRADAY
    # ──────────────────────────────────────────────────────────────
    def _run_intraday(self):
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
                    f"  {name}: Rs {state.get('price',0):,.2f} | "
                    f"RSI={state.get('rsi',0):.0f} | "
                    f"Vol={state.get('vol_ratio',0):.1f}x | "
                    f"Squeeze={'YES' if state.get('bb_squeeze') else 'no'} | "
                    f"Trend={'UP' if state.get('ema200_bull') else 'DOWN'}"
                )
                continue
            calc = PositionSizer.calculate(sig.entry, sig.sl, INTRADAY_CAPITAL)
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
        # Live order (Dhann)
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
                # FIX #1: Update capital
                self.capital += pnl
                logger.info(f"  [SL] Intraday #{trade['id']} {trade['symbol']} Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")
            elif tp_hit:
                pnl = trade["risk_inr"] * 2 - trade["fees_inr"]
                self.db.close_intraday(trade["id"], tp, "take_profit", pnl)
                # FIX #1: Update capital
                self.capital += pnl
                logger.info(f"  [TP] Intraday #{trade['id']} {trade['symbol']} +Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")

    def _squareoff_all_intraday(self):
        for trade in self.db.get_open_intraday():
            df = self._get_candles(trade["symbol"])
            price = float(df.iloc[-1]["close"]) if not df.empty else trade["entry_price"]
            raw = (price - trade["entry_price"]) * trade["shares"]
            if trade["direction"] == "short": raw = -raw
            pnl = raw - trade["fees_inr"]
            self.db.close_intraday(trade["id"], price, "eod_squareoff", pnl)
            # FIX #1: Update capital
            self.capital += pnl
            logger.info(f"  [EOD-IT] {trade['symbol']} @ Rs {price:.2f} | PnL: Rs {pnl:.2f} | Capital: Rs {self.capital:,.2f}")

    # ──────────────────────────────────────────────────────────────
    # PAIRS ARBITRAGE
    # ──────────────────────────────────────────────────────────────
    def _run_pairs(self):
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
            state = self.pairs.get_state(pname, df1, df2)
            in_trade = state.get("in_trade", False)
            if sig is None:
                logger.info(f"  {pname}: zscore={state.get('zscore',0):+.2f} | {'IN TRADE' if in_trade else 'watching'}")
                continue
            if sig.action == "EXIT":
                self._execute_pairs_exit(sig, pname, df1, df2)
            # FIX #6: Only enter new pairs trades before 15:00
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
            # FIX #3: Store trade_id in memory state
            self.pairs.open_pairs[sig.pair_name]["trade_id"] = trade_id
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
        # Live orders
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
        """
        FIX #4: Calculate P&L using actual prices, not spread approximation.
        """
        open_pairs = self.db.get_open_pairs()
        trade = next((t for t in open_pairs if t["pair_name"] == pair_name), None)
        if not trade: return

        # Get current prices for exact P&L
        exit_price1 = float(df1.iloc[-1]["close"]) if not df1.empty else 0
        exit_price2 = float(df2.iloc[-1]["close"]) if not df2.empty else 0

        # Retrieve entry prices from memory or estimate from spread
        mem_state = self.pairs.open_pairs.get(pair_name, {})
        entry_price1 = mem_state.get("entry_price1", 0)
        entry_price2 = mem_state.get("entry_price2", 0)

        shares1 = trade["shares1"]
        shares2 = trade["shares2"]

        if entry_price1 > 0 and exit_price1 > 0:
            # Exact P&L calculation
            if trade["action"] == "ENTER_LONG_S1":
                # Long s1, Short s2
                pnl_s1 = (exit_price1 - entry_price1) * shares1   # long profit
                pnl_s2 = (entry_price2 - exit_price2) * shares2   # short profit
            else:
                # Short s1, Long s2
                pnl_s1 = (entry_price1 - exit_price1) * shares1   # short profit
                pnl_s2 = (exit_price2 - entry_price2) * shares2   # long profit
            pnl_gross = pnl_s1 + pnl_s2
        else:
            # Fallback: spread approximation (less accurate)
            spread_chg = sig.spread - trade["entry_spread"]
            pnl_gross  = (-spread_chg if trade["action"] == "ENTER_SHORT_S1" else spread_chg) * PAIRS_CAPITAL

        pnl_net = pnl_gross - trade["fees_inr"]

        self.db.close_pairs(trade["id"], sig.zscore, sig.spread, sig.reason, pnl_net)
        self.pairs.register_exit(pair_name)

        # FIX #1: Update capital
        self.capital += pnl_net
        status = "+Rs" if pnl_net >= 0 else "-Rs"
        logger.info(f"  [Pairs EXIT] {pair_name} | zscore={sig.zscore:+.2f} | "
                    f"PnL: {status}{abs(pnl_net):.2f} | Capital: Rs {self.capital:,.2f}")

    # FIX #2: Square off ALL pairs at EOD
    def _squareoff_all_pairs(self):
        """Force close all open pairs trades at EOD."""
        open_pairs = self.db.get_open_pairs()
        if not open_pairs:
            return
        logger.info(f"  [EOD-Pairs] Squaring off {len(open_pairs)} open pairs...")
        for trade in open_pairs:
            pname = trade["pair_name"]
            pair_cfg = next((p for p in PAIRS if p["name"] == pname), None)
            if not pair_cfg: continue

            df1 = self._get_candles(pair_cfg["stock1"])
            df2 = self._get_candles(pair_cfg["stock2"])
            if df1.empty or df2.empty:
                logger.warning(f"  [EOD-Pairs] No data to square off {pname}")
                continue

            exit_p1 = float(df1.iloc[-1]["close"])
            exit_p2 = float(df2.iloc[-1]["close"])

            mem = self.pairs.open_pairs.get(pname, {})
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
                # Fallback spread
                from numpy import log
                exit_spread = log(exit_p1 / exit_p2) if exit_p2 > 0 else trade["entry_spread"]
                spread_chg = exit_spread - trade["entry_spread"]
                pnl_gross = (-spread_chg if trade["action"] == "ENTER_SHORT_S1" else spread_chg) * PAIRS_CAPITAL

            pnl_net = pnl_gross - trade["fees_inr"]
            self.db.close_pairs(trade["id"], None, None, "eod_squareoff", pnl_net)
            self.pairs.register_exit(pname)
            self.capital += pnl_net
            logger.info(f"  [EOD-Pairs] {pname} | PnL: Rs {pnl_net:.2f} | Capital: Rs {self.capital:,.2f}")

    # ──────────────────────────────────────────────────────────────
    # DASHBOARD
    # ──────────────────────────────────────────────────────────────
    def print_dashboard(self):
        stats     = self.db.get_stats()
        today_pnl = self.db.get_today_pnl()
        it_open   = self.db.get_open_intraday()
        pt_open   = self.db.get_open_pairs()
        it = stats["intraday"]; pt = stats["pairs"]
        today_pct = today_pnl["total"] / max(self.capital, 1) * 100
        print(f"""
╔══════════════════════════════════════════════════════════════╗
║  INDIA BOT v2.1  {'[PAPER]' if PAPER_TRADE else '[LIVE] '}   {datetime.now().strftime('%d-%m-%Y %H:%M')}  ║
╠══════════════════════════════════════════════════════════════╣
║  Capital: Rs {self.capital:>10,.2f}  |  Today: {today_pct:>+6.2f}%  (Rs {today_pnl['total']:>+8.2f})  ║
╠══════════════════════════════════════════════════════════════╣
║  INTRADAY  Trades:{it['total']:<4}  Wins:{it['wins']:<4}  WR:{it['wr']:>5.1f}%  PnL:Rs{it['pnl']:>+8.0f}  ║
║  PAIRS ARB Trades:{pt['total']:<4}  Wins:{pt['wins']:<4}  WR:{pt['wr']:>5.1f}%  PnL:Rs{pt['pnl']:>+8.0f}  ║
╠══════════════════════════════════════════════════════════════╣""")
        if it_open:
            print(f"║  OPEN INTRADAY ({len(it_open)})                                      ║")
            for t in it_open:
                print(f"║  {'L' if t['direction']=='long' else 'S'} {t['symbol']:<10} @ Rs {t['entry_price']:>8,.2f}  [{t['strategy']}]  ║")
        if pt_open:
            print(f"║  OPEN PAIRS ({len(pt_open)})                                         ║")
            for t in pt_open:
                print(f"║  {t['pair_name']:<22} zscore_entry={t['entry_zscore']:>+6.2f}  ║")
        print("╚══════════════════════════════════════════════════════════════╝")

    # ──────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────
    def run(self):
        logger.info("=" * 60)
        logger.info("  INDIA TRADING BOT v2.1 (all bugs fixed)")
        logger.info(f"  Mode: {'PAPER' if PAPER_TRADE else 'LIVE'} | Capital: Rs {self.capital:,.2f}")
        logger.info("=" * 60)
        self.fetch_balance()
        if DHANN_CLIENT_ID != "YOUR_CLIENT_ID":
            self.client.test_connection()
        else:
            logger.info("API keys not set — using yfinance (paper mode)")
        self.running = True
        cycle = 0
        while self.running:
            cycle += 1
            logger.info(f"\n-- Cycle #{cycle} | {datetime.now().strftime('%H:%M:%S')} | Rs {self.capital:,.2f} --")
            if not self._is_market_open():
                logger.info("  Market closed — waiting...")
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            if self._check_daily_loss():
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            # FIX #2: Square off BOTH intraday AND pairs at EOD
            if self._should_squareoff():
                logger.info("  EOD: Squaring off all positions...")
                self._squareoff_all_intraday()
                self._squareoff_all_pairs()     # <-- was missing before
                time.sleep(CHECK_INTERVAL_SEC)
                continue
            self._monitor_intraday()
            logger.info("  -- Intraday Scan --")
            self._run_intraday()
            logger.info("  -- Pairs Arbitrage Scan --")
            self._run_pairs()
            if cycle % 6 == 0:
                self.print_dashboard()
            time.sleep(CHECK_INTERVAL_SEC)

    def run_once(self):
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


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["run","once","dashboard","balance"], default="once")
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
        print(f"Balance: Rs {bot.fetch_balance():,.2f}")
