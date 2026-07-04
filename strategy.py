import pandas as pd

# ================= BASE =================

def add_indicators(df):
    return df.copy()


# ================= CANDLE =================

def candle_direction(c):
    if c["close"] > c["open"]:
        return "bull"
    elif c["close"] < c["open"]:
        return "bear"
    return "neutral"


# ================= STRUCTURE =================

def detect_structure(df):
    highs = df["high"].tail(8).tolist()
    lows = df["low"].tail(8).tolist()

    higher_highs = 0
    higher_lows = 0
    lower_highs = 0
    lower_lows = 0

    for i in range(1, len(highs)):
        if highs[i] > highs[i - 1]:
            higher_highs += 1
        else:
            lower_highs += 1

        if lows[i] > lows[i - 1]:
            higher_lows += 1
        else:
            lower_lows += 1

    if higher_highs >= 5 and higher_lows >= 5:
        return "bullish"

    if lower_highs >= 5 and lower_lows >= 5:
        return "bearish"

    return "range"


# ================= SIGNAL =================

def structure_signal(df):
    structure = detect_structure(df)

    if structure == "range":
        return None, None

    prev = df.iloc[-3]   # vela anterior
    curr = df.iloc[-2]   # vela cerrada actual

    prev_dir = candle_direction(prev)
    curr_dir = candle_direction(curr)

    # ================= CALL =================
    if structure == "bullish":
        # vela actual confirma continuidad
        if curr_dir == "bull":
            if curr["close"] > prev["close"]:
                return "call", "bull_structure"

    # ================= PUT =================
    if structure == "bearish":
        if curr_dir == "bear":
            if curr["close"] < prev["close"]:
                return "put", "bear_structure"

    return None, None


# ================= SCORE =================

def build_score(df):
    prev = df.iloc[-3]
    curr = df.iloc[-2]

    score = 0

    score += 6  # estructura dominante

    prev_body = abs(prev["close"] - prev["open"])
    curr_body = abs(curr["close"] - curr["open"])

    if curr_body > prev_body:
        score += 2

    if curr_body > 0:
        score += 2

    return score


# ================= MAIN =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 20:
        return None, None, None

    structure = detect_structure(df_m1)

    if structure == "range":
        return None, None, None

    signal, pattern = structure_signal(df_m1)

    if signal is None:
        return None, None, None

    score = build_score(df_m1)

    context = {
        "trend": structure,
        "pattern": pattern,
        "score": score
    }

    return signal, 1, context
