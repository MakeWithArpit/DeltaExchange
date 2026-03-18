"""
Dhann API Client
Docs: https://api.dhan.co
"""
import requests, logging, json
from datetime import datetime, timedelta
import pandas as pd

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co"


class DhannClient:

    def __init__(self, client_id: str, access_token: str):
        self.client_id    = client_id
        self.access_token = access_token
        self.session      = requests.Session()
        self.session.headers.update({
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "access-token":  access_token,
            "client-id":     client_id,
        })

    def _get(self, path, params=None):
        try:
            r = self.session.get(BASE_URL + path, params=params, timeout=10)
            if not r.ok:
                logger.error(f"GET {path} {r.status_code}: {r.text[:200]}")
                return {}
            return r.json()
        except Exception as e:
            logger.error(f"GET {path} failed: {e}")
            return {}

    def _post(self, path, body):
        try:
            r = self.session.post(BASE_URL + path,
                                  data=json.dumps(body), timeout=10)
            if not r.ok:
                logger.error(f"POST {path} {r.status_code}: {r.text[:200]}")
                return {}
            return r.json()
        except Exception as e:
            logger.error(f"POST {path} failed: {e}")
            return {}

    # ── MARKET DATA ───────────────────────────────────────────────

    def get_candles(self, security_id: str, exchange: str = "NSE",
                    instrument: str = "EQUITY",
                    interval: str = "60",   # 60 = 1 hour
                    days: int = 60) -> pd.DataFrame:
        """
        Fetch historical OHLCV candles.
        interval: "1","5","15","25","60" (minutes) or "D" (daily)
        """
        to_date   = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        body = {
            "securityId":  str(security_id),
            "exchangeSegment": f"{exchange}_EQ",
            "instrument":  instrument,
            "interval":    interval,
            "fromDate":    from_date,
            "toDate":      to_date,
        }

        data = self._post("/v2/charts/intraday", body)
        if not data or "open" not in data:
            # Try historical endpoint
            data = self._post("/v2/charts/historical", body)

        if not data or "open" not in data:
            logger.warning(f"No candles for {security_id}")
            return pd.DataFrame()

        try:
            n = len(data["open"])
            df = pd.DataFrame({
                "datetime": pd.to_datetime(data["timestamp"][:n], unit="s"),
                "open":     [float(x) for x in data["open"][:n]],
                "high":     [float(x) for x in data["high"][:n]],
                "low":      [float(x) for x in data["low"][:n]],
                "close":    [float(x) for x in data["close"][:n]],
                "volume":   [float(x) for x in data["volume"][:n]],
            })
            df = df.sort_values("datetime").reset_index(drop=True)
            df["datetime"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
            return df
        except Exception as e:
            logger.error(f"Candle parse error: {e}")
            return pd.DataFrame()

    def get_ltp(self, security_ids: list, exchange: str = "NSE") -> dict:
        """Get last traded price for multiple securities."""
        body = {
            "NSE_EQ": [str(s) for s in security_ids]
        }
        data = self._post("/v2/marketfeed/ltp", body)
        result = {}
        for seg, items in (data.get("data", {}) or {}).items():
            for item in (items or []):
                sid  = str(item.get("securityId", ""))
                ltp  = float(item.get("lastTradedPrice", 0))
                result[sid] = ltp
        return result

    # ── ORDERS ────────────────────────────────────────────────────

    def place_order(self, security_id: str, exchange: str,
                    transaction_type: str,   # "BUY" or "SELL"
                    quantity: int,
                    order_type: str = "MARKET",   # "MARKET" or "LIMIT"
                    price: float = 0.0,
                    product_type: str = "INTRADAY") -> dict:
        """
        Place an order on Dhann.
        product_type: "INTRADAY" (MIS) or "CNC" (delivery)
        """
        body = {
            "dhanClientId":    self.client_id,
            "transactionType": transaction_type,
            "exchangeSegment": f"{exchange}_EQ",
            "productType":     product_type,
            "orderType":       order_type,
            "validity":        "DAY",
            "securityId":      str(security_id),
            "quantity":        int(quantity),
            "price":           round(float(price), 2),
            "triggerPrice":    0,
            "afterMarketOrder": False,
        }
        return self._post("/v2/orders", body)

    def place_sl_order(self, security_id: str, exchange: str,
                       transaction_type: str, quantity: int,
                       trigger_price: float, price: float = 0.0,
                       product_type: str = "INTRADAY") -> dict:
        """Stop loss order."""
        body = {
            "dhanClientId":    self.client_id,
            "transactionType": transaction_type,
            "exchangeSegment": f"{exchange}_EQ",
            "productType":     product_type,
            "orderType":       "STOP_LOSS",
            "validity":        "DAY",
            "securityId":      str(security_id),
            "quantity":        int(quantity),
            "price":           round(float(price), 2),
            "triggerPrice":    round(float(trigger_price), 2),
        }
        return self._post("/v2/orders", body)

    def cancel_order(self, order_id: str) -> dict:
        return self._delete(f"/v2/orders/{order_id}")

    def _delete(self, path):
        try:
            r = self.session.delete(BASE_URL + path, timeout=10)
            return r.json() if r.ok else {}
        except Exception as e:
            logger.error(f"DELETE {path}: {e}")
            return {}

    # ── ACCOUNT ───────────────────────────────────────────────────

    def get_fund_limits(self) -> dict:
        """Get available funds."""
        data = self._get("/v2/fundlimit")
        return data

    def get_positions(self) -> list:
        """Get open positions."""
        data = self._get("/v2/positions")
        return data if isinstance(data, list) else []

    def get_orders(self) -> list:
        """Get today's orders."""
        data = self._get("/v2/orders")
        return data if isinstance(data, list) else []

    def test_connection(self) -> bool:
        """Test API connectivity."""
        data = self.get_fund_limits()
        ok = bool(data)
        logger.info(f"Dhann API connection: {'OK' if ok else 'FAILED'}")
        return ok

    def get_available_capital(self) -> float:
        """Extract available INR balance."""
        data = self.get_fund_limits()
        if not data:
            return 0.0
        # Dhann returns availabelBalance key
        return float(data.get("availabelBalance",
                     data.get("availableBalance",
                     data.get("net", 0))) or 0)

    # ── YFINANCE FALLBACK ─────────────────────────────────────────

    def get_candles_yfinance(self, symbol: str,
                             interval: str = "1h",
                             period: str = "60d") -> pd.DataFrame:
        """
        Fallback: fetch data from yfinance when Dhann unavailable.
        Used for paper trading and backtesting.
        """
        try:
            import yfinance as yf
            ticker = f"{symbol}.NS"
            df = yf.download(ticker, period=period,
                             interval=interval, auto_adjust=True,
                             progress=False)
            if df.empty:
                return pd.DataFrame()
            df = df.reset_index()
            # Handle multi-level columns from yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0] if col[1]=='' else col[0] for col in df.columns]
            df.columns = [c.lower() for c in df.columns]
            time_col = next((c for c in df.columns if 'date' in c or 'time' in c), df.columns[0])
            df = df.rename(columns={time_col: 'datetime'})
            df['datetime'] = pd.to_datetime(df['datetime'])
            if df['datetime'].dt.tz is not None:
                df['datetime'] = df['datetime'].dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
            df = df[['datetime','open','high','low','close','volume']].dropna()
            for c in ['open','high','low','close','volume']:
                df[c] = pd.to_numeric(df[c], errors='coerce')
            df = df[df['close'] > 0].sort_values('datetime').reset_index(drop=True)
            return df
        except Exception as e:
            logger.error(f"yfinance fallback failed for {symbol}: {e}")
            return pd.DataFrame()
