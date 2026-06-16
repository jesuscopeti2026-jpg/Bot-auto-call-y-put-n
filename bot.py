import time
import os
import pandas as pd
import logging
from threading import Thread, Lock
from iqoptionapi.stable_api import IQ_Option
from telegram import Bot
from telegram.error import TelegramError
from strategy import get_reversal_signal

# --------------------------
# CONFIGURACIÓN
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
REINTENTOS = 5
ESPERA = 0.2
MAX_DESFASE_PERMITIDO = 1
REINTENTOS_CONEXION = 10  # ✅ Reintentos automáticos para conectar
ESPERA_REINTENTO = 3      # ✅ Espera entre intentos

# Tiempos sincronizados
SEG_CONEXION = 53
SEG_DETECCION = 54
SEG_INICIO = 56
SEG_FIN = 59

# Activos correctos
ACTIVOS = [
    "EURUSD-OTC",
    "GBPUSD-OTC",
    "EURJPY-OTC",
    "USDCHF-OTC",
    "AUDCAD-OTC"
]

MAX_OPER = 15
OPERACIONES_C1 = 0
OPERACIONES_C2 = 0

BOT_ACTIVO = False
ULTIMA_VELA = None
YA_OPERO = {}
CUENTA_ANALISIS = 1
IQ1 = None
IQ2 = None
lock = Lock()

# Variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
IQ_EMAIL_1 = os.getenv("IQ_EMAIL_1", "")
IQ_PASSWORD_1 = os.getenv("IQ_PASSWORD_1", "")
IQ_EMAIL_2 = os.getenv("IQ_EMAIL_2", "")
IQ_PASSWORD_2 = os.getenv("IQ_PASSWORD_2", "")

# --------------------------
# TELEGRAM SIN CONFLICTOS
# --------------------------
def enviar_telegram(texto):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Faltan credenciales de Telegram")
        return
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=int(TELEGRAM_CHAT_ID), text=texto, parse_mode="HTML", disable_web_page_preview=True)
        logger.info(f"📤 Notificación enviada: {texto[:80]}...")
    except Exception as e:
        logger.error(f"❌ Error enviando a Telegram: {str(e)}")

# --------------------------
# CONEXIÓN DE CUENTAS CON REINTENTOS
# --------------------------
def conectar_cuenta(email, contraseña, nombre):
    try:
        logger.info(f"🔄 Conectando {nombre}...")
        if not email or not contraseña:
            logger.error(f"❌ {nombre}: Faltan credenciales")
            return None, 0

        iq = IQ_Option(email, contraseña)
        conectado, motivo = iq.connect()
        if conectado:
            time.sleep(0.5)
            iq.change_balance("PRACTICE")
            saldo = iq.get_balance()
            saldo = round(saldo, 2) if saldo is not None else 0  # ✅ Evita error con None
            logger.info(f"✅ {nombre} conectado | Saldo: ${saldo}")
            return iq, saldo
        else:
            logger.error(f"❌ {nombre} falló: {motivo}")
            return None, 0
    except Exception as e:
        logger.error(f"❌ Error en {nombre}: {str(e)}")
        return None, 0

def conectar_ambas_cuentas():
    global IQ1, IQ2
    enviar_telegram("🔄 CONECTANDO AMBAS CUENTAS...")
    intento = 0

    # ✅ Bucle de reintentos hasta conectar ambas
    while intento < REINTENTOS_CONEXION:
        intento += 1
        logger.info(f"🔁 Intento de conexión {intento}/{REINTENTOS_CONEXION}")

        res1 = {}
        res2 = {}

        hilo1 = Thread(target=lambda: res1.update(dict(zip(["iq", "saldo"], conectar_cuenta(IQ_EMAIL_1, IQ_PASSWORD_1, "CUENTA 1")))))
        hilo2 = Thread(target=lambda: res2.update(dict(zip(["iq", "saldo"], conectar_cuenta(IQ_EMAIL_2, IQ_PASSWORD_2, "CUENTA 2")))))

        hilo1.start()
        hilo2.start()
        hilo1.join()
        hilo2.join()

        IQ1 = res1.get("iq")
        IQ2 = res2.get("iq")
        saldo1 = res1.get("saldo", 0)
        saldo2 = res2.get("saldo", 0)

        if IQ1 and IQ2:
            enviar_telegram(
                f"✅ CONEXIÓN EXITOSA DESPUÉS DE {intento} INTENTO(S)\n"
                f"🔹 Cuenta 1: ${saldo1}\n"
                f"🔹 Cuenta 2: ${saldo2}\n"
                f"🚀 Analizando señales..."
            )
            return True
        else:
            faltan = []
            if not IQ1: faltan.append("Cuenta 1")
            if not IQ2: faltan.append("Cuenta 2")
            logger.warning(f"⚠️ No conectó: {', '.join(faltan)}. Reintentando en {ESPERA_REINTENTO}s...")
            time.sleep(ESPERA_REINTENTO)

    # Si se agotan los intentos
    enviar_telegram(f"❌ NO SE PUDO CONECTAR DESPUÉS DE {REINTENTOS_CONEXION} INTENTOS")
    return False

