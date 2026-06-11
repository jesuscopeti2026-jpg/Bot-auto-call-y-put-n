import numpy as np
import pandas as pd

# ==================================================
# 🚀 ESTRATEGIA EXTREMADAMENTE FLEXIBLE PARA MÁS SEÑALES
# ✅ Sin errores de Pandas
# ✅ Condiciones mínimas para asegurar señales
# ==================================================

def get_trend_signal(df):
    # Requiere menos velas para poder operar más frecuentemente
    if len(df) < 10:
        return None

    df = df.copy()

    # Indicadores simplificados y flexibles
    df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=7, min_periods=1).mean() # Menos periodos
    avg_loss = loss.rolling(window=7, min_periods=1).mean().replace(0, 0.001) # Menos periodos
    rs = avg_gain / avg_loss
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    df['macd'] = df['ema13'] - df['ema21']
    df['signal'] = df['macd'].ewm(span=5, adjust=False).mean() # MACD signal más rápido

    try:
        e8_1 = float(df['ema8'].iloc[-1])
        e13_1 = float(df['ema13'].iloc[-1])
        e21_1 = float(df['ema21'].iloc[-1])

        c1 = float(df['close'].iloc[-1])
        c2 = float(df['close'].iloc[-2])
        o1 = float(df['open'].iloc[-1])

        macd1 = float(df['macd'].iloc[-1])
        sig1 = float(df['signal'].iloc[-1])
        rsi1 = float(df['rsi'].iloc[-1])
        vol1 = float(df['volume'].iloc[-1])
        vol_prom = float(df['volume'].iloc[-5:-1].mean()) # Promedio de volumen más corto

    except Exception:
        return None

    fuerza = 0
    senal = None
    tipo = ""

    # ✅ COMPRA (ALCISTA) - Condiciones MÍNIMAS
    cond_compra = (
        e8_1 > e13_1 and # Cruce de EMAs
        macd1 > sig1 and # MACD alcista
        rsi1 > 35.0 and # RSI no sobrevendido
        c1 > o1         # Vela alcista
    )

    if cond_compra:
        senal = "call"
        tipo = "alcista"
        fuerza = 40 # Fuerza base muy baja
        # Pequeños aumentos de fuerza por confirmaciones
        if e13_1 > e21_1: fuerza += 5
        if c1 > c2: fuerza += 5
        if vol1 >= vol_prom * 0.4: fuerza += 5 # Umbral de volumen más bajo

    
