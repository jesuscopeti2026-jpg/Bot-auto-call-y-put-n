import os
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("Falta BOT_TOKEN en variables de entorno")

def start(update: Update, context: CallbackContext):
    update.message.reply_text("✅ Bot funcionando correctamente en Railway 🚀")

def ping(update: Update, context: CallbackContext):
    update.message.reply_text("🏓 Pong!")

def main():
    print("🚀 Iniciando bot...")

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("ping", ping))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
