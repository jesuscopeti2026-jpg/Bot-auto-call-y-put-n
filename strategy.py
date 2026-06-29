import pandas as pd
import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = ranges.max(axis=1).rolling(14).mean()

    return df


# ================= CANDLE =================

def classify_candle(candle):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]

    if total == 0:
        return "neutral"

    upper = candle["high"] - max(candle["open"], candle["close"])
    lower = min(candle["open"], candle["close"]) - candle["low"]

    body_ratio = body / total
    bullish = candle["close"] > candle["open"]

    if body_ratio < 0.20:
        return "doji"

    if lower > body * 1.5:
        return "bullish_rejection"

    if upper > body * 1.5:
        return "bearish_rejection"

    if bullish and body_ratio > 0.65:
        return "bullish_strong"

    if (not bullish) and body_ratio > 0.65:
        return "bearish_strong"

    if bullish:
        return "bullish_weak"

    return "bearish_weak"


# ================= STRUCTURE =================

def detect_structure(df):
    highs = df["high"].tail(8).tolist()
    lows = df["low"].tail(8).tolist()

    hh = 0
    ll = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i-1]:
            hh += 1
        if lows[i] < lows[i-1]:
            ll += 1

    if hh >= 5:
        return "bullish"

    if ll >= 5:
        return "bearish"

    return "range"


# ================= SEQUENCE =================

def analyze_sequence(df):
    seq = []

    for i in range(-6, -1):
        seq.append(classify_candle(df.iloc[i]))

    bullish = sum("bullish" in s for s in seq)
    bearish = sum("bearish" in s for s in seq)

    if bullish > bearish:
        return "buyers"

    if bearish > bullish:
        return "sellers"

    return "balanced"


# ================= REJECTION =================

def detect_rejection(df):
    c = classify_candle(df.iloc[-2])

    if c == "bullish_rejection":
        return "call"

    if c == "bearish_rejection":
        return "put"

    return None


# ================= BREAK =================

def confirm_break(df, direction):
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    if direction == "call":
        return c1["close"] > c2["high"]

    if direction == "put":
        return c1["close"] < c2["low"]

    return False


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 50:
        return None, None, None

    structure = detect_structure(df_m1)

    # SOLO RANGE
    if structure != "range":
        return None, None, None

    sequence = analyze_sequence(df_m1)
    rejection = detect_rejection(df_m1)

    score = 0
    signal = None

    if structure == "range":
        score += 2

    if sequence == "balanced":
        score += 1

    if rejection:
        score += 2
        signal = rejection

    if signal is None:
        return None, None, None

    if not confirm_break(df_m1, signal):
        return None, None, None

    context = {
        "score": score,
        "structure": structure
    }

    if score == 5:
        return signal, 1, context

    return None, None, None
