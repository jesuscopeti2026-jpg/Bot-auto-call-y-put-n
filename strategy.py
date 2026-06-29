import pandas as pd

# ================= BASE =================

def add_indicators(df):
    return df.copy()


# ================= ESTRUCTURA =================

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


# ================= ZONAS =================

def detect_zone(df):
    recent_high = df["high"].tail(8).max()
    recent_low = df["low"].tail(8).min()
    current = df["close"].iloc[-2]

    zone_size = (recent_high - recent_low) * 0.25

    if current >= recent_high - zone_size:
        return "resistance"

    if current <= recent_low + zone_size:
        return "support"

    return "middle"


# ================= FUERZA DE VELA =================

def candle_strength(candle):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]

    if total == 0:
        return 0

    return body / total


# ================= ANALISIS DE 2 VELAS =================

def candle_reader(df):
    prev = df.iloc[-3]
    curr = df.iloc[-2]

    prev_bull = prev["close"] > prev["open"]
    curr_bull = curr["close"] > curr["open"]

    prev_strength = candle_strength(prev)
    curr_strength = candle_strength(curr)

    # BUY
    if curr_bull:
        if curr["close"] > prev["high"]:
            if curr_strength > prev_strength:
                return "call", "bull_break"

        if (not prev_bull) and curr_bull:
            if curr_strength > 0.55:
                return "call", "bull_reversal"

    # SELL
    if not curr_bull:
        if curr["close"] < prev["low"]:
            if curr_strength > prev_strength:
                return "put", "bear_break"

        if prev_bull and (not curr_bull):
            if curr_strength > 0.55:
                return "put", "bear_reversal"

    return None, None


# ================= FILTRO POR ZONA =================

def zone_filter(signal, zone):
    if signal == "call":
        if zone == "resistance":
            return False

    if signal == "put":
        if zone == "support":
            return False

    return True


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 20:
        return None, None, None

    structure = detect_structure(df_m1)
    zone = detect_zone(df_m1)

    signal, pattern = candle_reader(df_m1)

    if signal is None:
        return None, None, None

    if not zone_filter(signal, zone):
        return None, None, None

    # Operar a favor de estructura
    if structure == "bullish" and signal != "call":
        return None, None, None

    if structure == "bearish" and signal != "put":
        return None, None, None

    context = {
        "structure": structure,
        "zone": zone,
        "pattern": pattern
    }

    return signal, 1, context
