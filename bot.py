import time
import os
import pandas as pd
import logging
import sys
import requests

# --------------------------
# DEPENDENCIAS ESTABLES
# --------------------------
try:
    # Versión corregida para nube
    from iqoptionapi.stable_api import IQ_Option
except ImportError:
    print("❌ Instala: pip install git+https://github.com/Lu-Yi-Hsun/iqoptionapi.git@v5.7.0")
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
FUERZA_MIN = 80
SEG_INICIO = 0
SEG_FIN = 9
REINTENTOS = 15
ESPERA_INTENTO = 0.4
MAX_OPER = 20
TIEMPO_MAX_SESION = 300  # Reinicia cada 5min automáticamente

ACTIVOS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURJPY-OTC",
    "USDCHF-OTC", "AUDUSD-OTC", "GBPJPY-OTC"
]

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD = os.getenv("IQ_PASSWORD_1", "")

IQ = None
ULTIMA_CONEXION = 0
OPERACIONES = 0
BOT_ACTIVO = True
ULTIMA_VELA = None
YA_EJECUTADO = {}
mejor_senal = None

# --------------------------
# TELEGRAM
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        Bot(token=TELEGRAM_TOKEN).send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML")
    except Exception as e: logger.error(f"Telegram: {e}")

# --------------------------
# CONEXIÓN SEGURA Y FORZADA
# --------------------------
def conectar_nuevo():
    global IQ, ULTIMA_CONEXION
    if not IQ_EMAIL or not IQ_PASSWORD:
        logger.error("❌ Faltan credenciales")
        return None, 0.0
    try:
        if IQ:
            try: IQ.disconnect()
            except: pass
            del IQ
            time.sleep(2)
    except: pass
    IQ = None
    for intento in range(12):
        try:
            logger.info(f"🔄 Conexión intento {intento+1}/12")
            IQ = IQ_Option(IQ_EMAIL, IQ_PASSWORD)
            check, razon = IQ.connect()
            time.sleep(3)
            if check and IQ.check_connect():
                IQ.change_balance("PRACTICE")  # ⚠️ Cambia a "REAL" si usas dinero real
                saldo = round(IQ.get_balance(), 2)
                ULTIMA_CONEXION = time.time()
                logger.info(f"✅ CONECTADO | Saldo: ${saldo}")
                enviar_telegram(f"✅ BOT INICIADO\n💵 Saldo: ${saldo}\n🎯 Modo: SOLO REVERSIÓN EXACTA")
                return IQ, saldo
            else:
                logger.warning(f"Intento {intento+1} falló: {razon}")
        except Exception as e: logger.error(f"Error conexión: {e}"); IQ = None
        time.sleep(2.5)
    logger.critical("❌ No se pudo conectar después de 12 intentos")
    return None, 0.0

def verificar_y_reconectar():
    global IQ, ULTIMA_CONEXION
    # Reinicio automático por tiempo
    if time.time() - ULTIMA_CONEXION > TIEMPO_MAX_SESION:
        logger.info("⏱️ Reinicio programado por tiempo")
        IQ, _ = conectar_nuevo()
        return IQ and IQ.check_connect()
    try:
        if not IQ or not IQ.check_connect():
            logger.warning("⚠️ Conexión perdida — reinicio completo")
            IQ, _ = conectar_nuevo()
            time.sleep(2)
        return IQ and IQ.check_connect()
    except:
        IQ, _ = conectar_nuevo()
        return IQ and IQ.check_connect()

# --------------------------
# API DE RESPALDO (ELIMINA ERROR get_candles)
# --------------------------
def obtener_velas_rest(activo, cantidad=45):
    """Obtiene datos por API REST si WebSocket falla"""
    try:
        url = f"https://api.iqoption.com/v2/candles/{activo}/{VELA}/0/{cantidad}"
        resp = requests.get(url, auth=(IQ_EMAIL, IQ_PASSWORD), timeout=10)
        if resp.status_code == 200:
            datos = resp.json().get("candles", [])
            if datos:
                df = pd.DataFrame(datos)
                df.rename(columns={"max":"high","min":"low","start":"time"}, inplace=True)
                df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
                return df
    except Exception as e: logger.warning(f"Error REST: {e}")
    return None

