"""
Delta Exchange Trading Bot  v2.0
=================================
Features:
  1. Manual ya Strategy se price input — Maker entry + auto SL/TP
  2. Trade EXIT bhi Maker hogi (post_only TP)
  3. Wallet real-time info — balance, available lots, margin
  4. Leverage set karo trade se pehle
  5. Liquidation protection — auto warning + position reduce
"""

import websocket
import json
import time
import hmac
import hashlib
import requests
import threading

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────
API_KEY = "iRB8OkexongFx2IWRJ7XrnmsIAdLSF"
API_SECRET = "RaQ1xQPrIia9NgsnaRDs1y5q2vHr7o9jgbfSNQGWjZEet4SJN5OtHClufcmL"
BASE_URL = "https://cdn-ind.testnet.deltaex.org"
WS_URL = "wss://socket-ind.testnet.deltaex.org"

# Risk Settings
LEVERAGE = 10  # Leverage (1x to 100x)
SL_PERCENT = 1.5  # Stop Loss %  (entry se kitna door)
TP_PERCENT = 3.0  # Take Profit % (entry se kitna door)
MAX_WALLET_RISK_PCT = 2.0  # Ek trade mein wallet ka max % risk
LIQUIDATION_WARN_PCT = 10.0  # Agar available balance <10% toh warning


