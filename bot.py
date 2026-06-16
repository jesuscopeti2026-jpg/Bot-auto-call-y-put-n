import time
import os
import pandas as pd
import logging
import sys
import threading

try:
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Instala: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git")
    sys.exit(1)

try:
    from telegram import Bot
    from telegram.error import TelegramError
except ImportError:
    print("❌ Instala: pip install python-telegram-bot==13.15")
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
SEG_INICIO = 0
SEG_FIN = 8
REINTENTOS = 8
ESPERA_INTENTO = 0.05
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDCAD-OTC"
]

# Variables de Railway
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

IQ1 = None
IQ2 = None
OPER1 = 0
OPER2 = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
YA_EJECUTADO = {}

# --------------------------
# NOTIFICACIONES
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Telegram: {e}")

# --------------------------
# CONEXIÓN
# --------------------------
def conectar_cuenta(email, passw, nombre):
    if not email or not passw:
        logger.error(f"{nombre}: Sin credenciales")
        return None, 0.0
    for _ in range(8):
        try:
            iq = IQ_Option(email, passw)
            ok, msg = iq.connect()
            if ok:
                time.sleep(0.3)
                iq.change_balance("PRACTICE") # Cambia a "REAL" si usas dinero real
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} conectada | Saldo: ${saldo}")
                return iq, saldo
        except Exception as e:
            logger.warning(f"{nombre} error: {e}")
        time.sleep(1)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 Conectando cuentas...")
    IQ1, s1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, s2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    while IQ1 is None or IQ2 is None:
        time.sleep(2)
        if IQ1 is None: IQ1, s1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None: IQ2, s2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")
    enviar_telegram(f"✅ BOT LISTO\n🔹 Cuenta 1: ${s1}\n🔹 Cuenta 2: ${s2}")
    return True

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_id, resultado):
    clave = f"{nombre}_{vela_id}"
    if YA_EJECUTADO.get(clave):
        resultado["ok"] = False
        resultado["msg"] = "Ya operó"
        return

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.05)
            saldo = round(iq.get_balance(), 2)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["msg"] = f"Saldo ${saldo}"
                return
            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = round(iq.get_balance(), 2)
                YA_EJECUTADO[clave] = True
                resultado.update({"ok": True, "id": id_op, "saldo": saldo_final})
                return
            time.sleep(ESPERA_INTENTO)
        except Exception as e:
            time.sleep(ESPERA_INTENTO)

    resultado["ok"] = False
    resultado["msg"] = "No ejecutado"

# --------------------------
# OBTENER VELAS
# --------------------------
def obtener_velas(iq, activo):
    try:
        ts = int(time.time()) - 2
        velas = iq.get_candles(activo, VELA, 50, ts)
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max":"high", "min":"low"}, inplace=True)
        df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
        return df
    except:
        return None

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPER1, OPER2
    logger.info("🚀 BOT SINCRONIZADO ACTIVO")

    while BOT_ACTIVO:
        try:
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                conectar_ambas()
                time.sleep(1)
                continue

            ts = IQ1.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            if OPER1 >= MAX_OPER or OPER2 >= MAX_OPER:
                s1 = round(IQ1.get_balance(), 2)
                s2 = round(IQ2.get_balance(), 2)
                enviar_telegram(f"✅ SESION FINALIZADA\n🔹 C1: {OPER1} | ${s1}\n🔹 C2: {OPER2} | ${s2}")
                BOT_ACTIVO = False
                break

            # ✅ SOLO UNA SEÑAL PARA AMBAS CUENTAS
            senal_actual = None
            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0
                mejor_senal = None

                for activo in ACTIVOS:
                    df = obtener_velas(IQ1, activo)
                    if df is None:
                        continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, _ = senal
                        if fuerza >= FUERZA_MIN and fuerza > mejor_fuerza:
                            mejor_fuerza = fuerza
                            mejor_senal = (activo, dirr, fuerza)

                if mejor_senal:
                    senal_actual = mejor_senal
                    enviar_telegram(
                        f"📊 SEÑAL ÚNICA\n"
                        f"📌 Activo: {mejor_senal[0]}\n"
                        f"➡️ Dirección: {mejor_senal[1].upper()}\n"
                        f"💪 Fuerza: {mejor_senal[2]}%"
                    )

            # ✅ EJECUTA LA MISMA SEÑAL EN AMBAS AL MISMO TIEMPO
            if senal_actual and SEG_INICIO <= segundos <= SEG_FIN:
                activo, direccion, fuerza = senal_actual
                enviar_telegram(f"⚡ EJECUTANDO IGUAL EN AMBAS | Seg {segundos}")

                res1 = {"ok": False}
                res2 = {"ok": False}

                hilo1 = threading.Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1))
                hilo2 = threading.Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2))

                hilo1.start()
                hilo2.start()
                hilo1.join(timeout=4)
                hilo2.join(timeout=4)

                if res1["ok"]: OPER1 += 1
                if res2["ok"]: OPER2 += 1

                enviar_telegram(
                    f"✅ OPERACIÓN IGUAL EN AMBAS\n"
                    f"📌 {activo} | {direccion.upper()}\n"
                    f"🔹 Cuenta 1: {'✅ ID '+str(res1['id'])+' $'+str(res1['saldo']) if res1['ok'] else '❌'}\n"
                    f"🔹 Cuenta 2: {'✅ ID '+str(res2['id'])+' $'+str(res2['saldo']) if res2['ok'] else '❌'}\n"
                    f"📊 Progreso: C1 {OPER1} | C2 {OPER2}"
                )

                senal_actual = None

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    if conectar_ambas():
        bucle_principal()
