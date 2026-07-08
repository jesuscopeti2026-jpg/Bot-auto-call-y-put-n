import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    # EMA 20
    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()

    # ATR 14
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    df["tr"] = np.maximum(high_low, np.maximum(high_close, low_close))
    df["atr"] = df["tr"].rolling(14).mean()

    return df


# ================= ZONA HTF =================

def get_zone(df_htf):
    highs = df_htf["high"].rolling(20).max()
    lows = df_htf["low"].rolling(20).min()

    resistance = highs.iloc[-1]
    support = lows.iloc[-1]

    return support, resistance


# ================= DOBLE TEST =================

def double_touch(df_m5, level, is_support=True):
    touches = 0

    for i in range(-10, 0):
        candle = df_m5.iloc[i]

        if is_support:
            if candle["low"] <= level:
                touches += 1
        else:
            if candle["high"] >= level:
                touches += 1

    return touches >= 2


# ================= CONFIRMACIÓN =================

def confirmation(df_m1, direction):
    last = df_m1.iloc[-1]

    if direction == "call":
        return last["close"] > last["open"]

    if direction == "put":
        return last["close"] < last["open"]

    return False


# ================= SEÑAL =================

def pro_signal(df_m1, df_m5, df_htf):

    if len(df_htf) < 20:
        return None, None

    support, resistance = get_zone(df_htf)

    price = df_m1["close"].iloc[-1]
    atr = df_m1["atr"].iloc[-1]

    if np.isnan(atr):
        return None, None

    # Margen dinámico basado en ATR
    buffer = atr * 1.2

    # ========= SOPORTE =========

    if abs(price - support) <= buffer:
        if double_touch(df_m5, support, True):
            if confirmation(df_m1, "call"):
                # Señal invertida
                return "put", 3

    # ========= RESISTENCIA =========

    if abs(price - resistance) <= buffer:
        if double_touch(df_m5, resistance, False):
            if confirmation(df_m1, "put"):
                # Señal invertida
                return "call", 3

    return None, None
