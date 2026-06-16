import numpy as np
import pandas as pd

def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    r = c["high"] - c["low"]
    return r if r != 0 else 0.0001

def bullish(c):
    return c["close"] > c["open"]

def bearish(c):
    return c["close"] < c["open"]

def get_reversal_signal(df):
    if df is None or df.empty or len(df) < 30:
        return None

    df = df.copy()
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]

    fuerza = 0
    if body(c1) / range_c(c1) >= 0.6:
        fuerza += 25
    if bullish(c1) and c1["close"] > c2["high"]:
        fuerza += 20
    if bearish(c1) and c1["close"] < c2["low"]:
        fuerza += 20
    if df["ema5"].iloc[-1] > df["ema13"].iloc[-1] and df["ema13"].iloc[-1] > df["ema21"].iloc[-1]:
        fuerza += 20
    if df["ema5"].iloc[-1] < df["ema13"].iloc[-1] and df["ema13"].iloc[-1] < df["ema21"].iloc[-1]:
        fuerza += 20
    if body(c1) > body(c2):
        fuerza += 15

    fuerza = min(fuerza, 100)

    if bullish(c1):
        return ("call", fuerza, "ALCISTA")
    elif bearish(c1):
        return ("put", fuerza, "BAJISTA")
    else:
        return None
