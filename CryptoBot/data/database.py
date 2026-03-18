"""
SQLite Database Manager v2.0
Supports: trailing SL tracking, partial TP, tp1_price, atr per trade
"""
import sqlite3, logging, os
from datetime import datetime

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: str = "data/bot_trades.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
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
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol         TEXT, direction TEXT, entry REAL,
                sl REAL, tp REAL, atr REAL, confidence REAL,
                ml_win_prob    REAL, ml_take_trade INTEGER,
                squeeze_dur    INTEGER, breakout_str REAL, vol_ratio REAL,
                trend_4h       TEXT, reason TEXT, timestamp TEXT,
                acted_on       INTEGER DEFAULT 0,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS trades (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id      INTEGER,
                symbol         TEXT, direction TEXT,
                entry_price    REAL, sl_price REAL, tp_price REAL,
                tp1_price      REAL DEFAULT 0,
                trail_sl       REAL DEFAULT 0,
                trail_active   INTEGER DEFAULT 0,
                partial_done   INTEGER DEFAULT 0,
                atr            REAL DEFAULT 0,
                lot_size       REAL, contracts REAL, notional REAL,
                leverage       INTEGER, margin_req REAL,
                risk_usdt      REAL, reward_usdt REAL, fees_usdt REAL,
                status         TEXT DEFAULT 'open',
                exit_price     REAL, exit_reason TEXT,
                pnl_r          REAL, pnl_usdt REAL,
                order_id       TEXT,
                is_paper       INTEGER DEFAULT 1,
                opened_at      TEXT, closed_at TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE TABLE IF NOT EXISTS performance (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date         TEXT UNIQUE,
                trades       INTEGER DEFAULT 0,
                wins         INTEGER DEFAULT 0,
                losses       INTEGER DEFAULT 0,
                net_r        REAL DEFAULT 0,
                pnl_usdt     REAL DEFAULT 0,
                fees_usdt    REAL DEFAULT 0,
                capital_end  REAL
            );
            CREATE INDEX IF NOT EXISTS idx_candles_sym_time ON candles(symbol, time);
            CREATE INDEX IF NOT EXISTS idx_trades_status    ON trades(status);
            """)
            # Migrate old DB: add new columns if they don't exist
            for col, definition in [
                ("tp1_price",    "REAL DEFAULT 0"),
                ("trail_sl",     "REAL DEFAULT 0"),
                ("trail_active", "INTEGER DEFAULT 0"),
                ("partial_done", "INTEGER DEFAULT 0"),
                ("atr",          "REAL DEFAULT 0"),
            ]:
                try:
                    c.execute(f"ALTER TABLE trades ADD COLUMN {col} {definition}")
                except Exception:
                    pass  # Column already exists
        logger.debug(f"Database ready: {self.db_path}")

    # ── CANDLES ─────────────────────────────────────────────────
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

    # ── SIGNALS ─────────────────────────────────────────────────
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

    # ── TRADES ──────────────────────────────────────────────────
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
                  signal.sl,   # trail_sl starts at original SL
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
        """Update trailing SL for an open trade"""
        with self._conn() as c:
            c.execute("""
                UPDATE trades SET trail_sl=?, trail_active=? WHERE id=?
            """, (new_sl, 1 if trail_active else 0, trade_id))

    def mark_partial_tp(self, trade_id, tp1_price, pnl_usdt):
        """Mark partial TP done, SL moved to breakeven"""
        now = datetime.now().isoformat()
        with self._conn() as c:
            # Get entry price to set breakeven
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

    # ── STATS ───────────────────────────────────────────────────
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
