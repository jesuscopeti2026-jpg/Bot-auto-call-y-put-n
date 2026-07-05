import time
import os
import requests
import pandas as pd
import sys
import logging

from iqoptionapi.stable_api import IQ_Option
from strategy import add_indicators, pro_signal

logging.getLogger().setLevel(logging.CRITICAL)
sys.stderr = open(os.devnull, 'w')

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

AMOUNT = 20

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC"
]

trade_open = False
last_trade_time = 0
bot_active = True
last_update_id = None
current_expiration = 1

# ================= TELEGRAM =================

def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception:
        pass


def check_commands():
    global bot_active, last_update_id

    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"timeout": 1, "offset": last_update_id},
            timeout=5
        ).json()

        for result in r.get("result", []):
            last_update_id = result["update_id"] + 1

            text = result.get("message", {}).get("text", "")

            if text == "/stop":
                bot_active = False
                send("⛔ BOT DETENIDO")

            elif text == "/start":
                bot_active = True
                send("✅ BOT ACTIVADO")

    except Exception:
        pass

# ================= IQ OPTION =================

iq = IQ_Option(EMAIL, PASSWORD)
iq.connect()

if not iq.check_connect():
    print("❌ Error de conexión con IQ Option")
    exit()

iq.change_balance("PRACTICE")

print("🔥 BOT ACTIVO")
send("🔥 BOT ACTIVO")

# ================= DATOS =================

def get_candles(pair, tf):
    try:
        data = iq.get_candles(pair, tf, 100, time.time())
        df = pd.DataFrame(data)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        return add_indicators(df)
    except Exception:
        return None

# ================= TRADE =================

def trade(pair, direction, expiration):
    global trade_open, last_trade_time, current_expiration

    status, _ = iq.buy(AMOUNT, pair, direction, expiration)

    if status:
        trade_open = True
        last_trade_time = time.time()
        current_expiration = expiration

        msg = f"🎯 {pair} {direction.upper()} ({expiration}m)"
        print(msg)
        send(msg)
    else:
        print(f"❌ No se pudo abrir operación en {pair}")

# ================= LOOP PRINCIPAL =================

while True:
    try:
        check_commands()

        if not bot_active:
            time.sleep(1)
            continue

        if trade_open:
            if time.time() - last_trade_time > current_expiration * 60:
                trade_open = False
            else:
                time.sleep(1)
                continue

        t = int(iq.get_server_timestamp())

        # Espera hasta los últimos segundos de la vela
        if t % 60 < 55:
            time.sleep(0.2)
            continue

        for pair in PAIRS:

            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)
            df_h3 = get_candles(pair, 900)

            if df_m1 is None or df_m5 is None or df_h3 is None:
                continue

            signal, expiration = pro_signal(df_m1, df_m5, df_h3)

            if signal:
                trade(pair, signal, expiration)
                break

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
