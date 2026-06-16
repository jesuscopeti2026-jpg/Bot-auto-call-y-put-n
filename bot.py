import time
import os
import pandas as pd
import logging
from threading import Thread, Lock
from iqoptionapi.stable_api import IQ_Option
from telegram import Bot
from telegram.error import TelegramError
from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN MEJORADA
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Parámetros ajustados para operar seguro
MONTO = 100                # Igual al saldo de práctica
EXPIRACION = 1             # Vencimiento en minutos
VELA = 60                  # Marco de tiempo en segundos
FUERZA_MIN = 70            # Fuerza más accesible, pero confiable
REINTENTOS = 5
ESPERA = 0.2
REINTENTOS_POR_CUENTA = 15
ESPERA_REINTENTO = 2

# Tiempos sincronizados con margen amplio
SEG_INICIO_BUSQUEDA = 50   # Empezar a buscar a los 50s
SEG_FIN_BUSQUEDA = 56      # Dejar de buscar a los 56s
SEG_INICIO_ENTRADA = 54    # Entrar desde el segundo 54
SEG_FIN_ENTRADA = 58       # Cerrar ventana de entrada

# Lista de activos con sufijo correcto
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = False
ULTIMA_VELA = None
YA_OPERO = {}
IQ1 = None
IQ2 = None
lock = Lock()

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Faltan credenciales de Telegram")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
        logger.info(f"📤 Notificación enviada: {texto[:60]}...")
    except Exception as e:
        logger.error(f"❌ Error Telegram: {str(e)}")

# --------------------------
# OBTENER SALDO REAL
# --------------------------
def obtener_saldo_actualizado(iq):
    for _ in range(5):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.3)
            saldo = iq.get_balance()
            if saldo is not None and isinstance(saldo, (int, float)) and saldo >= 0:
                return round(saldo, 2)
            time.sleep(0.2)
        except:
            time.sleep(0.2)
    return 0.0

# --------------------------
# CONEXIÓN DE CUENTAS
# --------------------------
def conectar_cuenta(email, password, nombre):
    for intento in range(REINTENTOS_POR_CUENTA):
        try:
            logger.info(f"🔄 {nombre} - Intento {intento+1}/{REINTENTOS_POR_CUENTA}")
            iq = IQ_Option(email, password)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.8)
                iq.change_balance("PRACTICE")
                time.sleep(0.5)
                saldo = obtener_saldo_actualizado(iq)
                logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"⚠️ {nombre} falló: {motivo}")
        except Exception as e:
            logger.error(f"❌ {nombre} error: {str(e)}")
        time.sleep(ESPERA_REINTENTO)
    logger.error(f"❌ {nombre} NO conectó después de {REINTENTOS_POR_CUENTA} intentos")
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")

    IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None:
            IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None:
            IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    enviar_telegram(
        f"✅ AMBAS CUENTAS CONECTADAS\n"
        f"🔹 Cuenta 1: ${saldo1}\n"
        f"🔹 Cuenta 2: ${saldo2}\n"
        f"🚀 INICIANDO ANÁLISIS CONTINUO..."
    )
    return True

# --------------------------
# OBTENER VELAS Y VALIDAR
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            logger.warning(f"⚠️ Sin conexión para {activo}")
            return None
        velas = iq.get_candles(activo, VELA, 50, time.time())
        if not velas or len(velas) < 30:
            logger.debug(f"ℹ️ {activo}: No hay suficientes velas")
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"⚠️ Error obteniendo velas de {activo}: {str(e)}")
        return None

# --------------------------
# EJECUTAR ORDEN EN AMBAS
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    clave = f"{nombre}_{vela}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó en esta vela"
        return

    dir_final = direccion
    exito = False
    id_op = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            saldo_actual = obtener_saldo_actualizado(iq)
            if saldo_actual < MONTO:
                resultado["ok"] = False
                resultado["razon"] = f"Saldo insuficiente: ${saldo_actual}"
                return
            ok, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if ok and id_op > 0:
                time.sleep(0.5)
                saldo_actual = obtener_saldo_actualizado(iq)
                exito = True
                logger.info(f"✅ {nombre} | {activo} | {dir_final.upper()} | ID: {id_op} | Saldo: ${saldo_actual}")
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ Intento {intento+1} {nombre}: {str(e)}")
            time.sleep(ESPERA)

    if exito:
        with lock:
            YA_OPERO[clave] = True
        resultado.update({
            "ok": True,
            "direccion": dir_final.upper(),
            "id": id_op,
            "saldo": saldo_actual
        })
    else:
        resultado["ok"] = False
        resultado["razon"] = "No se pudo ejecutar"

