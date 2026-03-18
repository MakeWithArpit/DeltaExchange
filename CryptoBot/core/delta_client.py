"""
Delta Exchange India — REST API Client
Based strictly on official docs: https://docs.delta.exchange

KEY FACTS FROM DOCS:
  - Candle resolution: "1","5","15","30","60","120","240","D" (NOT "30m","1h")
  - Stop orders: order_type="market_order" + stop_order_type="stop_loss_order" + stop_price
  - Cancel order: DELETE /v2/orders  body={id, product_id}  (NOT /v2/orders/{id})
  - All positions: GET /v2/positions/margined  (GET /v2/positions needs product_id)
  - Ticker: GET /v2/tickers/{symbol}  (NOT /v2/tickers?symbol=...)
  - Signature: method + timestamp + path + query_string + body
  - User-Agent header REQUIRED or CDN returns 4XX
"""
import hmac, hashlib, time, json, logging
import requests
from urllib.parse import urlencode

from config.settings import API_KEY, API_SECRET, BASE_URL

logger = logging.getLogger(__name__)

USER_AGENT = "python-trading-bot/1.0"

# Delta API candle resolutions (verified from actual API error):
# "Allowed values are 5s,1m,3m,5m,15m,30m,1h,2h,4h,6h,12h,1d,1w"
# Use the "m/h/d" format directly — NOT "30","60","240"
RESOLUTION_MAP = {
    # Internal names → Delta API format (pass-through for already-correct values)
    "1m":"1m",  "3m":"3m",  "5m":"5m",  "15m":"15m", "30m":"30m",
    "1h":"1h",  "2h":"2h",  "4h":"4h",  "6h":"6h",   "12h":"12h",
    "1d":"1d",  "1w":"1w",
    # Fallbacks for old-style numeric strings
    "30":"30m", "60":"1h", "240":"4h", "1440":"1d",
    "D":"1d",
}
CANDLE_SECS = {
    "1m":60,  "3m":180,  "5m":300,  "15m":900,  "30m":1800,
    "1h":3600,"2h":7200, "4h":14400,"6h":21600, "12h":43200,
    "1d":86400,"1w":604800,
}


def _make_signature(secret, method, path, query_string="", body=""):
    ts  = str(int(time.time()))
    msg = method + ts + path + query_string + body
    sig = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return ts, sig


