import pandas as pd
import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    # EMAs
    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    # ATR
    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df["atr"] = true_range.rolling(14).mean()

    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ================= TREND =================

def trend(df):
    if len(df) < 60:
        return None, 0

    e9 = df["ema9"].iloc[-2]
    e21 = df["ema21"].iloc[-2]
    e50 = df["ema50"].iloc[-2]

    if pd.isna(e9) or pd.isna(e21) or pd.isna(e50):
        return None, 0

    if e9 > e21 > e50:
        return "call", 2

    if e9 < e21 < e50:
        return "put", 2

    return None, 0


# ================= CANDLE QUALITY =================

def strong_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return (body / full) > 0.55


def rejection_filter(candle):
    body = abs(candle["close"] - candle["open"])

    if body == 0:
        return False

    upper = candle["high"] - max(candle["close"], candle["open"])
    lower = min(candle["close"], candle["open"]) - candle["low"]

    if upper > body * 1.2:
        return False

    if lower > body * 1.2:
        return False

    return True


# ================= CONTINUATION =================

def continuation(df, direction):
    if len(df) < 4:
        return False

    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    if direction == "call":
        if (
            c1["close"] > c1["open"] and
            c2["close"] > c2["open"] and
            c1["close"] > c2["close"] and
            strong_candle(c1)
        ):
            return True

    if direction == "put":
        if (
            c1["close"] < c1["open"] and
            c2["close"] < c2["open"] and
            c1["close"] < c2["close"] and
            strong_candle(c1)
        ):
            return True

    return False


# ================= SOPORTE / RESISTENCIA =================

def support_resistance(df):
    highs = []
    lows = []

    if len(df) < 30:
        return highs, lows

    for i in range(10, len(df) - 10):
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]

        if high == max(df["high"].iloc[i-5:i+5]):
            highs.append(high)

        if low == min(df["low"].iloc[i-5:i+5]):
            lows.append(low)

    return highs, lows


def near_reversal_zone(df):
    if len(df) < 30:
        return False

    price = df["close"].iloc[-2]
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return False

    highs, lows = support_resistance(df)
    zone_distance = atr * 0.4

    for h in highs:
        if abs(price - h) < zone_distance:
            return True

    for l in lows:
        if abs(price - l) < zone_distance:
            return True

    return False


# ================= VOLATILITY =================

def volatility_ok(df):
    if len(df) < 20:
        return False

    atr = df["atr"].iloc[-2]
    mean_atr = df["atr"].mean()

    if pd.isna(atr) or pd.isna(mean_atr):
        return False

    return atr > mean_atr * 0.7


# ================= RSI =================

def rsi_ok(df, direction):
    rsi = df["rsi"].iloc[-2]

    if pd.isna(rsi):
        return False

    if direction == "call":
        return 52 < rsi < 68

    if direction == "put":
        return 32 < rsi < 48

    return False


# ================= MOMENTUM =================

def momentum_score(df):
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    score = 0

    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])

    avg_body = abs(df["close"] - df["open"]).tail(20).mean()

    if body1 > avg_body:
        score += 1

    if body2 > avg_body:
        score += 1

    return score


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 80 or len(df_m5) < 80:
        return None, None

    score = 0

    direction, trend_score = trend(df_m5)

    if direction is None:
        return None, None

    score += trend_score

    if volatility_ok(df_m1):
        score += 1

    if continuation(df_m1, direction):
        score += 1

    if rsi_ok(df_m1, direction):
        score += 1

    candle = df_m1.iloc[-2]

    if rejection_filter(candle):
        score += 1

    score += momentum_score(df_m1)

    if near_reversal_zone(df_m1):
        score -= 3

    if score >= 5:
        return direction, 1

    return None, None
