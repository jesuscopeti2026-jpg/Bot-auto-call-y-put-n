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
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 6
ESPERA_REINTENTO = 0.08
SEGUNDO_DETECCION = 55
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

# Control de operaciones por vela
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}

# ==============================
# CONEXIÓN COMPLETAMENTE LIMPIA
# ==============================
def conectar_cuenta(email, password, nombre):
    """Cierra cualquier sesión anterior antes de conectar"""
    try:
        # Crear instancia nueva por cada cuenta
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            # Forzar cambio de saldo y esperar confirmación
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
        logger.error(f"❌ Error al conectar {nombre}: {str(e)}")
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
# EJECUTAR 1 SOLA VEZ POR CUENTA
# ==============================
def ejecutar_operacion(cuenta, activo, direccion, vela_id):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]

    # ❗ BLOQUEO ESTRICTO: no operar si ya se hizo en esta vela
    if ULTIMA_OPERACION[nombre] == vela_id:
        logger.info(f"🔒 {nombre}: Ya operó en esta vela, se omite")
        return False

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)

            activos = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos:
                logger.warning(f"⚠️ {nombre}: {activo} no disponible")
                time.sleep(0.2)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                ULTIMA_OPERACION[nombre] = vela_id
                logger.info(f"✅ {nombre} | Ejecutado: {activo} {direccion} | Intento {intento+1}")
                return True

            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre} | Intento {intento+1}: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | Falló en {activo}")
    return False

# ==============================
# BUCLE PRINCIPAL
# ==============================
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION
    CUENTAS = conectar_cuentas()

    if len(CUENTAS) != 2:
        logger.critical("❌ Se necesitan 2 cuentas conectadas")
        return

    logger.info("🚀 BOT FINAL | 1 operación por cuenta | SIN DUPLICADOS | Fuerza ≥ 98")

    while True:
        try:
            iq_ref = CUENTAS[0]["conexion"]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Reiniciar controladores al cambiar de vela
            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}
                ULTIMA_VELA_PROCESADA = vela_actual

            # Detectar señal
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

            # Ejecutar en rango seguro
            if 'SEÑAL_ACTUAL' in globals() and SEÑAL_ACTUAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, dir_ori, fuerza = SEÑAL_ACTUAL
                SEÑAL_ACTUAL = None

                dir_final = "put" if dir_ori == "call" else "call"
                logger.info(f"🎯 ENTRADA | {activo} | {dir_final} | Fuerza: {fuerza}")

                ok1 = ejecutar_operacion(CUENTAS[0], activo, dir_final, vela_actual)
                ok2 = ejecutar_operacion(CUENTAS[1], activo, dir_final, vela_actual)

                if ok1 and ok2:
                    logger.info("✅ AMBAS CUENTAS EJECUTADAS CORRECTAMENTE")
                elif ok1:
                    logger.warning("⚠️ Solo CUENTA 1 operó")
                elif ok2:
                    logger.warning("⚠️ Solo CUENTA 2 operó")
                else:
                    logger.error("❌ Ninguna operó")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error: {str(e)}")
            time.sleep(1)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
