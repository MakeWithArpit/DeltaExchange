"""
SQLite Database Manager
Stores: candles, signals, trades, performance stats
Safe, fast, no CSV corruption risk
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
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol    TEXT NOT NULL,
                time      TEXT NOT NULL,
                open      REAL, high REAL, low REAL, close REAL, volume REAL,
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
        logger.debug(f"Database ready: {self.db_path}")

    # ── CANDLES ──────────────────────────────────────────────────
    def upsert_candles(self, symbol: str, candles: list):
        with self._conn() as c:
            c.executemany("""
                INSERT OR REPLACE INTO candles (symbol,time,open,high,low,close,volume)
                VALUES (?,?,?,?,?,?,?)
            """, [(symbol, str(r["time"]), r["open"], r["high"],
                   r["low"], r["close"], r["volume"]) for r in candles])

    def get_candles(self, symbol: str, limit: int = 400) -> list:
        with self._conn() as c:
            rows = c.execute("""
                SELECT time,open,high,low,close,volume FROM candles
                WHERE symbol=? ORDER BY time DESC LIMIT ?
            """, (symbol, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── SIGNALS ──────────────────────────────────────────────────
    def save_signal(self, sig, ml_result: dict = None) -> int:
        ml_result = ml_result or {}
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO signals (symbol,direction,entry,sl,tp,atr,confidence,
                ml_win_prob,ml_take_trade,squeeze_dur,breakout_str,vol_ratio,
                trend_4h,reason,timestamp,acted_on)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (sig.symbol, sig.direction, sig.entry, sig.sl, sig.tp,
                  sig.atr, sig.confidence,
                  ml_result.get("win_prob", 0.5),
                  int(ml_result.get("take_trade", True)),
                  sig.squeeze_dur, sig.breakout_str, sig.vol_ratio,
                  sig.trend_4h, sig.reason, sig.timestamp,
                  int(ml_result.get("take_trade", True))))
        return cur.lastrowid

    # ── TRADES ───────────────────────────────────────────────────
    def open_trade(self, signal_id: int, sig, calc: dict,
                    order_id: str = None, is_paper: bool = True) -> int:
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO trades (signal_id,symbol,direction,entry_price,sl_price,
                tp_price,lot_size,contracts,notional,leverage,margin_req,risk_usdt,
                reward_usdt,fees_usdt,status,order_id,is_paper,opened_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (signal_id, sig.symbol, sig.direction,
                  calc["entry_price"], calc["sl_price"], calc["tp_price"],
                  calc["lot_size"], calc["contracts"], calc["notional_usdt"],
                  calc["leverage"], calc["margin_req"], calc["risk_usdt"],
                  calc["reward_usdt"], calc["fees_usdt"],
                  "open", order_id, int(is_paper),
                  datetime.now().isoformat()))
        return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float,
                     exit_reason: str, pnl_r: float, pnl_usdt: float):
        with self._conn() as c:
            c.execute("""
                UPDATE trades SET status=?,exit_price=?,exit_reason=?,
                pnl_r=?,pnl_usdt=?,closed_at=?
                WHERE id=?
            """, ("closed", exit_price, exit_reason, pnl_r, pnl_usdt,
                  datetime.now().isoformat(), trade_id))
        self._update_daily_performance(pnl_r, pnl_usdt)

    def get_open_trades(self) -> list:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        return [dict(r) for r in rows]

    def get_trade_stats(self, days: int = 30) -> dict:
        with self._conn() as c:
            rows = c.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN pnl_r>0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_r) net_r,
                       SUM(pnl_usdt) pnl_usdt,
                       SUM(fees_usdt) fees
                FROM trades
                WHERE status='closed'
                AND opened_at >= datetime('now', ?)
            """, (f"-{days} days",)).fetchone()
        r = dict(rows)
        # SQLite SUM() returns None when no rows — convert all to safe defaults
        r["total"]    = int(r["total"]      or 0)
        r["wins"]     = int(r["wins"]       or 0)
        r["net_r"]    = float(r["net_r"]    or 0.0)
        r["pnl_usdt"] = float(r["pnl_usdt"] or 0.0)
        r["fees"]     = float(r["fees"]     or 0.0)
        r["losses"]   = r["total"] - r["wins"]
        r["wr"]       = round(r["wins"] / max(r["total"], 1) * 100, 1)
        return r

    def _update_daily_performance(self, pnl_r: float, pnl_usdt: float):
        today = datetime.now().date().isoformat()
        with self._conn() as c:
            c.execute("""
                INSERT INTO performance (date,trades,wins,losses,net_r,pnl_usdt)
                VALUES (?,1,?,?,?,?)
                ON CONFLICT(date) DO UPDATE SET
                    trades=trades+1,
                    wins=wins+CASE WHEN ? > 0 THEN 1 ELSE 0 END,
                    losses=losses+CASE WHEN ? <= 0 THEN 1 ELSE 0 END,
                    net_r=net_r+?,
                    pnl_usdt=pnl_usdt+?
            """, (today,
                  1 if pnl_r > 0 else 0, 1 if pnl_r <= 0 else 0,
                  pnl_r, pnl_usdt,
                  pnl_r, pnl_r, pnl_r, pnl_usdt))

    def get_daily_loss_pct(self, capital: float) -> float:
        today = datetime.now().date().isoformat()
        with self._conn() as c:
            row = c.execute("SELECT pnl_usdt FROM performance WHERE date=?",
                            (today,)).fetchone()
        if row and row[0] and capital > 0:
            return abs(min(0, row[0])) / capital * 100
        return 0.0