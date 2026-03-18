"""
SQLite Database — Trades, Signals, P&L
"""
import sqlite3, logging, os
from datetime import datetime

logger = logging.getLogger(__name__)


class Database:

    def __init__(self, db_path: str = "data/bot_trades.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
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
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol       TEXT,
                strategy     TEXT,
                direction    TEXT,
                entry_price  REAL,
                sl_price     REAL,
                tp_price     REAL,
                shares       INTEGER,
                notional     REAL,
                risk_inr     REAL,
                fees_inr     REAL,
                status       TEXT DEFAULT 'open',
                exit_price   REAL,
                exit_reason  TEXT,
                pnl_inr      REAL,
                is_paper     INTEGER DEFAULT 1,
                opened_at    TEXT,
                closed_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS pairs_trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                pair_name    TEXT,
                stock1       TEXT,
                stock2       TEXT,
                action       TEXT,
                entry_zscore REAL,
                entry_spread REAL,
                shares1      INTEGER,
                shares2      INTEGER,
                notional1    REAL,
                notional2    REAL,
                fees_inr     REAL,
                status       TEXT DEFAULT 'open',
                exit_zscore  REAL,
                exit_spread  REAL,
                exit_reason  TEXT,
                pnl_inr      REAL,
                is_paper     INTEGER DEFAULT 1,
                opened_at    TEXT,
                closed_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS daily_summary (
                date         TEXT PRIMARY KEY,
                intraday_pnl REAL DEFAULT 0,
                pairs_pnl    REAL DEFAULT 0,
                total_pnl    REAL DEFAULT 0,
                trades_count INTEGER DEFAULT 0,
                capital_end  REAL
            );
            """)

    # ── INTRADAY ──────────────────────────────────────────────────
    def open_intraday(self, symbol, strategy, direction,
                      entry, sl, tp, shares, notional, risk, fees,
                      is_paper=True) -> int:
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO intraday_trades
                (symbol,strategy,direction,entry_price,sl_price,tp_price,
                 shares,notional,risk_inr,fees_inr,is_paper,opened_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (symbol, strategy, direction, entry, sl, tp,
                  shares, notional, risk, fees, 1 if is_paper else 0,
                  datetime.now().isoformat()))
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
            rows = c.execute("""
                SELECT * FROM intraday_trades WHERE status='open'
            """).fetchall()
        return [dict(r) for r in rows]

    # ── PAIRS ─────────────────────────────────────────────────────
    def open_pairs(self, pair_name, stock1, stock2, action,
                   entry_zscore, entry_spread,
                   shares1, shares2, notional1, notional2, fees,
                   is_paper=True) -> int:
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
            rows = c.execute("""
                SELECT * FROM pairs_trades WHERE status='open'
            """).fetchall()
        return [dict(r) for r in rows]

    # ── STATS ─────────────────────────────────────────────────────
    def get_stats(self) -> dict:
        with self._conn() as c:
            # Intraday stats
            it = c.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN pnl_inr>0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_inr) total_pnl,
                       SUM(fees_inr) total_fees
                FROM intraday_trades WHERE status='closed'
            """).fetchone()
            # Pairs stats
            pt = c.execute("""
                SELECT COUNT(*) total,
                       SUM(CASE WHEN pnl_inr>0 THEN 1 ELSE 0 END) wins,
                       SUM(pnl_inr) total_pnl,
                       SUM(fees_inr) total_fees
                FROM pairs_trades WHERE status='closed'
            """).fetchone()

        def safe(row, key, default=0):
            v = row[key]
            return v if v is not None else default

        it_total = safe(it, 'total')
        pt_total = safe(pt, 'total')
        return {
            "intraday": {
                "total":  it_total,
                "wins":   safe(it, 'wins'),
                "wr":     round(safe(it,'wins')/it_total*100,1) if it_total>0 else 0,
                "pnl":    round(safe(it, 'total_pnl'), 2),
                "fees":   round(safe(it, 'total_fees'), 2),
            },
            "pairs": {
                "total":  pt_total,
                "wins":   safe(pt, 'wins'),
                "wr":     round(safe(pt,'wins')/pt_total*100,1) if pt_total>0 else 0,
                "pnl":    round(safe(pt, 'total_pnl'), 2),
                "fees":   round(safe(pt, 'total_fees'), 2),
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
