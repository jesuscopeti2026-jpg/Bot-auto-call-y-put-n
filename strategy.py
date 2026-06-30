import pandas as pd
import numpy as np

# ================= BASE =================

def add_indicators(df):
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = tr.max(axis=1).rolling(14).mean()

    return df


# ================= CANDLE HELPERS =================

def candle_stats(c):
    body = abs(c["close"] - c["open"])
    total = c["high"] - c["low"]

    if total == 0:
        return {
            "body": 0,
            "ratio": 0,
            "upper": 0,
            "lower": 0,
            "dir": "neutral"
        }

    upper = c["high"] - max(c["open"], c["close"])
    lower = min(c["open"], c["close"]) - c["low"]

    if c["close"] > c["open"]:
        direction = "bull"
    elif c["close"] < c["open"]:
        direction = "bear"
    else:
        direction = "neutral"

    return {
        "body": body,
        "ratio": body / total,
        "upper": upper,
        "lower": lower,
        "dir": direction
    }


# ================= TREND / STRUCTURE =================

def detect_trend(df):
    highs = df["high"].tail(12).tolist()
    lows = df["low"].tail(12).tolist()

    hh = 0
    hl = 0
    lh = 0
    ll = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i-1]:
            hh += 1
        else:
            lh += 1

        if lows[i] > lows[i-1]:
            hl += 1
        else:
            ll += 1

    if hh >= 7 and hl >= 7:
        return "bullish"

    if lh >= 7 and ll >= 7:
        return "bearish"

    return "range"


# ================= ZONES =================

def detect_zones(df):
    resistance = df["high"].tail(20).max()
    support = df["low"].tail(20).min()
    return resistance, support


def near_zone(price, zone, atr):
    if pd.isna(atr):
        return False

    distance = abs(price - zone)
    return distance <= atr * 0.4


# ================= REVERSAL POINT =================

def reversal_point(df):
    c = df.iloc[-2]
    stats = candle_stats(c)

    resistance, support = detect_zones(df)
    atr = df["atr"].iloc[-2]

    if near_zone(c["high"], resistance, atr):
        if stats["upper"] > stats["body"] * 1.5:
            return "sell_reversal"

    if near_zone(c["low"], support, atr):
        if stats["lower"] > stats["body"] * 1.5:
            return "buy_reversal"

    return None


# ================= TWO-CANDLE READER =================

def read_two_candles(df):
    prev = df.iloc[-3]
    curr = df.iloc[-2]

    p = candle_stats(prev)
    c = candle_stats(curr)

    # BUY continuation / reversal
    if c["dir"] == "bull":
        if curr["close"] > prev["high"]:
            if c["ratio"] > 0.55:
                return "call", "bull_break"

        if p["dir"] == "bear":
            if c["ratio"] > p["ratio"]:
                return "call", "bull_absorption"

    # SELL continuation / reversal
    if c["dir"] == "bear":
        if curr["close"] < prev["low"]:
            if c["ratio"] > 0.55:
                return "put", "bear_break"

        if p["dir"] == "bull":
            if c["ratio"] > p["ratio"]:
                return "put", "bear_absorption"

    return None, None


# ================= SCORING =================

def build_score(df, trend, signal, pattern):
    score = 0

    if trend != "range":
        score += 2

    reversal = reversal_point(df)

    if reversal:
        score += 3

    curr = candle_stats(df.iloc[-2])

    if curr["ratio"] > 0.60:
        score += 2

    if "break" in pattern:
        score += 2

    if "absorption" in pattern:
        score += 3

    if trend == "bullish" and signal == "call":
        score += 2

    if trend == "bearish" and signal == "put":
        score += 2

    return score


# ================= MAIN SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 50:
        return None, None, None

    trend = detect_trend(df_m1)

    signal, pattern = read_two_candles(df_m1)

    if signal is None:
        return None, None, None

    reversal = reversal_point(df_m1)

    if reversal == "buy_reversal" and signal != "call":
        return None, None, None

    if reversal == "sell_reversal" and signal != "put":
        return None, None, None

    score = build_score(df_m1, trend, signal, pattern)

    # SOLO SETUPS DE ALTA PRECISION
    if score < 8:
        return None, None, None

    context = {
        "trend": trend,
        "pattern": pattern,
        "score": score,
        "reversal": reversal
    }

    return signal, 1, context