def desconectar_ambas_cuentas():
    global IQ1, IQ2
    if IQ1:
        try:
            IQ1.disconnect()
        except:
            pass
    if IQ2:
        try:
            IQ2.disconnect()
        except:
            pass
    IQ1 = None
    IQ2 = None
    enviar_telegram("⏹️ Cuentas desconectadas. Bot detenido.")

# --------------------------
# DATOS DE MERCADO
# --------------------------
def obtener_velas(iq, activo):
    try:
        if not iq or not iq.check_connect():
            return None
        velas = iq.get_candles(activo, VELA, 50, time.time())
        if not velas or len(velas) < 30:
            return None
        df = pd.DataFrame(velas)
        df.rename(columns={"max": "high", "min": "low"}, inplace=True)
        df[["open", "close", "high", "low"]] = df[["open", "close", "high", "low"]].astype(float)
        return df
    except Exception as e:
        logger.warning(f"⚠️ Velas {activo}: {str(e)}")
        return None

# --------------------------
# EJECUTAR ORDEN - MISMA ENTRADA EN AMBAS
# --------------------------
def ejecutar_orden(iq, nombre, activo, direccion, vela, resultado):
    clave = f"{nombre}_{vela}"
    if YA_OPERO.get(clave, False):
        resultado["ok"] = False
        resultado["razon"] = "Ya operó en esta vela"
        return

    dir_final = direccion
    exito = False
    saldo = None
    id_op = None

    for intento in range(REINTENTOS):
        try:
            if not iq.check_connect():
                iq.connect()
                time.sleep(0.1)
            ok, id_op = iq.buy(MONTO, activo, dir_final, EXPIRACION)
            if ok and id_op > 0:
                time.sleep(0.3)
                saldo_bruto = iq.get_balance()
                saldo = round(saldo_bruto, 2) if saldo_bruto is not None else 0
                exito = True
                logger.info(f"✅ {nombre} | ID: {id_op} | {dir_final.upper()} | Saldo: ${saldo}")
                break
            time.sleep(ESPERA)
        except Exception as e:
            logger.warning(f"⚠️ Intento {intento+1} {nombre}: {str(e)}")
            time.sleep(ESPERA)

    if exito:
        with lock:
            YA_OPERO[clave] = True
        resultado.update({
            "ok": True,
            "direccion": dir_final.upper(),
            "id": id_op,
            "saldo": saldo
        })
    else:
        resultado["ok"] = False
        resultado["razon"] = "No se pudo ejecutar"

