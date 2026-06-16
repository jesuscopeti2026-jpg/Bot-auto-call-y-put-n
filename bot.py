import time
import os
import pandas as pd
import logging
import sys
import threading

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
# CONFIGURACIÓN
# --------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

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

BOT_ACTIVO = True
ULTIMA_VELA_CERRADA = None
SEÑAL_PENDIENTE = None
YA_OPERO = {}
IQ1 = None
IQ2 = None

# ✅ LEE VARIABLES DE RAILWAY
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# NOTIFICACIONES
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram sin configurar")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
        logger.info(f"📤 Telegram: {texto[:70]}...")
    except Exception as e:
        logger.error(f"❌ Telegram: {e}")

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
            if saldo and isinstance(saldo, (int, float)) and saldo >= 0:
                return round(saldo, 2)
            time.sleep(0.1)
        except:
            time.sleep(0.1)
    return 0.0

# --------------------------
# CONEXIÓN CUENTAS
# --------------------------
def conectar_cuenta(email, password, nombre):
    if not email or not password:
        logger.error(f"❌ {nombre}: Credenciales faltantes en variables")
        return None, 0.0
    for intento in range(REINTENTOS_POR_CUENTA):
        try:
            logger.info(f"🔄 {nombre} - Intento {intento+1}/{REINTENTOS_POR_CUENTA}")
            iq = IQ_Option(email, password)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE") # Cambia a "REAL" si usas cuenta real
                saldo = obtener_saldo_actualizado(iq)
                logger.info(f"✅ {nombre} conectada | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"⚠️ {nombre}: {motivo}")
        except Exception as e:
            logger.error(f"❌ {nombre}: {e}")
        time.sleep(ESPERA_REINTENTO)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    while IQ1 is None or IQ2 is None:
        time.sleep(3)
        if IQ1 is None: IQ1, saldo1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None: IQ2, saldo2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    mensaje = (
        f"✅ BOT LISTO\n"
        f"🔹 Cuenta 1: ${saldo1}\n"
        f"🔹 Cuenta 2: ${saldo2}\n"
        f"⏱️ Entrada: seg {SEG_INICIO_ENTRADA}-{SEG_FIN_ENTRADA}\n"
        f"💪 Fuerza mínima: {FUERZA_MIN}%"
    )
    enviar_telegram(mensaje)
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
        logger.warning(f"⚠️ {activo}: {e}")
        return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_id, resultado):
    clave = f"{nombre}_{vela_id}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó"
        return

    exito = False
    id_op = None
    saldo_final = 0

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            saldo = obtener_saldo_actualizado(iq)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["razon"] = f"Saldo insuficiente ${saldo}"
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = obtener_saldo_actualizado(iq)
                YA_OPERO[clave] = True
                exito = True
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {intento+1}: {e}")
            time.sleep(ESPERA)

    if exito:
        resultado.update({"ok": True, "id": id_op, "saldo": saldo_final})
    else:
        resultado["ok"] = False
        resultado["razon"] = f"No ejecutado en {REINTENTOS} intentos"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA_CERRADA, SEÑAL_PENDIENTE, OPERACIONES_C1, OPERACIONES_C2
    logger.info("🔁 ANÁLISIS VELA A VELA ACTIVADO")

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

            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                s1 = obtener_saldo_actualizado(IQ1)
                s2 = obtener_saldo_actualizado(IQ2)
                enviar_telegram(f"✅ SESION FINALIZADA\n1: {OPERACIONES_C1}/{MAX_OPER} | ${s1}\n2: {OPERACIONES_C2}/{MAX_OPER} | ${s2}")
                BOT_ACTIVO = False
                break

            # Analizar vela nueva
            if vela_cerrada != ULTIMA_VELA_CERRADA:
                ULTIMA_VELA_CERRADA = vela_cerrada
                SEÑAL_PENDIENTE = None
                YA_OPERO.clear()
                logger.info(f"🔍 VELA CERRADA: {vela_cerrada}")

                mejor = None
                fuerza_max = 0
                for activo in ACTIVOS:
                    df = obtener_velas_cerradas(IQ1, activo)
                    if df is None or df.empty:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, _ = senal
                        if fuerza >= FUERZA_MIN and fuerza > fuerza_max:
                            fuerza_max = fuerza
                            mejor = (activo, dirr, fuerza)

                if mejor:
                    SEÑAL_PENDIENTE = mejor
                    enviar_telegram(f"📊 SEÑAL\n{mejor[0]} | {mejor[1].upper()} | {mejor[2]}%")

            # ✅ EJECUCIÓN EN PARALELO (AL MISMO TIEMPO)
            if SEÑAL_PENDIENTE and SEG_INICIO_ENTRADA <= segundos <= SEG_FIN_ENTRADA:
                activo, direccion, fuerza = SEÑAL_PENDIENTE
                enviar_telegram(f"⚡ EJECUTANDO EN AMBAS CUENTAS: {activo} | {direccion.upper()}")

                res1 = {"ok": False}
                res2 = {"ok": False}

                # Lanzar ambas órdenes en hilos separados
                hilo1 = threading.Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1))
                hilo2 = threading.Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2))

                hilo1.start()
                hilo2.start()

                # Esperar a que ambas terminen
                hilo1.join()
                hilo2.join()

                # Actualizar contadores
                if res1["ok"]: OPERACIONES_C1 += 1
                if res2["ok"]: OPERACIONES_C2 += 1

                # Enviar resumen
                enviar_telegram(
                    f"✅ OPERACIÓN FINALIZADA\n"
                    f"📌 {activo} | {direccion.upper()}\n"
                    f"🔹 Cuenta 1: {'✅ ID '+str(res1['id'])+' $'+str(res1['saldo']) if res1['ok'] else '❌ '+res1.get('razon','')}\n"
                    f"🔹 Cuenta 2: {'✅ ID '+str(res2['id'])+' $'+str(res2['saldo']) if res2['ok'] else '❌ '+res2.get('razon','')}\n"
                    f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                )

                SEÑAL_PENDIENTE = None

            time.sleep(0.2)

        except Exception as e:
            logger.error(f"💥 Error: {e}")
            enviar_telegram(f"⚠️ {e}")
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
