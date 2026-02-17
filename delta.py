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

def place_market_order(product_id, side, size):

    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    timestamp = str(int(time.time()))

    body_dict = {
        "product_id": product_id,
        "size": size,
        "side": side,
        "order_type": "market_order",
    }

    body = json.dumps(body_dict, separators=(",", ":"))

    signature = generate_signature(API_SECRET, method, path, timestamp, body)

    headers = {
        "api-key": API_KEY,
        "timestamp": timestamp,
        "signature": signature,
        "User-Agent": "python-3.10",
        "Content-Type": "application/json",
    }

    response = requests.post(url, headers=headers, data=body)
    print("Order Response:")
    print(response.json())

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
            print(f"Live Price: {price}")

    ws = websocket.WebSocketApp(WS_URL, on_message=on_message, on_open=on_open)
    ws.run_forever()

def place_limit_with_stoploss(product_id, side, size, limit_price, stop_price):

    method = "POST"
    path = "/v2/orders"
    url = BASE_URL + path
    timestamp = str(int(time.time()))
    limit_body = {
        "product_id": product_id,
        "size": size,
        "side": side,
        "order_type": "limit_order",
        "limit_price": str(limit_price),
        "time_in_force": "gtc"
    }

    limit_json = json.dumps(limit_body, separators=(",", ":"))
    signature = generate_signature(API_SECRET, method, path, timestamp, limit_json)

    headers = {
        "api-key": API_KEY,
        "timestamp": timestamp,
        "signature": signature,
        "User-Agent": "python-3.10",
        "Content-Type": "application/json",
    }

    limit_response = requests.post(url, headers=headers, data=limit_json)
    print("Limit Order Response:")
    print(limit_response.json())

    time.sleep(1)
    timestamp = str(int(time.time()))


    stop_side = "sell" if side == "buy" else "buy"
    stop_body = {
        "product_id": product_id,
        "size": size,
        "side": stop_side,
        "order_type": "stop_market_order",
        "stop_price": str(stop_price)
    }
    
    stop_json = json.dumps(stop_body, separators=(",", ":"))
    signature = generate_signature(API_SECRET, method, path, timestamp, stop_json)
    headers["timestamp"] = timestamp
    headers["signature"] = signature
    stop_response = requests.post(url, headers=headers, data=stop_json)
    print("Stoploss Order Response:")
    print(stop_response.json())

if __name__ == "__main__":

    SYMBOL = "BTCUSD"
    SIDE = "sell"  # "buy" or "sell"
    SIZE = 1
    product_id = get_product_id(SYMBOL)

    # place_market_order(product_id, SIDE, SIZE))
    show_live_price(SYMBOL)

    place_limit_with_stoploss(
        product_id=product_id,
        side="sell",           # entry side
        size=1,
        limit_price=68045.8,    # entry price
        stop_price=67900      # stoploss price
    )

   