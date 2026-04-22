import json
import logging
import sqlite3
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

from telegram import (
    Update,
    WebAppInfo,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

load_dotenv()
TOKEN = os.getenv("TOKEN")
TETRIS_URL = "https://t-cantero.github.io/GameBot/"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────
VIDAS_INICIALES = 3
VIDAS_MAX = 3
HORAS_POR_VIDA = 2


# ─────────────────────────────
# BASE DE DATOS
# ─────────────────────────────
def init_db():
    with sqlite3.connect("gamebot.db") as con:
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


def get_usuario(user_id, username=""):
    with sqlite3.connect("gamebot.db") as con:
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
    return aplicar_regeneracion(usuario)


def aplicar_regeneracion(usuario):
    vidas = usuario["vidas"]
    ultima = usuario["ultima_perdida"]

    if not ultima or vidas >= VIDAS_MAX:
        return usuario

    ahora = datetime.now(timezone.utc)
    ultima_dt = datetime.fromisoformat(ultima)

    segundos_transcurridos = (ahora - ultima_dt).total_seconds()
    segundos_por_vida = HORAS_POR_VIDA * 3600
    recuperadas = int(segundos_transcurridos // segundos_por_vida)

    if recuperadas > 0:
        nuevas = min(VIDAS_MAX, vidas + recuperadas)
        if nuevas >= VIDAS_MAX:
            nueva_ultima = None
        else:
            nueva_ultima = (ultima_dt + (recuperadas * segundos_por_vida)).isoformat()

        with sqlite3.connect("gamebot.db") as con:
            con.execute("UPDATE usuarios SET vidas=?, ultima_perdida=? WHERE user_id=?",
                        (nuevas, nueva_ultima, usuario["user_id"]))

        usuario["vidas"] = nuevas
        usuario["ultima_perdida"] = nueva_ultima

    return usuario


def actualizar_vidas_post_partida(user_id, vidas_actuales):
    nuevas = max(0, vidas_actuales - 1)
    ahora = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect("gamebot.db") as con:
        if vidas_actuales == VIDAS_MAX:
            con.execute("UPDATE usuarios SET vidas=?, ultima_perdida=? WHERE user_id=?",
                        (nuevas, ahora, user_id))
        else:
            con.execute("UPDATE usuarios SET vidas=? WHERE user_id=?", (nuevas, user_id))


def registrar_partida(user_id, score):
    with sqlite3.connect("gamebot.db") as con:
        con.execute("""
            UPDATE usuarios
            SET partidas = partidas + 1,
                max_score = MAX(max_score, ?)
            WHERE user_id=?
        """, (score, user_id))


# ─────────────────────────────
# UI (TECLADO CON PARÁMETRO DE VIDAS)
# ─────────────────────────────
def teclado_principal(vidas):
    # Pasamos las vidas como parámetro en la URL
    url_con_vidas = f"{TETRIS_URL}?vidas={vidas}"
    return ReplyKeyboardMarkup([
        [KeyboardButton("🎮 Jugar Tetris", web_app=WebAppInfo(url=url_con_vidas))]
    ], resize_keyboard=True)


# ─────────────────────────────
# HANDLERS
# ─────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    usuario = get_usuario(user.id, user.username or user.first_name)

    texto = (
        f"¡Hola, {user.first_name}! 🎮\n\n"
        f"❤️ Vidas: **{usuario['vidas']}**\n"
        f"🏆 Récord: **{usuario['max_score']}**\n\n"
        "Toca el botón de abajo para jugar."
    )
    await update.message.reply_text(texto, reply_markup=teclado_principal(usuario['vidas']), parse_mode="Markdown")


async def resultado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        data = json.loads(update.message.web_app_data.data)
        score = data.get("score", 0)
    except Exception as e:
        logger.error(f"Error parseando datos: {e}")
        return

    usuario = get_usuario(user.id)

    if usuario["vidas"] > 0:
        actualizar_vidas_post_partida(user.id, usuario["vidas"])
        registrar_partida(user.id, score)

        usuario_upd = get_usuario(user.id)

        texto = (
            f"🕹 **Partida registrada**\n\n"
            f"⭐ Score: {score}\n"
            f"❤️ Vidas restantes: {usuario_upd['vidas']}\n"
            f"🏆 Máximo: {usuario_upd['max_score']}"
        )
        # Actualizamos el teclado con las nuevas vidas
        await update.message.reply_text(texto, reply_markup=teclado_principal(usuario_upd['vidas']),
                                        parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No tenías vidas suficientes.", reply_markup=teclado_principal(0))


async def restaurar_vidas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    with sqlite3.connect("gamebot.db") as con:
        con.execute("UPDATE usuarios SET vidas = 3, ultima_perdida = NULL WHERE user_id=?", (user_id,))
    await update.message.reply_text("❤️ Vidas restauradas.", reply_markup=teclado_principal(3))


# ─────────────────────────────
# MAIN
# ─────────────────────────────
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("restaurar", restaurar_vidas))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, resultado))

    app.run_polling()


if __name__ == "__main__":
    main()