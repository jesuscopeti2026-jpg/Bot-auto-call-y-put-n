import pandas as pd
import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    tr = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = tr.max(axis=1).rolling(14).mean()

    return df


# ================= CANDLE =================

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

    direction = "neutral"

    if c["close"] > c["open"]:
        direction = "bull"
    elif c["close"] < c["open"]:
        direction = "bear"

    return {
        "body": body,
        "ratio": body / total,
        "upper": upper,
        "lower": lower,
        "dir": direction
    }


# ================= ESTRUCTURA =================

def detect_trend(df):
    highs = df["high"].tail(12).tolist()
    lows = df["low"].tail(12).tolist()

    hh = 0
    hl = 0
    lh = 0
    ll = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            hh += 1
        else:
            lh += 1

        if lows[i] > lows[i - 1]:
            hl += 1
        else:
            ll += 1

    if hh >= 7 and hl >= 7:
        return "bullish"

    if lh >= 7 and ll >= 7:
        return "bearish"

    return "range"


# ================= ZONAS =================

def support_resistance(df):
    resistance = df["high"].tail(20).max()
    support = df["low"].tail(20).min()
    return resistance, support


def near_reversal_zone(df):
    c = df.iloc[-2]
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return True

    resistance, support = support_resistance(df)

    near_res = abs(c["close"] - resistance) <= atr * 0.40
    near_sup = abs(c["close"] - support) <= atr * 0.40

    return near_res or near_sup


# ================= CONTINUATION =================

def continuation_signal(df):
    trend = detect_trend(df)

    if trend == "range":
        return None, None

    prev = df.iloc[-3]
    curr = df.iloc[-2]

    p = candle_stats(prev)
    c = candle_stats(curr)

    # ===== CALL =====
    if trend == "bullish":

        # pullback pequeño
        if p["dir"] != "bear":
            return None, None

        # vela actual alcista fuerte
        if c["dir"] != "bull":
            return None, None

        if c["ratio"] < 0.55:
            return None, None

        # ruptura de máximo
        if curr["close"] > prev["high"]:
            return "call", "bull_continuation"

    # ===== PUT =====
    if trend == "bearish":

        if p["dir"] != "bull":
            return None, None

        if c["dir"] != "bear":
            return None, None

        if c["ratio"] < 0.55:
            return None, None

        if curr["close"] < prev["low"]:
            return "put", "bear_continuation"

    return None, None


# ================= SCORE =================

def build_score(df, trend, pattern):
    score = 0

    score += 3  # tendencia clara

    prev = candle_stats(df.iloc[-3])
    curr = candle_stats(df.iloc[-2])

    # pullback limpio
    if prev["ratio"] < 0.55:
        score += 2

    # vela fuerte
    if curr["ratio"] > 0.60:
        score += 2

    # ruptura fuerte
    score += 3

    return score


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 50:
        return None, None, None

    trend = detect_trend(df_m1)

    if trend == "range":
        return None, None, None

    # NO operar zonas de reversión
    if near_reversal_zone(df_m1):
        return None, None, None

    signal, pattern = continuation_signal(df_m1)

    if signal is None:
        return None, None, None

    score = build_score(df_m1, trend, pattern)

    # SOLO setups de alta calidad
    if score < 8:
        return None, None, None

    context = {
        "trend": trend,
        "pattern": pattern,
        "score": score
    }

    return signal, 1, context