class DeltaClient:

    def __init__(self, api_key=API_KEY, api_secret=API_SECRET):
        self.api_key    = api_key
        self.api_secret = api_secret
        self.base_url   = BASE_URL.rstrip("/")
        self.session    = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   USER_AGENT,
        })

    def _auth_headers(self, method, path, query_string="", body=""):
        ts, sig = _make_signature(self.api_secret, method, path, query_string, body)
        return {"api-key": self.api_key, "signature": sig,
                "timestamp": ts, "User-Agent": USER_AGENT}

    def _build_qs(self, params):
        if not params:
            return ""
        return "?" + urlencode(sorted(params.items()))

    def _get_private(self, path, params=None):
        qs      = self._build_qs(params) if params else ""
        headers = self._auth_headers("GET", path, qs)
        try:
            r    = self.session.get(self.base_url + path, params=params,
                                    headers=headers, timeout=10)
            data = r.json()
            if not r.ok:
                logger.error(f"GET {path} {r.status_code}: {data}")
                return {"success": False, "error": data}
            return data
        except Exception as e:
            logger.error(f"GET {path} failed: {e}")
            return {"success": False, "error": str(e)}

    def _get_public(self, path, params=None):
        try:
            r    = requests.get(self.base_url + path, params=params,
                                headers={"User-Agent": USER_AGENT,
                                         "Accept": "application/json"},
                                timeout=10)
            data = r.json()
            if not r.ok:
                logger.error(f"PUBLIC GET {path} {r.status_code}: {data}")
                return {"success": False, "error": data}
            return data
        except Exception as e:
            logger.error(f"PUBLIC GET {path} failed: {e}")
            return {"success": False, "error": str(e)}

    def _post(self, path, body):
        body_str = json.dumps(body, separators=(",", ":"))
        headers  = self._auth_headers("POST", path, body=body_str)
        try:
            r    = self.session.post(self.base_url + path, data=body_str,
                                     headers=headers, timeout=10)
            data = r.json()
            if not r.ok:
                logger.error(f"POST {path} {r.status_code}: {data}")
                return {"success": False, "error": data}
            return data
        except Exception as e:
            logger.error(f"POST {path} failed: {e}")
            return {"success": False, "error": str(e)}

    def _delete(self, path, body=None):
        body_str = json.dumps(body or {}, separators=(",", ":"))
        headers  = self._auth_headers("DELETE", path, body=body_str)
        try:
            r    = self.session.delete(self.base_url + path, data=body_str,
                                       headers=headers, timeout=10)
            data = r.json()
            if not r.ok:
                logger.error(f"DELETE {path} {r.status_code}: {data}")
                return {"success": False, "error": data}
            return data
        except Exception as e:
            logger.error(f"DELETE {path} failed: {e}")
            return {"success": False, "error": str(e)}

    # ── MARKET DATA (public) ──────────────────────────────────────

    def get_candles(self, symbol, resolution="30m", limit=350):
        """
        Docs: GET /v2/history/candles — PUBLIC, no auth.
        CRITICAL: resolution must be "30" not "30m", "60" not "1h"
        Returns list of candle dicts, sorted OLDEST first.
        """
        res  = RESOLUTION_MAP.get(str(resolution), str(resolution))  # "30m"→"30m"
        secs = CANDLE_SECS.get(res, 1800)
        end_ts   = int(time.time())
        start_ts = end_ts - (limit * secs)

        data = self._get_public("/v2/history/candles", params={
            "symbol":     symbol,
            "resolution": res,       # "30" not "30m" ← docs format
            "start":      start_ts,
            "end":        end_ts,
        })

        if data.get("success") and data.get("result"):
            candles = list(reversed(data["result"]))   # API: latest first → reverse
            logger.debug(f"  {symbol}: {len(candles)} candles (res={res})")
            return candles

        logger.warning(f"Candles failed for {symbol}: {data}")
        return []

    def get_products(self):
        """Public: all perpetual futures"""
        data = self._get_public("/v2/products", params={
            "contract_types": "perpetual_futures", "page_size": "100"
        })
        return data.get("result", [])

    def get_ticker(self, symbol):
        """
        Public: 24h ticker.
        Docs: GET /v2/tickers/{symbol}  ← symbol in PATH, not query param
        """
        data = self._get_public(f"/v2/tickers/{symbol}")
        return data.get("result", {})

    def get_orderbook(self, symbol, depth=5):
        """Public: L2 orderbook"""
        data = self._get_public(f"/v2/l2orderbook/{symbol}", params={"depth": depth})
        return data.get("result", {})

    # ── ACCOUNT (private) ────────────────────────────────────────

    def get_balance(self):
        """
        Private: wallet balances.
        Docs: GET /v2/wallet/balances
        Returns {asset_symbol: {balance, available}, ...}
        """
        data = self._get_private("/v2/wallet/balances")
        if not data.get("success"):
            logger.error(f"Balance fetch failed: {data}")
            return {}
        balances = {}
        for b in data.get("result", []):
            asset = b.get("asset_symbol", "?")
            balances[asset] = {
                "balance":   float(b.get("balance",           "0") or 0),
                "available": float(b.get("available_balance", "0") or 0),
            }
        return balances

    def get_positions(self):
        """
        Private: ALL open positions.
        Docs: GET /v2/positions/margined  ← all positions, no params needed
              GET /v2/positions?product_id=X  ← single product (product_id required!)
        """
        data = self._get_private("/v2/positions/margined")
        return data.get("result", []) if data.get("success") else []

    def get_position(self, product_id):
        """Private: single real-time position"""
        data = self._get_private("/v2/positions", params={"product_id": product_id})
        return data.get("result", {}) if data.get("success") else {}

    def get_orders(self, product_id=None, state="open"):
        """Private: active orders"""
        params = {"state": state}
        if product_id:
            params["product_id"] = product_id
        data = self._get_private("/v2/orders", params=params)
        return data.get("result", []) if data.get("success") else []

    # ── ORDER MANAGEMENT (private) ───────────────────────────────

    def place_order(self, product_id, side, size,
                    order_type="limit_order",
                    limit_price=None, stop_price=None,
                    reduce_only=False):
        """
        Docs: POST /v2/orders
        order_type: "limit_order" | "market_order"  ← ONLY these two
        size: integer (contracts, not BTC amount)
        prices: string format (BigDecimal)
        """
        body = {
            "product_id":  product_id,
            "side":        side,
            "size":        int(size),
            "order_type":  order_type,
            "reduce_only": reduce_only,
        }
        if limit_price is not None:
            body["limit_price"] = str(round(limit_price, 2))
        if stop_price is not None:
            body["stop_price"]  = str(round(stop_price,  2))

        result = self._post("/v2/orders", body)
        if result.get("success"):
            oid = result.get("result", {}).get("id", "?")
            logger.info(f"Order placed #{oid}: {side} {int(size)}x product {product_id}")
        return result

    def place_stop_loss(self, product_id, side, size,
                        stop_price, limit_price=None):
        """
        Correct stop loss order per docs:
          order_type      = "market_order" (or "limit_order" if limit_price given)
          stop_order_type = "stop_loss_order"   ← this field sets it as stop
          stop_price      = trigger price (string)
          reduce_only     = True

        WRONG (causes error): order_type="stop_market_order"
        """
        body = {
            "product_id":      product_id,
            "side":            side,
            "size":            int(size),
            "order_type":      "limit_order" if limit_price else "market_order",
            "stop_order_type": "stop_loss_order",
            "stop_price":      str(round(stop_price, 2)),
            "reduce_only":     True,
        }
        if limit_price is not None:
            body["limit_price"] = str(round(limit_price, 2))

        result = self._post("/v2/orders", body)
        if result.get("success"):
            oid = result.get("result", {}).get("id", "?")
            logger.info(f"Stop loss placed #{oid}: {side} @ {stop_price}")
        return result

    def cancel_order(self, order_id, product_id):
        """
        Docs: DELETE /v2/orders  body={id, product_id}
        NOT: DELETE /v2/orders/{order_id}  ← wrong!
        """
        return self._delete("/v2/orders",
                            body={"id": order_id, "product_id": product_id})

    def cancel_all_orders(self, product_id):
        """Docs: DELETE /v2/orders/all"""
        return self._delete("/v2/orders/all", body={
            "product_id":                product_id,
            "cancel_limit_orders":       True,
            "cancel_stop_orders":        True,
            "cancel_reduce_only_orders": True,
        })

    def close_position(self, product_id, size, direction):
        """Close position with reduce-only market order"""
        side = "sell" if direction == "long" else "buy"
        return self.place_order(product_id=product_id, side=side,
                                size=size, order_type="market_order",
                                reduce_only=True)

    # ── HELPERS ──────────────────────────────────────────────────

    def test_connection(self):
        """Test via public endpoint"""
        try:
            data = self._get_public("/v2/products", params={
                "contract_types": "perpetual_futures", "page_size": "1"
            })
            ok = bool(data.get("success"))
            logger.info(f"Connection: {'OK' if ok else 'FAILED'} — {BASE_URL}")
            return ok
        except Exception as e:
            logger.error(f"Connection test: {e}")
            return False

    def discover_product_ids(self):
        """Fetch product IDs from current environment (testnet/production differ)"""
        found = {}
        for p in self.get_products():
            sym = p.get("symbol", "")
            if any(s in sym for s in ["BTC", "ETH", "SOL"]):
                found[sym] = {
                    "product_id":     p.get("id"),
                    "contract_type":  p.get("contract_type"),
                    "contract_value": p.get("contract_value"),
                    "tick_size":      p.get("tick_size"),
                    "taker_fee":      p.get("taker_commission_rate"),
                    "maker_fee":      p.get("maker_commission_rate"),
                }
        return found