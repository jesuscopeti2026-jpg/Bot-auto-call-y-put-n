import time
import os
import pandas as pd
import logging
import asyncio
from threading import Thread
from iqoptionapi.stable_api import IQ_Option
from telegram import Bot
from telegram.ext import Updater, CommandHandler
from telegram.error import TelegramError
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

# Parámetros de operación
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 98
REINTENTOS = 8
ESPERA = 0.2
SEG_DETECCION = 54
SEG_INICIO = 56
SEG_FIN = 59

# Activos válidos en IQ Option
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

# Límite de operaciones
MAX_OPER_C1 = 15
MAX_OPER_C2 = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

# Control de estado
BOT_ACTIVO = False
ULTIMA_VELA = None
OP_VELA_C1 = None
OP_VELA_C2 = None
CUENTA_ANALISIS = 1

# Configuración Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")  # ID de tu grupo

# --------------------------
# CONEXIÓN CUENTAS IQ OPTION
# --------------------------
def conectar(email, clave, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, clave)
        ok, motivo = iq.connect()
        if ok:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            mensaje = f"✅ {nombre} conectado | Saldo: ${saldo}"
            logger.info(mensaje)
            enviar_mensaje_telegram(mensaje)
            return iq, saldo
        else:
            mensaje = f"❌ Error en {nombre}: {motivo}"
            logger.error(mensaje)
            enviar_mensaje_telegram(mensaje)
            return None, 0
    except Exception as e:
        mensaje = f"❌ Fallo en conexión {nombre}: {str(e)}"
        logger.error(mensaje)
        enviar_mensaje_telegram(mensaje)
        return None, 0

def conectar_ambas():
    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    return iq1, iq2

# --------------------------
# FUNCIONES TELEGRAM
# --------------------------
def enviar_mensaje_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=texto, parse_mode="HTML")
    except TelegramError as e:
        logger.warning(f"⚠️ No se pudo enviar mensaje a Telegram: {e}")

def cmd_start(update, context):
    global BOT_ACTIVO, OPERACIONES_C1, OPERACIONES_C2
    if BOT_ACTIVO:
        update.message.reply_text("ℹ️ El bot ya está en ejecución")
        return
    OPERACIONES_C1 = 0
    OPERACIONES_C2 = 0
    BOT_ACTIVO = True
    Thread(target=bucle_principal, daemon=True).start()
    update.message.reply_text("✅ Bot INICIADO. Realizará 15 operaciones en cada cuenta.")

def cmd_stop(update, context):
    global BOT_ACTIVO
    BOT_ACTIVO = False
    update.message.reply_text("⏹️ Bot DETENIDO.")

