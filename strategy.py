import pandas as pd

# ================= BASE =================

def add_indicators(df):
    return df.copy()


# ================= VELAS =================

def candle_direction(candle):
    if candle["close"] > candle["open"]:
        return "bull"
    elif candle["close"] < candle["open"]:
        return "bear"
    return "neutral"


# ================= ESTRUCTURA =================

def detect_structure(df):
    highs = df["high"].tail(6).tolist()
    lows = df["low"].tail(6).tolist()

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

    if hh >= 3 and hl >= 3:
        return "bullish"

    if lh >= 3 and ll >= 3:
        return "bearish"

    return "range"


# ================= CONTINUIDAD =================

def structure_signal(df):
    structure = detect_structure(df)

    if structure == "range":
        return None, None

    prev = df.iloc[-3]
    curr = df.iloc[-2]

    # CALL
    if structure == "bullish":

        if candle_direction(curr) != "bull":
            return None, None

        if curr["high"] >= prev["high"]:
            return "call", "bull_continuation"

    # PUT
    if structure == "bearish":

        if candle_direction(curr) != "bear":
            return None, None

        if curr["low"] <= prev["low"]:
            return "put", "bear_continuation"

    return None, None


# ================= SCORE =================

def build_score(df):
    prev = df.iloc[-3]
    curr = df.iloc[-2]

    score = 5

    prev_body = abs(prev["close"] - prev["open"])
    curr_body = abs(curr["close"] - curr["open"])

    if curr_body > prev_body:
        score += 2

    if curr_body > 0:
        score += 1

    return score


# ================= SEÑAL PRINCIPAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 20:
        return None, None, None

    structure = detect_structure(df_m1)

    if structure == "range":
        return None, None, None

    signal, pattern = structure_signal(df_m1)

    if signal is None:
        return None, None, None

    context = {
        "trend": structure,
        "pattern": pattern,
        "score": build_score(df_m1)
    }

    return signal, 1, context
