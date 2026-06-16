import time
import os
import pandas as pd
import logging
import sys

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Falta instalar iqoptionapi")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Falta instalar python-telegram-bot")
    sys.exit(1)

from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN LOGS
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --------------------------
# PARÁMETROS
# --------------------------
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 75

SEG_INICIO_ENTRADA = 1
SEG_FIN_ENTRADA = 6

REINTENTOS = 5
ESPERA = 0.08
REINTENTOS_POR_CUENTA = 8
ESPERA_REINTENTO = 2

ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 20
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = True  # Lo activamos directamente
ULTIMA_VELA_CERRADA = None
SEÑAL_PENDIENTE = None
YA_OPERO = {}
IQ1 = None
IQ2 = None

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
        logger.warning("⚠️ Telegram sin configurar")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
        logger.info(f"📤 TELEGRAM: {texto[:70]}...")
    except Exception as e:
        logger.error(f"❌ Error Telegram: {str(e)}")

# --------------------------
# SALDO
# --------------------------
def obtener_saldo_actualizado(iq):
    for _ in range(3):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            saldo = iq.get_balance()
            if saldo and saldo >= 0:
                return round(saldo, 2)
            time.sleep(0.1)
        except:
            time.sleep(0.1)
    return 0.0

# --------------------------
# CONEXIÓN
# --------------------------
def conectar_cuenta(email, password, nombre):
    if not email or not password:
        logger.error(f"❌ {nombre}: Faltan credenciales")
        return None, 0.0
    for intento in range(REINTENTOS_POR_CUENTA):
        try:
            logger.info(f"🔄 {nombre} - Intento {intento+1}/{REINTENTOS_POR_CUENTA}")
            iq = IQ_Option(email, password)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE")
                saldo = obtener_saldo_actualizado(iq)
                logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"⚠️ {nombre} falló: {motivo}")
        except Exception as e:
            logger.error(f"❌ {nombre} error: {str(e)}")
        time.sleep(ESPERA_REINTENTO)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO CUENTAS...")
    IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None: IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None: IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    mensaje = (
        f"✅ BOT ACTIVO\n"
        f"🔹 Cuenta 1: ${saldo1}\n"
        f"🔹 Cuenta 2: ${saldo2}\n"
        f"⏱️ Analiza cada vela cerrada | Entrada 1-6 seg"
    )
    enviar_telegram(mensaje)
    logger.info(mensaje)
    return True

# --------------------------
# OBTENER VELAS
# --------------------------
def obtener_velas_cerradas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        ts = int(time.time()) - 2
        velas = iq.get_candles(activo, VELA, 50, ts)
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"⚠️ {activo}: {str(e)}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_id, res):
    clave = f"{nombre}_{vela_id}"
    if YA_OPERO.get(clave):
        res["ok"] = False
        res["razon"] = "Ya operó"
        return
    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            saldo = obtener_saldo_actualizado(iq)
            if saldo < MONTO:
                res["ok"] = False
                res["razon"] = "Saldo insuficiente"
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = obtener_saldo_actualizado(iq)
                YA_OPERO[clave] = True
                res.update({"ok":True, "id":id_op, "saldo":saldo_final})
                logger.info(f"✅ {nombre} | {activo} | {direccion} | ID: {id_op}")
                return
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {intento+1}: {e}")
            time.sleep(ESPERA)
    res["ok"] = False
    res["razon"] = "No ejecutado"

# --------------------------
# BUCLE PRINCIPAL (CORREGIDO SIN REINICIOS)
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA_CERRADA, SEÑAL_PENDIENTE, OPERACIONES_C1, OPERACIONES_C2
    logger.info("🔁 INICIANDO ANÁLISIS VELA A VELA...")

    while BOT_ACTIVO:
        try:
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Reconectando...")
                conectar_ambas()
                time.sleep(1)
                continue

            ts = IQ1.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            # Límite de operaciones
            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                s1 = obtener_saldo_actualizado(IQ1)
                s2 = obtener_saldo_actualizado(IQ2)
                enviar_telegram(f"✅ FIN\n1: {OPERACIONES_C1}/{MAX_OPER} | ${s1}\n2: {OPERACIONES_C2}/{MAX_OPER} | ${s2}")
                BOT_ACTIVO = False
                break

            # Analizar cada vela nueva cerrada
            if vela_cerrada != ULTIMA_VELA_CERRADA:
                ULTIMA_VELA_CERRADA = vela_cerrada
                SEÑAL_PENDIENTE = None
                YA_OPERO.clear()
                logger.info(f"🔍 ANALIZANDO VELA CERRADA: {vela_cerrada}")

                mejor = None
                fuerza_max = 0
                for activo in ACTIVOS:
                    df = obtener_velas_cerradas(IQ1, activo)
                    if not df:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, _ = senal
                        logger.info(f"ℹ️ {activo}: {dirr.upper()} | Fuerza: {fuerza}%")
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor = (activo, dirr, fuerza)

                if mejor:
                    SEÑAL_PENDIENTE = mejor
                    enviar_telegram(
                        f"📊 SEÑAL DETECTADA\n"
                        f"📈 {mejor[0]}\n➡️ {mejor[1].upper()}\n💪 {mejor[2]}%\n⏱️ Entrada 1-6 seg"
                    )
                else:
                    logger.info("ℹ️ Sin señal válida esta vela")

            # Ejecutar solo en el rango permitido
            if SEÑAL_PENDIENTE and SEG_INICIO_ENTRADA <= segundos <= SEG_FIN_ENTRADA:
                activo, dirr, fuerza = SEÑAL_PENDIENTE
                logger.info(f"⚡ ENTRADA | Segundo {segundos} | {activo}")
                enviar_telegram(f"⚡ EJECUTANDO: {activo} | {dirr.upper()}")

                res1 = {"ok":False}
                res2 = {"ok":False}
                ejecutar_orden(IQ1, "CUENTA 1", activo, dirr, vela_actual, res1)
                ejecutar_orden(IQ2, "CUENTA 2", activo, dirr, vela_actual, res2)

                if res1["ok"] or res2["ok"]:
                    if res1["ok"]: OPERACIONES_C1 += 1
                    if res2["ok"]: OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EJECUTADA\n"
                        f"🔹 1: {'✅ ID '+str(res1['id'])+' $'+str(res1['saldo']) if res1['ok'] else '❌'}\n"
                        f"🔹 2: {'✅ ID '+str(res2['id'])+' $'+str(res2['saldo']) if res2['ok'] else '❌'}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    enviar_telegram(f"❌ No se ejecutó: {res1.get('razon','')} | {res2.get('razon','')}")

                SEÑAL_PENDIENTE = None

            # Espera corta para no saturar
            time.sleep(0.3)

        except Exception as e:
            logger.error(f"💥 Error: {e}")
            enviar_telegram(f"⚠️ Error: {e}")
            time.sleep(2)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 BOT INICIANDO...")
    try:
        if TELEGRAM_TOKEN:
            Bot(token=TELEGRAM_TOKEN).delete_webhook(drop_pending_updates=True)
    except:
        pass
    if conectar_ambas():
        bucle_principal()
