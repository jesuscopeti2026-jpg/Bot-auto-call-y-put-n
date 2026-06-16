import time
import os
import pandas as pd
import logging
import sys
import threading
from datetime import datetime

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

# PARÁMETROS OPTIMIZADOS
MONTO = 600
EXPIRACION = 1
VELA = 60
FUERZA_MIN = 75
# Ventana amplia para evitar desfases
SEG_INICIO = 0
SEG_FIN = 9
# Más intentos, más rápido
REINTENTOS = 10
ESPERA_INTENTO = 0.03
MAX_OPER = 20

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDCAD-OTC"
]

# ✅ LEE TUS VARIABLES EXACTAS
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
# CONEXIÓN CON VERIFICACIÓN
# --------------------------
def conectar_cuenta(email, passw, nombre):
    if not email or not passw:
        logger.error(f"{nombre}: Faltan credenciales")
        return None, 0.0
    for intento in range(10):
        try:
            iq = IQ_Option(email, passw)
            ok, motivo = iq.connect()
            if ok:
                time.sleep(0.5)
                iq.change_balance("PRACTICE") # ⚠️ Cambia a "REAL" si usas dinero real
                saldo = round(iq.get_balance(), 2)
                logger.info(f"✅ {nombre} CONECTADA | Saldo: ${saldo}")
                return iq, saldo
            else:
                logger.warning(f"{nombre} fallo: {motivo} | Intento {intento+1}")
        except Exception as e:
            logger.error(f"{nombre} error: {e}")
        time.sleep(1)
    return None, 0.0

def conectar_ambas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    IQ1, s1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
    IQ2, s2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    while IQ1 is None or IQ2 is None:
        time.sleep(2)
        if IQ1 is None: IQ1, s1 = conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")
        if IQ2 is None: IQ2, s2 = conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")

    enviar_telegram(
        f"✅ BOT LISTO\n"
        f"🔹 Cuenta 1: ${s1}\n"
        f"🔹 Cuenta 2: ${s2}\n"
        f"⏱️ Ventana: {SEG_INICIO}-{SEG_FIN} seg\n"
        f"🔁 Reintentos: {REINTENTOS}"
    )
    return True

# --------------------------
# EJECUCIÓN RÁPIDA CON REGISTRO
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela_id, resultado):
    clave = f"{nombre}_{vela_id}"
    if YA_EJECUTADO.get(clave):
        resultado["ok"] = False
        resultado["msg"] = "Ya operó esta vela"
        return

    for intento in range(REINTENTOS):
        try:
            # Reconectar inmediato si hay fallo
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.03)

            saldo = round(iq.get_balance(), 2)
            if saldo < MONTO:
                resultado["ok"] = False
                resultado["msg"] = f"Saldo insuficiente: ${saldo}"
                return

            ok, id_op = iq.buy(MONTO, activo, direccion, EXPIRACION)
            if ok and id_op > 0:
                saldo_final = round(iq.get_balance(), 2)
                YA_EJECUTADO[clave] = True
                resultado.update({
                    "ok": True,
                    "id": id_op,
                    "saldo": saldo_final,
                    "intento": intento + 1
                })
                logger.info(f"✅ {nombre} | {activo} | {direccion} | Intento {intento+1} | ID {id_op}")
                return

            logger.info(f"⏳ {nombre} reintento {intento+1}...")
            time.sleep(ESPERA_INTENTO)

        except Exception as e:
            resultado["error"] = str(e)
            time.sleep(ESPERA_INTENTO)

    resultado["ok"] = False
    resultado["msg"] = f"No ejecutado tras {REINTENTOS} intentos"
    logger.error(f"❌ {nombre} NO ENTRÓ | {activo} | {direccion}")

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
    except Exception as e:
        logger.warning(f"Error velas {activo}: {e}")
        return None

# --------------------------
# BUCLE PRINCIPAL 100% SINCRONIZADO
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPER1, OPER2
    logger.info("🚀 BOT SINCRONIZADO INICIADO")

    while BOT_ACTIVO:
        try:
            # Verificar conexión en cada ciclo
            if not IQ1 or not IQ2 or not IQ1.check_connect() or not IQ2.check_connect():
                logger.warning("⚠️ Revisando conexiones...")
                conectar_ambas()
                time.sleep(1)
                continue

            ts = IQ1.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1

            # Fin de sesión
            if OPER1 >= MAX_OPER or OPER2 >= MAX_OPER:
                s1 = round(IQ1.get_balance(), 2)
                s2 = round(IQ2.get_balance(), 2)
                enviar_telegram(f"✅ SESION FINALIZADA\n🔹 C1: {OPER1} | ${s1}\n🔹 C2: {OPER2} | ${s2}")
                BOT_ACTIVO = False
                break

            # ✅ CALCULAR UNA SOLA SEÑAL PARA AMBAS
            senal_unica = None
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
                    senal_unica = mejor_senal
                    enviar_telegram(
                        f"📊 SEÑAL ÚNICA PARA AMBAS\n"
                        f"📌 Activo: {mejor_senal[0]}\n"
                        f"➡️ Dirección: {mejor_senal[1].upper()}\n"
                        f"💪 Fuerza: {mejor_senal[2]}%"
                    )

            # ✅ EJECUTAR AL MISMO TIEMPO, MISMO ACTIVO, MISMA DIRECCIÓN
            if senal_unica and SEG_INICIO <= segundos <= SEG_FIN:
                activo, direccion, fuerza = senal_unica
                enviar_telegram(f"⚡ EJECUTANDO EN PARALELO | Seg {segundos}")

                res1 = {"ok": False, "msg": ""}
                res2 = {"ok": False, "msg": ""}

                # Hilos separados = 0 desfase
                hilo1 = threading.Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", activo, direccion, vela_actual, res1))
                hilo2 = threading.Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", activo, direccion, vela_actual, res2))

                hilo1.start()
                hilo2.start()

                # Esperar máximo 5 segundos
                hilo1.join(timeout=5)
                hilo2.join(timeout=5)

                # Actualizar contadores
                if res1["ok"]: OPER1 += 1
                if res2["ok"]: OPER2 += 1

                # Resumen detallado
                enviar_telegram(
                    f"📋 RESULTADO OPERACIÓN\n"
                    f"📌 {activo} | {direccion.upper()}\n"
                    f"🔹 Cuenta 1: {'✅ ID '+str(res1['id'])+' $'+str(res1['saldo'])+' | Intento '+str(res1.get('intento','-')) if res1['ok'] else '❌ '+res1['msg']}\n"
                    f"🔹 Cuenta 2: {'✅ ID '+str(res2['id'])+' $'+str(res2['saldo'])+' | Intento '+str(res2.get('intento','-')) if res2['ok'] else '❌ '+res2['msg']}\n"
                    f"📊 Progreso: C1 {OPER1} | C2 {OPER2}"
                )

                senal_unica = None

            time.sleep(0.05)

        except Exception as e:
            logger.error(f"💥 Error bucle: {e}")
            enviar_telegram(f"⚠️ Error: {e}")
            time.sleep(1)

# --------------------------
# ARRANQUE
# --------------------------
if __name__ == "__main__":
    logger.info("🤖 BOT DEFINITIVO INICIANDO...")
    if conectar_ambas():
        bucle_principal()
