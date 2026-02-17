"""
Delta Exchange Trading Bot
==========================
Testnet-ready bot with:
- Live price feed (WebSocket) — runs in background thread
- Entry order with SL + TP
- OCO (One Cancels Other) monitor
- Backtesting scaffold (strategy add karo baad mein)
"""

import websocket
import json
import time
import hmac
import hashlib
import requests
import threading

# ─────────────────────────────────────────────
# CONFIG  (Testnet credentials — real pe mat lagana!)
# ─────────────────────────────────────────────
API_KEY    = "iRB8OkexongFx2IWRJ7XrnmsIAdLSF"
API_SECRET = "RaQ1xQPrIia9NgsnaRDs1y5q2vHr7o9jgbfSNQGWjZEet4SJN5OtHClufcmL"
BASE_URL   = "https://cdn-ind.testnet.deltaex.org"
WS_URL     = "wss://socket-ind.testnet.deltaex.org"


# ─────────────────────────────────────────────
# 1. SIGNATURE GENERATOR
# Bug Fix: hmac.new → Python mein hmac.new() sahi hai,
#          lekin GET requests ke liye bhi signature chahiye
# ─────────────────────────────────────────────
def generate_signature(secret: str, method: str, path: str,
                        timestamp: str, body: str = "", query_string: str = "") -> str:
    """
    Delta Exchange signature format:
    HMAC-SHA256 of: method + timestamp + path + query_string + body
    """
    message = method + timestamp + path + query_string + body
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def make_headers(method: str, path: str, body: str = "", query_string: str = "") -> dict:
    """
    Har request ke liye fresh timestamp + signature banao.
    Bug Fix: Pehle same headers reuse ho rahe the — timestamp expire ho jata tha.
    """
    timestamp = str(int(time.time()))
    signature = generate_signature(API_SECRET, method, path, timestamp, body, query_string)
    return {
        "api-key":       API_KEY,
        "timestamp":     timestamp,
        "signature":     signature,
        "User-Agent":    "python-3.10",
        "Content-Type":  "application/json",
    }


# ─────────────────────────────────────────────
# 2. PRODUCT ID FETCH
# ─────────────────────────────────────────────
def get_product_id(symbol: str) -> int | None:
    """Symbol se product_id fetch karo (e.g. 'BTCUSD' → int id)"""
    url  = BASE_URL + "/v2/products"
    resp = requests.get(url, timeout=10)
    data = resp.json()

    if not data.get("success"):
        print(f"[ERROR] Products fetch failed: {data}")
        return None

    for product in data["result"]:
        if product["symbol"] == symbol:
            print(f"[INFO] {symbol} → product_id = {product['id']}")
            return product["id"]

    print(f"[ERROR] Symbol '{symbol}' nahi mila.")
    return None


# ─────────────────────────────────────────────
# 3. LIVE PRICE FEED  (background thread mein)
# Bug Fix: Pehle show_live_price() main thread block karta tha
#          → buy_sell() kabhi run nahi hota tha
# ─────────────────────────────────────────────
class PriceFeed:
    """
    WebSocket se live mark price subscribe karo.
    Background thread mein chalta hai — bot ko block nahi karta.
    """
    def __init__(self, symbol: str):
        self.symbol      = symbol
        self.latest_price = None
        self._thread     = None

    def start(self):
        """Background thread shuru karo"""
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[WS] Price feed started for {self.symbol}")

    def _run(self):
        def on_open(ws):
            msg = {
                "type": "subscribe",
                "payload": {
                    "channels": [{"name": "v2/ticker", "symbols": [self.symbol]}]
                }
            }
            ws.send(json.dumps(msg))

        def on_message(ws, message):
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                self.latest_price = float(data["mark_price"])
                print(f"[PRICE] {self.symbol}: {self.latest_price:.2f}")

        def on_error(ws, error):
            print(f"[WS ERROR] {error}")

        def on_close(ws, code, msg):
            print(f"[WS] Connection closed: {code}")

        ws = websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever(ping_interval=30, ping_timeout=10)

    def get_price(self) -> float | None:
        return self.latest_price


# ─────────────────────────────────────────────
# 4. ORDER HELPERS
# Bug Fix: GET aur DELETE requests ke liye bhi
#          fresh signature banana zaroori hai
# ─────────────────────────────────────────────
def get_order_status(order_id: int) -> str:
    """Order ka current status fetch karo"""
    path = f"/v2/orders/{order_id}"
    headers = make_headers("GET", path)  # Bug Fix: fresh headers
    resp = requests.get(BASE_URL + path, headers=headers, timeout=10)
    data = resp.json()

    if data.get("success"):
        return data["result"]["state"]
    else:
        print(f"[WARN] Order status fetch failed: {data}")
        return "unknown"


