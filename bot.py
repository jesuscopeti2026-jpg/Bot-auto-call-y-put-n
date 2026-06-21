import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
import traceback
from datetime import datetime, timezone

from strategy import get_reversal_signal
from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN OPTIMIZADA RAILWAY
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 91
TIMEFRAME_M1 = 60

# Pares OTC completos
PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC"
]

MAX_DAILY_TRADES = 150
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 3
KEEPALIVE_INTERVAL = 18  # Evita corte Railway

FUERZA_MINIMA = 32
TOLERANCIA_NIVEL = 0.0028
VENTANA_NIVELES = 5

TIEMPO_ESPERA_EJECUCION = 0.02
REINTENTOS_EJECUCION = 4
TIEMPO_MINIMO_VALIDO = 58

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None
LAST_PING = 0

# ====================================================
# 📱 TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=8
            )
        except Exception as e:
            logging.error(f"Telegram: {str(e)}")

def listen_commands():
    global BOT_RUNNING
    last_update_id = 0
    while True:
        try:
            res = requests.get(
                f"https://api.telegram.org/bot{TOKEN}/getUpdates",
                params={"offset": last_update_id + 1, "timeout": 20},
                timeout=25
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(1)
                continue
            for update in data.get("result", []):
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if chat_id != str(CHAT_ID):
                    continue
                if text == "/start":
                    if not BOT_RUNNING:
                        BOT_RUNNING = True
                        send("✅ <b>BOT INICIADO</b>\nEstrategia: Reversión\nAnalizando pares…\nEjecución: vela siguiente")
                    else:
                        send("ℹ️ Ya está activo y analizando.")
                elif text == "/stop":
                    if BOT_RUNNING:
                        BOT_RUNNING = False
                        send("🛑 <b>BOT DETENIDO</b>")
        except Exception as e:
            logging.error(f"Comandos: {str(e)}")
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
            send("🔄 <b>NUEVO DÍA</b> | Contadores reiniciados.")

# ====================================================
# 🔌 CONEXIÓN + MANTENIMIENTO
# ====================================================
def connect():
    global LAST_PING
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ ERROR: Credenciales IQ faltantes.")
                time.sleep(10)
                attempts += 1
                continue
            iq = IQ_Option(EMAIL, PASSWORD)
            ok, reason = iq.connect()
            time.sleep(2)
            if ok:
                try:
                    _ = iq.get_server_timestamp()
                    iq.change_balance("PRACTICE")
                    balance = iq.get_balance()
                    LAST_PING = time.time()
                    send(f"✅ <b>CONECTADO</b>\nSaldo: ${balance:.2f}")
                    return iq
                except Exception:
                    ok = False
            logging.warning(f"Intento {attempts+1}: {reason}")
        except Exception as e:
            logging.error(f"Conexión: {str(e)}")
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    send("💥 Reintentando en 60s...")
    time.sleep(60)
    return connect()

def check_and_reconnect(iq):
    global LAST_PING
    ahora = time.time()
    try:
        if iq and iq.check_connect():
            if ahora - LAST_PING > KEEPALIVE_INTERVAL:
                iq.get_server_timestamp()
                LAST_PING = ahora
            return iq
    except Exception:
        pass
    return connect()

# ====================================================
# 📥 OBTENER VELAS
# ====================================================
def get_df(iq, pair, retries=2):
    for _ in range(retries):
        try:
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(0.3)
                continue
            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 10:
                time.sleep(0.3)
                continue
            df = pd.DataFrame(data)
            df.rename(columns={"max":"high", "min":"low"}, inplace=True)
            df[["open","close","high","low","volume"]] = df[["open","close","high","low","volume"]].astype(float)
            return df
        except Exception as e:
            err = str(e).lower()
            if "need reconnect" in err or "websocket" in err:
                iq = connect()
            logging.error(f"{pair}: {str(e)}")
            time.sleep(0.4)
    return None

# ====================================================
# 🚀 EJECUCIÓN PRECISA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            iq = check_and_reconnect(iq)
            if not iq:
                continue
            tiempo_servidor = iq.get_server_timestamp()
            segundos_restantes = 60 - (tiempo_servidor % 60)
            if segundos_restantes < TIEMPO_MINIMO_VALIDO:
                return False, None
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            status, trade_id = iq.buy(monto, par, direccion, vencimiento)
            if status and trade_id > 0:
                return True, trade_id
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.2)
        except Exception:
            iq = check_and_reconnect(iq)
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL: ANÁLISIS + SEÑAL + EJECUCIÓN
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE
    threading.Thread(target=listen_commands, daemon=True).start()

    iq = connect()
    last_candle = None
    send("ℹ️ <b>SISTEMA LISTO</b>\nEnvía /start para comenzar a analizar y operar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(0.5)
                continue
            reset_day()
            iq = check_and_reconnect(iq)
            if not iq:
                time.sleep(1)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario alcanzado.")
                BOT_RUNNING = False
                time.sleep(300)
                continue
            if LOSS_STREAK >= MAX_LOSS_STREAK:
                restante = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if restante > 0:
                    send(f"⏸️ Pausa: {restante//60} min")
                    time.sleep(5)
                    continue
                else:
                    LOSS_STREAK = 0
                    LAST_TRADE = None
                    send("✅ Pausa finalizada — volviendo a analizar")

            server_time = iq.get_server_timestamp()
            sec = server_time % 60
            current_candle = int(server_time // 60)

            # ✅ EJECUTAR SEÑAL GUARDADA AL INICIO DE VELA
            if current_candle != last_candle:
                last_candle = current_candle
                if SEÑAL_PENDIENTE:
                    pair, signal, fuerza, tipo_nivel = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None
                    if (pair, signal) == LAST_TRADE:
                        continue
                    LAST_TRADE = (pair, signal)

                    send(f"""🚀 <b>EJECUTANDO ENTRADA</b>
💹 Activo: {pair}
📍 Zona: {tipo_nivel}
💪 Fuerza: {fuerza}/100
📊 Tipo: {'🟢 COMPRA' if signal == 'call' else '🔴 VENTA'}""")

                    status, trade_id = ejecutar_operacion(iq, BASE_AMOUNT, pair, signal, EXPIRATION)
                    if status:
                        DAILY_TRADES += 1
                        send(f"✅ <b>OPERACIÓN ABIERTA</b> | ${BASE_AMOUNT:.2f}")
                        time.sleep(65)
                        try:
                            res = iq.check_win_v4(trade_id)
                            if res < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send(f"❌ <b>PERDIDA</b> | -${abs(res):.2f}\nRacha: {LOSS_STREAK}/{MAX_LOSS_STREAK}")
                            else:
                                LOSS_STREAK = 0
                                send(f"✅ <b>GANADA</b> | +${res:.2f}")
                        except Exception as e:
                            send(f"⚠️ Verificación: {str(e)}")
                            iq = check_and_reconnect(iq)
                    else:
                        send(f"❌ No se pudo ejecutar en {pair}")

            # ✅ ANALIZA TODOS LOS PARES ENTRE 10‑58s
            if 10 <= sec <= 58:
                mejor_opcion = None
                mayor_fuerza = 0
                for pair in PAIRS:
                    df = get_df(iq, pair)
                    if df is None:
                        time.sleep(0.25)  # Evita saturación Railway
                        continue
                    resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if resultado:
                        signal, fuerza, tipo_nivel = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_opcion = (pair, signal, fuerza, tipo_nivel)
                # ✅ GUARDA Y AVISA ANTES DE CAMBIAR VELA
                if 55 <= sec <= 58 and mejor_opcion:
                    SEÑAL_PENDIENTE = mejor_opcion
                    pair, signal, fuerza, tipo_nivel = mejor_opcion
                    send(f"""🔍 <b>SEÑAL DETECTADA</b>
💹 {pair} | {tipo_nivel}
💪 Fuerza: {fuerza}/100
⏳ Ejecución: vela siguiente""")

            time.sleep(0.05)  # Estabilidad extra

        except Exception as e:
            send(f"💥 Error: {str(e)} — continuando…")
            logging.exception("Error global")
            time.sleep(2)
            iq = check_and_reconnect(iq)

if __name__ == "__main__":
    try:
        required = ["IQ_EMAIL", "IQ_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
        missing = [v for v in required if not os.getenv(v)]
        if missing:
            print(f"❌ Faltan variables: {missing}")
            sys.exit(1)
        if not os.path.exists("strategy.py"):
            print("❌ Falta archivo strategy.py")
            sys.exit(1)
        main()
    except Exception as fatal:
        print(f"💥 ERROR FATAL: {str(fatal)}")
        traceback.print_exc()
        sys.exit(1)
