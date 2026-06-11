import numpy as np
import pandas as pd

# ==================================================
# 🚀 ESTRATEGIA EXTREMADAMENTE FLEXIBLE PARA MÁS SEÑALES
# ✅ Sin errores de Pandas
# ✅ Condiciones mínimas para asegurar señales
# ✅ NOMBRE CORREGIDO para coincidir con bot.py
# ==================================================

def get_reversal_signal(df, tolerancia_nivel=0.0012, ventana_niveles=6):
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
    avg_gain = gain.rolling(window=7, min_periods=1).mean()
    avg_loss = loss.rolling(window=7, min_periods=1).mean().replace(0, 0.001)
    rs = avg_gain / avg_loss
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    df['macd'] = df['ema13'] - df['ema21']
    df['signal'] = df['macd'].ewm(span=5, adjust=False).mean()

    try:
        e8_1 = float(df['ema8'].iloc[-1])
        e13_1 = float(df['ema13'].iloc[-1])
        e21_1 = float(df['ema21'].iloc[-1])

        c1 = float(df['close'].iloc[-1])
        c2 = float(df['close'].iloc[-2])
        o1 = float(df['open'].iloc[-1])
        h1 = float(df['high'].iloc[-1])
        l1 = float(df['low'].iloc[-1])

        macd1 = float(df['macd'].iloc[-1])
        sig1 = float(df['signal'].iloc[-1])
        rsi1 = float(df['rsi'].iloc[-1])
        vol1 = float(df['volume'].iloc[-1])
        
        # Promedio de volumen seguro
        if len(df) >= 6:
            vol_prom = float(df['volume'].iloc[-5:-1].mean())
        else:
            vol_prom = vol1 if vol1 > 0 else 1.0

    except Exception:
        return None

    fuerza = 0
    senal = None
    tipo = ""

    # ✅ COMPRA (ALCISTA) - Condiciones MÍNIMAS
    cond_compra = (
        e8_1 > e13_1 and
        macd1 > sig1 and
        rsi1 > 35.0 and rsi1 < 75.0 and
        c1 > o1
    )

    if cond_compra:
        senal = "call"
        tipo = "alcista"
        fuerza = 40
        if e13_1 > e21_1:
            fuerza += 5
        if c1 > c2:
            fuerza += 5
        if vol1 >= vol_prom * 0.4:
            fuerza += 5

    # ✅ VENTA (BAJISTA) - Completado
    cond_venta = (
        e8_1 < e13_1 and
        macd1 < sig1 and
        rsi1 < 65.0 and rsi1 > 25.0 and
        c1 < o1
    )

    if cond_venta:
        senal = "put"
        tipo = "bajista"
        fuerza = 40
        if e13_1 < e21_1:
            fuerza += 5
        if c1 < c2:
            fuerza += 5
        if vol1 >= vol_prom * 0.4:
            fuerza += 5

    # Limitar fuerza entre 0 y 100
    if senal is not None:
        fuerza = max(0, min(fuerza, 100))
        return (senal, fuerza, tipo)

    return None
