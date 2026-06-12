import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
from datetime import datetime, timezone

from strategy import get_reversal_signal
from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN OPTIMIZADA - EJECUCIÓN EN SIGUIENTE VELA
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 25
TIMEFRAME_M1 = 60

# ✅ Lista ampliada de pares para más oportunidades
PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC"
]

MAX_DAILY_TRADES = 100
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 3

# ✅ Parámetros alineados con la estrategia
FUERZA_MINIMA = 35
TOLERANCIA_NIVEL = 0.0020
VENTANA_NIVELES = 5

# ✅ TIEMPOS AJUSTADOS PARA EJECUTAR AL INICIO DE LA VELA
TIEMPO_ESPERA_EJECUCION = 0.05
REINTENTOS_EJECUCION = 3
TIEMPO_MINIMO_VALIDO = 57  # Suficiente margen para ejecutar antes de que cierre

# Variables globales
DAILY_TRADES = 0
CURRENT_DAY = datetime.now(timezone.utc).day
LOSS_STREAK = 0
LAST_LOSS = 0
LAST_TRADE = None
BOT_RUNNING = False
SEÑAL_PENDIENTE = None

# ====================================================
# 📱 FUNCIONES TELEGRAM
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
                params={"offset": last_update_id + 1, "timeout": 30},
                timeout=35
            )
            data = res.json()
            if not data.get("ok"):
                time.sleep(2)
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
                        send("✅ <b>BOT INICIADO</b>\nEstrategia: Reversión S/R\nModo: Ejecución en siguiente vela")
                    else:
                        send("ℹ️ El bot ya está activo.")
                elif text == "/stop":
                    if BOT_RUNNING:
                        BOT_RUNNING = False
                        send("🛑 <b>BOT DETENIDO</b>")
                    else:
                        send("ℹ️ El bot ya está detenido.")

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
        DAILY_TRADES = 0
        LOSS_STREAK = 0
        LAST_TRADE = None
        SEÑAL_PENDIENTE = None
        CURRENT_DAY = today
        if BOT_RUNNING:
            send("🔄 <b>NUEVO DÍA</b> | Contadores reiniciados.")

# ====================================================
# 🔌 CONEXIÓN IQ OPTION
# ====================================================
def connect():
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ ERROR: Credenciales no configuradas.")
                time.sleep(10)
                attempts += 1
                continue

            iq = IQ_Option(EMAIL, PASSWORD)
            ok, reason = iq.connect()
            
            if ok:
                try:
                    iq.change_balance("PRACTICE")  # Cambia a "REAL" cuando estés listo
                    balance = iq.get_balance()
                    send(f"✅ <b>CONECTADO</b>\nSaldo: ${balance:.2f}")
                    return iq
                except Exception as e:
                    send(f"⚠️ Cargando datos del mercado...")
                    time.sleep(1)
            else:
                send(f"❌ Conexión fallida: {reason}")
                
        except Exception as e:
            send(f"❌ Error conexión: {str(e)}")
        
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    
    send("💥 Reintentando conexión en 60 segundos...")
    time.sleep(60)
    return connect()

# ====================================================
# 📥 OBTENER DATOS
# ====================================================
def get_df(iq, pair, retries=2):
    for _ in range(retries):
        try:
            if not iq or not iq.check_connect():
                iq = connect()
                if not iq:
                    time.sleep(1)
                    continue

            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 8:
                time.sleep(0.2)
                continue

            df = pd.DataFrame(data)
            df.rename(columns={"max": "high", "min": "low"}, inplace=True)
            df[["open", "close", "high", "low", "volume"]] = df[["open", "close", "high", "low", "volume"]].astype(float)
            return df

        except Exception as e:
            logging.error(f"Datos {pair}: {str(e)}")
            time.sleep(0.3)
    
    return None

