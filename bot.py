import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ⚙️ CONFIGURACIÓN
BASE_AMOUNT = 600
EXPIRATION = 1
TIMEFRAME = 60
MIN_FORCE = 98
MAX_REINTENTOS = 5
ESPERA_REINTENTO = 0.06
SEGUNDO_DETECCION = 56
SEGUNDO_INICIO = 57
SEGUNDO_FIN = 59

PARES = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

CUENTAS = []
SEÑAL = None
ULTIMA_VELA = None

# ==============================
# CONEXIÓN Y VERIFICACIÓN DE CUENTAS
# ==============================
def connect_accounts():
    cuentas = []
    credenciales = [
        ("CUENTA_1", os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        ("CUENTA_2", os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2")),
    ]

    for nombre, email, password in credenciales:
        if not email or not password:
            logger.error(f"{nombre}: Faltan credenciales")
            continue

        iq = IQ_Option(email, password)
        conectado, razon = iq.connect()

        if conectado:
            try:
                # Cambiar a saldo de práctica y verificar
                iq.change_balance("PRACTICE")
                time.sleep(0.1)
                saldo = iq.get_balance()

                if saldo >= BASE_AMOUNT:
                    logger.info(f"✅ {nombre} conectada | Saldo: ${saldo:.2f}")
                    cuentas.append(iq)
                else:
                    logger.error(f"❌ {nombre}: Saldo insuficiente (${saldo:.2f})")
            except Exception as e:
                logger.error(f"⚠️ {nombre} conectada pero no se pudo verificar saldo: {e}")
        else:
            logger.error(f"❌ {nombre} no conectó | Motivo: {razon}")

    # Verificar que estén las 2 cuentas
    if len(cuentas) != 2:
        logger.critical(f"⚠️ Solo {len(cuentas)} cuentas activas. Se necesitan 2 para operar")
    return cuentas

# ==============================
# OBTENER DATOS DE VELAS
# ==============================
def get_df(iq, par):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.05)

        velas = iq.get_candles(par, TIMEFRAME, 50, time.time())
        if not velas or len(velas) < 30:
            return None

        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"⚠️ Error en {par}: {e}")
        return None

# ==============================
# EJECUTAR EN UNA CUENTA
# ==============================
def ejecutar_en_cuenta(iq, nombre_cuenta, monto, par, direccion):
    for intento in range(MAX_REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.03)

            estado, id_op = iq.buy(monto, par, direccion, EXPIRATION)
            if estado and id_op > 0:
                logger.info(f"✅ {nombre_cuenta} | Ejecutado en intento {intento+1} | {par} {direccion}")
                return True

            if intento < MAX_REINTENTOS - 1:
                time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre_cuenta} | Intento {intento+1} fallido: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre_cuenta} | No se pudo ejecutar {par} tras {MAX_REINTENTOS} intentos")
    return False

# ==============================
# EJECUTAR SIMULTÁNEO EN AMBAS
# ==============================
def ejecutar_ambas(cuentas, par, direccion):
    # Ejecutar en paralelo con margen de tiempo
    ok1 = ejecutar_en_cuenta(cuentas[0], "CUENTA 1", BASE_AMOUNT, par, direccion)
    ok2 = ejecutar_en_cuenta(cuentas[1], "CUENTA 2", BASE_AMOUNT, par, direccion)
    return ok1 and ok2

# ==============================
# BUCLE PRINCIPAL
# ==============================
def run():
    global SEÑAL, ULTIMA_VELA, CUENTAS

    CUENTAS = connect_accounts()
    if len(CUENTAS) != 2:
        return

    logger.info("🚀 BOT INICIADO | 2 CUENTAS SINCRONIZADAS | Entrada: 57-59s | Fuerza ≥ 98")

    while True:
        try:
            # Usar cuenta 1 como referencia de tiempo
            iq_ref = CUENTAS[0]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Detectar señal en segundo 56
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                for par in PARES:
                    df = get_df(iq_ref, par)
                    if df is None:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_, fuerza, _ = res
                        if fuerza >= MIN_FORCE and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (par, dir_, fuerza)
                SEÑAL = mejor
                if SEÑAL:
                    logger.info(f"🔍 Señal: {SEÑAL}")

            # Ejecutar en rango seguro
            if SEÑAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                par, dir_ori, fuerza = SEÑAL
                SEÑAL = None

                # Aplicar inversión
                dir_final = "put" if dir_ori == "call" else "call"
                logger.info(f"🎯 ENTRADA | {par} | {dir_final} | Fuerza: {fuerza}")

                exito = ejecutar_ambas(CUENTAS, par, dir_final)
                if not exito:
                    logger.warning("⚠️ Una o ambas cuentas fallaron en la ejecución")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {e}")
            time.sleep(1)
            CUENTAS = connect_accounts()

if __name__ == "__main__":
    run()