def cancel_order(order_id: int) -> bool:
    """Order cancel karo"""
    path = f"/v2/orders/{order_id}"
    headers = make_headers("DELETE", path)  # Bug Fix: fresh signature
    resp = requests.delete(BASE_URL + path, headers=headers, timeout=10)
    data = resp.json()

    if data.get("success"):
        print(f"[INFO] Order {order_id} cancelled.")
        return True
    else:
        print(f"[WARN] Cancel failed: {data}")
        return False


def place_order(product_id: int, side: str, size: int,
                order_type: str, limit_price: float = None,
                stop_price: float = None) -> dict | None:
    """
    Generic order placer.
    order_type: 'limit_order' | 'stop_market_order'
    """
    path = "/v2/orders"

    body_dict = {
        "product_id": product_id,
        "size":       size,
        "side":       side,
        "order_type": order_type,
        "time_in_force": "gtc",
    }
    if limit_price is not None:
        body_dict["limit_price"] = str(limit_price)
    if stop_price is not None:
        body_dict["stop_price"] = str(stop_price)

    body_json = json.dumps(body_dict, separators=(",", ":"))
    headers   = make_headers("POST", path, body=body_json)

    resp = requests.post(BASE_URL + path, headers=headers, data=body_json, timeout=10)
    data = resp.json()

    if data.get("success"):
        oid = data["result"]["id"]
        print(f"[ORDER] {order_type} {side} placed → id={oid}")
        return data["result"]
    else:
        print(f"[ERROR] Order failed: {data}")
        return None


# ─────────────────────────────────────────────
# 5. MAIN TRADE FLOW
# Entry → Wait Fill → SL + TP → OCO Monitor
# ─────────────────────────────────────────────
def execute_trade(product_id: int, side: str, size: int,
                  limit_price: float, stop_price: float, target_price: float):
    """
    Pura trade lifecycle:
    1. Entry limit order
    2. Fill hone ka wait
    3. SL + TP place karo
    4. OCO monitor — jo pehle fill ho, doosra cancel
    """

    # ── 1. ENTRY ORDER ──────────────────────
    print(f"\n[TRADE] Entry: {side.upper()} {size} @ {limit_price}")
    entry = place_order(product_id, side, size, "limit_order", limit_price=limit_price)
    if not entry:
        print("[ABORT] Entry order fail. Trade nahi hua.")
        return

    entry_id = entry["id"]

    # ── 2. WAIT FOR FILL ────────────────────
    print("[WAIT] Entry fill hone ka intezaar...")
    while True:
        time.sleep(2)
        status = get_order_status(entry_id)
        print(f"       Entry status: {status}")

        if status == "filled":
            print("[OK] Entry Filled!")
            break
        if status in ["cancelled", "rejected", "unknown"]:
            print(f"[ABORT] Entry {status}. Trade exit.")
            return

    # ── 3. SL + TP ──────────────────────────
    exit_side = "sell" if side == "buy" else "buy"

    print(f"[SL] Placing Stop Loss @ {stop_price}")
    sl = place_order(product_id, exit_side, size, "stop_market_order", stop_price=stop_price)

    time.sleep(1)  # slight delay between orders

    print(f"[TP] Placing Take Profit @ {target_price}")
    tp = place_order(product_id, exit_side, size, "limit_order", limit_price=target_price)

    if not sl or not tp:
        print("[ERROR] SL ya TP place nahi hua! Manual check karo!")
        return

    sl_id = sl["id"]
    tp_id = tp["id"]

    # ── 4. OCO MONITOR ──────────────────────
    print("\n[OCO] Monitoring SL + TP...")
    while True:
        time.sleep(2)

        sl_status = get_order_status(sl_id)
        tp_status = get_order_status(tp_id)
        print(f"       SL: {sl_status} | TP: {tp_status}")

        if sl_status == "filled":
            print("[RESULT] ❌ Stoploss hit! TP cancel kar rahe hain...")
            cancel_order(tp_id)
            break

        if tp_status == "filled":
            print("[RESULT] ✅ Target hit! SL cancel kar rahe hain...")
            cancel_order(sl_id)
            break


