import numpy as np

def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    return c["high"] - c["low"]

def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])

def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]

def get_reversal_signal(df):

    if len(df) < 50:
        return None

    df = df.copy()

    # EMAs
    df['ema5'] = df['close'].ewm(span=5).mean()
    df['ema8'] = df['close'].ewm(span=8).mean()
    df['ema21'] = df['close'].ewm(span=21).mean()

    # RSI
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(6).mean()
    avg_loss = loss.rolling(6).mean().replace(0, 0.001)

    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    c1 = df.iloc[-1]
    c2 = df.iloc[-2]

    fuerza = body(c1) / (range_c(c1) + 1e-6)

    # 🔥 SOLO VELAS FUERTES
    if fuerza < 0.8:
        return None

    # ❌ evitar manipulación
    if upper_wick(c1) > body(c1) or lower_wick(c1) > body(c1):
        return None

    # ❌ evitar rango
    rango = abs(df["close"].iloc[-6] - df["close"].iloc[-1])
    if rango < 0.0005:
        return None

    rsi = c1["rsi"]

    # ❌ agotamiento
    if rsi > 70 or rsi < 30:
        return None

    tendencia_alcista = df['ema5'].iloc[-1] > df['ema8'].iloc[-1] > df['ema21'].iloc[-1]
    tendencia_bajista = df['ema5'].iloc[-1] < df['ema8'].iloc[-1] < df['ema21'].iloc[-1]

    # CALL
    if tendencia_alcista and c1["close"] > c2["close"]:
        return ("call", 98, "SNIPER TREND")

    # PUT
    if tendencia_bajista and c1["close"] < c2["close"]:
        return ("put", 98, "SNIPER TREND")

    return None