def obtener_velas(activo):
    """PRUEBA WEBSOCKET → SI FALLA → USA REST"""
    for intento in range(4):
        if not verificar_y_reconectar(): time.sleep(1); continue
        try:
            time.sleep(0.6)
            ts = int(time.time())
            velas = IQ.get_candles(activo, VELA, 40, ts)
            if velas and len(velas) >= 40:
                df = pd.DataFrame(velas)
                df.rename(columns={"max":"high","min":"low"}, inplace=True)
                df[["open","close","high","low"]] = df[["open","close","high","low"]].astype(float)
                return df
            logger.info("⚠️ WebSocket falló → usando API REST")
        except Exception as e: logger.warning(f"WebSocket: {e}")
        # Respaldo garantizado
        df_rest = obtener_velas_rest(activo)
        if df_rest is not None and len(df_rest)>=40: return df_rest
        time.sleep(0.8)
    return None

# --------------------------
# EJECUTAR ORDEN
# --------------------------
def ejecutar_orden(activo, direccion, vela_id):
    clave = f"orden_{vela_id}"
    if YA_EJECUTADO.get(clave): return False, None, 0.0
    for intento in range(REINTENTOS):
        if not verificar_y_reconectar(): time.sleep(0.5); continue
        try:
            saldo = round(IQ.get_balance(), 2)
            if saldo < MONTO: enviar_telegram(f"❌ Saldo insuficiente: ${saldo}"); return False, None, saldo
            ok, id_op = IQ.buy(MONTO, activo, direccion, EXPIRACION)
            time.sleep(0.3)
            if ok and id_op > 0:
                saldo_final = round(IQ.get_balance(), 2)
                YA_EJECUTADO[clave] = True
                logger.info(f"✅ REVERSIÓN EJECUTADA | {activo} | {direccion} | ID:{id_op}")
                return True, id_op, saldo_final
        except Exception as e: logger.warning(f"Orden: {e}"); verificar_y_reconectar()
        time.sleep(ESPERA_INTENTO)
    enviar_telegram(f"❌ No se pudo operar en {activo}")
    return False, None, 0.0

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES, IQ, mejor_senal
    logger.info("🚀 BOT: SIN ERROR — SOLO REVERSIÓN EXACTA")
    while BOT_ACTIVO:
        try:
            if not verificar_y_reconectar(): time.sleep(2); continue
            ts = IQ.get_server_timestamp()
            segundos = ts % 60
            vela_actual = int(ts // 60)
            vela_cerrada = vela_actual - 1
            if OPERACIONES >= MAX_OPER:
                saldo_final = round(IQ.get_balance(), 2)
                enviar_telegram(f"✅ SESIÓN FINALIZADA\n📊 Operaciones: {OPERACIONES}\n💵 Saldo: ${saldo_final}")
                BOT_ACTIVO = False; break
            if vela_cerrada != ULTIMA_VELA:
                ULTIMA_VELA = vela_cerrada
                YA_EJECUTADO.clear()
                mejor_fuerza = 0; mejor_senal = None
                logger.info(f"🔍 Analizando vela {vela_cerrada}")
                for activo in ACTIVOS:
                    df = obtener_velas(activo)
                    if df is None: continue
                    senal = get_reversal_signal(df)
                    if senal:
                        dirr, fuerza, tipo = senal
                        logger.info(f"🎯 {activo} → {dirr.upper()} | {fuerza}% | {tipo}")
                        if fuerza >= FUERZA_MIN and fuerza > mejor_fuerza:
                            mejor_fuerza = fuerza; mejor_senal = (activo, dirr, fuerza, tipo)
                if mejor_senal:
                    a,d,f,t = mejor_senal
                    enviar_telegram(f"📊 SEÑAL DE REVERSIÓN\n📌 {a}\n➡️ {d.upper()}\n💪 {f}%\n📍 {t}")
            if mejor_senal and SEG_INICIO <= segundos <= SEG_FIN:
                a,d,f,t = mejor_senal
                ok,id_op,saldo = ejecutar_orden(a,d,vela_actual)
                if ok: OPERACIONES += 1; enviar_telegram(f"✅ OPERACIÓN\n📌 {a} | {d.upper()}\n🆔 {id_op}\n💵 ${saldo}")
                mejor_senal = None
            time.sleep(0.4)
        except Exception as e: logger.error(f"💥 Error: {e}"); verificar_y_reconectar(); time.sleep(4)

if __name__ == "__main__":
    IQ, _ = conectar_nuevo()
    if IQ: bucle_principal()
    else: logger.critical("❌ No se pudo iniciar — revisa credenciales/variables")
