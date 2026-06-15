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
MONTO_POR_OPERACION = 600       # Monto fijo por operación
EXPIRACION = 1                   # Vencimiento en minutos
TIEMPO_VELA = 60                 # Temporalidad 1 minuto (en segundos)
FUERZA_MINIMA = 98               # Fuerza mínima para abrir operación
REINTENTOS_MAX = 4               # Reintentos si falla la ejecución
ESPERA_REINTENTO = 0.05         # Tiempo entre reintentos
SEGUNDO_DETECCION = 56          # Segundo para buscar la señal
SEGUNDO_INICIO = 57             # Inicio del rango de entrada
SEGUNDO_FIN = 59                # Fin del rango de entrada

# Activos a monitorear
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

# Variables de control
CUENTAS = []
SEÑAL_ACTUAL = None
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION_C1 = None
ULTIMA_OPERACION_C2 = None

# ==============================
# CONEXIÓN A LAS 2 CUENTAS
# ==============================
def conectar_cuentas():
    cuentas = []
    credenciales = [
        ("CUENTA_1", os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        ("CUENTA_2", os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"))
    ]

    for nombre, email, password in credenciales:
        if not email or not password:
            logger.error(f"{nombre}: Faltan credenciales")
            continue

        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            try:
                iq.change_balance("PRACTICE")
                time.sleep(0.1)
                saldo = iq.get_balance()
                if saldo >= MONTO_POR_OPERACION:
                    logger.info(f"✅ {nombre} conectada | Saldo: ${saldo:.2f}")
                    cuentas.append( (nombre, iq) )
                else:
                    logger.error(f"❌ {nombre}: Saldo insuficiente (${saldo:.2f})")
            except Exception as e:
                logger.error(f"⚠️ {nombre}: Conectada, error al verificar saldo: {e}")
        else:
            logger.error(f"❌ {nombre}: No conectó | Motivo: {motivo}")

    if len(cuentas) != 2:
        logger.critical("⚠️ Se requieren 2 cuentas conectadas para operar")
    return cuentas

# ==============================
# OBTENER DATOS DE VELAS
# ==============================
def obtener_datos(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.05)

        velas = iq.get_candles(activo, TIEMPO_VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None

        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df

    except Exception as e:
        logger.error(f"⚠️ Error en {activo}: {e}")
        return None

# ==============================
# EJECUTAR OPERACIÓN EN UNA CUENTA
# ==============================
def ejecutar(nombre, iq, activo, direccion):
    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.03)

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                logger.info(f"✅ {nombre} | {activo} | {direccion} | Intento {intento+1}")
                return True
            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre} | Intento {intento+1} fallido: {e}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | No se pudo ejecutar {activo}")
    return False

# ==============================
# BUCLE PRINCIPAL
# ==============================
def iniciar_bot():
    global SEÑAL_ACTUAL, ULTIMA_VELA_PROCESADA, CUENTAS
    global ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2

    CUENTAS = conectar_cuentas()
    if len(CUENTAS) != 2:
        return

    logger.info("🚀 BOT INICIADO | 1 operación por cuenta | Sin duplicados | Fuerza ≥ 98")

    while True:
        try:
            _, iq_ref = CUENTAS[0]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Detectar señal en segundo 56
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                for activo in ACTIVOS:
                    df = obtener_datos(iq_ref, activo)
                    if df is None:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_, fuerza, _ = res
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (activo, dir_, fuerza)
                SEÑAL_ACTUAL = mejor
                if SEÑAL_ACTUAL:
                    logger.info(f"🔍 Señal: {SEÑAL_ACTUAL}")

            # Ejecutar solo 1 vez por cuenta y por vela
            if SEÑAL_ACTUAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_VELA_PROCESADA = vela_actual
                activo, dir_ori, fuerza = SEÑAL_ACTUAL
                SEÑAL_ACTUAL = None

                # Aplicar inversión de dirección
                dir_final = "put" if dir_ori == "call" else "call"
                logger.info(f"🎯 ENTRADA | {activo} | {dir_final} | Fuerza: {fuerza}")

                ok1 = False
                ok2 = False

                if ULTIMA_OPERACION_C1 != vela_actual:
                    ok1 = ejecutar(CUENTAS[0][0], CUENTAS[0][1], activo, dir_final)
                    if ok1:
                        ULTIMA_OPERACION_C1 = vela_actual

                if ULTIMA_OPERACION_C2 != vela_actual:
                    ok2 = ejecutar(CUENTAS[1][0], CUENTAS[1][1], activo, dir_final)
                    if ok2:
                        ULTIMA_OPERACION_C2 = vela_actual

                if ok1 and ok2:
                    logger.info("✅ AMBAS CUENTAS EJECUTADAS")
                elif ok1:
                    logger.warning("⚠️ Solo Cuenta 1 ejecutó")
                elif ok2:
                    logger.warning("⚠️ Solo Cuenta 2 ejecutó")
                else:
                    logger.error("❌ Ninguna cuenta ejecutó")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {e}")
            time.sleep(1)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
