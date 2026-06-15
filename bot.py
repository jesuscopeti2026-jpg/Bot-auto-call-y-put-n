import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# Configuración de registros
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ⚙️ CONFIGURACIÓN
MONTO_POR_OPERACION = 600       # Cada operación es de este monto
EXPIRACION = 1                   # Vencimiento en minutos
TIEMPO_VELA = 60                 # Temporalidad en segundos
FUERZA_MINIMA = 98              # Fuerza mínima para operar
REINTENTOS_MAX = 4              # Reintentos si falla
ESPERA_REINTENTO = 0.05
SEGUNDO_DETECCION = 56          # Momento para buscar señal
SEGUNDO_INICIO = 57             # Inicio rango de entrada
SEGUNDO_FIN = 59                # Fin rango de entrada

# Activos a monitorear
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "USDJPY-OTC"
]

# Variables globales
CUENTAS = []
SEÑAL_ACTUAL = None
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION_CUENTA1 = None
ULTIMA_OPERACION_CUENTA2 = None

# ==============================
# CONEXIÓN A LAS CUENTAS
# ==============================
def conectar_cuentas():
    cuentas = []
    credenciales = [
        {"nombre": "CUENTA_1", "email": os.getenv("IQ_EMAIL_1"), "password": os.getenv("IQ_PASSWORD_1")},
        {"nombre": "CUENTA_2", "email": os.getenv("IQ_EMAIL_2"), "password": os.getenv("IQ_PASSWORD_2")}
    ]

    for datos in credenciales:
        if not datos["email"] or not datos["password"]:
            logger.error(f"{datos['nombre']}: Faltan credenciales")
            continue

        iq = IQ_Option(datos["email"], datos["password"])
        conectado, motivo = iq.connect()

        if conectado:
            try:
                iq.change_balance("PRACTICE")
                time.sleep(0.1)
                saldo = iq.get_balance()
                if saldo >= MONTO_POR_OPERACION:
                    logger.info(f"✅ {datos['nombre']} conectada | Saldo: ${saldo:.2f}")
                    cuentas.append({"nombre": datos["nombre"], "conexion": iq})
                else:
                    logger.error(f"❌ {datos['nombre']}: Saldo insuficiente (${saldo:.2f})")
            except Exception as e:
                logger.error(f"⚠️ {datos['nombre']}: Conectada pero error al verificar saldo: {e}")
        else:
            logger.error(f"❌ {datos['nombre']}: No se pudo conectar | Motivo: {motivo}")

    if len(cuentas) != 2:
        logger.critical(f"⚠️ Solo {len(cuentas)} cuentas activas. Se requieren exactamente 2")
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
        logger.error(f"⚠️ Error obteniendo datos de {activo}: {e}")
        return None

# ==============================
# EJECUTAR OPERACIÓN EN UNA CUENTA
# ==============================
def ejecutar_operacion(nombre_cuenta, conexion, activo, direccion):
    for intento in range(REINTENTOS_MAX):
        try:
            if not conexion.check_connect():
                conexion.connect()
                time.sleep(0.03)

            estado, id_operacion = conexion.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_operacion > 0:
                logger.info(f"✅ {nombre_cuenta} | Ejecutado | {activo} | {direccion} | Intento {intento+1}")
                return True
            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre_cuenta} | Intento {intento+1} fallido: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre_cuenta} | No se pudo ejecutar {activo} tras {REINTENTOS_MAX} intentos")
    return False

# ==============================
# BUCLE PRINCIPAL DEL BOT
# ==============================
def iniciar_bot():
    global SEÑAL_ACTUAL, ULTIMA_VELA_PROCESADA, CUENTAS
    global ULTIMA_OPERACION_CUENTA1, ULTIMA_OPERACION_CUENTA2

    CUENTAS = conectar_cuentas()
    if len(CUENTAS) != 2:
        return

    logger.info("🚀 BOT INICIADO | 1 operación por cuenta | Sin duplicados | Fuerza ≥ 98")

    while True:
        try:
            # Usamos la primera cuenta como referencia de tiempo
            iq_referencia = CUENTAS[0]["conexion"]
            tiempo_servidor = iq_referencia.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # --------------------------
            # PASO 1: Detectar señal en segundo 56
            # --------------------------
            if segundos == SEGUNDO_DETECCION:
                mejor_señal = None
                mayor_fuerza = 0

                for activo in ACTIVOS:
                    df = obtener_datos(iq_referencia, activo)
                    if df is None:
                        continue

                    resultado = get_reversal_signal(df)
                    if resultado:
                        direccion, fuerza, tipo = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_señal = (activo, direccion, fuerza)

                SEÑAL_ACTUAL = mejor_señal
                if SEÑAL_ACTUAL:
                    logger.info(f"🔍 Señal detectada: {SEÑAL_ACTUAL}")

            # --------------------------
            # PASO 2: Ejecutar solo 1 vez por cuenta, sin duplicar
            # --------------------------
            if SEÑAL_ACTUAL and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN and vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_VELA_PROCESADA = vela_actual
                activo, direccion_original, fuerza = SEÑAL_ACTUAL
                SEÑAL_ACTUAL = None

                # Aplicamos la lógica de inversión que usas
                direccion_final = "put" if direccion_original == "call" else "call"
                logger.info(f"🎯 ENTRADA | Activo: {activo} | Dirección: {direccion_final} | Fuerza: {fuerza}")

                # ✅ CONTROL ESTRICTO: 1 operación por cuenta, no más
                ok1 = False
                ok2 = False

                if ULTIMA_OPERACION_CUENTA1 != vela_actual:
                    ok1 = ejecutar_operacion(CUENTAS[0]["nombre"], CUENTAS[0]["conexion"], activo, direccion_final)
                    if ok1:
                        ULTIMA_OPERACION_CUENTA1 = vela_actual

                if ULTIMA_OPERACION_CUENTA2 != vela_actual:
                    ok2 = ejecutar_operacion(CUENTAS[1]["nombre"], CUENTAS[1]["conexion"], activo, direccion_final)
                    if ok2:
                        ULTIMA_OPERACION_CUENTA2 = vela_actual

                # Resumen final
                if ok1 and ok2:
                    logger.info("✅ AMBAS CUENTAS EJECUTADAS CORRECTAMENTE")
                elif ok1:
                    logger.warning("⚠️ Solo CUENTA 1 ejecutó, CUENTA 2 falló")
                elif ok2:
                    logger.warning("⚠️ Solo CUENTA 2 ejecutó, CUENTA 1 falló")
                else:
                    logger.error("❌ Ninguna cuenta ejecutó la operación")

            time.sleep(0.02)

        except Exception as e:
            logger.error(f"💥 Error en el bucle principal: {str(e)}")
            time.sleep(1)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