# ─────────────────────────────────────────────────────────────
# 1. SIGNATURE + HEADERS
# ─────────────────────────────────────────────────────────────
def generate_signature(secret, method, path, timestamp, body="", query_string=""):
    message = method + timestamp + path + query_string + body
    return hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def make_headers(method, path, body="", query_string=""):
    timestamp = str(int(time.time()))
    sig = generate_signature(API_SECRET, method, path, timestamp, body, query_string)
    return {
        "api-key": API_KEY,
        "timestamp": timestamp,
        "signature": sig,
        "User-Agent": "python-3.10",
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────
# 2. PRODUCT INFO
# ─────────────────────────────────────────────────────────────
def get_product(symbol):
    url = BASE_URL + "/v2/products"
    data = requests.get(url, timeout=10).json()
    for p in data.get("result", []):
        if p["symbol"] == symbol:
            return p
    return None


# ─────────────────────────────────────────────────────────────
# 3. WALLET MONITOR  (Feature 3 — real-time balance)
# ─────────────────────────────────────────────────────────────
class WalletMonitor:
    """
    Wallet ki real-time info:
    - balance, available_balance, blocked_margin
    - kitne lots buy kar sakte ho
    - liquidation warning (Feature 5)
    """

    def __init__(self, asset_symbol="USDT", refresh_sec=10):
        self.asset_symbol = asset_symbol
        self.refresh_sec = refresh_sec
        self._data = {}
        self._thread = None
        self._stop_flag = False

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[WALLET] Monitor started ({self.refresh_sec}s refresh)")

    def stop(self):
        self._stop_flag = True

    def _run(self):
        while not self._stop_flag:
            self._fetch()
            self._check_liquidation_risk()
            time.sleep(self.refresh_sec)

    def _fetch(self):
        path = "/v2/wallet/balances"
        headers = make_headers("GET", path)
        try:
            resp = requests.get(BASE_URL + path, headers=headers, timeout=10).json()
            if resp.get("success"):
                for asset in resp["result"]:
                    if asset["asset_symbol"] == self.asset_symbol:
                        self._data = asset
                        return
        except Exception as e:
            print(f"[WALLET ERROR] {e}")

    def _check_liquidation_risk(self):
        """Feature 5 — Liquidation warning"""
        bal = self.get_balance()
        avail = self.get_available()
        if bal and bal > 0:
            pct = (avail / bal) * 100
            if pct < LIQUIDATION_WARN_PCT:
                print(f"\n*** LIQUIDATION WARNING ***")
                print(f"    Available sirf {pct:.1f}% bacha hai!")
                print(f"    Balance: {bal:.2f} | Available: {avail:.2f}")
                print(f"    ACTION: Naya trade mat lo! Position reduce karo!")
                print(f"*** *** *** *** *** *** ***\n")

    def get_balance(self):
        val = self._data.get("balance")
        return float(val) if val else None

    def get_available(self):
        val = self._data.get("available_balance")
        return float(val) if val else None

    def get_blocked_margin(self):
        val = self._data.get("blocked_margin")
        return float(val) if val else None

    def get_position_margin(self):
        val = self._data.get("position_margin")
        return float(val) if val else None

    def max_lots(self, price, leverage, contract_value):
        """
        Kitne lots safely buy kar sakte ho?
        Formula: safe_balance / margin_per_lot
        margin_per_lot = (price x contract_value) / leverage
        """
        avail = self.get_available()
        if not avail or not price or not leverage:
            return 0
        safe_balance = avail * (MAX_WALLET_RISK_PCT / 100)
        margin_per_lot = (price * contract_value) / leverage
        if margin_per_lot <= 0:
            return 0
        return max(int(safe_balance / margin_per_lot), 0)

    def print_status(self, price=None, leverage=LEVERAGE, contract_value=0.001):
        bal = self.get_balance()
        avail = self.get_available()
        block = self.get_blocked_margin()
        pos_m = self.get_position_margin()
        lots = self.max_lots(price, leverage, contract_value) if price else "N/A"
        pct = f"{(avail/bal*100):.1f}%" if (bal and bal > 0 and avail) else "N/A"
        warn = (
            " <<< LOW!"
            if (
                bal and avail and bal > 0 and (avail / bal * 100) < LIQUIDATION_WARN_PCT
            )
            else ""
        )

        print(
            f"""
+------------------------------------------+
|           WALLET STATUS                  |
+------------------------------------------+
|  Total Balance   : {str(bal or 'N/A')+" USDT":<22}|
|  Available       : {str(avail or 'N/A')+" USDT":<22}|{warn}
|  Available %     : {pct:<22}|
|  Blocked Margin  : {str(block or 'N/A')+" USDT":<22}|
|  Position Margin : {str(pos_m or 'N/A')+" USDT":<22}|
+------------------------------------------+
|  Current Price   : {str(price or 'N/A'):<22}|
|  Leverage        : {str(leverage)+"x":<22}|
|  Max Safe Lots   : {str(lots):<22}|
|  (Risk: {MAX_WALLET_RISK_PCT}% per trade)                   |
+------------------------------------------+"""
        )


# ─────────────────────────────────────────────────────────────
# 4. LEVERAGE SET  (Feature 4)
# ─────────────────────────────────────────────────────────────
def set_leverage(product_id, leverage):
    """Feature 4 — Trade se pehle leverage set karo"""
    path = f"/v2/products/{product_id}/orders/leverage"
    body_dict = {"leverage": str(leverage)}
    body_json = json.dumps(body_dict, separators=(",", ":"))
    headers = make_headers("POST", path, body=body_json)
    resp = requests.post(
        BASE_URL + path, headers=headers, data=body_json, timeout=10
    ).json()
    if resp.get("success"):
        print(f"[LEVERAGE] {leverage}x set successfully!")
        return True
    else:
        print(f"[LEVERAGE] Response: {resp}")
        print(f"[LEVERAGE] Leverage {leverage}x will be applied per order.")
        return False


# ─────────────────────────────────────────────────────────────
# 5. LIVE PRICE FEED
# ─────────────────────────────────────────────────────────────
class PriceFeed:
    def __init__(self, symbol):
        self.symbol = symbol
        self.latest_price = None
        self._thread = None
        self._connected = False
        self._msg_count = 0
        self._last_msg_time = None
        self._error = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[WS] Price feed started for {self.symbol}")

    def _run(self):
        def on_open(ws):
            self._connected = True
            self._error = None
            print("[WS] Connected!")
            ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "payload": {
                            "channels": [
                                {"name": "v2/ticker", "symbols": [self.symbol]}
                            ]
                        },
                    }
                )
            )

        def on_message(ws, message):
            data = json.loads(message)
            if data.get("type") == "v2/ticker":
                self.latest_price = float(data["mark_price"])
                self._msg_count += 1
                self._last_msg_time = time.time()
                print(f"[PRICE] {self.symbol}: {self.latest_price:.2f}")

        def on_error(ws, error):
            self._error = str(error)
            self._connected = False
            print(f"[WS ERROR] {error}")

        def on_close(ws, code, msg):
            self._connected = False
            print(f"[WS] Closed: {code}")

        websocket.WebSocketApp(
            WS_URL,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        ).run_forever(ping_interval=30, ping_timeout=10)

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def is_connected(self):
        return self._connected

    def is_receiving(self, stale_after_sec=10):
        if self._last_msg_time is None:
            return False
        return (time.time() - self._last_msg_time) < stale_after_sec

    def get_price(self):
        return self.latest_price

    def status(self):
        t = "Running" if self.is_running() else "Stopped"
        w = "Connected" if self.is_connected() else "Disconnected"
        p = "Receiving" if self.is_receiving() else "Stale/None"
        pv = f"{self.latest_price:.2f}" if self.latest_price else "N/A"
        ls = (
            f"{time.time()-self._last_msg_time:.1f}s ago"
            if self._last_msg_time
            else "never"
        )
        print(
            f"""
+-------------------------------------+
|       PriceFeed STATUS              |
+-------------------------------------+
|  Symbol   : {self.symbol:<25}|
|  Thread   : {t:<25}|
|  WebSocket: {w:<25}|
|  Prices   : {p:<25}|
|  Last     : {pv:<25}|
|  Updated  : {ls:<25}|
|  Count    : {str(self._msg_count):<25}|
+-------------------------------------+"""
        )


