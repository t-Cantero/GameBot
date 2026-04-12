import json
import logging
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackContext
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("TOKEN")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TETRIS_URL = "https://t-cantero.github.io/GameBot/"

async def tetris(update: Update, context: CallbackContext):
    keyboard = [[
        InlineKeyboardButton(
            text = "🎮 Jugar Tetris",
            web_app=WebAppInfo(url=TETRIS_URL)
        )
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "¡Bienvenido al Tetris! 🕹️\nPulsa el botón para empezar:",
        reply_markup=reply_markup
    )
async def resultado_tetris(update: Update, context: CallbackContext):
    try:
        data = json.loads(update.message.web_app_data.data)

        puntuacion = data.get("score",0)
        tiempo = data.get("tiempo","00:00")
        nivel = data.get("nivel",1)
        lineas = data.get("lineas",0)

        usuario = update.effective_user.first_name

        await update.message.reply_text(
            f"🎮 *Partida de {usuario}*\n"
            f"━━━━━━━━━━━━━━\n"
            f"⭐ Score:  `{puntuacion}`\n"
            f"⏱️ Tiempo: `{tiempo}`\n"
            f"📈 Nivel:  `{nivel}`\n"
            f"✅ Líneas: `{lineas}`\n"
            f"━━━━━━━━━━━━━━\n"
            f"¡Bien jugado! Usa /jugar para otra partida.",
            parse_mode="Markdown"
        )

        logger.info(f"Resultado de {usuario}: puntuación={puntuacion}, tiempo={tiempo}, nivel={nivel}, lineas={lineas}")
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error procesando datos del juego: {e}")
        await update.message.reply_text("❌ Error al recibir los datos del juego.")
async def jugar(update: Update, context: CallbackContext):
    """Alias de /tetris para volver a jugar rápido"""
    await tetris(update, context)
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("tetris", tetris))
    app.add_handler(CommandHandler("jugar", jugar))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, resultado_tetris))

    logger.info("Bot iniciado...")
    app.run_polling()
if __name__ == "__main__":
    main()