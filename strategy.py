import numpy as np
import pandas as pd

def get_reversal_signal(df):
    if df is None or len(df) < 20:
        return None

    df = df.copy()
    df = df.replace([np.inf, -np.inf], np.nan).dropna()

    df['ema8'] = df['close'].ewm(span=8).mean()
    df['ema13'] = df['close'].ewm(span=13).mean()
    df['ema21'] = df['close'].ewm(span=21).mean()

    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(6).mean()
    avg_loss = loss.rolling(6).mean().replace(0, 0.0001)

    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    df['macd'] = df['ema13'] - df['ema21']
    df['signal'] = df['macd'].ewm(span=4).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # CALL
    if (
        last['ema8'] > last['ema13'] and
        last['macd'] > last['signal'] and
        30 < last['rsi'] < 75 and
        last['close'] > last['open']
    ):
        return ("call", 70, "soporte")

    # PUT
    if (
        last['ema8'] < last['ema13'] and
        last['macd'] < last['signal'] and
        25 < last['rsi'] < 70 and
        last['close'] < last['open']
    ):
        return ("put", 70, "resistencia")

    return None
