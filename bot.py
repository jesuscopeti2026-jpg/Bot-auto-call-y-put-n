import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import logging

from iqoptionapi.stable_api import IQ_Option

# ================= CONFIG =================

logging.getLogger().setLevel(logging.CRITICAL)
sys.stderr = open(os.devnull, 'w')

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TIMEFRAME = 60
EXPIRATION = 1
BASE_AMOUNT = 2000  # 🔥 baja riesgo

MAX_LOSS_STREAK = 3

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

# ================= ESTADO =================

trade_open = False
last_trade_time = 0
last_trade_candle = None
loss_streak = 0

# ================= TELEGRAM =================

def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass

# ================= IQ =================

iq = IQ_Option(EMAIL, PASSWORD)
iq.connect()

if not iq.check_connect():
    print("❌ Error conectando")
    exit()

iq.change_balance("PRACTICE")

print("🔥 BOT PRO ACTIVO")
send("🔥 BOT PRO ACTIVO")

# ================= INDICADORES =================

def indicators(df):
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    df["tr"] = np.maximum(df["high"] - df["low"],
                np.maximum(abs(df["high"] - df["close"].shift()),
                           abs(df["low"] - df["close"].shift())))
    df["atr"] = df["tr"].rolling(14).mean()

    return df

# ================= DATOS =================

def get_candles(pair, tf):
    try:
        data = iq.get_candles(pair, tf, 100, time.time())
        df = pd.DataFrame(data)

        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        return indicators(df)

    except:
        return None

# ================= SNIPER PRO =================

def sniper_pro(df_m1, df_m5):

    last = df_m1.iloc[-1]
    prev = df_m1.iloc[-2]

    # 🔥 tendencia M5 (filtro fuerte)
    trend_up = df_m5.iloc[-1]["ema20"] > df_m5.iloc[-1]["ema50"]
    trend_down = df_m5.iloc[-1]["ema20"] < df_m5.iloc[-1]["ema50"]

    # 🔥 evitar lateral
    if last["atr"] < df_m1["atr"].mean():
        return None

    body = abs(last["close"] - last["open"])
    range_ = last["high"] - last["low"]

    if range_ == 0:
        return None

    strength = body / range_

    # ================= PUT =================
    if (
        prev["close"] > prev["open"] and
        last["close"] < last["open"] and
        strength > 0.7 and
        last["close"] < prev["low"] and
        trend_down
    ):
        return "put"

    # ================= CALL =================
    if (
        prev["close"] < prev["open"] and
        last["close"] > last["open"] and
        strength > 0.7 and
        last["close"] > prev["high"] and
        trend_up
    ):
        return "call"

    return None

# ================= TRADE =================

def trade(pair, direction):
    global trade_open, last_trade_time

    try:
        status, trade_id = iq.buy(BASE_AMOUNT, pair, direction, EXPIRATION)

        if status:
            trade_open = True
            last_trade_time = time.time()

            msg = f"🎯 {pair} {direction.upper()}"
            print(msg)
            send(msg)

    except:
        pass

# ================= RESULTADO =================

def check_result():
    global trade_open, loss_streak

    try:
        if not trade_open:
            return

        if time.time() - last_trade_time < 65:
            return

        # simulación básica (puedes mejorar con API)
        result = iq.get_balance()

        trade_open = False

    except:
        trade_open = False

# ================= LOOP =================

while True:
    try:
        check_result()

        if trade_open:
            time.sleep(1)
            continue

        server_time = int(iq.get_server_timestamp())
        current_candle = server_time // 60

        if last_trade_candle == current_candle:
            time.sleep(1)
            continue

        for pair in PAIRS:

            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)

            if df_m1 is None or df_m5 is None:
                continue

            signal = sniper_pro(df_m1, df_m5)

            if signal:

                # 🔥 protección pérdidas
                if loss_streak >= MAX_LOSS_STREAK:
                    send("🛑 STOP POR RACHAS")
                    time.sleep(120)
                    loss_streak = 0
                    break

                trade(pair, signal)
                last_trade_candle = current_candle
                break

        time.sleep(1)

    except Exception as e:
        print("Error:", e)
        time.sleep(1)
