import pandas as pd
import numpy as np

def get_reversal_signal(df, tolerancia=0.0018, ventana=5):
    """
    Devuelve SIEMPRE tupla (señal, fuerza, tipo_nivel) o None
    ✅ Evita error 'local variable fz'
    """
    try:
        if len(df) < ventana + 2:
            return None

        ultimos = df.tail(ventana)
        max_nivel = ultimos["high"].max()
        min_nivel = ultimos["low"].min()
        ultimo = df.iloc[-1]
        penultimo = df.iloc[-2]

        fuerza = 0
        señal = None
        tipo_nivel = ""

        # Señal COMPRA (soporte + rechazo)
        if abs(ultimo["close"] - min_nivel) <= tolerancia:
            if ultimo["close"] > ultimo["open"] and penultimo["close"] < penultimo["open"]:
                fuerza = int( ( (max_nivel - ultimo["low"]) / (max_nivel - min_nivel) ) * 100 )
                señal = "call"
                tipo_nivel = "soporte"

        # Señal VENTA (resistencia + rechazo)
        elif abs(ultimo["close"] - max_nivel) <= tolerancia:
            if ultimo["close"] < ultimo["open"] and penultimo["close"] > penultimo["open"]:
                fuerza = int( ( (ultimo["high"] - min_nivel) / (max_nivel - min_nivel) ) * 100 )
                señal = "put"
                tipo_nivel = "resistencia"

        # ✅ Garantiza retorno completo
        if señal and fuerza > 0:
            return señal, fuerza, tipo_nivel
        return None

    except Exception as e:
        return None
