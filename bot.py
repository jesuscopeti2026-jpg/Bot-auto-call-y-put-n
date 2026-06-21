import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
import gc
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN — CONEXIÓN DIRECTA
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 25
TIMEFRAME_M1 = 60

PARES = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC",
    "NZDJPY-OTC", "AUDNZD-OTC", "EURCHF-OTC"
]

MAX_DAILY_TRADES = 100
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_DELAY = 5          # Espera corta para reconectar rápido
RECONNECT_DELAY_LONG = 60
MAX_SILENCE = 18              # Detecta caída rápido
RECONNECT_INTERVAL = 22       # Reconexión programada antes de corte Railway

FUERZA_MINIMA = 35
TOLERANCIA_NIVEL = 0.0018
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.3
REINTENTOS_EJECUCION = 2
TIEMPO_MINIMO_VALIDO = 57
ESPERA_TRAS_ERROR = 1

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
IQ_API = None
LAST_VALID = 0
LAST_FORCED_RECONNECT = 0

# ====================================================
# 📱 TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                "https://api.telegram.org/bot{}/sendMessage".format(TOKEN),
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
            )
        except Exception as e:
            logging.error("Telegram: {}".format(str(e)))

def listen_commands():
    global BOT_RUNNING
    last_update_id = 0
    while True:
        try:
            res = requests.get(
                "https://api.telegram.org/bot{}/getUpdates".format(TOKEN),
                params={"offset": last_update_id + 1, "timeout": 20},
                timeout=25
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(1)
                continue
            for update in data.get("result", []):
                last_update_id = update["update_id"]
                text = update.get("message", {}).get("text", "").strip().lower()
                chat_id = str(update["message"]["chat"]["id"])
                if chat_id != str(CHAT_ID):
                    continue
                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ <b>BOT INICIADO</b> — analizando pares OTC")
                    else:
                        send("ℹ️ Ya está activo")
                elif text == "/stop":
                    BOT_RUNNING = False
                    send("🛑 Detenido")
        except Exception as e:
            logging.error("Comandos: {}".format(e))
            time.sleep(1)

# ====================================================
# 🔄 REINICIO DIARIO
# ====================================================
def reset_day():
    global DAILY_TRADES, CURRENT_DAY, LOSS_STREAK, LAST_TRADE, SEÑAL_PENDIENTE
    today = datetime.now(timezone.utc).day
    if today != CURRENT_DAY:
        DAILY_TRADES = LOSS_STREAK = 0
        LAST_TRADE = SEÑAL_PENDIENTE = None
        CURRENT_DAY = today
        if BOT_RUNNING:
            send("🔄 Nuevo día — contadores reiniciados")

# ====================================================
# 🔌 CONEXIÓN DIRECTA — SIN DEMORAS INNECESARIAS
# ====================================================
from iqoptionapi.stable_api import IQ_Option

def connect():
    global IQ_API, LAST_VALID, LAST_FORCED_RECONNECT
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ Faltan credenciales IQ_EMAIL / IQ_PASSWORD")
                time.sleep(RECONNECT_DELAY_LONG)
                attempts += 1
                continue
            # Cierre limpio antes de conectar
            if IQ_API is not None:
                try:
                    IQ_API.close_connect()
                except Exception:
                    pass
                IQ_API = None
                gc.collect()
            time.sleep(1)  # Solo pausa mínima

            IQ_API = IQ_Option(EMAIL, PASSWORD)
            ok, reason = IQ_API.connect()
            # Prueba inmediata
            if ok:
                try:
                    _ = IQ_API.get_server_timestamp()
                    IQ_API.change_balance("PRACTICE")
                    balance = IQ_API.get_balance()
                    LAST_VALID = LAST_FORCED_RECONNECT = time.time()
                    send("✅ CONECTADO DIRECTO | Saldo: ${:.2f}".format(balance))
                    return IQ_API
                except Exception as val_err:
                    logging.warning("Conectado sin datos: {}".format(val_err))
                    ok = False
            logging.warning("Intento {} fallido: {}".format(attempts+1, reason))
        except Exception as e:
            logging.error("Error conexión: {}".format(str(e)))
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    send("💥 Pausa larga — reintentando…")
    time.sleep(RECONNECT_DELAY_LONG)
    return connect()

def ensure_connection():
    """Verificación rápida + reconexión antes de corte"""
    global IQ_API, LAST_VALID, LAST_FORCED_RECONNECT
    ahora = time.time()
    # Reconexión programada fija
    if ahora - LAST_FORCED_RECONNECT > RECONNECT_INTERVAL:
        logging.info("🔄 Reconexión directa programada…")
        IQ_API = connect()
        return IQ_API is not None
    # Verificación ligera
    try:
        if IQ_API and IQ_API.check_connect() and (ahora - LAST_VALID < MAX_SILENCE):
            IQ_API.get_server_timestamp()
            LAST_VALID = ahora
            return True
    except Exception as e:
        logging.warning("Conexión perdida: {}".format(e))
    IQ_API = connect()
    return IQ_API is not None

# ====================================================
# 📥 OBTENER VELAS — PROCESO LIGERO
# ====================================================
def get_df(iq, pair, retries=2):
    global LAST_VALID
    for _ in range(retries):
        try:
            if not ensure_connection():
                time.sleep(0.2)
                continue
            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 10:
                time.sleep(0.2)
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            LAST_VALID = time.time()
            return df
        except Exception as e:
            err = str(e).lower()
            logging.error("{}: {}".format(pair, err))
            if "need reconnect" in err or "websocket" in err:
                ensure_connection()
            time.sleep(ESPERA_TRAS_ERROR)
    return None

# ====================================================
# 🚀 EJECUCIÓN RÁPIDA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION+1):
        try:
            if not ensure_connection():
                continue
            ts = iq.get_server_timestamp()
            sec_rest = 60 - (ts % 60)
            if sec_rest < TIEMPO_MINIMO_VALIDO:
                logging.warning("Tiempo insuficiente {}s".format(sec_rest))
                return False, None
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            ok, tid = iq.buy(monto, par, direccion, vencimiento)
            if ok and tid > 0:
                return True, tid
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.2)
        except Exception as e:
            logging.error("Operación: {}".format(e))
            ensure_connection()
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE, IQ_API
    threading.Thread(target=listen_commands, daemon=True).start()
    # CONEXIÓN DIRECTA AL INICIAR
    IQ_API = connect()
    send("ℹ️ Sistema listo — usa /start para operar")
    last_candle = None

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.5)
                continue
            reset_day()
            if not ensure_connection():
                time.sleep(0.5)
                continue
            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario alcanzado")
                BOT_RUNNING = False
                time.sleep(300)
                continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                rest = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if rest > 0:
                    send("⏸️ Pausa {}min".format(rest//60))
                    time.sleep(5)
                    continue
                else:
                    LOSS_STREAK = 0
                    send("✅ Pausa finalizada")

            st = IQ_API.get_server_timestamp()
            sec = st % 60
            current_candle = int(st // 60)

            if current_candle != last_candle:
                last_candle = current_candle
                if SEÑAL_PENDIENTE:
                    p, sig, fz, tn = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None
                    if (p, sig) == LAST_TRADE:
                        continue
                    LAST_TRADE = (p, sig)
                    send("""🚀 OPERACIÓN
💹 {} | 📍 {} | 💪 {}
{}""".format(p, tn.upper(), fz, "🟢 COMPRA" if sig == "call" else "🔴 VENTA"))
                    ok, tid = ejecutar_operacion(IQ_API, BASE_AMOUNT, p, sig, EXPIRATION)
                    if ok:
                        DAILY_TRADES += 1
                        send("✅ Abierta — Total: {}".format(DAILY_TRADES))
                        time.sleep(65)
                        try:
                            res = IQ_API.check_win_v4(tid)
                            if res < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send("❌ -${:.2f} | Racha {}".format(abs(res), LOSS_STREAK))
                            else:
                                LOSS_STREAK = 0
                                send("✅ +${:.2f}".format(res))
                        except Exception as e:
                            send("⚠️ Verificación: {}".format(e))
                            ensure_connection()
                    else:
                        send("❌ Falló en {}".format(p))

            if 10 <= sec <= 57:
                mejor = None
                max_fz = 0
                for par in PARES:
                    df = get_df(IQ_API, par)
                    if df is None:
                        time.sleep(0.15)
                        continue
                    try:
                        from strategy import get_reversal_signal
                        resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                        if resultado and isinstance(resultado, tuple) and len(resultado) == 3:
                            sig, fz, tn = resultado
                            if isinstance(fz, (int, float)) and fz >= FUERZA_MINIMA and fz > max_fz:
                                max_fz = fz
                                mejor = (par, sig, fz, tn)
                    except Exception as e:
                        logging.error("Estrategia {}: {}".format(par, e))
                if 55 <= sec <= 57 and mejor:
                    SEÑAL_PENDIENTE = mejor
                    par, sig, fz, tn = mejor
                    send("🔍 Señal {} {} | Fuerza: {}".format(par, tn, fz))
            time.sleep(0.15)  # Ciclo rápido y estable
        except Exception as e:
            send("💥 Error: {} — reconectando…".format(str(e)))
            logging.exception("Error global")
            time.sleep(2)
            ensure_connection()

if __name__ == "__main__":
    req = ["IQ_EMAIL", "IQ_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    faltan = [v for v in req if not os.getenv(v)]
    if faltan:
        print("❌ Faltan vars: {}".format(faltan))
        sys.exit(1)
    if not os.path.exists("strategy.py"):
        print("❌ Falta strategy.py")
        sys.exit(1)
    main()
