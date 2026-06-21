import pandas as pd
import numpy as np

def get_reversal_signal(df, tolerancia=0.0018, ventana=5):
    try:
        # Verificar que hay suficientes velas
        if len(df) < ventana + 2:
            return None

        # Tomar las últimas velas para analizar soporte/resistencia
        ultimos = df.tail(ventana).copy()
        max_nivel = ultimos["high"].max()
        min_nivel = ultimos["low"].min()
        
        ultimo = df.iloc[-1]
        penultimo = df.iloc[-2]

        # Evitar división por cero
        if max_nivel == min_nivel:
            return None

        fuerza = 0
        señal = None
        tipo_nivel = ""

        # Señal de COMPRA (soporte + vela alcista)
        if abs(ultimo["close"] - min_nivel) <= tolerancia:
            if ultimo["close"] > ultimo["open"] and penultimo["close"] < penultimo["open"]:
                fuerza = int(((max_nivel - ultimo["low"]) / (max_nivel - min_nivel)) * 100)
                señal = "call"
                tipo_nivel = "soporte"

        # Señal de VENTA (resistencia + vela bajista)
        elif abs(ultimo["close"] - max_nivel) <= tolerancia:
            if ultimo["close"] < ultimo["open"] and penultimo["close"] > penultimo["open"]:
                fuerza = int(((ultimo["high"] - min_nivel) / (max_nivel - min_nivel)) * 100)
                señal = "put"
                tipo_nivel = "resistencia"

        # Devolver solo si se encontró señal válida
        if señal is not None and fuerza > 0:
            return señal, fuerza, tipo_nivel
        return None

    except Exception as e:
        # Si hay error en cálculo, devolver vacío
        return None
