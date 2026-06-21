import time
import os
import requests
import pandas as pd
import numpy as np
import sys
import threading
import logging
from datetime import datetime, timezone

from iqoptionapi.stable_api import IQ_Option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ==========================================
# ⚙️ CONFIGURACIÓN OPTIMIZADA - MÁS SEÑALES
# ==========================================
EMAIL = os.getenv("IQ_EMAIL")
PASSWORD = os.getenv("IQ_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

EXPIRATION = 1
BASE_AMOUNT = 25
TIMEFRAME_M1 = 60

# ✅ LISTA AMPLIADA DE PARES OTC (24/7)
PAIRS = [
    "EURUSD-OTC", "GBPUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "GBPJPY-OTC",
    "AUDUSD-OTC", "USDCAD-OTC", "USDCHF-OTC", "NZDUSD-OTC",
    "EURCAD-OTC", "GBPCAD-OTC", "GBPCHF-OTC", "AUDJPY-OTC", "CADJPY-OTC",
    "NZDJPY-OTC", "AUDNZD-OTC", "EURCHF-OTC"
]

MAX_DAILY_TRADES = 100
MAX_LOSS_STREAK = 5
PAUSE_TIME = 900
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY = 3
RECONNECT_DELAY_LONG = 10

# ✅ PARÁMETROS AJUSTADOS PARA MÁS ENTRADAS
FUERZA_MINIMA = 35
TOLERANCIA_NIVEL = 0.0018
VENTANA_NIVELES = 5

# ⏱️ TIEMPOS SEGUROS PARA IQ OPTION
TIEMPO_ESPERA_EJECUCION = 0.1
REINTENTOS_EJECUCION = 3
TIEMPO_MINIMO_VALIDO = 59
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

# ====================================================
# 📱 FUNCIONES TELEGRAM
# ====================================================
def send(msg):
    if TOKEN and CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
                timeout=10
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
                        send("✅ <b>BOT INICIADO</b>\nEstrategia: Reversión S/R\nEntrada: Siguiente vela\nAnalizando múltiples pares")
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
# 🔌 CONEXIÓN IQ OPTION - SOLUCIÓN ERROR RECONNECT
# ====================================================
def connect():
    global IQ_API
    attempts = 0
    while attempts < MAX_RECONNECT_ATTEMPTS:
        try:
            if not EMAIL or not PASSWORD:
                send("❌ ERROR: Credenciales IQ no configuradas.")
                time.sleep(RECONNECT_DELAY_LONG)
                attempts += 1
                continue

            # Cerrar conexión anterior si existe
            if IQ_API and hasattr(IQ_API, 'close_connect'):
                try:
                    IQ_API.close_connect()
                except:
                    pass

            IQ_API = IQ_Option(EMAIL, PASSWORD)
            ok, reason = IQ_API.connect()
            
            if ok:
                try:
                    IQ_API.change_balance("PRACTICE")  # Cambia a "REAL" cuando estés listo
                    balance = IQ_API.get_balance()
                    send(f"✅ <b>CONECTADO CORRECTAMENTE</b>\nSaldo: ${balance:.2f}")
                    return IQ_API
                except Exception as e:
                    logging.warning(f"Esperando sincronización: {str(e)}")
                    time.sleep(2)
            else:
                logging.error(f"Fallo conexión: {reason}")
                send(f"❌ Conexión fallida: {reason}")
                
        except Exception as e:
            logging.error(f"Error conexión: {str(e)}")
            send(f"❌ Error conexión: {str(e)}")
        
        attempts += 1
        time.sleep(RECONNECT_DELAY)
    
    send("💥 Reintentando conexión completa en 30 segundos...")
    time.sleep(30)
    return connect()

# ✅ FUNCIÓN AUXILIAR: VERIFICAR Y RECONECTAR ANTES DE CUALQUIER ACCIÓN
def ensure_connection():
    global IQ_API
    try:
        if IQ_API and IQ_API.check_connect():
            return True
    except:
        pass
    logging.warning("Conexión perdida → reconectando...")
    IQ_API = connect()
    return IQ_API is not None

# ====================================================
# 📥 OBTENER DATOS DE MERCADO - CORREGIDO
# ====================================================
def get_df(iq, pair, retries=3):
    """Soluciona 'need reconnect' comprobando conexión antes de pedir velas"""
    for _ in range(retries):
        try:
            # Verificar conexión obligatoria antes de solicitar datos
            if not ensure_connection():
                time.sleep(ESPERA_TRAS_ERROR)
                continue

            data = iq.get_candles(pair, TIMEFRAME_M1, 25, time.time())
            if not data or len(data) < 10:
                time.sleep(0.3)
                continue

            df = pd.DataFrame(data)
            df.rename(columns={"max": "high", "min": "low"}, inplace=True)
            df[["open", "close", "high", "low", "volume"]] = df[["open", "close", "high", "low", "volume"]].astype(float)
            return df

        except Exception as e:
            logging.error(f"Datos {pair}: {str(e)}")
            # Forzar reconexión en caso de error de lectura
            if "reconnect" in str(e).lower() or "connection" in str(e).lower():
                ensure_connection()
            time.sleep(ESPERA_TRAS_ERROR)
    
    return None

# ====================================================
# 🚀 EJECUCIÓN SEGURA DE OPERACIONES
# ====================================================
def ejecutar_operacion(iq, monto, par, direccion, vencimiento):
    """Ejecuta solo si queda tiempo suficiente y el mercado está disponible"""
    for intento in range(REINTENTOS_EJECUCION + 1):
        try:
            if not ensure_connection():
                time.sleep(0.3)
                continue
            
            tiempo_servidor = iq.get_server_timestamp()
            segundos_restantes = 60 - (tiempo_servidor % 60)
            
            if segundos_restantes < TIEMPO_MINIMO_VALIDO:
                logging.warning(f"Tiempo insuficiente: {segundos_restantes}s - esperando siguiente vela")
                return False, None
            
            time.sleep(TIEMPO_ESPERA_EJECUCION)
            status, trade_id = iq.buy(monto, par, direccion, vencimiento)
            
            if status and trade_id > 0:
                return True, trade_id
            
            if intento < REINTENTOS_EJECUCION:
                time.sleep(0.3)

        except Exception as e:
            logging.error(f"Ejecución: {str(e)}")
            if intento < REINTENTOS_EJECUCION:
                ensure_connection()
                time.sleep(0.3)
    
    return False, None

# ====================================================
# 🧠 BUCLE PRINCIPAL DE OPERACIÓN
# ====================================================
def main():
    global BOT_RUNNING, LOSS_STREAK, LAST_LOSS, DAILY_TRADES, LAST_TRADE, SEÑAL_PENDIENTE, IQ_API
    threading.Thread(target=listen_commands, daemon=True).start()

    IQ_API = connect()
    last_candle = None
    send("ℹ️ <b>SISTEMA LISTO</b>\nEstrategia: Reversión en Soporte/Resistencia\nAnalizando " + str(len(PAIRS)) + " pares\nEnvía /start para comenzar")

    while True:
        try:
            if not BOT_RUNNING:
                time.sleep(1)
                continue

            reset_day()

            if not ensure_connection():
                time.sleep(2)
                continue

            if DAILY_TRADES >= MAX_DAILY_TRADES:
                send("ℹ️ Límite diario de operaciones alcanzado.")
                BOT_RUNNING = False
                time.sleep(300)
                continue

            if LOSS_STREAK >= MAX_LOSS_STREAK:
                restante = int(PAUSE_TIME - (time.time() - LAST_LOSS))
                if restante > 0:
                    send(f"⏸️ Pausa por racha de pérdidas: {restante//60} minutos restantes")
                    time.sleep(5)
                    continue
                else:
                    LOSS_STREAK = 0
                    LAST_TRADE = None
                    send("✅ Pausa finalizada. Volviendo a operar.")

            server_time = IQ_API.get_server_timestamp()
            sec = server_time % 60
            current_candle = int(server_time // 60)

            # ==========================================
            # EJECUTAR SEÑAL PENDIENTE AL INICIO DE VELA
            # ==========================================
            if current_candle != last_candle:
                last_candle = current_candle
                
                if SEÑAL_PENDIENTE is not None:
                    pair, signal, fuerza, tipo_nivel = SEÑAL_PENDIENTE
                    SEÑAL_PENDIENTE = None

                    if (pair, signal) == LAST_TRADE:
                        continue
                    LAST_TRADE = (pair, signal)

                    send(f"""🚀 <b>EJECUTANDO OPERACIÓN</b>
💹 Activo: {pair}
📍 Nivel: {tipo_nivel.upper()}
💪 Fuerza: {fuerza}/100
📊 Tipo: {'🟢 COMPRA' if signal == 'call' else '🔴 VENTA'}
⏱️ Vencimiento: 1 minuto""")

                    status, trade_id = ejecutar_operacion(IQ_API, BASE_AMOUNT, pair, signal, EXPIRATION)

                    if status:
                        DAILY_TRADES += 1
                        send(f"✅ <b>OPERACIÓN ABIERTA</b> | Monto: ${BASE_AMOUNT:.2f} | Total hoy: {DAILY_TRADES}/{MAX_DAILY_TRADES}")

                        time.sleep(65)
                        try:
                            res = IQ_API.check_win_v4(trade_id)
                            if res is None:
                                send("⚠️ Resultado no pudo ser verificado.")
                                continue

                            if res < 0:
                                LOSS_STREAK += 1
                                LAST_LOSS = time.time()
                                send(f"❌ <b>OPERACIÓN PERDIDA</b> | -${abs(res):.2f}\nRacha: {LOSS_STREAK}/{MAX_LOSS_STREAK}")
                            else:
                                LOSS_STREAK = 0
                                send(f"✅ <b>OPERACIÓN GANADA</b> | +${res:.2f}\n_________________________")

                        except Exception as e:
                            logging.error(f"Verificación: {str(e)}")
                            send(f"⚠️ Error al verificar resultado: {str(e)}")
                            ensure_connection()
                    else:
                        send(f"❌ No se pudo ejecutar la operación en {pair}")

            # ==========================================
            # BUSCAR MEJORES SEÑALES EN TODOS LOS PARES
            # ==========================================
            if 10 <= sec <= 57:
                mejor_opcion = None
                mayor_fuerza = 0

                for pair in PAIRS:
                    df = get_df(IQ_API, pair)
                    if df is None:
                        continue

                    try:
                        from strategy import get_reversal_signal
                        resultado = get_reversal_signal(df, TOLERANCIA_NIVEL, VENTANA_NIVELES)
                        if resultado is not None:
                            signal, fuerza, tipo_nivel = resultado
                            if fuerza >= FUERZA_MINIMA and fuerza > mayor_fuerza:
                                mayor_fuerza = fuerza
                                mejor_opcion = (pair, signal, fuerza, tipo_nivel)
                    except Exception as e:
                        logging.error(f"Estrategia {pair}: {str(e)}")
                        continue

                if 55 <= sec <= 57 and mejor_opcion is not None:
                    SEÑAL_PENDIENTE = mejor_opcion
                    pair, signal, fuerza, tipo_nivel = mejor_opcion
                    send(f"""🔍 <b>SEÑAL DETECTADA</b>
💹 Activo: {pair}
📍 Nivel: {tipo_nivel.upper()}
💪 Fuerza: {fuerza}/100
⏳ Entrada: inicio de la siguiente vela""")

            time.sleep(0.05)

        except Exception as e:
            send(f"💥 Error en el sistema: {str(e)} | Reconectando...")
            logging.exception("Error en bucle principal")
            time.sleep(2)
            ensure_connection()

if __name__ == "__main__":
    # Verificación de variables obligatorias
    required = ["IQ_EMAIL", "IQ_PASSWORD", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        print(f"❌ Faltan variables de entorno: {', '.join(missing)}")
        sys.exit(1)

    # Verificar existencia del archivo de estrategia
    if not os.path.exists("strategy.py"):
        print("❌ Falta el archivo strategy.py en la misma carpeta")
        sys.exit(1)

    main()
