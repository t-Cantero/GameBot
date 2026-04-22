import json
import logging
import sqlite3
from datetime import datetime, timezone

from telegram import (
    Update,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("TOKEN")

TETRIS_URL = "https://t-cantero.github.io/GameBot/"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────
# CONFIG
# ─────────────────────────────

VIDAS_INICIALES = 3
VIDAS_MAX = 3
HORAS_POR_VIDA = 2


# ─────────────────────────────
# DB
# ─────────────────────────────

def init_db():
    con = sqlite3.connect("gamebot.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            vidas INTEGER DEFAULT 3,
            partidas INTEGER DEFAULT 0,
            max_score INTEGER DEFAULT 0,
            ultima_perdida TEXT
        )
    """)
    con.commit()
    con.close()


def get_usuario(user_id, username=""):
    con = sqlite3.connect("gamebot.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("SELECT * FROM usuarios WHERE user_id=?", (user_id,))
    row = cur.fetchone()

    if not row:
        cur.execute(
            "INSERT INTO usuarios (user_id, username, vidas) VALUES (?, ?, ?)",
            (user_id, username, VIDAS_INICIALES)
        )
        con.commit()
        cur.execute("SELECT * FROM usuarios WHERE user_id=?", (user_id,))
        row = cur.fetchone()

    usuario = dict(row)
    con.close()

    return aplicar_regeneracion(usuario)


def aplicar_regeneracion(usuario):
    vidas = usuario["vidas"]
    ultima = usuario["ultima_perdida"]

    if not ultima or vidas >= VIDAS_MAX:
        return usuario

    ahora = datetime.now(timezone.utc)
    ultima_dt = datetime.fromisoformat(ultima)

    horas = (ahora - ultima_dt).total_seconds() / 3600
    recuperadas = int(horas // HORAS_POR_VIDA)

    if recuperadas <= 0:
        return usuario

    nuevas = min(VIDAS_MAX, vidas + recuperadas)

    nueva_ultima = None if nuevas >= VIDAS_MAX else ultima

    con = sqlite3.connect("gamebot.db")
    con.execute("""
        UPDATE usuarios
        SET vidas=?, ultima_perdida=?
        WHERE user_id=?
    """, (nuevas, nueva_ultima, usuario["user_id"]))
    con.commit()
    con.close()

    usuario["vidas"] = nuevas
    usuario["ultima_perdida"] = nueva_ultima

    return usuario


def set_vidas(user_id, vidas, timestamp=False):
    con = sqlite3.connect("gamebot.db")

    if timestamp:
        con.execute("""
            UPDATE usuarios
            SET vidas=?, ultima_perdida=?
            WHERE user_id=?
        """, (vidas, datetime.now(timezone.utc).isoformat(), user_id))
    else:
        con.execute("UPDATE usuarios SET vidas=? WHERE user_id=?", (vidas, user_id))

    con.commit()
    con.close()


def registrar_partida(user_id, score):
    con = sqlite3.connect("gamebot.db")
    con.execute("""
        UPDATE usuarios
        SET partidas = partidas + 1,
            max_score = MAX(max_score, ?)
        WHERE user_id=?
    """, (score, user_id))
    con.commit()
    con.close()


# ─────────────────────────────
# UI
# ─────────────────────────────

def teclado():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🎮 Jugar Tetris", callback_data="jugar")
    ]])


def webapp_btn():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Abrir juego", web_app=WebAppInfo(url=TETRIS_URL))
    ]])


# ─────────────────────────────
# START
# ─────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    usuario = get_usuario(user.id, user.username or user.first_name)

    texto = (
        f"¡Hola, {user.first_name}! 🎮\n\n"
        f"❤️ Vidas disponibles: {usuario['vidas']}\n"
        f"🏆 Mejor score: {usuario['max_score']}\n"
        f"🎯 Partidas: {usuario['partidas']}"
    )

    await update.message.reply_text(texto, reply_markup=teclado())

# ─────────────────────────────
# BOTÓN JUGAR (CONTROL REAL)
# ─────────────────────────────

async def jugar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user = q.from_user
    usuario = get_usuario(user.id)

    if usuario["vidas"] <= 0:
        await q.message.edit_text("💀 No tienes vidas")
        return

    nuevas = usuario["vidas"] - 1
    set_vidas(user.id, nuevas, timestamp=True)

    # MENSAJE PREVIO
    msg = await q.message.reply_text(
        f"🎮 Preparando juego...\n❤️ Vidas: {nuevas}",
        reply_markup=webapp_btn()
    )

    context.user_data["msg_id"] = msg.message_id


# ─────────────────────────────
# RESULTADO WEBAPP
# ─────────────────────────────

async def resultado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    try:
        data = json.loads(update.message.web_app_data.data)

        score = data.get("score", 0)
        tiempo = data.get("tiempo", "00:00")
        nivel = data.get("nivel", 1)
        lineas = data.get("lineas", 0)

    except Exception as e:
        logger.error(f"WebApp error: {e}")
        return

    # 1. Guardar partida (IMPORTANTE: puedes ampliar esto si quieres)
    registrar_partida(user.id, score)

    # 2. RECARGAR usuario DESPUÉS de actualizar BD
    usuario = get_usuario(user.id, user.username or user.first_name)

    # 3. MENSAJE FINAL
    texto = (
        f"🎮 Partida terminada\n\n"
        f"⭐ Score: {score}\n"
        f"📈 Nivel: {nivel}\n"
        f"🧩 Líneas: {lineas}\n\n"
        f"❤️ Vidas: {usuario['vidas']}\n"
        f"🏆 Max score: {usuario['max_score']}\n"
        f"🎯 Partidas: {usuario['partidas']}"
    )

    chat_id = update.effective_chat.id
    msg_id = context.user_data.get("msg_id")

    try:
        if msg_id:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=texto
            )
        else:
            await update.message.reply_text(texto)

    except Exception as e:
        logger.error(f"edit error: {e}")

    context.user_data.pop("msg_id", None)

# ─────────────────────────────
# MAIN
# ─────────────────────────────

async def restaurar_vidas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    usuario = get_usuario(update.effective_user.id, update.effective_user.username)

    if usuario["vidas"] <= 0:
        con = sqlite3.connect("gamebot.db")

        con.execute("""
                UPDATE usuarios
                SET vidas = 3
                WHERE user_id=?
            """, (user_id,))
        con.commit()
        con.close()

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(jugar, pattern="jugar"))
    app.add_handler(CommandHandler("restaurar", restaurar_vidas))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, resultado))

    app.run_polling()


if __name__ == "__main__":
    main()