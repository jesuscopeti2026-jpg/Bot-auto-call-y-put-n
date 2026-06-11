import numpy as np
import pandas as pd

# ==================================================
# 🚀 ESTRATEGIA DE REVERSIÓN EN SOPORTE / RESISTENCIA
# ✅ Solo opera si el precio toca un nivel importante
# ✅ Compatible con bot.py
# ✅ Cálculo automático de niveles clave
# ==================================================

def get_reversal_signal(df, tolerancia_nivel=0.0012, ventana_niveles=6):
    if len(df) < ventana_niveles + 5:
        return None

    df = df.copy()

    # --------------------------
    # CÁLCULO DE INDICADORES
    # --------------------------
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

    # --------------------------
    # DETECTAR NIVELES DE SOPORTE Y RESISTENCIA
    # --------------------------
    def detectar_niveles(datos, ventana):
        soportes = []
        resistencias = []
        for i in range(ventana, len(datos)-ventana):
            min_local = datos['low'].iloc[i-ventana:i+ventana+1].min()
            max_local = datos['high'].iloc[i-ventana:i+ventana+1].max()
            if datos['low'].iloc[i] == min_local:
                soportes.append(round(min_local, 5))
            if datos['high'].iloc[i] == max_local:
                resistencias.append(round(max_local, 5))
        # Eliminar duplicados y ordenar
        return sorted(list(set(soportes))), sorted(list(set(resistencias)))

    soportes, resistencias = detectar_niveles(df, ventana_niveles)

    # --------------------------
    # EXTRACCIÓN DE DATOS ACTUALES
    # --------------------------
    try:
        e8_1 = float(df['ema8'].iloc[-1])
        e13_1 = float(df['ema13'].iloc[-1])
        e21_1 = float(df['ema21'].iloc[-1])

        c1 = float(df['close'].iloc[-1])
        o1 = float(df['open'].iloc[-1])
        l1 = float(df['low'].iloc[-1])
        h1 = float(df['high'].iloc[-1])

        macd1 = float(df['macd'].iloc[-1])
        sig1 = float(df['signal'].iloc[-1])
        rsi1 = float(df['rsi'].iloc[-1])
        vol1 = float(df['volume'].iloc[-1])

        if len(df) >= 6:
            vol_prom = float(df['volume'].iloc[-5:-1].mean())
        else:
            vol_prom = vol1 if vol1 > 0 else 1.0

    except Exception:
        return None

    # --------------------------
    # VERIFICAR SI ESTÁ EN ZONA DE SOPORTE O RESISTENCIA
    # --------------------------
    en_soporte = False
    en_resistencia = False

    for sop in soportes:
        if abs(c1 - sop) / sop <= tolerancia_nivel:
            en_soporte = True
            break

    for res in resistencias:
        if abs(c1 - res) / res <= tolerancia_nivel:
            en_resistencia = True
            break

    if not en_soporte and not en_resistencia:
        return None  # No opera si no está en nivel clave

    # --------------------------
    # CONDICIONES DE REVERSIÓN
    # --------------------------
    fuerza = 0
    senal = None
    tipo_nivel = ""

    # ✅ REVERSIÓN ALCISTA (COMPRA) → en SOPORTE
    if en_soporte:
        cond_rev_compra = (
            e8_1 > e13_1 and
            macd1 > sig1 and
            rsi1 > 30.0 and rsi1 < 65.0 and
            c1 > o1
        )
        if cond_rev_compra:
            senal = "call"
            tipo_nivel = "soporte"
            fuerza = 45
            if e13_1 > e21_1:
                fuerza += 5
            if vol1 >= vol_prom * 0.5:
                fuerza += 5

    # ✅ REVERSIÓN BAJISTA (VENTA) → en RESISTENCIA
    if en_resistencia:
        cond_rev_venta = (
            e8_1 < e13_1 and
            macd1 < sig1 and
            rsi1 < 70.0 and rsi1 > 35.0 and
            c1 < o1
        )
        if cond_rev_venta:
            senal = "put"
            tipo_nivel = "resistencia"
            fuerza = 45
            if e13_1 < e21_1:
                fuerza += 5
            if vol1 >= vol_prom * 0.5:
                fuerza += 5

    # --------------------------
    # RETORNO FINAL
    # --------------------------
    if senal is not None:
        fuerza = max(0, min(fuerza, 100))
        return (senal, fuerza, tipo_nivel)

    return None
