import time
import os
import pandas as pd
import logging
from threading import Thread, Lock
from iqoptionapi.stable_api import IQ_Option

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Parámetros
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 10
ESPERA_REINTENTO = 0.2
SEGUNDO_DETECCION = 54
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}
lock = Lock()

# --------------------------
# CONEXIÓN 100% AISLADA
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        # Forzamos instancia totalmente nueva
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

def conectar_cuentas():
    cuentas = []
    credenciales = [
        ("CUENTA_1", os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1")),
        ("CUENTA_2", os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"))
    ]
    for nombre, correo, clave in credenciales:
        if not correo or not clave:
            logger.error(f"{nombre}: Sin credenciales")
            continue
        iq, saldo = conectar_cuenta(correo, clave, nombre)
        if iq and saldo >= MONTO_POR_OPERACION:
            cuentas.append({"nombre": nombre, "conexion": iq, "saldo": saldo})
        # Espera mayor para evitar mezcla de sesiones
        time.sleep(2)
    if len(cuentas) != 2:
        logger.critical("⚠️ No se pudieron conectar las 2 cuentas")
    return cuentas

# --------------------------
# OBTENER DATOS
# --------------------------
def obtener_datos(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.3)
        velas = iq.get_candles(activo, TIEMPO_VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"⚠️ Error {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN SIN INTERFERENCIAS
# --------------------------
def ejecutar_orden(cuenta, activo, direccion, vela_id, resultado):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]

    with lock:
        if ULTIMA_OPERACION[nombre] == vela_id:
            resultado["ok"] = False
            return

    exito = False
    id_op = None
    saldo_final = None

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)

            activos = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos:
                time.sleep(0.3)
                continue

            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.5)
                saldo_final = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} | Ejecutado | ID: {id_op} | Saldo: ${saldo_final}")
                exito = True
                break

            time.sleep(ESPERA_REINTENTO)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} Intento {intento+1}: {e}")
            time.sleep(ESPERA_REINTENTO)

    if exito:
        with lock:
            ULTIMA_OPERACION[nombre] = vela_id
        resultado["ok"] = True
        resultado["id"] = id_op
        resultado["saldo"] = saldo_final
    else:
        logger.error(f"❌ {nombre} | No se pudo ejecutar la orden")
        resultado["ok"] = False

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION
    CUENTAS = conectar_cuentas()
    if len(CUENTAS) != 2:
        return

    logger.info("="*70)
    logger.info("🤖 BOT | 2 CUENTAS INDEPENDIENTES | MISMA ORDEN EN AMBAS")
    logger.info(f"⚙️ Fuerza ≥ {FUERZA_MINIMA} | Entrada: {SEGUNDO_INICIO}-{SEGUNDO_FIN}s")
    logger.info("="*70)

    senal_guardada = None

    while True:
        try:
            iq_ref = CUENTAS[0]["conexion"]
            tiempo_servidor = iq_ref.get_server_timestamp()
            segundos = int(tiempo_servidor % 60)
            vela_actual = int(tiempo_servidor // 60)

            if vela_actual != ULTIMA_VELA_PROCESADA:
                ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}
                ULTIMA_VELA_PROCESADA = vela_actual
                senal_guardada = None

            # Detectar señal
            if segundos == SEGUNDO_DETECCION:
                mejor = None
                mayor_fuerza = 0
                logger.info("🔍 Buscando señales...")
                for activo in ACTIVOS:
                    df = obtener_datos(iq_ref, activo)
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

            # Ejecutar en ambas al mismo tiempo
            if senal_guardada and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, dir_final, fuerza = senal_guardada
                logger.info(f"🚀 ENVIANDO A AMBAS: {activo} | {dir_final}")

                res1 = {"ok": False, "id": None, "saldo": None}
                res2 = {"ok": False, "id": None, "saldo": None}

                # Hilos separados para cada cuenta
                t1 = Thread(target=ejecutar_orden, args=(CUENTAS[0], activo, dir_final, vela_actual, res1))
                t2 = Thread(target=ejecutar_orden, args=(CUENTAS[1], activo, dir_final, vela_actual, res2))

                t1.start()
                t2.start()
                t1.join()
                t2.join()

                # Verificación final
                if res1["ok"] and res2["ok"]:
                    logger.info("="*70)
                    logger.info("✅✅ ÉXITO: AMBAS CUENTAS OPERARON CORRECTAMENTE")
                    logger.info(f"💵 CUENTA 1: ${res1['saldo']} | CUENTA 2: ${res2['saldo']}")
                    logger.info("="*70)
                elif res1["ok"]:
                    logger.warning("⚠️ Solo operó CUENTA 1 - revisando conexión de la 2")
                    # Reconectar cuenta 2 si falló
                    CUENTAS[1], _ = conectar_cuenta(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
                elif res2["ok"]:
                    logger.warning("⚠️ Solo operó CUENTA 2 - revisando conexión de la 1")
                    CUENTAS[0], _ = conectar_cuenta(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
                else:
                    logger.error("❌ Ninguna cuenta operó")

                senal_guardada = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error: {str(e)}")
            CUENTAS = conectar_cuentas()
            time.sleep(3)

if __name__ == "__main__":
    iniciar_bot()
