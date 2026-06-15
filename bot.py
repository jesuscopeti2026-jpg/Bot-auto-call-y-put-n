import time
import os
import pandas as pd
import logging
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# Configuración de logs completa
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ⚙️ CONFIGURACIÓN OPTIMIZADA
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 8
ESPERA_REINTENTO = 0.1
ESPERA_ENTRE_CUENTAS = 0.2
SEGUNDO_DETECCION = 55
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control de ejecución
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}

# ==============================
# CONEXIÓN INDEPENDIENTE POR CUENTA
# ==============================
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()

        if conectado:
            time.sleep(0.5)
            iq.change_balance("PRACTICE")
            time.sleep(0.5)
            saldo = iq.get_balance()
            logger.info(f"✅ {nombre} conectada | Correo: {email} | Saldo: ${saldo:.2f}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_todas():
    cuentas = []
    credenciales = [
        ("CUENTA_1", os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        ("CUENTA_2", os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"))
    ]

    for nombre, correo, clave in credenciales:
        if not correo or not clave:
            logger.error(f"{nombre}: Faltan credenciales")
            continue
        iq, saldo = conectar_cuenta(correo, clave, nombre)
        if iq and saldo >= MONTO_POR_OPERACION:
            cuentas.append({"nombre": nombre, "conexion": iq, "saldo": saldo})
        time.sleep(0.7)

    if len(cuentas) != 2:
        logger.critical(f"⚠️ Solo {len(cuentas)} cuentas conectadas. Se requieren 2")
    return cuentas

# ==============================
# OBTENER DATOS DE VELAS
# ==============================
def obtener_datos(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
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
# EJECUTAR ORDEN EN UNA CUENTA
# ==============================
def ejecutar_orden(cuenta, activo, direccion, vela_id):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]

    if ULTIMA_OPERACION[nombre] == vela_id:
        logger.info(f"🔒 {nombre}: Ya operó en esta vela")
        return False

    logger.info(f"🚀 Enviando orden a {nombre}: {activo} {direccion} ${MONTO_POR_OPERACION}")

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.15)

            activos_disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos_disponibles:
                logger.warning(f"⚠️ {nombre}: {activo} no disponible")
                time.sleep(0.2)
                continue

            estado, id_operacion = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_operacion > 0:
                ULTIMA_OPERACION[nombre] = vela_id
                logger.info(f"✅ {nombre} | Ejecutado con éxito | ID: {id_operacion} | {activo} {direccion}")
                return True

            time.sleep(ESPERA_REINTENTO)

        except Exception as e:
            logger.warning(f"⚠️ {nombre} | Intento {intento+1} fallido: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | No se pudo ejecutar {activo}")
    return False

# ==============================
# BUCLE PRINCIPAL SIN DETENCIONES
# ==============================
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION
    cuentas = conectar_todas()

    if len(cuentas) != 2:
        return

    logger.info("="*60)
    logger.info("🤖 BOT ACTIVO | 1 OPERACIÓN POR CUENTA | SIN DUPLICADOS")
    logger.info(f"⚙️ Fuerza mínima: {FUERZA_MINIMA} | Entrada: {SEGUNDO_INICIO}-{SEGUNDO_FIN}s")
    logger.info("="*60)

    while True:
        try:
            iq_ref = cuentas[0]["conexion"]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            # Reiniciar control al cambiar de vela
            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}
                ULTIMA_VELA_PROCESADA = vela_actual
                logger.debug(f"🔄 Nueva vela iniciada: {vela_actual}")

            # Paso 1: Detectar señal en segundo 55
            if segundos == SEGUNDO_DETECCION:
                mejor_señal = None
                mayor_fuerza = 0
                logger.info("🔍 Buscando señales...")
                for activo in ACTIVOS:
                    df = obtener_datos(iq_ref, activo)
                    if not df:
                        continue
                    resultado = get_reversal_signal(df)
                    if resultado:
                        dir_, fuerza, _ = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_señal = (activo, dir_, fuerza)

                if mejor_señal:
                    activo, dir_ori, fuerza = mejor_señal
                    dir_final = "put" if dir_ori == "call" else "call"
                    logger.info(f"✅ Señal lista: {activo} | {dir_final} | Fuerza: {fuerza}")

                    # Paso 2: Ejecutar en el rango correcto
                    if SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                        ok1 = ejecutar_orden(cuentas[0], activo, dir_final, vela_actual)
                        time.sleep(ESPERA_ENTRE_CUENTAS)
                        ok2 = ejecutar_orden(cuentas[1], activo, dir_final, vela_actual)

                        # Resultado final
                        if ok1 and ok2:
                            logger.info("✅✅ AMBAS CUENTAS EJECUTADAS CORRECTAMENTE")
                        elif ok1:
                            logger.warning("⚠️ Solo CUENTA 1 operó")
                        elif ok2:
                            logger.warning("⚠️ Solo CUENTA 2 operó")
                        else:
                            logger.error("❌ Ninguna cuenta ejecutó")

            # Mantener el bot activo
            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            time.sleep(2)
            cuentas = conectar_todas()

if __name__ == "__main__":
    iniciar_bot()
