import time
import os
import requests
import pandas as pd
from iqoptionapi.stable_api import IQ_Option
from strategy import get_reversal_signal

# ==============================
# CONFIG
# ==============================

EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")

RIESGO = 0.02
EXPIRACION = 1

PARES = ["EURUSD-OTC", "GBPUSD-OTC"]

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==============================
# TELEGRAM
# ==============================

def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg}
            )
        except:
            pass

# ==============================
# CONEXIÓN
# ==============================

def connect():
    iq = IQ_Option(EMAIL, PASSWORD)
    iq.connect()
    iq.change_balance("PRACTICE")
    return iq

# ==============================
# DATOS
# ==============================

def get_df(iq, par):
    data = iq.get_candles(par, 60, 50, time.time())
    df = pd.DataFrame(data)
    df.rename(columns={"max": "high", "min": "low"}, inplace=True)
    return df

# ==============================
# MONTO DINÁMICO
# ==============================

def calcular_monto(iq):
    balance = iq.get_balance()
    return round(balance * RIESGO, 2)

# ==============================
# 🔥 ENTRADA SNIPER
# ==============================

def esperar_confirmacion(iq, par, direccion):
    """
    Espera micro movimiento del precio en la dirección correcta
    """

    precio_inicial = iq.get_candles(par, 1, 1, time.time())[0]["close"]

    for _ in range(10):  # ~0.5 segundos
        time.sleep(0.05)

        precio_actual = iq.get_candles(par, 1, 1, time.time())[0]["close"]

        if direccion == "call" and precio_actual > precio_inicial:
            return True

        if direccion == "put" and precio_actual < precio_inicial:
            return True

    return False

# ==============================
# MAIN
# ==============================

def main():
    iq = connect()
    send("🎯 BOT SNIPER ACTIVADO")

    ultima_vela = None

    while True:
        try:
            tiempo = iq.get_server_timestamp()
            vela = int(tiempo // 60)

            # 🔥 SOLO EN APERTURA REAL
            if vela != ultima_vela:
                ultima_vela = vela

                time.sleep(0.15)  # micro delay real

                for par in PARES:

                    df = get_df(iq, par)
                    señal = get_reversal_signal(df)

                    if señal:
                        tipo, fuerza, motivo = señal

                        # 🔥 CONFIRMACIÓN SNIPER
                        confirmado = esperar_confirmacion(iq, par, tipo)

                        if not confirmado:
                            continue

                        monto = calcular_monto(iq)

                        send(f"""🎯 SNIPER ENTRY
Par: {par}
Tipo: {tipo}
Fuerza: {fuerza}
Monto: ${monto}""")

                        iq.buy(monto, par, tipo, EXPIRACION)

                        time.sleep(60)

            time.sleep(0.3)

        except Exception as e:
            send(f"💥 Error: {str(e)}")
            time.sleep(5)

if __name__ == "__main__":
    main()
