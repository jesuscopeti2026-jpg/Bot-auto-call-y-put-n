import time
import os
import pandas as pd
import logging
from threading import Thread
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN GENERAL
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# --------------------------
# PARÁMETROS DE OPERACIÓN
# --------------------------
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 10
ESPERA_REINTENTO = 0.15
SEGUNDO_DETECCION = 54
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control independiente por cuenta
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION_C1 = None
ULTIMA_OPERACION_C2 = None

# --------------------------
# CONEXIÓN 100% SEPARADA
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()
        if conectado:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            time.sleep(1)
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} | Correo: {email} | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_ambas_cuentas():
    iq1, _ = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    time.sleep(2)
    return (iq1, iq2) if (iq1 and iq2) else (None, None)

# --------------------------
# OBTENER DATOS DE MERCADO
# --------------------------
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
        logger.error(f"⚠️ Error al obtener datos de {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN EN UNA CUENTA
# --------------------------
def ejecutar_orden_en_cuenta(iq, nombre_cuenta, activo, direccion, vela_id, resultado):
    global ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2

    if (nombre_cuenta == "CUENTA_1" and ULTIMA_OPERACION_C1 == vela_id) or (nombre_cuenta == "CUENTA_2" and ULTIMA_OPERACION_C2 == vela_id):
        resultado["ok"] = False
        return

    logger.info(f"📤 Enviando orden a {nombre_cuenta}: {activo} | {direccion} | ${MONTO_POR_OPERACION}")
    exito = False
    id_operacion = None
    saldo_final = None

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)

            activos_disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos_disponibles:
                logger.warning(f"⚠️ {nombre_cuenta}: {activo} no disponible, reintentando...")
                time.sleep(0.3)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo_final = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre_cuenta} | Ejecutada | ID: {id_op} | Saldo: ${saldo_final}")
                exito = True
                id_operacion = id_op
                break

            time.sleep(ESPERA_REINTENTO)
        except Exception as e:
            logger.warning(f"⚠️ {nombre_cuenta} | Intento {intento+1}: {str(e)}")
            time.sleep(ESPERA_REINTENTO)

    if exito:
        if nombre_cuenta == "CUENTA_1":
            ULTIMA_OPERACION_C1 = vela_id
        else:
            ULTIMA_OPERACION_C2 = vela_id
        resultado.update({"ok": True, "id": id_operacion, "saldo": saldo_final})
    else:
        logger.error(f"❌ {nombre_cuenta} | No se pudo ejecutar la orden")
        resultado["ok"] = False

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION_C1, ULTIMA_OPERACION_C2
    iq1, iq2 = conectar_ambas_cuentas()
    if not iq1 or not iq2:
        logger.critical("❌ No se pudieron conectar las 2 cuentas, revisa credenciales")
        return

    logger.info("="*70)
    logger.info("🤖 BOT ACTIVO | MISMA ORDEN DUPLICADA PARA AMBAS CUENTAS")
    logger.info(f"⚙️ Fuerza mínima: {FUERZA_MINIMA} | Entrada: {SEGUNDO_INICIO}-{SEGUNDO_FIN}s")
    logger.info("="*70)

    senal_guardada = None

    while True:
        try:
            tiempo_servidor = iq1.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_VELA_PROCESADA = vela_actual
                ULTIMA_OPERACION_C1 = None
                ULTIMA_OPERACION_C2 = None
                senal_guardada = None

            # Detectar señal una sola vez
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                logger.info("🔍 Buscando señales...")
                for activo in ACTIVOS:
                    df = obtener_datos(iq1, activo)
                    if df is None:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_ori, fuerza, _ = res
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor = (activo, dir_ori, fuerza)
                if mejor:
                    activo, dir_ori, fuerza = mejor
                    dir_final = "put" if dir_ori == "call" else "call"
                    senal_guardada = (activo, dir_final, fuerza)
                    logger.info(f"✅ Señal lista: {activo} | {dir_final} | Fuerza: {fuerza}")

            # Enviar la misma orden a las dos cuentas
            if senal_guardada and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, dir_final, fuerza = senal_guardada
                logger.info("🚀 ENVIANDO LA MISMA ORDEN A AMBAS CUENTAS")

                res1 = {"ok": False, "id": None, "saldo": None}
                res2 = {"ok": False, "id": None, "saldo": None}

                t1 = Thread(target=ejecutar_orden_en_cuenta, args=(iq1, "CUENTA_1", activo, dir_final, vela_actual, res1))
                t2 = Thread(target=ejecutar_orden_en_cuenta, args=(iq2, "CUENTA_2", activo, dir_final, vela_actual, res2))

                t1.start()
                t2.start()
                t1.join()
                t2.join()

                if res1["ok"] and res2["ok"]:
                    logger.info("="*70)
                    logger.info("✅✅ ÉXITO: AMBAS CUENTAS RECIBIERON LA MISMA ORDEN")
                    logger.info(f"💵 CUENTA 1: ${res1['saldo']} | CUENTA 2: ${res2['saldo']}")
                    logger.info("="*70)
                elif res1["ok"]:
                    logger.warning("⚠️ Solo operó CUENTA 1 - reconectando CUENTA 2")
                    iq2, _ = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
                elif res2["ok"]:
                    logger.warning("⚠️ Solo operó CUENTA 2 - reconectando CUENTA 1")
                    iq1, _ = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
                else:
                    logger.error("❌ No se ejecutó en ninguna cuenta")

                senal_guardada = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error en el bucle: {str(e)}")
            iq1, iq2 = conectar_ambas_cuentas()
            time.sleep(3)

if __name__ == "__main__":
    iniciar_bot()
