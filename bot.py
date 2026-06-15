import time
import os
import pandas as pd
import logging
from threading import Thread
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

# PARÁMETROS
MONTO_POR_OPERACION = 600
EXPIRACION = 1
TIEMPO_VELA = 60
FUERZA_MINIMA = 98
REINTENTOS_MAX = 8
ESPERA_REINTENTO = 0.1
SEGUNDO_DETECCION = 55
SEGUNDO_INICIO = 56
SEGUNDO_FIN = 59

ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC"]

# Control
ULTIMA_VELA_PROCESADA = None
ULTIMA_OPERACION = {"CUENTA_1": None, "CUENTA_2": None}

# --------------------------
# CONEXIÓN TOTALMENTE INDEPENDIENTE
# --------------------------
def conectar_cuenta(email, password, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, password)
        conectado, motivo = iq.connect()
        if conectado:
            time.sleep(0.8)
            iq.change_balance("PRACTICE")
            time.sleep(0.8)
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} | Correo: {email} | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} no conectó: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def desconectar_todas(cuentas):
    for c in cuentas:
        try:
            c["conexion"].close_connect()
        except:
            pass

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
        time.sleep(1.2)
    if len(cuentas) != 2:
        logger.critical("⚠️ Se requieren 2 cuentas activas")
    return cuentas

# --------------------------
# OBTENER DATOS
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
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"⚠️ Error {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN EN HILO PROPIO
# --------------------------
def ejecutar_orden(cuenta, activo, direccion, vela_id, resultado):
    nombre = cuenta["nombre"]
    iq = cuenta["conexion"]
    if ULTIMA_OPERACION[nombre] == vela_id:
        resultado["ok"] = False
        resultado["saldo"] = None
        resultado["id"] = None
        return

    for intento in range(REINTENTOS_MAX):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            activos = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos:
                time.sleep(0.2)
                continue
            estado, id_op = iq.buy(MONTO_POR_OPERACION, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                ULTIMA_OPERACION[nombre] = vela_id
                time.sleep(0.3)
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} | Ejecutado | ID: {id_op} | Saldo: ${saldo}")
                resultado["ok"] = True
                resultado["saldo"] = saldo
                resultado["id"] = id_op
                return
            time.sleep(ESPERA_REINTENTO)
        except Exception as e:
            time.sleep(ESPERA_REINTENTO)

    logger.error(f"❌ {nombre} | No se ejecutó")
    resultado["ok"] = False
    resultado["saldo"] = None
    resultado["id"] = None

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bot():
    global ULTIMA_VELA_PROCESADA, ULTIMA_OPERACION
    CUENTAS = conectar_cuentas()
    if len(CUENTAS) != 2:
        return

    logger.info("="*70)
    logger.info("🤖 BOT | 2 CUENTAS INDEPENDIENTES | MISMA ORDEN | BLOQUEO SI FALTA")
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
                    logger.info(f"✅ Señal: {activo} | {dir_final} | Fuerza: {fuerza}")

            # Ejecutar al mismo tiempo en ambas
            if senal_guardada and SEGUNDO_INICIO <= segundos <= SEGUNDO_FIN:
                activo, dir_final, fuerza = senal_guardada
                logger.info(f"🚀 ENVIANDO ORDEN SIMULTÁNEA: {activo} | {dir_final}")

                res1 = {"ok": False, "saldo": None, "id": None}
                res2 = {"ok": False, "saldo": None, "id": None}

                # Hilos separados para enviar al mismo tiempo
                t1 = Thread(target=ejecutar_orden, args=(CUENTAS[0], activo, dir_final, vela_actual, res1))
                t2 = Thread(target=ejecutar_orden, args=(CUENTAS[1], activo, dir_final, vela_actual, res2))
                t1.start()
                t2.start()
                t1.join()
                t2.join()

                # Verificación estricta
                if res1["ok"] and res2["ok"]:
                    logger.info("="*70)
                    logger.info("✅✅ AMBAS CUENTAS RECIBIERON LA ORDEN CORRECTAMENTE")
                    logger.info(f"💵 CUENTA 1: ${res1['saldo']} | CUENTA 2: ${res2['saldo']}")
                    logger.info("="*70)
                elif res1["ok"]:
                    logger.critical("⛔ BLOQUEADO: SOLO CUENTA 1 OPERÓ - SE IGNORA")
                elif res2["ok"]:
                    logger.critical("⛔ BLOQUEADO: SOLO CUENTA 2 OPERÓ - SE IGNORA")
                else:
                    logger.critical("⛔ BLOQUEADO: NINGUNA OPERÓ")

                senal_guardada = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error: {str(e)}")
            desconectar_todas(CUENTAS)
            time.sleep(2)
            CUENTAS = conectar_cuentas()

if __name__ == "__main__":
    iniciar_bot()