# --------------------------
# BUCLE PRINCIPAL
# --------------------------
def bucle_principal():
    global BOT_ACTIVO, ULTIMA_VELA, OPERACIONES_C1, OPERACIONES_C2, CUENTA_ANALISIS, IQ1, IQ2

    while BOT_ACTIVO:
        try:
            # ✅ Si se pierde conexión, reintenta automáticamente
            if not IQ1 or not IQ2:
                logger.warning("⚠️ Conexión perdida, reintentando...")
                if not conectar_ambas_cuentas():
                    BOT_ACTIVO = False
                    break
                continue

            ts_servidor = IQ1.get_server_timestamp()
            segundos = ts_servidor % 60
            vela_actual = ts_servidor // 60

            if OPERACIONES_C1 >= MAX_OPER or OPERACIONES_C2 >= MAX_OPER:
                enviar_telegram(
                    f"✅ PROCESO FINALIZADO ✅\n"
                    f"🔹 Cuenta 1: {OPERACIONES_C1}/{MAX_OPER}\n"
                    f"🔹 Cuenta 2: {OPERACIONES_C2}/{MAX_OPER}"
                )
                BOT_ACTIVO = False
                desconectar_ambas_cuentas()
                break

            if vela_actual != ULTIMA_VELA:
                ULTIMA_VELA = vela_actual
                YA_OPERO.clear()
                logger.info(f"🔄 Nueva vela iniciada: {vela_actual}")

            senal = None
            if segundos == SEG_DETECCION:
                mejor = None
                fuerza_max = 0
                iq_ana = IQ1 if CUENTA_ANALISIS == 1 else IQ2
                CUENTA_ANALISIS = 2 if CUENTA_ANALISIS == 1 else 1
                for act in ACTIVOS:
                    df = obtener_velas(iq_ana, act)
                    if not df: continue
                    sig = get_reversal_signal(df)
                    if sig:
                        d, f, _ = sig
                        if f >= FUERZA_MIN and f > fuerza_max:
                            fuerza_max = f
                            mejor = (act, d, f)
                if mejor:
                    act, d, f = mejor
                    senal = (act, d, f)
                    enviar_telegram(f"🔔 SEÑAL DETECTADA\n📈 Activo: {act}\n➡️ Dirección: {d.upper()}\n💪 Fuerza: {f}%")

            if senal and SEG_INICIO <= segundos <= SEG_FIN:
                act, d, f = senal
                res1 = {"ok": False}
                res2 = {"ok": False}

                enviar_telegram("⚡ ENVIANDO MISMA OPERACIÓN A AMBAS CUENTAS...")

                hilo1 = Thread(target=ejecutar_orden, args=(IQ1, "CUENTA 1", act, d, vela_actual, res1))
                hilo2 = Thread(target=ejecutar_orden, args=(IQ2, "CUENTA 2", act, d, vela_actual, res2))
                hilo1.start()
                hilo2.start()
                hilo1.join()
                hilo2.join()

                if res1["ok"] and res2["ok"]:
                    OPERACIONES_C1 += 1
                    OPERACIONES_C2 += 1
                    enviar_telegram(
                        f"✅ OPERACIÓN EJECUTADA EN AMBAS CUENTAS\n"
                        f"🔹 Cuenta 1: {res1['direccion']} | ID: {res1['id']} | Saldo: ${res1['saldo']}\n"
                        f"🔹 Cuenta 2: {res2['direccion']} | ID: {res2['id']} | Saldo: ${res2['saldo']}\n"
                        f"📊 Progreso: {OPERACIONES_C1}/{MAX_OPER}"
                    )
                else:
                    motivo = ""
                    if not res1["ok"]: motivo += f"Cuenta 1: {res1.get('razon','Error')} | "
                    if not res2["ok"]: motivo += f"Cuenta 2: {res2.get('razon','Error')}"
                    enviar_telegram(f"❌ OPERACIÓN FALLIDA\n{motivo}")

                senal = None

            time.sleep(0.1)

        except Exception as e:
            logger.error(f"💥 Error en bucle: {str(e)}")
            enviar_telegram(f"⚠️ Error en el ciclo: {str(e)}")
            time.sleep(2)

# --------------------------
# INICIO
# --------------------------
if __name__ == "__main__":
    # Limpieza de conflictos previos
    try:
        if TELEGRAM_TOKEN:
            Bot(token=TELEGRAM_TOKEN).delete_webhook(drop_pending_updates=True)
    except:
        pass

    logger.info("🤖 Bot iniciado, modo sin conflictos activo")
    enviar_telegram("🤖 BOT LISTO ✅\n🔄 Intentando conectar cuentas automáticamente...")

    # Inicio automático con reintentos
    if conectar_ambas_cuentas():
        BOT_ACTIVO = True
        Thread(target=bucle_principal, daemon=True).start()
    else:
        enviar_telegram("❌ No se pudo iniciar. Revisa credenciales.")