# ─────────────────────────────────────────────────────────────
# 6. ORDER HELPERS
# ─────────────────────────────────────────────────────────────
def get_order_status(order_id):
    path = f"/v2/orders/{order_id}"
    headers = make_headers("GET", path)
    resp = requests.get(BASE_URL + path, headers=headers, timeout=10).json()
    if resp.get("success"):
        return resp["result"]["state"]
    print(f"[WARN] Status fetch failed: {resp}")
    return "unknown"


def cancel_order(order_id):
    path = f"/v2/orders/{order_id}"
    headers = make_headers("DELETE", path)
    resp = requests.delete(BASE_URL + path, headers=headers, timeout=10).json()
    if resp.get("success"):
        print(f"[INFO] Order {order_id} cancelled.")
        return True
    print(f"[WARN] Cancel failed: {resp}")
    return False


def place_order(
    product_id,
    side,
    size,
    order_type,
    limit_price=None,
    stop_price=None,
    post_only=False,
    leverage=None,
):
    path = "/v2/orders"
    body_dict = {
        "product_id": product_id,
        "size": size,
        "side": side,
        "order_type": order_type,
        "time_in_force": "gtc",
    }
    if limit_price is not None:
        body_dict["limit_price"] = str(limit_price)
    if stop_price is not None:
        body_dict["stop_price"] = str(stop_price)
    if leverage is not None:
        body_dict["leverage"] = str(leverage)
    if post_only and order_type == "limit_order":
        body_dict["post_only"] = True

    body_json = json.dumps(body_dict, separators=(",", ":"))
    headers = make_headers("POST", path, body=body_json)
    data = requests.post(
        BASE_URL + path, headers=headers, data=body_json, timeout=10
    ).json()

    if data.get("success"):
        oid = data["result"]["id"]
        tag = " [MAKER]" if post_only else ""
        print(f"[ORDER] {order_type} {side} → id={oid}{tag}")
        return data["result"]
    else:
        err = data.get("error", {})
        if post_only and err.get("code") == "post_only_would_be_taker":
            print(f"[MAKER REJECT] Taker ban raha tha — reject! Price adjust karo.")
        else:
            print(f"[ERROR] Order failed: {data}")
        return None


