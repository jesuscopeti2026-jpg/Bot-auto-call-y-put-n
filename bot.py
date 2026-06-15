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

# ⚙️ CONFIGURACIÓN OPTIMIZADA
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 6               # Más intentos para activos difíciles
ESPERA_REINTENTO = 0.08          # Más tiempo entre intentos
SEGUNDO_DETECCION = 55           # Detener señal 1 segundo antes
SEGUNDO_INICIO = 56              # Entrada más temprana
SEGUNDO_FIN = 59                 # Rango amplio

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

CUENTAS = []
SEÑAL_ACTUAL = None
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION_C1 = None
ULTIMA_OPERACION_C2 = None

# ==============================
# CONEXIÓN LIMPIA E INDEPENDIENTE
# ==============================
def conectar_cuenta(email, password, nombre):
    try:
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            time.sleep(0.3)
            iq.change_balance("PRACTICE")
            time.sleep(0.4)
            saldo = iq.get_balance()
            logger.info(f"✅ {nombre} | Correo: {email} | Saldo: ${saldo:.2f}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error conexión {nombre}: {str(e)}")
        return None, 0

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
        iq, saldo = conectar_cuenta(email, password, nombre)
        if iq and saldo >= MONTO_POR_OPERACION:
            cuentas.append({"nombre": nombre, "conexion": iq, "saldo": saldo})
    return cuentas

# ==============================
# OBTENER DATOS DE VELAS
# ==============================
def obtener_datos(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.1)
        velas = iq.get_candles(activo, TIEMPO_VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"⚠️ Datos {activo}: {e}")
        return None

# ==============================
# EJECUTAR CON VALIDACIÓN MEJORADA
# ==============================
def ejecutar_operacion(cuenta, activo, direccion):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)

            # Verificar activo disponible
            activos = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos:
                logger.warning(f"⚠️ {nombre}: {activo} no disponible en este momento")
                time.sleep(0.2)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                logger.info(f"✅ {nombre} | Ejecutado: {activo} {direccion} | Intento {intento+1}")
                return True

            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre} | Intento {intento+1} fallido: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | Falló en {activo} tras {REINTENTOS_MAX} intentos")
    return False

# ==============================
# BUCLE PRINCIPAL SINCRONIZADO
# ==============================
def iniciar_bot():
    global SEÑAL_ACTUAL, ULTIMA_VELA_PROCESADA, CUENTAS
    global ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2

    CUENTAS = conectar_cuentas()
    if len(CUENTAS) != 2:
        logger.critical("❌ No hay 2 cuentas conectadas")
        return

    logger.info("🚀 BOT MEJORADO | Sesiones separadas | Entrada 56-59s | Fuerza ≥ 98")

    while True:
        try:
            iq_ref = CUENTAS[0]["conexion"]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Detectar señal en segundo 55
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

            # Ejecutar en rango amplio
            if SEÑAL_ACTUAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_VELA_PROCESADA = vela_actual
                activo, dir_ori, fuerza = SEÑAL_ACTUAL
                SEÑAL_ACTUAL = None

                dir_final = "put" if dir_ori == "call" else "call"
                logger.info(f"🎯 ENTRADA | {activo} | {dir_final} | Fuerza: {fuerza}")

                ok1 = ok2 = False
                if ULTIMA_OPERACION_C1 != vela_actual:
                    ok1 = ejecutar_operacion(CUENTAS[0], activo, dir_final)
                    if ok1: ULTIMA_OPERACION_C1 = vela_actual

                if ULTIMA_OPERACION_C2 != vela_actual:
                    ok2 = ejecutar_operacion(CUENTAS[1], activo, dir_final)
                    if ok2: ULTIMA_OPERACION_C2 = vela_actual

                if ok1 and ok2:
                    logger.info("✅ AMBAS CUENTAS EJECUTADAS")
                elif ok1:
                    logger.warning("⚠️ Solo Cuenta 1 ejecutó")
                elif ok2:
                    logger.warning("⚠️ Solo Cuenta 2 ejecutó")
                else:
                    logger.error("❌ Ninguna ejecutó")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error: {str(e)}")
            time.sleep(1)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
