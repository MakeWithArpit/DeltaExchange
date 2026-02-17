import websocket
import json
import time
import hmac
import hashlib
import requests

API_KEY = "iRB8OkexongFx2IWRJ7XrnmsIAdLSF"
API_SECRET = "RaQ1xQPrIia9NgsnaRDs1y5q2vHr7o9jgbfSNQGWjZEet4SJN5OtHClufcmL"
BASE_URL = "https://cdn-ind.testnet.deltaex.org"
WS_URL = "wss://socket-ind.testnet.deltaex.org"


def generate_signature(secret, method, path, timestamp, body="", query_string=""):
    message = method + timestamp + path + query_string + body
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def get_product_id(symbol_name):
    url = BASE_URL + "/v2/products"
    response = requests.get(url)
    data = response.json()

    for product in data["result"]:
        if product["symbol"] == symbol_name:
            return product["id"]

    return None


def show_live_price(symbol_name):

    def on_open(ws):
        print("Connected to WebSocket")

        subscribe_msg = {
            "type": "subscribe",
            "payload": {"channels": [{"name": "v2/ticker", "symbols": [symbol_name]}]},
        }
        ws.send(json.dumps(subscribe_msg))

    def on_message(ws, message):
        data = json.loads(message)

        if data.get("type") == "v2/ticker":
            price = float(data["mark_price"])
            print(f"Live Price: {price:.3f}")

    ws = websocket.WebSocketApp(WS_URL, on_message=on_message, on_open=on_open)
    ws.run_forever()


def buy_sell(product_id, side, size, limit_price, stop_price, target_price):

    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path

    headers = {
        "api-key": API_KEY,
        "User-Agent": "python-3.10",
        "Content-Type": "application/json",
    }

    # =========================
    # 1️⃣ ENTRY ORDER
    # =========================
    timestamp = str(int(time.time()))

    entry_body = {
        "product_id": product_id,
        "size": size,
        "side": side,
        "order_type": "limit_order",
        "limit_price": str(limit_price),
        "time_in_force": "gtc",
    }

    entry_json = json.dumps(entry_body, separators=(",", ":"))
    signature = generate_signature(API_SECRET, method, path, timestamp, entry_json)

    headers["timestamp"] = timestamp
    headers["signature"] = signature

    entry_res = requests.post(url, headers=headers, data=entry_json).json()
    print("Entry:", entry_res)

    if not entry_res.get("success"):
        print("Entry failed")
        return
    

    

    entry_order_id = entry_res["result"]["id"]

    # =========================
    # 2️⃣ WAIT FOR FILL
    # =========================
    print("Waiting for entry fill...")

    while True:
        time.sleep(2)

        order_check = requests.get(
            BASE_URL + f"/v2/orders/{entry_order_id}", headers=headers
        ).json()

        status = order_check["result"]["state"]

        if status == "filled":
            print("Entry Filled!")
            break

        if status in ["cancelled", "rejected"]:
            print("Entry not filled. Exiting.")
            return

    # =========================
    # 3️⃣ PLACE SL + TP
    # =========================

    exit_side = "sell" if side == "buy" else "buy"

    # Stoploss
    timestamp = str(int(time.time()))
    sl_body = {
        "product_id": product_id,
        "size": size,
        "side": exit_side,
        "order_type": "stop_market_order",
        "stop_price": str(stop_price),
    }

    sl_json = json.dumps(sl_body, separators=(",", ":"))
    signature = generate_signature(API_SECRET, method, path, timestamp, sl_json)

    headers["timestamp"] = timestamp
    headers["signature"] = signature

    sl_res = requests.post(url, headers=headers, data=sl_json).json()
    sl_id = sl_res["result"]["id"]
    print("SL Placed:", sl_res)

    time.sleep(1)

    # Target
    timestamp = str(int(time.time()))
    tp_body = {
        "product_id": product_id,
        "size": size,
        "side": exit_side,
        "order_type": "limit_order",
        "limit_price": str(target_price),
        "time_in_force": "gtc",
    }

    tp_json = json.dumps(tp_body, separators=(",", ":"))
    signature = generate_signature(API_SECRET, method, path, timestamp, tp_json)

    headers["timestamp"] = timestamp
    headers["signature"] = signature

    tp_res = requests.post(url, headers=headers, data=tp_json).json()
    tp_id = tp_res["result"]["id"]
    print("TP Placed:", tp_res)

    # =========================
    # 4️⃣ OCO MONITOR
    # =========================
    print("Monitoring OCO...")

    while True:
        time.sleep(2)

        sl_status = requests.get(
            BASE_URL + f"/v2/orders/{sl_id}", headers=headers
        ).json()["result"]["state"]

        tp_status = requests.get(
            BASE_URL + f"/v2/orders/{tp_id}", headers=headers
        ).json()["result"]["state"]

        if sl_status == "filled":
            print("Stoploss hit. Cancelling TP...")
            requests.delete(BASE_URL + f"/v2/orders/{tp_id}", headers=headers)
            break

        if tp_status == "filled":
            print("Target hit. Cancelling SL...")
            requests.delete(BASE_URL + f"/v2/orders/{sl_id}", headers=headers)
            break


if __name__ == "__main__":

    # productID = get_product_id("BTCUSD")
    LIMIT_PRICE = 30000
    STOP_PRICE = 35000
    TARGET_PRICE = 68140
    SIDE = "sell"
    SIZE = 1

    # show_live_price(SYMBOL)
    # buy_sell(
    #     product_id=productID,
    #     side=SIDE,
    #     size=SIZE,
    #     limit_price=LIMIT_PRICE,
    #     stop_price=STOP_PRICE,
    #     target_price=TARGET_PRICE,
    # )

    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'api-key': API_KEY,
        'signature': API_SECRET,
        'timestamp': str(int(time.time()))
    }

    r = requests.put('https://cdn-ind.testnet.deltaex.org/v2/orders', params={}, headers = headers)
    print(r.json())