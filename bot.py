import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# Configuración de logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ⚙️ CONFIGURACIÓN PRINCIPAL
BASE_AMOUNT = 600               # Monto por operación
EXPIRATION = 1                  # Vencimiento en minutos
TIMEFRAME = 60                  # Temporalidad en segundos
MIN_FORCE = 98                  # Fuerza mínima para operar
MAX_REINTENTOS = 4              # Reintentos si falla
ESPERA_REINTENTO = 0.05         # Tiempo entre intentos
SEGUNDO_DETECCION = 56          # Segundo para buscar señal
SEGUNDO_INICIO = 57             # Inicio rango de entrada
SEGUNDO_FIN = 59                # Fin rango de entrada

# Activos a monitorear
PARES = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

# Variables globales
CUENTAS = []
SEÑAL = None
ULTIMA_VELA = None

# ==============================
# CONEXIÓN A LAS 2 CUENTAS
# ==============================
def connect_accounts():
    cuentas = []
    credenciales = [
        (os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        (os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2")),
    ]

    for idx, (email, password) in enumerate(credenciales, 1):
        if not email or not password:
            logger.warning(f"⚠️ Cuenta {idx}: faltan credenciales")
            continue

        iq = IQ_Option(email, password)
        conectado, razon = iq.connect()

        if conectado:
            try:
                iq.change_balance("PRACTICE")
                saldo = iq.get_balance()
                logger.info(f"✅ Cuenta {idx} conectada | {email} | Saldo: ${saldo:.2f}")
                cuentas.append(iq)
            except Exception as e:
                logger.error(f"❌ Cuenta {idx} conectada pero sin acceso a saldo: {e}")
        else:
            logger.error(f"❌ Cuenta {idx} no conectó | Motivo: {razon}")

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
        logger.error(f"⚠️ Error datos {par}: {e}")
        return None

# ==============================
# EJECUTAR OPERACIÓN EN 1 CUENTA
# ==============================
def ejecutar_en_cuenta(iq, monto, par, direccion):
    for intento in range(MAX_REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.03)

            estado, id_operacion = iq.buy(monto, par, direccion, EXPIRATION)

            if estado and id_operacion > 0:
                logger.info(f"✅ Ejecutado en intento {intento+1} | {par} {direccion}")
                return True

            if intento < MAX_REINTENTOS - 1:
                time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"Intento {intento+1} fallido: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    return False

# ==============================
# EJECUTAR EN AMBAS CUENTAS AL MISMO TIEMPO
# ==============================
def ejecutar_ambas_cuentas(cuentas, par, direccion):
    exito_total = True
    for i, iq in enumerate(cuentas, 1):
        ok = ejecutar_en_cuenta(iq, BASE_AMOUNT, par, direccion)
        if not ok:
            logger.error(f"❌ Cuenta {i} no pudo ejecutar {par}")
            exito_total = False
    return exito_total

# ==============================
# BUCLE PRINCIPAL
# ==============================
def run():
    global SEÑAL, ULTIMA_VELA, CUENTAS

    CUENTAS = connect_accounts()
    if len(CUENTAS) != 2:
        logger.critical("❌ Se requieren las 2 cuentas conectadas para funcionar")
        return

    logger.info("🚀 BOT INICIADO | Ejecución simultánea 2 cuentas | Rango: 57‑59 seg | Fuerza ≥ 98")

    while True:
        try:
            # Usamos la primera cuenta como referencia de tiempo
            iq_ref = CUENTAS[0]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # --------------------------
            # PASO 1: Detectar señal en segundo 56
            # --------------------------
            if segundos == SEGUNDO_DETECCION:
                mejor_señal = None
                mayor_fuerza = 0

                for par in PARES:
                    df = get_df(iq_ref, par)
                    if df is None:
                        continue

                    resultado = get_reversal_signal(df)
                    if resultado:
                        direccion, fuerza, tipo = resultado
                        if fuerza >= MIN_FORCE and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_señal = (par, direccion, fuerza)

                SEÑAL = mejor_señal
                if SEÑAL:
                    logger.info(f"🔍 Señal detectada: {SEÑAL}")

            # --------------------------
            # PASO 2: Ejecutar en rango 57‑59
            # --------------------------
            if SEÑAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                par, direccion_original, fuerza = SEÑAL
                SEÑAL = None

                # Aplicamos la lógica invertida que usas
                direccion_final = "put" if direccion_original == "call" else "call"

                logger.info(f"🎯 ENTRADA | {par} | {direccion_final} | Fuerza: {fuerza}")

                # Ejecutar en las 2 cuentas al mismo tiempo
                exito = ejecutar_ambas_cuentas(CUENTAS, par, direccion_final)

                if not exito:
                    logger.error(f"❌ Error ejecución final en {par} tras {MAX_REINTENTOS} intentos")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            time.sleep(1)
            CUENTAS = connect_accounts()

if __name__ == "__main__":
    run()
