import time
import os
import pandas as pd
import logging
import asyncio
from threading import Thread
from iqoptionapi.stable_api import IQ_Option
from telegram import Bot
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

# Activos configurados
ACTIVOS = ["EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC", "USDCHF-OTC", "AUDCAD-OTC"]

# Límite de operaciones
MAX_OPERACIONES = 30
OPERACIONES_CUENTA1 = 0
OPERACIONES_CUENTA2 = 0

# Control de estado
BOT_EN_EJECUCION = False
ULTIMA_VELA = None
OP_C1 = None
OP_C2 = None

# Configuración Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --------------------------
# CONEXIÓN CUENTAS
# --------------------------
def conectar(email, passw, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        iq = IQ_Option(email, passw)
        ok, razon = iq.connect()
        if ok:
            time.sleep(1)
            iq.change_balance("PRACTICE")
            saldo = round(iq.get_balance(), 2)
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre}: {razon}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error {nombre}: {e}")
        return None, 0

def conectar_ambas():
    iq1, _ = conectar(os.getenv("IQ_EMAIL_1"), os.getenv("IQ_PASSWORD_1"), "CUENTA_1")
    time.sleep(2)
    iq2, _ = conectar(os.getenv("IQ_EMAIL_2"), os.getenv("IQ_PASSWORD_2"), "CUENTA_2")
    return iq1, iq2

# --------------------------
# DATOS DE MERCADO
# --------------------------
def velas(iq, activo):
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
def orden(iq, nombre, activo, dir, vela, res):
    global OP_C1, OP_C2, OPERACIONES_CUENTA1, OPERACIONES_CUENTA2

    if nombre == "CUENTA_1":
        if OP_C1 == vela or OPERACIONES_CUENTA1 >= 15:
            res["ok"] = False
            return
    else:
        if OP_C2 == vela or OPERACIONES_CUENTA2 >= 15:
            res["ok"] = False
            return

    logger.info(f"📤 Enviando a {nombre}: {activo} {dir} ${MONTO}")
    ok = False
    saldo = id_op = None

    for _ in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.2)
            activos_disp = iq.get_all_ACTIVES_OPCODE()
            if activo not in activos_disp:
                logger.warning(f"⚠️ {activo} no disponible")
                time.sleep(0.3)
                continue
            estado, id_op = iq.buy(MONTO, activo, dir, EXPIRACION)
            if estado and id_op > 0:
                time.sleep(0.4)
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} | ID: {id_op} | Saldo: ${saldo}")
                ok = True
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ {nombre} intento {_+1}: {e}")
            time.sleep(ESPERA)

    if ok:
        if nombre == "CUENTA_1":
            OP_C1 = vela
            OPERACIONES_CUENTA1 += 1
        else:
            OP_C2 = vela
            OPERACIONES_CUENTA2 += 1
        res.update({"ok":True, "id":id_op, "saldo":saldo})
    else:
        res["ok"] = False

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def iniciar_bucle():
    global BOT_EN_EJECUCION, ULTIMA_VELA, OP_C1, OP_C2, OPERACIONES_CUENTA1, OPERACIONES_CUENTA2
    iq1, iq2 = conectar_ambas()
    if not iq1 or not iq2:
        logger.critical("❌ No se pudieron conectar ambas cuentas")
        return

    logger.info("="*70)
    logger.info("🤖 BOT ACTIVO | 15 operaciones por cuenta | Control Telegram")
    logger.info("="*70)

    senal = None
    cuenta_analisis = 1  # Rotación: empieza analizando con cuenta 1

    while BOT_EN_EJECUCION:
        try:
            # Detener si ya completamos las 30 operaciones
            if OPERACIONES_CUENTA1 >= 15 and OPERACIONES_CUENTA2 >= 15:
                logger.info("✅ Se completaron las 30 operaciones programadas")
                BOT_EN_EJECUCION = False
                break

            ts = iq1.get_server_timestamp()
            seg = int(ts % 60)
            vela_act = int(ts // 60)

            if vela_act != ULTIMA_VELA:
                ULTIMA_VELA = vela_act
                OP_C1 = OP_C2 = None
                senal = None
                # Cambiamos de cuenta para analizar la siguiente vela
                cuenta_analisis = 2 if cuenta_analisis == 1 else 1

            if seg == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                logger.info(f"🔍 Analizando con CUENTA_{cuenta_analisis}...")
                iq_analisis = iq1 if cuenta_analisis == 1 else iq2

                for act in ACTIVOS:
                    df = velas(iq_analisis, act)
                    if df is None or df.empty:
                        continue
                    s = get_reversal_signal(df)
                    if s:
                        d, f, _ = s
                        if f >= FUERZA_MIN and f > fuerza_max:
                            fuerza_max = f
                            mejor = (act, d, f)

                if mejor:
                    act, d, f = mejor
                    dir_final = "put" if d == "call" else "call"
                    senal = (act, dir_final, f)
                    logger.info(f"✅ Señal lista: {act} {dir_final} | Fuerza: {f}")

            if senal and SEG_INICIO <= seg <= SEG_FIN:
                act, dir_final, f = senal
                logger.info("🚀 ENVIANDO OPERACIONES")

                r1 = {"ok":False}
                r2 = {"ok":False}

                if OPERACIONES_CUENTA1 < 15:
                    t1 = Thread(target=orden, args=(iq1, "CUENTA_1", act, dir_final, vela_act, r1))
                    t1.start()
                    t1.join()

                if OPERACIONES_CUENTA2 < 15:
                    t2 = Thread(target=orden, args=(iq2, "CUENTA_2", act, dir_final, vela_act, r2))
                    t2.start()
                    t2.join()

                if r1["ok"]:
                    logger.info(f"✅ CUENTA 1 | Total: {OPERACIONES_CUENTA1}/15")
                if r2["ok"]:
                    logger.info(f"✅ CUENTA 2 | Total: {OPERACIONES_CUENTA2}/15")

                senal = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {e}")
            iq1, iq2 = conectar_ambas()
            time.sleep(3)

# --------------------------
# CONTROL POR TELEGRAM
# --------------------------
async def escuchar_comandos():
    global BOT_EN_EJECUCION, OPERACIONES_CUENTA1, OPERACIONES_CUENTA2
    bot = Bot(token=TELEGRAM_TOKEN)
    logger.info("📡 Escuchando comandos de Telegram...")

    while True:
        updates = await bot.get_updates()
        for update in updates:
            if not update.message or str(update.message.chat_id) != TELEGRAM_CHAT_ID:
                continue
            texto = update.message.text.strip().lower()

            if texto == "/start":
                if not BOT_EN_EJECUCION:
                    OPERACIONES_CUENTA1 = 0
                    OPERACIONES_CUENTA2 = 0
                    BOT_EN_EJECUCION = True
                    Thread(target=iniciar_bucle, daemon=True).start()
                    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="✅ Bot iniciado. Realizará 15 operaciones en cada cuenta.")
                else:
                    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="ℹ️ El bot ya está funcionando.")

            elif texto == "/stop":
                BOT_EN_EJECUCION = False
                await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text="⏹️ Bot detenido.")

        await asyncio.sleep(1)

# --------------------------
# EJECUCIÓN PRINCIPAL
# --------------------------
if __name__ == "__main__":
    asyncio.run(escuchar_comandos())
