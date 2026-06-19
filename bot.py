from iqoptionapi.stable_api import IQ_Option
import time, logging, math, threading, os
from dotenv import load_dotenv

# ------------------ CARGA VARIABLES + CONFIGURACIÓN LOGS ------------------
load_dotenv()  # Compatibilidad con .env local y Railway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[logging.FileHandler("bot_logs.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ✅ LECTURA EXACTA DE VARIABLES DE RAILWAY
IQ_EMAIL = os.getenv("IQ_EMAIL", "").strip()
IQ_PASS = os.getenv("IQ_PASS", "").strip()
IQ_BALANCE = os.getenv("IQ_BALANCE", "demo").strip()
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", 1.0))
MIN_PAYOUT = int(os.getenv("MIN_PAYOUT", 70))

CUENTA = {
    "email": IQ_EMAIL,
    "pass": IQ_PASS,
    "alias": "CUENTA-PRINCIPAL",
    "tipo": IQ_BALANCE
}

# ACTIVOS E INDICADORES
ASSETS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "EURGBP"]
TIMEFRAME = 60
EXPIRY = 1
RSI_PERIOD = 7
RSI_SOBRE = 75
RSI_SOBREV = 25
ADX_PERIOD = 14
ADX_FUERTE = 30
MAX_VELA_RANGO = 0.012
MIN_RANGO = 0.001

# TELEGRAM (OPCIONAL)
TELEGRAM_TOKEN = os.getenv("TG_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
bot_tg = None
try:
    import telebot
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        bot_tg = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="HTML")
except ImportError:
    logger.info("ℹ️ Sin notificaciones Telegram — módulo no instalado/activado")
# -----------------------------------------------------

def notificar(texto):
    logger.info(texto)
    if bot_tg:
        try: bot_tg.send_message(TELEGRAM_CHAT_ID, texto[:4000])
        except Exception as e: logger.warning(f"Telegram: {e}")

def conectar_cuenta(datos_cuenta):
    alias = datos_cuenta["alias"]
    # ✅ VERIFICACIÓN ANTES DE INTENTAR CONEXIÓN
    if not datos_cuenta["email"] or not datos_cuenta["pass"]:
        logger.error("❌ FALTAN VARIABLES: IQ_EMAIL o IQ_PASS NO DEFINIDAS EN RAILWAY")
        time.sleep(10)
        return None
    while True:
        try:
            api = IQ_Option(datos_cuenta["email"], datos_cuenta["pass"])
            api.timeout = 20  # Evita corte rápido por red
            ok, razon = api.connect()
            if ok:
                api.change_balance(datos_cuenta["tipo"])
                saldo = api.get_balance()
                notificar(f"✅ {alias} CONECTADO | Saldo: ${saldo:.2f}")
                return api
            if "invalid_credentials" in str(razon):
                logger.warning(f"{alias} ⚠️ USUARIO/CLAVE INCORRECTOS — revisa variables")
            elif "timed out" in str(razon).lower():
                logger.warning(f"{alias} ⏱️ TIEMPO DE ESPERA AGOTADO — red lenta/bloqueada")
            else:
                logger.warning(f"{alias} Fallo conexión: {razon}")
            logger.info("→ Reintento en 10 segundos...")
        except Exception as e:
            logger.error(f"{alias} Error: {e} → reintento 10s")
        time.sleep(10)

def calcular_rsi(precios, periodo):
    gan, per = [], []
    for i in range(1, len(precios)):
        dif = precios[i] - precios[i-1]
        gan.append(dif if dif>0 else 0)
        per.append(-dif if dif<0 else 0)
    if len(gan) < periodo: return 50
    media_gan = sum(gan[-periodo:])/periodo
    media_per = sum(per[-periodo:])/periodo
    if media_per == 0: return 100
    rs = media_gan / media_per
    return 100 - (100/(1+rs))

