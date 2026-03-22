"""
Unified Database Manager
Handles: Crypto trades (trailing SL, partial TP) + India intraday + Pairs arbitrage
"""
import sqlite3, logging, os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class Database:

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.db_path = db_path
        self._init_tables()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_tables(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS candles (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol  TEXT NOT NULL,
                time    TEXT NOT NULL,
                open    REAL, high REAL, low REAL, close REAL, volume REAL,
                UNIQUE(symbol, time)
            );
            CREATE TABLE IF NOT EXISTS signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT, direction TEXT,
                confidence  REAL, squeeze_dur INTEGER,
                breakout_str REAL, vol_ratio REAL,
                trend_4h    TEXT, reason TEXT, timestamp TEXT,
                acted_on    INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id    INTEGER,
                symbol       TEXT, direction TEXT,
                entry_price  REAL, sl_price REAL, tp_price REAL,
                tp1_price    REAL DEFAULT 0,
                trail_sl     REAL DEFAULT 0,
                trail_active INTEGER DEFAULT 0,
                partial_done INTEGER DEFAULT 0,
                atr          REAL DEFAULT 0,
                lot_size     REAL, contracts REAL, notional REAL,
                leverage     INTEGER, margin_req REAL,
                risk_usdt    REAL, reward_usdt REAL, fees_usdt REAL,
                status       TEXT DEFAULT 'open',
                exit_price   REAL, exit_reason TEXT,
                pnl_r        REAL, pnl_usdt REAL,
                order_id     TEXT,
                is_paper     INTEGER DEFAULT 1,
                opened_at    TEXT, closed_at TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE TABLE IF NOT EXISTS performance (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT UNIQUE,
                trades      INTEGER DEFAULT 0,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0,
                net_r       REAL DEFAULT 0,
                pnl_usdt    REAL DEFAULT 0,
                fees_usdt   REAL DEFAULT 0,
                capital_end REAL
            );
            CREATE INDEX IF NOT EXISTS idx_candles_sym_time ON candles(symbol, time);
            CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades(status);
            """)
            # Migrate: add columns if missing (safe to run multiple times)
            for col, defn in [
                ("tp1_price",    "REAL DEFAULT 0"),
                ("trail_sl",     "REAL DEFAULT 0"),
                ("trail_active", "INTEGER DEFAULT 0"),
                ("partial_done", "INTEGER DEFAULT 0"),
                ("atr",          "REAL DEFAULT 0"),
            ]:
                try:
                    c.execute(f"ALTER TABLE trades ADD COLUMN {col} {defn}")
                except Exception:
                    pass
        logger.debug(f"Crypto DB ready: {self.db_path}")

    # ── CANDLES ──────────────────────────────────────────────────
    def upsert_candles(self, symbol: str, candles: list):
        with self._conn() as c:
            c.executemany("""
                INSERT OR REPLACE INTO candles (symbol,time,open,high,low,close,volume)
                VALUES (?,?,?,?,?,?,?)
            """, [(symbol, str(r["time"]), r["open"], r["high"],
                   r["low"], r["close"], r["volume"]) for r in candles])

    def get_candles(self, symbol: str, limit: int = 200) -> list:
        with self._conn() as c:
            rows = c.execute("""
                SELECT time,open,high,low,close,volume FROM candles
                WHERE symbol=? ORDER BY time DESC LIMIT ?
            """, (symbol, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── SIGNALS ──────────────────────────────────────────────────
    def log_signal(self, sig: dict) -> int:
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO signals (symbol,direction,confidence,squeeze_dur,
                breakout_str,vol_ratio,trend_4h,reason,timestamp,acted_on)
                VALUES (?,?,?,?,?,?,?,?,?,1)
            """, (sig.get("symbol"), sig.get("direction"), sig.get("confidence"),
                  sig.get("squeeze_dur", 0), sig.get("breakout_str", 0),
                  sig.get("vol_ratio", 1), sig.get("trend_4h"),
                  sig.get("reason"), sig.get("timestamp")))
            return cur.lastrowid

    # ── TRADES ───────────────────────────────────────────────────
    def open_trade(self, signal_id, signal, calc, order_id,
                   is_paper=True, tp1_price=None, atr=0) -> int:
        now = datetime.now().isoformat()
        tp1 = tp1_price or calc.get("tp_price", signal.tp)
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO trades (signal_id,symbol,direction,entry_price,sl_price,
                tp_price,tp1_price,trail_sl,trail_active,partial_done,atr,
                lot_size,contracts,notional,leverage,margin_req,
                risk_usdt,reward_usdt,fees_usdt,order_id,is_paper,opened_at)
                VALUES (?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (signal_id, signal.symbol, signal.direction,
                  signal.entry, signal.sl, signal.tp, tp1,
                  signal.sl,  # trail_sl starts at original SL
                  atr,
                  calc["lot_size"], calc["contracts"], calc["notional_usdt"],
                  calc["leverage"], calc["margin_req"],
                  calc["risk_usdt"], calc["reward_usdt"], calc["fees_usdt"],
                  order_id, 1 if is_paper else 0, now))
            return cur.lastrowid

    def close_trade(self, trade_id, exit_price, exit_reason, pnl_r, pnl_usdt):
        now = datetime.now().isoformat()
        with self._conn() as c:
            c.execute("""
                UPDATE trades SET status='closed', exit_price=?, exit_reason=?,
                pnl_r=?, pnl_usdt=?, closed_at=? WHERE id=?
            """, (exit_price, exit_reason, pnl_r, pnl_usdt, now, trade_id))

    def update_trade_trail(self, trade_id, new_sl, trail_active):
        with self._conn() as c:
            c.execute("""
                UPDATE trades SET trail_sl=?, trail_active=? WHERE id=?
            """, (new_sl, 1 if trail_active else 0, trade_id))

    def mark_partial_tp(self, trade_id, tp1_price, pnl_usdt):
        with self._conn() as c:
            row = c.execute("SELECT entry_price FROM trades WHERE id=?",
                            (trade_id,)).fetchone()
            breakeven = float(row['entry_price']) if row else 0
            c.execute("""
                UPDATE trades SET partial_done=1, trail_sl=?,
                trail_active=1, pnl_usdt=COALESCE(pnl_usdt,0)+?
                WHERE id=?
            """, (breakeven, pnl_usdt, trade_id))

    def get_open_trades(self) -> list:
        with self._conn() as c:
            rows = c.execute("""
                SELECT * FROM trades WHERE status='open' ORDER BY opened_at
            """).fetchall()
        return [dict(r) for r in rows]

    # ── STATS ────────────────────────────────────────────────────
    def get_trade_stats(self, days: int = 30) -> dict:
        with self._conn() as c:
            row = c.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN pnl_usdt < 0 THEN 1 ELSE 0 END) as losses,
                       SUM(pnl_r) as net_r,
                       SUM(pnl_usdt) as pnl_usdt,
                       SUM(fees_usdt) as fees
                FROM trades
                WHERE status='closed'
                  AND datetime(closed_at) >= datetime('now', ?)
            """, (f"-{days} days",)).fetchone()
        r = dict(row) if row else {}
        total = r.get('total') or 0
        wins  = r.get('wins')  or 0
        r['wr'] = round(wins / total * 100, 1) if total > 0 else 0.0
        return r

    def get_daily_loss_pct(self, capital: float) -> float:
        with self._conn() as c:
            row = c.execute("""
                SELECT COALESCE(SUM(pnl_usdt), 0) as day_pnl
                FROM trades WHERE status='closed'
                  AND date(closed_at) = date('now')
            """).fetchone()
        pnl = float(row['day_pnl']) if row else 0.0
        return abs(min(0.0, pnl)) / max(capital, 1) * 100

    def get_monthly_pnl_pct(self, capital: float) -> dict:
        with self._conn() as c:
            row = c.execute("""
                SELECT COALESCE(SUM(pnl_usdt), 0) as mo_pnl
                FROM trades WHERE status='closed'
                  AND strftime('%Y-%m', closed_at) = strftime('%Y-%m', 'now')
            """).fetchone()
        pnl = float(row['mo_pnl']) if row else 0.0
        return {
            'pnl_usdt': round(pnl, 4),
            'pnl_pct':  round(pnl / max(capital, 1) * 100, 4),
        }


class IndiaDatabase:
    """India-specific DB: intraday trades + pairs + capital persistence."""

    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        self.db_path = db_path
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS intraday_trades (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT, strategy TEXT, direction TEXT,
                entry_price REAL, sl_price REAL, tp_price REAL,
                shares      INTEGER, notional REAL,
                risk_inr    REAL, fees_inr REAL,
                status      TEXT DEFAULT 'open',
                exit_price  REAL, exit_reason TEXT,
                pnl_inr     REAL,
                is_paper    INTEGER DEFAULT 1,
                opened_at   TEXT, closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pairs_trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_name    TEXT, stock1 TEXT, stock2 TEXT,
                action       TEXT, entry_zscore REAL, entry_spread REAL,
                shares1      INTEGER, shares2 INTEGER,
                notional1    REAL, notional2 REAL,
                fees_inr     REAL,
                status       TEXT DEFAULT 'open',
                exit_zscore  REAL, exit_spread REAL, exit_reason TEXT,
                pnl_inr      REAL,
                is_paper     INTEGER DEFAULT 1,
                opened_at    TEXT, closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_summary (
                date        TEXT PRIMARY KEY,
                intraday_pnl REAL DEFAULT 0,
                pairs_pnl   REAL DEFAULT 0,
                total_pnl   REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                capital_end REAL
            );
            """)
        logger.debug(f"India DB ready: {self.db_path}")

    # ── INTRADAY ─────────────────────────────────────────────────
    def open_intraday(self, symbol, strategy, direction,
                      entry, sl, tp, shares, notional,
                      risk, fees, is_paper=True) -> int:
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO intraday_trades
                (symbol,strategy,direction,entry_price,sl_price,tp_price,
                 shares,notional,risk_inr,fees_inr,is_paper,opened_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, strategy, direction, entry, sl, tp,
                  shares, notional, risk, fees,
                  1 if is_paper else 0, datetime.now().isoformat()))
            return cur.lastrowid

    def close_intraday(self, trade_id: int, exit_price: float,
                       exit_reason: str, pnl: float):
        with self._conn() as c:
            c.execute("""
                UPDATE intraday_trades
                SET status='closed', exit_price=?, exit_reason=?,
                    pnl_inr=?, closed_at=?
                WHERE id=?
            """, (exit_price, exit_reason, pnl,
                  datetime.now().isoformat(), trade_id))

    def get_open_intraday(self) -> list:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM intraday_trades WHERE status='open'"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── PAIRS ────────────────────────────────────────────────────
    def open_pairs(self, pair_name, stock1, stock2, action,
                   entry_zscore, entry_spread,
                   shares1, shares2, notional1, notional2,
                   fees, is_paper=True) -> int:
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO pairs_trades
                (pair_name,stock1,stock2,action,entry_zscore,entry_spread,
                 shares1,shares2,notional1,notional2,fees_inr,is_paper,opened_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (pair_name, stock1, stock2, action,
                  entry_zscore, entry_spread,
                  shares1, shares2, notional1, notional2, fees,
                  1 if is_paper else 0, datetime.now().isoformat()))
            return cur.lastrowid

    def close_pairs(self, trade_id: int, exit_zscore: float,
                    exit_spread: float, exit_reason: str, pnl: float):
        with self._conn() as c:
            c.execute("""
                UPDATE pairs_trades
                SET status='closed', exit_zscore=?, exit_spread=?,
                    exit_reason=?, pnl_inr=?, closed_at=?
                WHERE id=?
            """, (exit_zscore, exit_spread, exit_reason,
                  pnl, datetime.now().isoformat(), trade_id))

    def get_open_pairs(self) -> list:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM pairs_trades WHERE status='open'"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── STATS ────────────────────────────────────────────────────
    def get_stats(self) -> dict:
        with self._conn() as c:
            it = c.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN pnl_inr>0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_inr) total_pnl, SUM(fees_inr) total_fees
                FROM intraday_trades WHERE status='closed'
            """).fetchone()
            pt = c.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN pnl_inr>0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_inr) total_pnl, SUM(fees_inr) total_fees
                FROM pairs_trades WHERE status='closed'
            """).fetchone()

        def safe(row, key, default=0):
            v = row[key]
            return v if v is not None else default

        it_total = safe(it, 'total')
        pt_total = safe(pt, 'total')
        return {
            "intraday": {
                "total": it_total,
                "wins":  safe(it, 'wins'),
                "wr":    round(safe(it, 'wins') / it_total * 100, 1) if it_total > 0 else 0,
                "pnl":   round(safe(it, 'total_pnl'), 2),
                "fees":  round(safe(it, 'total_fees'), 2),
            },
            "pairs": {
                "total": pt_total,
                "wins":  safe(pt, 'wins'),
                "wr":    round(safe(pt, 'wins') / pt_total * 100, 1) if pt_total > 0 else 0,
                "pnl":   round(safe(pt, 'total_pnl'), 2),
                "fees":  round(safe(pt, 'total_fees'), 2),
            },
        }

    def get_today_pnl(self) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as c:
            it = c.execute("""
                SELECT COALESCE(SUM(pnl_inr),0) FROM intraday_trades
                WHERE status='closed' AND date(closed_at)=?
            """, (today,)).fetchone()[0]
            pt = c.execute("""
                SELECT COALESCE(SUM(pnl_inr),0) FROM pairs_trades
                WHERE status='closed' AND date(closed_at)=?
            """, (today,)).fetchone()[0]
        return {
            "intraday": round(float(it), 2),
            "pairs":    round(float(pt), 2),
            "total":    round(float(it) + float(pt), 2),
        }

    # ── CAPITAL PERSISTENCE ───────────────────────────────────────
    def get_last_capital(self) -> Optional[float]:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as c:
            row = c.execute("""
                SELECT capital_end FROM daily_summary
                WHERE date < ? AND capital_end IS NOT NULL
                ORDER BY date DESC LIMIT 1
            """, (today,)).fetchone()
        if row and row["capital_end"]:
            return float(row["capital_end"])
        return None

    def save_daily_capital(self, capital: float):
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as c:
            c.execute("""
                INSERT INTO daily_summary (date, capital_end)
                VALUES (?, ?)
                ON CONFLICT(date) DO UPDATE SET capital_end = excluded.capital_end
            """, (today, round(capital, 2)))

    def check_daily_loss(self, capital: float, max_pct: float) -> bool:
        today_pnl = self.get_today_pnl()
        loss = abs(min(0, today_pnl["total"]))
        return loss / max(capital, 1) * 100 >= max_pct
