import numpy as np
import pandas as pd

def body(c):
    return abs(c["close"] - c["open"])

def range_c(c):
    r = c["high"] - c["low"]
    return r if r != 0 else 0.0001

def mecha_superior(c):
    return c["high"] - max(c["open"], c["close"])

def mecha_inferior(c):
    return min(c["open"], c["close"]) - c["low"]

def bullish(c):
    return c["close"] > c["open"]

def bearish(c):
    return c["close"] < c["open"]

def get_reversal_signal(df):
    if df is None or df.empty or len(df) < 50:
        return None

    df = df.copy()
    # EMAs para tendencia
    df["ema5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema13"] = df["close"].ewm(span=13, adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()

    c1 = df.iloc[-1]   # vela actual cerrada
    c2 = df.iloc[-2]
    c3 = df.iloc[-3]

    fuerza = 0

    # 🔹 1. Cuerpo grande (mínimo 65% del rango)
    if body(c1) / range_c(c1) >= 0.65:
        fuerza += 25

    # 🔹 2. Rompe máximo/mínimo anterior (confirmación de impulso)
    if bullish(c1) and c1["close"] > c2["high"] and c1["close"] > c3["high"]:
        fuerza += 25
    if bearish(c1) and c1["close"] < c2["low"] and c1["close"] < c3["low"]:
        fuerza += 25

    # 🔹 3. Tendencia clara (solo operar a favor)
    tendencia_alcista = (df["ema5"].iloc[-1] > df["ema13"].iloc[-1] > df["ema21"].iloc[-1] > df["ema50"].iloc[-1])
    tendencia_bajista = (df["ema5"].iloc[-1] < df["ema13"].iloc[-1] < df["ema21"].iloc[-1] < df["ema50"].iloc[-1])

    if bullish(c1) and tendencia_alcista:
        fuerza += 25
    if bearish(c1) and tendencia_bajista:
        fuerza += 25

    # 🔹 4. Mechas cortas (sin retroceso)
    if mecha_superior(c1) < body(c1)*0.2 and mecha_inferior(c1) < body(c1)*0.2:
        fuerza += 15

    # 🔹 5. Volumen/impulso mayor a vela anterior
    if body(c1) > body(c2)*1.1:
        fuerza += 10

    fuerza = min(fuerza, 100)

    # 🎯 NUEVO: SOLO señales ≥85% (antes 75%)
    if fuerza >= 85:
        if bullish(c1):
            return ("call", fuerza, "ALCISTA FUERTE")
        elif bearish(c1):
            return ("put", fuerza, "BAJISTA FUERTE")

    return None