# ====================================================
# 🚀 EJECUCIÓN SEGURA
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            if not iq.check_connect():
                iq = connect()
                time.sleep(0.1)
            
            tiempo_servidor = iq.get_server_timestamp()
            segundos_restantes = 60 - (tiempo_servidor % 60)
            
            if segundos_restantes < TIEMPO_MINIMO_VALIDO:
                return False, None
            
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            status, trade_id = iq.buy(monto, par, direccion, vencimiento)
            
            if status and trade_id > 0:
                return True, trade_id
            
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)

        except Exception as e:
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.1)
    
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL - LÓGICA DE VELA SIGUIENTE
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE
    threading.Thread(target=listen_commands, daemon=True).start()

    iq = connect()
    ultima_vela_procesada = None
    send("ℹ️ <b>SISTEMA LISTO</b>\nAnalizando mercados...\nEnvía /start para operar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(1)
                continue

            reset_day()

            if not iq or not iq.check_connect():
                iq = connect()
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
                    send(f"⏸️ Pausa por racha: {restante//60} min")
                    time.sleep(5)
                    continue
                else:
                    LOSS_STREAK = 0
                    LAST_TRADE = None
                    send("✅ Pausa finalizada.")

            server_time = iq.get_server_timestamp()
            segundos = server_time % 60
            vela_actual = int(server_time // 60)

            # ==========================================
            # EJECUTAR SEÑAL PENDIENTE AL INICIO DE VELA
            # ==========================================
            if vela_actual != ultima_vela_procesada:
                ultima_vela_procesada = vela_actual

                if SEÑAL_PENDIENTE is not None:
                    par, direccion, fuerza, tipo_nivel = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None

                    if (par, direccion) == LAST_TRADE:
                        continue
                    LAST_TRADE = (par, direccion)

                    send(f"""🚀 <b>EJECUTANDO ENTRADA</b>
💹 Activo: {par}
📍 Zona: {tipo_nivel.upper()}
💪 Fuerza: {fuerza}/100
📊 Tipo: {'🟢 COMPRA' if direccion == 'call' else '🔴 VENTA'}
⏱️ Vencimiento: 1 minuto""")

                    ok, trade_id = ejecutar_operacion(iq, BASE_AMOUNT, par, direccion, EXPIRATION)

                    if ok:
                        DAILY_TRADES += 1
                        send(f"✅ <b>OPERACIÓN ABIERTA</b> | Monto: ${BASE_AMOUNT:.2f} | Total: {DAILY_TRADES}/{MAX_DAILY_TRADES}")

                        time.sleep(65)
                        try:
                            resultado = iq.check_win_v4(trade_id)
                            if resultado is None:
                                send("⚠️ Resultado no verificado.")
                                continue

                            if resultado < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send(f"❌ <b>PERDIDA</b> | -${abs(resultado):.2f}\nRacha: {LOSS_STREAK}/{MAX_LOSS_STREAK}")
                            else:
                                LOSS_STREAK = 0
                                send(f"✅ <b>GANADA</b> | +${resultado:.2f}\n_________________________")

                        except Exception as e:
                            send(f"⚠️ Error al verificar: {str(e)}")
                    else:
                        send(f"❌ No se pudo ejecutar en {par}")

            # ==========================================
            # BUSCAR SEÑALES ENTRE LOS SEGUNDOS 10 Y 55
            # ==========================================
            if 10 <= segundos <= 55:
                mejor_señal = None
                mayor_fuerza = 0

                for par in PAIRS:
                    df = get_df(iq, par)
                    if df is None:
                        continue

                    resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                    if resultado is not None:
                        direccion, fuerza, nivel = resultado
                        if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                            mayor_fuerza = fuerza
                            mejor_señal = (par, direccion, fuerza, nivel)

                # Guardar señal para ejecutar en la siguiente vela
                if 52 <= segundos <= 55 and mejor_señal is not None:
                    SEÑAL_PENDIENTE = mejor_señal
                    par, direccion, fuerza, nivel = mejor_señal
                    send(f"""🔍 <b>SEÑAL DETECTADA</b>
💹 Activo: {par}
📍 Nivel: {nivel.upper()}
💪 Fuerza: {fuerza}/100
⏳ <b>EJECUCIÓN: SIGUIENTE VELA</b>""")

            time.sleep(0.05)

        except Exception as e:
            send(f"💥 Error: {str(e)} | Reiniciando...")
            logging.exception("Error en bucle principal")
            time.sleep(2)
            try:
                iq = connect()
            except:
                pass

if __name__ == "__main__":
    required = ["IQ_EMAIL", "IQ_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"❌ Faltan variables de entorno: {', '.join(missing)}")
        sys.exit(1)
    main()