# ─────────────────────────────────────────────
# 6. BACKTESTING SCAFFOLD
# Strategy baad mein yahan add karo
# ─────────────────────────────────────────────
def backtest(candles: list[dict], strategy_fn) -> dict:
    """
    Simple backtester.

    candles: list of dicts — {'open', 'high', 'low', 'close', 'volume'}
    strategy_fn: function jo candles le aur
                 {'side', 'limit_price', 'stop_price', 'target_price'} return kare
                 ya None return kare agar koi signal nahi

    Returns: {'trades': [...], 'total_pnl': float, 'win_rate': float}
    """
    trades   = []
    wins     = 0
    total_pnl = 0.0

    for i in range(1, len(candles)):
        past_candles = candles[:i]
        signal       = strategy_fn(past_candles)

        if not signal:
            continue

        # Simulate: next candle pe execute
        next_c = candles[i]
        side   = signal["side"]
        entry  = signal["limit_price"]
        sl     = signal["stop_price"]
        tp     = signal["target_price"]

        # Check agar SL ya TP hit hua next candle mein
        if side == "buy":
            if next_c["low"] <= sl:
                pnl = sl - entry
                result = "SL"
            elif next_c["high"] >= tp:
                pnl = tp - entry
                result = "TP"
            else:
                continue  # No fill in this candle
        else:  # sell
            if next_c["high"] >= sl:
                pnl = entry - sl
                result = "SL"
            elif next_c["low"] <= tp:
                pnl = entry - tp
                result = "TP"
            else:
                continue

        total_pnl += pnl
        if result == "TP":
            wins += 1

        trades.append({
            "candle_index": i,
            "side":   side,
            "entry":  entry,
            "sl":     sl,
            "tp":     tp,
            "pnl":    round(pnl, 2),
            "result": result,
        })

    win_rate = (wins / len(trades) * 100) if trades else 0.0

    print(f"\n{'='*40}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*40}")
    print(f"  Total Trades : {len(trades)}")
    print(f"  Wins         : {wins}")
    print(f"  Win Rate     : {win_rate:.1f}%")
    print(f"  Total PnL    : {total_pnl:.2f}")
    print(f"{'='*40}\n")

    return {"trades": trades, "total_pnl": total_pnl, "win_rate": win_rate}


# ─────────────────────────────────────────────
# 7. EXAMPLE STRATEGY (baad mein replace karo)
# ─────────────────────────────────────────────
def example_strategy(candles: list[dict]) -> dict | None:
    """
    Bahut simple example strategy:
    Agar last 3 candles mein close badhta raha → Buy signal

    Baad mein yahan apni strategy likhna:
    - RSI
    - Moving Average Crossover
    - Breakout, etc.
    """
    if len(candles) < 3:
        return None

    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if c1["close"] < c2["close"] < c3["close"]:
        price = c3["close"]
        return {
            "side":        "buy",
            "limit_price": price,
            "stop_price":  price * 0.98,   # 2% SL
            "target_price": price * 1.04,  # 4% TP
        }
    return None


# ─────────────────────────────────────────────
# 8. MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":

    SYMBOL       = "BTCUSD"      # Bug Fix: pehle SYMBOL defined nahi tha
    SIDE         = "sell"
    SIZE         = 1
    LIMIT_PRICE  = 30000
    STOP_PRICE   = 35000
    TARGET_PRICE = 68140

    # ── Product ID fetch ──
    product_id = get_product_id(SYMBOL)
    if not product_id:
        print("[ABORT] Product ID nahi mila.")
        exit(1)

    # ── Live Price Feed shuru karo (background) ──
    # Bug Fix: pehle yeh main thread block karta tha
    price_feed = PriceFeed(SYMBOL)
    price_feed.start()

    # Thoda wait karo pehli price aane tak
    time.sleep(2)
    print(f"[INFO] Current Price: {price_feed.get_price()}")

    # ── Trade execute karo ──
    execute_trade(
        product_id   = product_id,
        side         = SIDE,
        size         = SIZE,
        limit_price  = LIMIT_PRICE,
        stop_price   = STOP_PRICE,
        target_price = TARGET_PRICE,
    )

    # ── Backtest example ──
    # (Real candles Delta API se fetch karo, yeh sirf demo data hai)
    sample_candles = [
        {"open": 29000, "high": 29500, "low": 28800, "close": 29200, "volume": 100},
        {"open": 29200, "high": 29800, "low": 29100, "close": 29600, "volume": 120},
        {"open": 29600, "high": 30200, "low": 29500, "close": 30000, "volume": 150},
        {"open": 30000, "high": 30800, "low": 29900, "close": 30500, "volume": 200},
        {"open": 30500, "high": 31000, "low": 29800, "close": 29900, "volume": 180},
    ]
    backtest(sample_candles, example_strategy)