# --------------------------
# BUCLE PRINCIPAL CON REGISTROS DETALLADOS
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES_C1, OPERACIONES_C2, IQ1, IQ2

    logger.info("🔁 BUCLE DE OPERACIONES INICIADO")

    while BOT_ACTIVO:
        try:
            # Verificar conexión
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Conexión perdida, reconectando...")
                if not conectar_ambas():
                    BOT_ACTIVO = False
                    break
                continue

            ts_servidor = IQ1.get_server_timestamp()
            segundos = ts_servidor % 60
            vela_actual = ts_servidor // 60

            # Fin de operaciones
            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                saldo1_final = obtener_saldo_actualizado(IQ1)
                saldo2_final = obtener_saldo_actualizado(IQ2)
                enviar_telegram(
                    f"✅ PROCESO FINALIZADO ✅\n"
                    f"🔹 Cuenta 1: {OPERACIONES_C1}/{MAX_OPER} | Saldo: ${saldo1_final}\n"
                    f"🔹 Cuenta 2: {OPERACIONES_C2}/{MAX_OPER} | Saldo: ${saldo2_final}"
                )
                BOT_ACTIVO = False
                break

            # Nueva vela
            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                YA_OPERO.clear()
                logger.info(f"🔄 Nueva vela: {vela_actual} | Hora servidor: {segundos}s")

            # Buscar señales
            senal = None
            if SEG_INICIO_BUSQUEDA <= segundos <= SEG_FIN_BUSQUEDA:
                logger.info(f"🔍 Buscando señales entre {SEG_INICIO_BUSQUEDA}s y {SEG_FIN_BUSQUEDA}s...")
                mejor_senal = None
                fuerza_max = 0

                for act in ACTIVOS:
                    df = obtener_velas(IQ1, act)
                    if df is None:
                        continue
                    resultado_senal = get_reversal_signal(df)
                    if resultado_senal:
                        direccion, fuerza, tipo = resultado_senal
                        logger.info(f"ℹ️ {act}: {direccion.upper()} | Fuerza: {fuerza}%")
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor_senal = (act, direccion, fuerza)

                if mejor_senal:
                    act, direccion, fuerza = mejor_senal
                    senal = (act, direccion, fuerza)
                    mensaje = f"🔔 SEÑAL CONFIRMADA\n📈 Activo: {act}\n➡️ Dirección: {direccion.upper()}\n💪 Fuerza: {fuerza}%\n⏱️ Entrando en breve..."
                    logger.info(mensaje)
                    enviar_telegram(mensaje)

            # Ejecutar operación
            if senal and SEG_INICIO_ENTRADA <= segundos <= SEG_FIN_ENTRADA:
                act, direccion, fuerza = senal
                logger.info(f"⚡ EJECUTANDO ENTRADA | {act} | {direccion.upper()} | {segundos}s")
                enviar_telegram("⚡ ENVIANDO LA MISMA OPERACIÓN A AMBAS CUENTAS...")

                res1 = {"ok": False}
                res2 = {"ok": False}

                hilo1 = Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", act, direccion, vela_actual, res1))
                hilo2 = Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", act, direccion, vela_actual, res2))
                hilo1.start()
                hilo2.start()
                hilo1.join()
                hilo2.join()

                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EXITOSA EN AMBAS CUENTAS\n"
                        f"🔹 Cuenta 1: {res1['direccion']} | ID: {res1['id']} | Saldo: ${res1['saldo']}\n"
                        f"🔹 Cuenta 2: {res2['direccion']} | ID: {res2['id']} | Saldo: ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    motivo = []
                    if not res1["ok"]:
                        motivo.append(f"Cuenta 1: {res1.get('razon', 'Error')}")
                    if not res2["ok"]:
                        motivo.append(f"Cuenta 2: {res2.get('razon', 'Error')}")
                    enviar_telegram(f"❌ OPERACIÓN FALLIDA\n{ ' | '.join(motivo) }")

                senal = None

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            enviar_telegram(f"⚠️ Error en el ciclo: {str(e)}")
            time.sleep(2)

# --------------------------
# INICIO
# --------------------------
if __name__ == "__main__":
    try:
        if TELEGRAM_TOKEN:
            Bot(token=TELEGRAM_TOKEN).delete_webhook(drop_pending_updates=True)
    except:
        pass

    enviar_telegram("🤖 BOT INICIADO ✅")

    if conectar_ambas():
        BOT_ACTIVO = True
        Thread(target=bucle_principal, daemon=True).start()
    else:
        enviar_telegram("❌ No se pudo conectar. Revisa credenciales.")
