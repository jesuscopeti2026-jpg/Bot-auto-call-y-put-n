import pandas as pd
import numpy as np

# ================= INDICADORES =================

def add_indicators(df):
    df = df.copy()

    df["ema9"] = df["close"].ewm(span=9).mean()
    df["ema21"] = df["close"].ewm(span=21).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()

    high_low = df["high"] - df["low"]
    high_close = abs(df["high"] - df["close"].shift())
    low_close = abs(df["low"] - df["close"].shift())

    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    df["atr"] = ranges.max(axis=1).rolling(14).mean()

    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    return df


# ================= TENDENCIA =================

def trend(df):
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


# ================= MERCADO LATERAL =================

def market_strength(df):
    atr = df["atr"].iloc[-2]
    slope = abs(df["ema21"].iloc[-2] - df["ema21"].iloc[-7])

    if pd.isna(atr):
        return False

    return slope > atr * 0.45


# ================= MARKET STRUCTURE =================

def market_structure(df):
    highs = df["high"].tail(6).tolist()
    lows = df["low"].tail(6).tolist()

    hh = highs[-1] > highs[-3]
    hl = lows[-1] > lows[-3]

    lh = highs[-1] < highs[-3]
    ll = lows[-1] < lows[-3]

    if hh and hl:
        return "bullish"

    if lh and ll:
        return "bearish"

    return "neutral"


# ================= BOS =================

def structure_confirms(df, direction):
    structure = market_structure(df)

    if direction == "call":
        return structure != "bearish"

    if direction == "put":
        return structure != "bullish"

    return False


# ================= CANDLE =================

def strong_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return body / full > 0.60


def rejection_filter(candle):
    body = abs(candle["close"] - candle["open"])

    if body == 0:
        return False

    upper = candle["high"] - max(candle["close"], candle["open"])
    lower = min(candle["close"], candle["open"]) - candle["low"]

    if upper > body:
        return False

    if lower > body:
        return False

    return True


# ================= SOBREEXTENSION =================

def overextended(df, direction):
    candles = [df.iloc[-2], df.iloc[-3], df.iloc[-4]]

    if direction == "call":
        return all(c["close"] > c["open"] for c in candles)

    if direction == "put":
        return all(c["close"] < c["open"] for c in candles)

    return False


# ================= PULLBACK =================

def pullback(df, direction):
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    if direction == "call":
        return c2["close"] < c2["open"] and c1["close"] > c1["open"]

    if direction == "put":
        return c2["close"] > c2["open"] and c1["close"] < c1["open"]

    return False


# ================= SOPORTE / RESISTENCIA =================

def support_resistance(df):
    highs = []
    lows = []

    for i in range(10, len(df)-10):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]

        if h >= df["high"].iloc[i-5:i+5].max():
            highs.append(h)

        if l <= df["low"].iloc[i-5:i+5].min():
            lows.append(l)

    return highs, lows


def near_reversal_zone(df):
    price = df["close"].iloc[-2]
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return False

    highs, lows = support_resistance(df)
    zone = atr * 0.5

    for h in highs:
        if abs(price - h) <= zone:
            return True

    for l in lows:
        if abs(price - l) <= zone:
            return True

    return False


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

    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    avg_body = abs(df["close"] - df["open"]).tail(20).mean()

    score = 0

    if body1 > avg_body:
        score += 1

    if body2 > avg_body:
        score += 1

    return score


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 80 or len(df_m5) < 80:
        return None, None, None

    if not market_strength(df_m5):
        return None, None, None

    direction, trend_score = trend(df_m5)

    if direction is None:
        return None, None, None

    if not structure_confirms(df_m1, direction):
        return None, None, None

    if overextended(df_m1, direction):
        return None, None, None

    score = trend_score

    if pullback(df_m1, direction):
        score += 2

    if rsi_ok(df_m1, direction):
        score += 1

    candle = df_m1.iloc[-2]

    if strong_candle(candle):
        score += 1

    if rejection_filter(candle):
        score += 1

    score += momentum_score(df_m1)

    if near_reversal_zone(df_m1):
        score -= 3

    structure = market_structure(df_m1)

    context = {
        "score": score,
        "structure": structure
    }

    if score >= 7:
        return direction, 2, context

    return None, None, None
