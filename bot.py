import time
import os
import csv
import requests
import pandas as pd
import sys
import logging

from iqoptionapi.stable_api import IQ_Option
from strategy import add_indicators, pro_signal

logging.getLogger().setLevel(logging.CRITICAL)
sys.stderr = open(os.devnull, "w")

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_AMOUNT = 10
MAX_LOSS_STREAK = 3

PAIRS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC"
]

trade_open = False
last_trade_time = 0
loss_streak = 0
BOT_RUNNING = True
LAST_UPDATE_ID = None
last_balance = None
last_direction = None
last_pair = None
last_context = None
last_processed_candle = None


def send(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": msg},
            timeout=5
        )
    except:
        pass


def connect_iq():
    iq = IQ_Option(EMAIL, PASSWORD)
    iq.connect()

    if not iq.check_connect():
        raise Exception("Error IQ")

    iq.change_balance("PRACTICE")
    return iq


iq = connect_iq()
send("🔥 BOT RANGE SCORE 5 ACTIVO")


def get_candles(pair, tf):
    try:
        data = iq.get_candles(pair, tf, 120, time.time())

        if not data:
            return None

        df = pd.DataFrame(data)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)

        return add_indicators(df)

    except:
        return None


def wait_new_candle():
    while True:
        server_time = iq.get_server_timestamp()
        sec = int(server_time) % 60
        ms = server_time - int(server_time)

        if sec == 0 and ms < 0.2:
            return

        time.sleep(0.01)


def trade(pair, direction, expiration, context):
    global trade_open, last_trade_time
    global last_balance, last_direction
    global last_pair, last_context

    wait_new_candle()

    last_balance = iq.get_balance()

    status, trade_id = iq.buy(
        BASE_AMOUNT,
        pair,
        direction,
        expiration
    )

    if status:
        trade_open = True
        last_trade_time = time.time()
        last_direction = direction
        last_pair = pair
        last_context = context

        send(
            f"🎯 {pair} {direction.upper()} {expiration}m\n"
            f"Score: {context['score']}\n"
            f"Structure: {context['structure']}"
        )


def check_result():
    global trade_open, loss_streak

    if not trade_open:
        return

    if time.time() - last_trade_time < 80:
        return

    current_balance = iq.get_balance()
    pnl = current_balance - last_balance
    trade_open = False

    if pnl > 0:
        loss_streak = 0
        send(f"✅ WIN +{round(pnl,2)}")

    elif pnl < 0:
        loss_streak += 1
        send(f"❌ LOSS {round(pnl,2)} | Racha {loss_streak}")

    else:
        send("⚪ DRAW")


while True:
    try:
        check_result()

        if trade_open:
            time.sleep(0.2)
            continue

        server_time = int(iq.get_server_timestamp())
        current_candle = server_time // 60

        if current_candle == last_processed_candle:
            time.sleep(0.2)
            continue

        if server_time % 60 != 1:
            time.sleep(0.05)
            continue

        last_processed_candle = current_candle

        for pair in PAIRS:
            df_m1 = get_candles(pair, 60)
            df_m5 = get_candles(pair, 300)

            if df_m1 is None:
                continue

            signal, expiration, context = pro_signal(df_m1, df_m5)

            if signal:
                if context["structure"] != "range":
                    continue

                if context["score"] != 5:
                    continue

                trade(pair, signal, expiration, context)
                break

        time.sleep(0.2)

    except Exception as e:
        print(e)
        time.sleep(2)
