import pandas as pd
import numpy as np

# ================= BASE =================

def add_indicators(df):
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = ranges.max(axis=1).rolling(14).mean()

    return df


# ================= HELPERS =================

def candle_strength(c):
    body = abs(c["close"] - c["open"])
    total = c["high"] - c["low"]

    if total == 0:
        return 0

    return body / total


def candle_direction(c):
    if c["close"] > c["open"]:
        return "bull"
    elif c["close"] < c["open"]:
        return "bear"
    return "neutral"


def rejection(c):
    body = abs(c["close"] - c["open"])

    if body == 0:
        return None

    upper = c["high"] - max(c["close"], c["open"])
    lower = min(c["close"], c["open"]) - c["low"]

    if upper > body * 1.5:
        return "upper"

    if lower > body * 1.5:
        return "lower"

    return None


# ================= STRUCTURE =================

def detect_structure(df):
    highs = df["high"].tail(10).tolist()
    lows = df["low"].tail(10).tolist()

    hh = 0
    ll = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            hh += 1

        if lows[i] < lows[i - 1]:
            ll += 1

    if hh >= 6:
        return "bullish"

    if ll >= 6:
        return "bearish"

    return "range"


# ================= ZONES =================

def support_resistance(df):
    high = df["high"].tail(15).max()
    low = df["low"].tail(15).min()
    return high, low


# ================= REVERSAL =================

def reversal_signal(df):
    resistance, support = support_resistance(df)

    c = df.iloc[-2]
    rej = rejection(c)
    zone_size = (resistance - support) * 0.15

    # Reversal SELL
    if abs(c["high"] - resistance) <= zone_size:
        if rej == "upper":
            if candle_direction(c) == "bear":
                return "put", "reversal"

    # Reversal BUY
    if abs(c["low"] - support) <= zone_size:
        if rej == "lower":
            if candle_direction(c) == "bull":
                return "call", "reversal"

    return None, None


# ================= BREAKOUT =================

def breakout_signal(df):
    resistance, support = support_resistance(df)
    c = df.iloc[-2]

    strength = candle_strength(c)

    if strength < 0.60:
        return None, None

    if c["close"] > resistance:
        return "call", "breakout"

    if c["close"] < support:
        return "put", "breakout"

    return None, None


# ================= FAKE BREAKOUT =================

def fake_breakout_signal(df):
    resistance, support = support_resistance(df)
    c = df.iloc[-2]

    # rompió arriba y cerró dentro
    if c["high"] > resistance and c["close"] < resistance:
        return "put", "fake_breakout"

    # rompió abajo y cerró dentro
    if c["low"] < support and c["close"] > support:
        return "call", "fake_breakout"

    return None, None


# ================= CONTINUATION =================

def continuation_signal(df):
    structure = detect_structure(df)

    prev = df.iloc[-3]
    curr = df.iloc[-2]

    prev_dir = candle_direction(prev)
    curr_dir = candle_direction(curr)

    # Bull continuation
    if structure == "bullish":
        if prev_dir == "bear" and curr_dir == "bull":
            if curr["close"] > prev["high"]:
                return "call", "continuation"

    # Bear continuation
    if structure == "bearish":
        if prev_dir == "bull" and curr_dir == "bear":
            if curr["close"] < prev["low"]:
                return "put", "continuation"

    return None, None


# ================= MAIN SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 30:
        return None, None, None

    structure = detect_structure(df_m1)

    signal = None
    pattern = None

    detectors = [
        fake_breakout_signal,
        reversal_signal,
        breakout_signal,
        continuation_signal
    ]

    for detector in detectors:
        signal, pattern = detector(df_m1)

        if signal:
            context = {
                "structure": structure,
                "pattern": pattern
            }

            return signal, 1, context

    return None, None, None