def calcular_adx(velas, periodo):
    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(velas)):
        hh = velas[i]["max"] - velas[i-1]["max"]
        ll = velas[i-1]["min"] - velas[i]["min"]
        plus_dm.append(hh if hh>ll and hh>0 else 0)
        minus_dm.append(ll if ll>hh and ll>0 else 0)
        tr.append(max(velas[i]["max"]-velas[i]["min"],
                      abs(velas[i]["max"]-velas[i-1]["close"]),
                      abs(velas[i]["min"]-velas[i-1]["close"])))
    if len(tr) < periodo: return 50
    tr_suav = sum(tr[-periodo:])/periodo
    plus_suav = sum(plus_dm[-periodo:])/periodo
    minus_suav = sum(minus_dm[-periodo:])/periodo
    if tr_suav == 0: return 0
    plus_di = 100*(plus_suav/tr_suav)
    minus_di = 100*(minus_suav/tr_suav)
    if plus_di+minus_di == 0: return 0
    dx = 100*abs(plus_di-minus_di)/(plus_di+minus_di)
    return dx

def es_agotamiento(vela):
    rango = (vela["max"] - vela["min"])/vela["close"]
    cuerpo = abs(vela["close"] - vela["open"])/vela["close"]
    return rango > MAX_VELA_RANGO or rango < MIN_RANGO or cuerpo/rango < 0.15

def obtener_velas_seguro(api, activo, cantidad=30):
    try:
        if not api or not api.check_connect(): return None
        return api.get_candles(activo, TIMEFRAME, cantidad, time.time())
    except Exception as e:
        logger.error(f"Error velas {activo}: {e}")
        return None

def obtener_señal(api, activo):
    velas = obtener_velas_seguro(api, activo)
    if not velas or len(velas)<20: return None
    if any(es_agotamiento(v) for v in velas[-3:]): return None
    precios_cierre = [v["close"] for v in velas]
    rsi = calcular_rsi(precios_cierre, RSI_PERIOD)
    adx = calcular_adx(velas, ADX_PERIOD)
    ult = velas[-1]; ant = velas[-2]
    if adx > ADX_FUERTE: return None
    if rsi < RSI_SOBREV and ult["close"] > ult["open"] and ant["close"] < ant["open"]:
        return "call"
    if rsi > RSI_SOBRE and ult["close"] < ult["open"] and ant["close"] > ant["open"]:
        return "put"
    return None

def ciclo_principal():
    alias = CUENTA["alias"]
    api = conectar_cuenta(CUENTA)
    while True:
        try:
            if not api or not api.check_connect():
                notificar(f"⚠️ {alias} DESCONECTADO — reconectando...")
                api = conectar_cuenta(CUENTA)
                continue
            for activo in ASSETS:
                try:
                    estado = api.get_all_open_time()["turbo"].get(activo, {})
                    if not estado.get("open", False): continue
                    pago = api.get_payout(activo, "turbo")
                    if pago < MIN_PAYOUT: continue
                    direccion = obtener_señal(api, activo)
                    if direccion:
                        ok_op, id_op = api.buy(TRADE_AMOUNT, activo, direccion, EXPIRY)
                        if ok_op:
                            notificar(f"📈 {alias} | {activo} | {direccion.upper()} | Pago: {pago}%")
                            res = api.check_win_v4(id_op, 10)
                            if res[1]>0: notificar(f"🟢 GANANCIA: +${res[1]:.2f}")
                            elif res[1]<0: notificar(f"🔴 PÉRDIDA: -${abs(res[1]):.2f}")
                    time.sleep(1.2)
                except Exception as e:
                    logger.error(f"{alias} {activo}: {e}")
                    time.sleep(3)
            time.sleep(8)
        except Exception as e:
            logger.error(f"{alias} Error ciclo: {e} → reinicio")
            api = conectar_cuenta(CUENTA)
            time.sleep(5)

if __name__ == "__main__":
    notificar("🚀 BOT LISTO — 1 CUENTA + VARIABLES + TIEMPO DE ESPERA AJUSTADO")
    ciclo_principal()
