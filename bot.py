import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# ==============================
# CONFIG
# ==============================
logging.basicConfig(level=logging.INFO)

BASE_AMOUNT = 600
EXPIRATION = 1
TIMEFRAME = 60

ENTRY_START = 0
ENTRY_END = 2
MIN_FORCE = 98

PARES = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDCHF-OTC",
    "EURJPY-OTC"
]

# ==============================
# VARIABLES
# ==============================
CUENTAS = []
SEÑAL = None
ULTIMA_VELA = None

# ==============================
# 🔌 CONECTAR 2 CUENTAS
# ==============================
def connect_accounts():
    cuentas = []

    credenciales = [
        (os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        (os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2")),
    ]

    for email, password in credenciales:
        if not email or not password:
            continue

        iq = IQ_Option(email, password)
        iq.connect()

        if iq.check_connect():
            iq.change_balance("PRACTICE")
            print(f"✅ Conectado: {email}")
            cuentas.append(iq)
        else:
            print(f"❌ Error conexión: {email}")

    return cuentas

# ==============================
# 📊 DATAFRAME
# ==============================
def get_df(iq, par):
    candles = iq.get_candles(par, TIMEFRAME, 50, time.time())
    df = pd.DataFrame(candles)

    df.rename(columns={
        "max": "high",
        "min": "low"
    }, inplace=True)

    return df

# ==============================
# 🚀 EJECUTAR EN TODAS LAS CUENTAS
# ==============================
def ejecutar(cuentas, par, direccion):
    for iq in cuentas:
        try:
            estado, _ = iq.buy(BASE_AMOUNT, par, direccion, EXPIRATION)

            if estado:
                print(f"✅ {direccion.upper()} {par} ejecutado")
            else:
                print(f"❌ Error en {par}")

        except Exception as e:
            print(f"💥 Error cuenta: {e}")

# ==============================
# 🔁 LOOP PRINCIPAL
# ==============================
def run():
    global SEÑAL, ULTIMA_VELA, CUENTAS

    CUENTAS = connect_accounts()

    if not CUENTAS:
        print("❌ No hay cuentas conectadas")
        return

    print("🚀 BOT MULTI-CUENTA INICIADO")

    while True:
        try:
            iq_ref = CUENTAS[0]  # usamos una cuenta como referencia de tiempo

            server_time = iq_ref.get_server_timestamp()
            segundos = server_time % 60
            vela = int(server_time // 60)

            # =========================
            # 🔍 DETECTAR SEÑAL
            # =========================
            if 55 <= segundos <= 58:
                mejor = None

                for par in PARES:
                    df = get_df(iq_ref, par)

                    resultado = get_reversal_signal(df)

                    if resultado:
                        direccion, fuerza, tipo = resultado

                        if fuerza >= MIN_FORCE:
                            mejor = (par, direccion, fuerza)

                if mejor:
                    SEÑAL = mejor
                    print(f"🔍 Señal detectada: {mejor}")

            # =========================
            # 🎯 EJECUTAR SNIPER
            # =========================
            if (
                SEÑAL
                and ENTRY_START <= segundos <= ENTRY_END
                and vela != ULTIMA_VELA
            ):
                ULTIMA_VELA = vela

                par, direccion, fuerza = SEÑAL
                SEÑAL = None

                # 🔁 INVERTIR SIEMPRE
                direccion = "put" if direccion == "call" else "call"

                print(f"🚀 SNIPER MULTI | {par} | {direccion} | {fuerza}")

                ejecutar(CUENTAS, par, direccion)

            time.sleep(0.2)

        except Exception as e:
            print(f"💥 Error general: {e}")
            time.sleep(2)
            CUENTAS = connect_accounts()

# ==============================
# START
# ==============================
if __name__ == "__main__":
    requeridas = ["IQ_EMAIL_1","IQ_PASSWORD_1","IQ_EMAIL_2","IQ_PASSWORD_2"]
    faltantes = [v for v in requeridas if not os.getenv(v)]

    if faltantes:
        print(f"❌ Faltan variables: {', '.join(faltantes)}")
    else:
        run()
