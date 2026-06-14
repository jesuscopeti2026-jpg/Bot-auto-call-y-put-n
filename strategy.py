import numpy as np
import pandas as pd

def get_reversal_signal(df, tolerancia_nivel=0.0022, ventana_niveles=5):
    if len(df) < ventana_niveles + 5:
        return None

    df = df.copy()

    # Medias móviles
    df['ema8'] = df['close'].ewm(span=8, adjust=False).mean()
    df['ema13'] = df['close'].ewm(span=13, adjust=False).mean()
    df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

    # Cálculo RSI para detectar SOBREVENTA
    delta = df['close'].diff()
    ganancia = delta.where(delta > 0, 0.0)
    perdida = -delta.where(delta < 0, 0.0)
    ganancia_media = ganancia.rolling(window=5, min_periods=1).mean()
    perdida_media = perdida.rolling(window=5, min_periods=1).mean().replace(0, 0.001)
    rs = ganancia_media / perdida_media
    df['rsi'] = 100.0 - (100.0 / (1.0 + rs))

    # MACD para confirmación de reversión
    df['macd'] = df['ema13'] - df['ema21']
    df['senal_macd'] = df['macd'].ewm(span=4, adjust=False).mean()

    # Detectar soportes
    def detectar_soportes(datos, ventana):
        soportes = []
        total = len(datos)
        for i in range(ventana, total - ventana):
            minimo = datos['low'].iloc[i-ventana:i+ventana+1].min()
            if abs(datos['low'].iloc[i] - minimo) / minimo <= 0.0015:
                soportes.append(round(minimo, 5))
        return sorted(list(set(soportes)))

    soportes = detectar_soportes(df, ventana_niveles)

    try:
        cierre = float(df['close'].iloc[-1])
        apertura = float(df['open'].iloc[-1])
        macd_val = float(df['macd'].iloc[-1])
        senal_macd_val = float(df['senal_macd'].iloc[-1])
        rsi_val = float(df['rsi'].iloc[-1])
        volumen = float(df['volume'].iloc[-1])
        vol_prom = float(df['volume'].iloc[-4:-1].mean()) if len(df) >= 5 else max(volumen, 1.0)
    except Exception:
        return None

    en_soporte = any(abs(cierre - s) / s <= tolerancia_nivel for s in soportes)

    # ✅ SOLO COMPRA: SOBREVENTA + REVERSIÓN + CONFIRMACIÓN
    senal = None
    fuerza = 0
    tipo = ""

    if rsi_val < 25:  # Nivel claro de sobreventa
        if en_soporte and macd_val >= senal_macd_val and cierre > apertura:
            senal = "call"
            tipo = "REVERSIÓN SOBREVENTA - COMPRA"
            fuerza = 50
            if rsi_val < 20: fuerza += 15
            if volumen >= vol_prom * 0.4: fuerza += 10

    if senal is not None:
        fuerza = max(40, min(fuerza, 100))
        return (senal, fuerza, tipo)

    return None
