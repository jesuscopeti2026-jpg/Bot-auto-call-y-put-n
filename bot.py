import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ⚙️ CONFIGURACIÓN
BASE_AMOUNT = 600
EXPIRATION = 1
TIMEFRAME = 60
MIN_FORCE = 98
MAX_REINTENTOS = 5           # Reintentos si falla
ESPERA_REINTENTO = 0.08      # Tiempo entre intentos
SEGUNDO_INICIO = 57          # Empezar antes, margen de seguridad
SEGUNDO_FIN = 59             # Rango válido

PARES = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

CUENTAS = []
SEÑAL = None
ULTIMA_VELA = None

# ==============================
# CONEXIÓN A MÚLTIPLES CUENTAS
# ==============================
def connect_accounts():
    cuentas = []
    credenciales = [
        (os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        (os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2")),
    ]

    for email, password in credenciales:
        if not email or not password:
            logger.warning("Faltan credenciales para una cuenta")
            continue

        iq = IQ_Option(email, password)
        conectado, razon = iq.connect()

        if conectado:
            try:
                iq.change_balance("PRACTICE")
                balance = iq.get_balance()
                logger.info(f"✅ Conectado: {email} | Saldo: ${balance:.2f}")
                cuentas.append(iq)
            except Exception as e:
                logger.error(f"⚠️ Conectado pero no se pudo obtener saldo: {e}")
        else:
            logger.error(f"❌ Error conexión {email}: {razon}")

    return cuentas

# ==============================
# OBTENER DATOS DE VELAS
# ==============================
def get_df(iq, par):
    try:
        if not iq.check_connect():
            iq.connect()
        candles = iq.get_candles(par, TIMEFRAME, 50, time.time())
        if not candles:
            return None
        df = pd.DataFrame(candles)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"Error datos {par}: {e}")
        return None

# ==============================
# EJECUTAR CON REINTENTOS
# ==============================
def ejecutar_con_reintentos(iq, monto, par, direccion, vencimiento):
    for intento in range(MAX_REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.05)

            estado, id_operacion = iq.buy(monto, par, direccion, vencimiento)
            if estado and id_operacion > 0:
                logger.info(f"✅ Ejecutado en intento {intento+1} | {par} {direccion}")
                return True, id_operacion

            if intento < MAX_REINTENTOS - 1:
                time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"Intento {intento+1} fallido: {e}")
            time.sleep(ESPERA_REINTENTO)

    return False, None

def ejecutar(cuentas, par, direccion):
    resultados = []
    for iq in cuentas:
        ok, _ = ejecutar_con_reintentos(iq, BASE_AMOUNT, par, direccion, EXPIRATION)
        resultados.append(ok)
    return any(resultados)

# ==============================
# BUCLE PRINCIPAL
# ==============================
def run():
    global SEÑAL, ULTIMA_VELA, CUENTAS

    CUENTAS = connect_accounts()
    if not CUENTAS:
        logger.critical("❌ Sin cuentas conectadas")
        return

    logger.info("🚀 BOT INICIADO | Rango entrada: 57‑59 seg | Fuerza ≥ 98")

    while True:
        try:
            iq = CUENTAS[0]
            server_time = iq.get_server_timestamp()
            segundos = int(server_time % 60)
            vela_actual = int(server_time // 60)

            # DETECTAR SEÑAL EN SEGUNDO 56
            if segundos == 56:
                mejor = None
                mayor_fuerza = 0
                for par in PARES:
                    df = get_df(iq, par)
                    if df is None:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_, fuerza, tipo = res
                        if fuerza >= MIN_FORCE and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (par, dir_, fuerza)
                SEÑAL = mejor
                if SEÑAL:
                    logger.info(f"🔍 Señal: {SEÑAL}")

            # EJECUTAR EN RANGO 57‑59
            if SEÑAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                par, direccion, fuerza = SEÑAL
                SEÑAL = None

                direccion_final = "put" if direccion == "call" else "call"
                logger.info(f"🎯 ENTRADA | {par} | {direccion_final} | Fuerza: {fuerza}")

                exito = ejecutar(CUENTAS, par, direccion_final)
                if not exito:
                    logger.error(f"❌ Error ejecución final en {par} tras {MAX_REINTENTOS} intentos")

            time.sleep(0.03)

        except Exception as e:
            logger.error(f"💥 Error bucle: {e}")
            time.sleep(1)
            CUENTAS = connect_accounts()

if __name__ == "__main__":
    run()
