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
    tr = ranges.max(axis=1)
    df["atr"] = tr.rolling(14).mean()

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
    e9 = df["ema9"].iloc[-2]
    e21 = df["ema21"].iloc[-2]
    e50 = df["ema50"].iloc[-2]

    if pd.isna(e9) or pd.isna(e21) or pd.isna(e50):
        return None

    if e9 > e21 > e50:
        return "call"

    if e9 < e21 < e50:
        return "put"

    return None


# ================= MARKET STRUCTURE =================

def market_structure(df):
    highs = df["high"].tail(8).values
    lows = df["low"].tail(8).values

    if len(highs) < 4:
        return None

    if highs[-1] > highs[-3] and lows[-1] > lows[-3]:
        return "bullish"

    if highs[-1] < highs[-3] and lows[-1] < lows[-3]:
        return "bearish"

    return "range"


# ================= ZONE CLUSTER =================

def build_zones(levels, atr):
    zones = []

    for level in levels:
        merged = False

        for z in zones:
            if abs(level - z["price"]) < atr * 0.35:
                z["touches"] += 1
                z["price"] = (z["price"] + level) / 2
                merged = True
                break

        if not merged:
            zones.append({
                "price": level,
                "touches": 1
            })

    return zones


def support_resistance(df):
    highs = []
    lows = []

    for i in range(10, len(df) - 10):
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]

        if high == max(df["high"].iloc[i-5:i+5]):
            highs.append(high)

        if low == min(df["low"].iloc[i-5:i+5]):
            lows.append(low)

    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return [], []

    return build_zones(highs, atr), build_zones(lows, atr)


# ================= ZONE CONTEXT =================

def nearest_zone(df):
    price = df["close"].iloc[-2]
    atr = df["atr"].iloc[-2]

    if pd.isna(atr):
        return None

    resistances, supports = support_resistance(df)

    nearest = None
    nearest_dist = 999999

    for z in resistances:
        dist = abs(price - z["price"])
        if dist < nearest_dist:
            nearest = ("resistance", z)
            nearest_dist = dist

    for z in supports:
        dist = abs(price - z["price"])
        if dist < nearest_dist:
            nearest = ("support", z)
            nearest_dist = dist

    if nearest and nearest_dist < atr:
        return nearest

    return None


# ================= LIQUIDITY SWEEP =================

def liquidity_sweep(df):
    zone = nearest_zone(df)

    if zone is None:
        return None

    zone_type, z = zone
    candle = df.iloc[-2]

    if zone_type == "support":
        if candle["low"] < z["price"] and candle["close"] > z["price"]:
            return "bullish"

    if zone_type == "resistance":
        if candle["high"] > z["price"] and candle["close"] < z["price"]:
            return "bearish"

    return None


# ================= PULLBACK =================

def pullback(df, direction):
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]

    if direction == "call":
        return c2["close"] < c2["open"] and c1["close"] > c1["open"]

    if direction == "put":
        return c2["close"] > c2["open"] and c1["close"] < c1["open"]

    return False


# ================= STRONG CANDLE =================

def strong_candle(candle):
    body = abs(candle["close"] - candle["open"])
    full = candle["high"] - candle["low"]

    if full == 0:
        return False

    return body / full > 0.6


# ================= RSI =================

def rsi_ok(df, direction):
    rsi = df["rsi"].iloc[-2]

    if pd.isna(rsi):
        return False

    if direction == "call":
        return 50 < rsi < 67

    if direction == "put":
        return 33 < rsi < 50

    return False


# ================= SIGNAL =================

def pro_signal(df_m1, df_m5):
    if len(df_m1) < 80 or len(df_m5) < 80:
        return None, None, None

    score = 0

    direction = trend(df_m5)

    if direction is None:
        return None, None, None

    score += 2

    structure = market_structure(df_m5)

    if structure == "bullish" and direction == "call":
        score += 2

    if structure == "bearish" and direction == "put":
        score += 2

    sweep = liquidity_sweep(df_m1)

    if sweep == "bullish" and direction == "call":
        score += 2

    if sweep == "bearish" and direction == "put":
        score += 2

    if pullback(df_m1, direction):
        score += 2

    if rsi_ok(df_m1, direction):
        score += 1

    candle = df_m1.iloc[-2]

    if strong_candle(candle):
        score += 1

    zone = nearest_zone(df_m1)

    if zone:
        zone_type, z = zone

        if z["touches"] >= 4:
            if direction == "put" and zone_type == "support":
                score -= 4

            if direction == "call" and zone_type == "resistance":
                score -= 4

    context = {
        "score": score,
        "structure": structure
    }

    if score >= 8:
        return direction, 2, context

    return None, None, context