# ─────────────────────────────────────────────────────────────
# 7. MAIN TRADE FUNCTION  (Feature 1 + 2)
# ─────────────────────────────────────────────────────────────
def execute_trade(
    product_id, side, size, entry_price, sl_price=None, tp_price=None, leverage=LEVERAGE
):
    """
    Feature 1: entry_price manual ya strategy se aayegi
    Feature 2: Entry + TP = MAKER | SL = Market (guaranteed fill)

    sl_price/tp_price = None rakho toh SL_PERCENT/TP_PERCENT se auto banega
    """

    # Auto SL/TP calculate
    if sl_price is None:
        sl_price = (
            round(entry_price * (1 - SL_PERCENT / 100), 1)
            if side == "buy"
            else round(entry_price * (1 + SL_PERCENT / 100), 1)
        )
    if tp_price is None:
        tp_price = (
            round(entry_price * (1 + TP_PERCENT / 100), 1)
            if side == "buy"
            else round(entry_price * (1 - TP_PERCENT / 100), 1)
        )

    print(
        f"""
+------------------------------------------+
|           TRADE SUMMARY                  |
+------------------------------------------+
|  Side      : {side.upper():<28}|
|  Size      : {str(size)+" lots":<28}|
|  Entry     : {str(entry_price):<28}|
|  Stop Loss : {str(sl_price)+" (auto)" if sl_price else "N/A":<28}|
|  Target    : {str(tp_price)+" (auto)" if tp_price else "N/A":<28}|
|  Leverage  : {str(leverage)+"x":<28}|
+------------------------------------------+
|  Entry = MAKER (post_only)               |
|  TP    = MAKER (post_only) - fees kam!   |
|  SL    = Market (guaranteed fill)        |
+------------------------------------------+"""
    )

    # 1. ENTRY — MAKER
    entry = place_order(
        product_id,
        side,
        size,
        "limit_order",
        limit_price=entry_price,
        post_only=True,
        leverage=leverage,
    )
    if not entry:
        print("[ABORT] Entry fail.")
        return

    entry_id = entry["id"]
    exit_side = "sell" if side == "buy" else "buy"

    # 2. WAIT FOR FILL
    print("[WAIT] Entry fill hone ka intezaar...")
    while True:
        time.sleep(2)
        status = get_order_status(entry_id)
        print(f"       Status: {status}")
        if status == "filled":
            print("[OK] Entry Filled!")
            break
        if status in ["cancelled", "rejected", "unknown"]:
            print(f"[ABORT] Entry {status}.")
            return

    # 3. SL — Market order (taker, but SL guaranteed fill honi chahiye)
    print(f"[SL]  @ {sl_price}  (Market — guaranteed)")
    sl = place_order(
        product_id,
        exit_side,
        size,
        "stop_market_order",
        stop_price=sl_price,
        post_only=False,
    )

    time.sleep(1)

    # 4. TP — MAKER (Feature 2)
    print(f"[TP]  @ {tp_price}  (MAKER — fees kam!)")
    tp = place_order(
        product_id, exit_side, size, "limit_order", limit_price=tp_price, post_only=True
    )

    if not sl or not tp:
        print("[ERROR] SL ya TP place nahi hua! Manual check karo!")
        return

    # 5. OCO Monitor
    print("\n[OCO] Monitoring...")
    while True:
        time.sleep(2)
        sl_st = get_order_status(sl["id"])
        tp_st = get_order_status(tp["id"])
        print(f"      SL: {sl_st} | TP: {tp_st}")
        if sl_st == "filled":
            print("[RESULT] SL hit! TP cancel...")
            cancel_order(tp["id"])
            break
        if tp_st == "filled":
            print("[RESULT] Target hit! SL cancel...")
            cancel_order(sl["id"])
            break


# ─────────────────────────────────────────────────────────────
# 8. BACKTEST SCAFFOLD
# ─────────────────────────────────────────────────────────────
def backtest(candles, strategy_fn):
    trades, wins, total_pnl = [], 0, 0.0

    for i in range(1, len(candles)):
        signal = strategy_fn(candles[:i])
        if not signal:
            continue
        next_c = candles[i]
        side = signal["side"]
        entry = signal["entry_price"]
        sl = signal.get("sl_price") or (
            entry * (1 - SL_PERCENT / 100)
            if side == "buy"
            else entry * (1 + SL_PERCENT / 100)
        )
        tp = signal.get("tp_price") or (
            entry * (1 + TP_PERCENT / 100)
            if side == "buy"
            else entry * (1 - TP_PERCENT / 100)
        )

        if side == "buy":
            if next_c["low"] <= sl:
                pnl, r = sl - entry, "SL"
            elif next_c["high"] >= tp:
                pnl, r = tp - entry, "TP"
            else:
                continue
        else:
            if next_c["high"] >= sl:
                pnl, r = entry - sl, "SL"
            elif next_c["low"] <= tp:
                pnl, r = entry - tp, "TP"
            else:
                continue

        total_pnl += pnl
        if r == "TP":
            wins += 1
        trades.append(
            {
                "i": i,
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "pnl": round(pnl, 2),
                "result": r,
            }
        )

    wr = (wins / len(trades) * 100) if trades else 0.0
    print(f"\n{'='*35}\n  BACKTEST RESULTS\n{'='*35}")
    print(f"  Trades   : {len(trades)}")
    print(f"  Wins     : {wins}")
    print(f"  Win Rate : {wr:.1f}%")
    print(f"  Total PnL: {total_pnl:.2f}\n{'='*35}\n")
    return {"trades": trades, "total_pnl": total_pnl, "win_rate": wr}