def iniciar_comandos_telegram():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Sin credenciales de Telegram, funcionará sin control remoto")
        return
    try:
        updater = Updater(token=TELEGRAM_TOKEN, use_context=True)
        dp = updater.dispatcher
        dp.add_handler(CommandHandler("start", cmd_start))
        dp.add_handler(CommandHandler("stop", cmd_stop))
        updater.start_polling(drop_pending_updates=True)  # ✅ Evita conflictos
        logger.info("📡 Conectado a Telegram sin conflictos")
        enviar_mensaje_telegram("🤖 Bot listo. Usa /start para operar y /stop para detener.")
        updater.idle()
    except Exception as e:
        logger.error(f"❌ Error al iniciar Telegram: {e}")

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq.check_connect():
            iq.connect()
            time.sleep(0.2)
        datos = iq.get_candles(activo, VELA, 50, time.time())
        if not datos or len(datos) < 30:
            return None
        df = pd.DataFrame(datos)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.error(f"⚠️ {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_actual, resultado):
    global OP_VELA_C1, OP_VELA_C2, OPERACIONES_C1, OPERACIONES_C2

    if nombre == "CUENTA_1":
        if OP_VELA_C1 == vela_actual or OPERACIONES_C1 >= MAX_OPER_C1:
            resultado["ok"] = False
            return
    else:
        if OP_VELA_C2 == vela_actual or OPERACIONES_C2 >= MAX_OPER_C2:
            resultado["ok"] = False
            return

    logger.info(f"📤 Enviando a {nombre}: {activo} {direccion} ${MONTO}")
    exito = False
    saldo_final = id_operacion = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            disponibles = iq.get_all_ACTIVES_OPCODE()
            if activo not in disponibles:
                logger.warning(f"⚠️ {activo} no disponible")
                time.sleep(0.3)
                continue
            estado, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo_final = round(iq.get_balance(), 2)
                mensaje = f"✅ {nombre} | Operación: {activo} {direccion} | ID: {id_op} | Saldo: ${saldo_final}"
                logger.info(mensaje)
                enviar_mensaje_telegram(mensaje)
                exito = True
                id_operacion = id_op
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {intento+1}: {e}")
            time.sleep(ESPERA)

    if exito:
        if nombre == "CUENTA_1":
            OP_VELA_C1 = vela_actual
            OPERACIONES_C1 += 1
            enviar_mensaje_telegram(f"📊 CUENTA 1: {OPERACIONES_C1}/15 operaciones completadas")
        else:
            OP_VELA_C2 = vela_actual
            OPERACIONES_C2 += 1
            enviar_mensaje_telegram(f"📊 CUENTA 2: {OPERACIONES_C2}/15 operaciones completadas")
        resultado.update({"ok": True, "id": id_operacion, "saldo": saldo_final})
    else:
        resultado["ok"] = False
        enviar_mensaje_telegram(f"❌ No se pudo ejecutar orden en {nombre} para {activo}")

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OP_VELA_C1, OP_VELA_C2, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS
    iq1, iq2 = conectar_ambas()
    if not iq1 or not iq2:
        enviar_mensaje_telegram("❌ No se pudieron conectar ambas cuentas, deteniendo operación")
        BOT_ACTIVO = False
        return

    enviar_mensaje_telegram("🤖 BOT ACTIVO | 15 operaciones por cuenta | Analizando señales...")
    senal_actual = None

    while BOT_ACTIVO:
        try:
            if OPERACIONES_C1 >= MAX_OPER_C1 and OPERACIONES_C2 >= MAX_OPER_C2:
                mensaje = "✅ ¡OBJETIVO CUMPLIDO! 15 operaciones en cada cuenta. Bot detenido automáticamente."
                logger.info(mensaje)
                enviar_mensaje_telegram(mensaje)
                BOT_ACTIVO = False
                break

            ts = iq1.get_server_timestamp()
            segundos = int(ts % 60)
            vela_actual = int(ts // 60)

            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                OP_VELA_C1 = OP_VELA_C2 = None
                senal_actual = None
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1

            if segundos == SEG_DETECCION:
                mejor_senal = None
                mayor_fuerza = 0
                logger.info(f"🔍 Analizando con CUENTA_{CUENTA_ANALISIS}...")
                iq_analisis = iq1 if CUENTA_ANALISIS == 1 else iq2

                for activo in ACTIVOS:
                    df = obtener_velas(iq_analisis, activo)
                    if df is None or df.empty:
                        continue
                    res = get_reversal_signal(df)
                    if res:
                        dir_ori, fuerza, _ = res
                        if fuerza >= FUERZA_MIN and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_senal = (activo, dir_ori, fuerza)

                if mejor_senal:
                    activo, dir_ori, fuerza = mejor_senal
                    dir_final = "put" if dir_ori == "call" else "call"
                    senal_actual = (activo, dir_final, fuerza)
                    mensaje = f"🔔 NUEVA SEÑAL: {activo} | {dir_final.upper()} | Fuerza: {fuerza}%"
                    logger.info(mensaje)
                    enviar_mensaje_telegram(mensaje)

            if senal_actual and SEG_INICIO <= segundos <= SEG_FIN:
                activo, dir_final, fuerza = senal_actual
                logger.info("🚀 Enviando operaciones a ambas cuentas...")

                res1 = {"ok": False}
                res2 = {"ok": False}

                if OPERACIONES_C1 < MAX_OPER_C1:
                    h1 = Thread(target=ejecutar_orden, args=(iq1, "CUENTA_1", activo, dir_final, vela_actual, res1))
                    h1.start()
                    h1.join()

                if OPERACIONES_C2 < MAX_OPER_C2:
                    h2 = Thread(target=ejecutar_orden, args=(iq2, "CUENTA_2", activo, dir_final, vela_actual, res2))
                    h2.start()
                    h2.join()

                senal_actual = None

            time.sleep(0.05)

        except Exception as e:
            mensaje = f"💥 Error en el bucle: {str(e)} | Reintentando conexión..."
            logger.error(mensaje)
            enviar_mensaje_telegram(mensaje)
            iq1, iq2 = conectar_ambas()
            time.sleep(3)

# --------------------------
# EJECUCIÓN PRINCIPAL
# --------------------------
if __name__ == "__main__":
    iniciar_comandos_telegram()