# ─────────────────────────────────────────────────────────────
# 9. EXAMPLE STRATEGY
# ─────────────────────────────────────────────────────────────
def example_strategy(candles):
    """
    Yahan apni strategy daalo.
    Return: {"side": "buy"/"sell", "entry_price": float}
    Optional: "sl_price", "tp_price" bhi de sakte ho
    """
    if len(candles) < 3:
        return None
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    if c1["close"] < c2["close"] < c3["close"]:
        return {"side": "buy", "entry_price": c3["close"]}
    return None


# ─────────────────────────────────────────────────────────────
# 10. MAIN
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":

    SYMBOL = "BTCUSD"
    SIDE = "buy"
    SIZE = 1

    # Product info
    product = get_product(SYMBOL)
    if not product:
        print("[ABORT] Product nahi mila.")
        exit(1)

    product_id = product["id"]
    contract_value = float(product.get("contract_value", 0.001))
    print(f"[INFO] {SYMBOL} id={product_id}, contract={contract_value}")

    # Feature 4: Leverage set karo
    set_leverage(product_id, LEVERAGE)

    # Wallet monitor start
    wallet = WalletMonitor(asset_symbol="USDT", refresh_sec=15)
    wallet.start()

    # Price feed start
    price_feed = PriceFeed(SYMBOL)
    price_feed.start()

    # Pehli price ka wait
    print("[WAIT] Pehli price ka intezaar...")
    for i in range(15):
        time.sleep(1)
        if price_feed.get_price() is not None:
            print(f"[OK] Price aayi ({i+1}s): {price_feed.get_price():.2f}")
            break
        print(f"       {i+1}s...")

    price_feed.status()
    time.sleep(3)

    current_price = price_feed.get_price()
    wallet.print_status(
        price=current_price, leverage=LEVERAGE, contract_value=contract_value
    )

    # Kitne lots safe hain?
    safe_lots = wallet.max_lots(current_price, LEVERAGE, contract_value)
    print(f"\n[INFO] Max safe lots: {safe_lots}")

    # ─────────────────────────────────────────
    # OPTION A: Manual price
    # ─────────────────────────────────────────
    ENTRY_PRICE = 94000.0  # <-- Apni price yahan daalo
    SL_PRICE = None  # None = auto (SL_PERCENT se)
    TP_PRICE = None  # None = auto (TP_PERCENT se)

    # ─────────────────────────────────────────
    # OPTION B: Strategy se (uncomment karo)
    # ─────────────────────────────────────────
    # signal = example_strategy(your_candles)
    # if signal:
    #     ENTRY_PRICE = signal["entry_price"]
    #     SL_PRICE    = signal.get("sl_price")
    #     TP_PRICE    = signal.get("tp_price")

    execute_trade(
        product_id=product_id,
        side=SIDE,
        size=min(SIZE, safe_lots),
        entry_price=ENTRY_PRICE,
        sl_price=SL_PRICE,
        tp_price=TP_PRICE,
        leverage=LEVERAGE,
    )

    # Trade ke baad wallet check
    time.sleep(3)
    print("\n[POST-TRADE] Wallet:")
    wallet.print_status(
        price=price_feed.get_price(), leverage=LEVERAGE, contract_value=contract_value
    )
