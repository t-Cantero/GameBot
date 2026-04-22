import json
import logging
import sqlite3
from datetime import datetime
from telegram import Update, WebAppInfo, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    PreCheckoutQueryHandler, filters, ContextTypes
)
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN= os.getenv("TOKEN")
TETRIS_URL = "https://t-cantero.github.io/GameBot/"

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

VIDAS_INICIALES = 3
VIDAS_MAX       = 3      # máximo al que regenera (las compradas con Stars no tienen límite)
HORAS_POR_VIDA  = 2      # 1 vida cada 2 horas

# ── Paquetes de vidas ────────────────────────────────────────────────────────
PAQUETES = {
    "vidas_1": {"vidas": 1, "stars": 10, "label": "1 vida"},
    "vidas_3": {"vidas": 3, "stars": 25, "label": "3 vidas"},
    "vidas_5": {"vidas": 5, "stars": 40, "label": "5 vidas"},
}

# ════════════════════════════════════════════════════════════════════════════
#  BASE DE DATOS (SQLite)
# ════════════════════════════════════════════════════════════════════════════

def init_db():
    """Crea la tabla si no existe. Añade ultima_perdida para la regeneración."""
    con = sqlite3.connect("gamebot.db")
    con.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            user_id        INTEGER PRIMARY KEY,
            username       TEXT,
            vidas          INTEGER DEFAULT 3,
            partidas       INTEGER DEFAULT 0,
            max_score      INTEGER DEFAULT 0,
            ultima_perdida TEXT    DEFAULT NULL
        )
    """)
    # Por si la tabla ya existía sin la columna nueva (migracion suave)
    try:
        con.execute("ALTER TABLE usuarios ADD COLUMN ultima_perdida TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # La columna ya existe, no pasa nada
    con.commit()
    con.close()


def get_usuario(user_id: int, username: str = "") -> dict:
    """Devuelve la fila del usuario aplicando regeneración, creándola si no existe."""
    con = sqlite3.connect("gamebot.db")
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
    row = cur.fetchone()

    if row is None:
        cur.execute(
            "INSERT INTO usuarios (user_id, username, vidas) VALUES (?, ?, ?)",
            (user_id, username, VIDAS_INICIALES)
        )
        con.commit()
        cur.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
        row = cur.fetchone()

    usuario = dict(row)
    con.close()

    # Aplicar regeneración antes de devolver
    usuario = aplicar_regeneracion(usuario)
    return usuario


def aplicar_regeneracion(usuario: dict) -> dict:
    """
    Calcula cuántas vidas se han regenerado desde ultima_perdida
    y actualiza la BD si corresponde.
    Solo regenera hasta VIDAS_MAX (las extras compradas con Stars no se tocan).
    """
    vidas          = usuario["vidas"]
    ultima_perdida = usuario["ultima_perdida"]

    # Si ya tiene el máximo o nunca perdió una vida, nada que hacer
    if vidas >= VIDAS_MAX or not ultima_perdida:
        return usuario

    ahora         = datetime.utcnow()
    entonces      = datetime.fromisoformat(ultima_perdida)
    horas_pasadas = (ahora - entonces).total_seconds() / 3600

    vidas_recuperadas = int(horas_pasadas // HORAS_POR_VIDA)

    if vidas_recuperadas <= 0:
        return usuario

    nuevas_vidas = min(VIDAS_MAX, vidas + vidas_recuperadas)

    # Si ya llegó al máximo, limpiar ultima_perdida (no sigue contando)
    nueva_ultima = None if nuevas_vidas >= VIDAS_MAX else ultima_perdida

    con = sqlite3.connect("gamebot.db")
    con.execute(
        "UPDATE usuarios SET vidas = ?, ultima_perdida = ? WHERE user_id = ?",
        (nuevas_vidas, nueva_ultima, usuario["user_id"])
    )
    con.commit()
    con.close()

    usuario["vidas"]          = nuevas_vidas
    usuario["ultima_perdida"] = nueva_ultima
    return usuario


def set_vidas(user_id: int, vidas: int, guardar_timestamp: bool = False):
    """Actualiza las vidas. Si guardar_timestamp=True, guarda la hora actual como ultima_perdida."""
    con = sqlite3.connect("gamebot.db")
    if guardar_timestamp:
        con.execute(
            "UPDATE usuarios SET vidas = ?, ultima_perdida = ? WHERE user_id = ?",
            (vidas, datetime.utcnow().isoformat(), user_id)
        )
    else:
        con.execute("UPDATE usuarios SET vidas = ? WHERE user_id = ?", (vidas, user_id))
    con.commit()
    con.close()


def add_vidas(user_id: int, cantidad: int):
    """Añade vidas (compra con Stars). No toca ultima_perdida."""
    con = sqlite3.connect("gamebot.db")
    con.execute("UPDATE usuarios SET vidas = vidas + ? WHERE user_id = ?", (cantidad, user_id))
    con.commit()
    con.close()


def registrar_partida(user_id: int, score: int):
    con = sqlite3.connect("gamebot.db")
    con.execute("""
        UPDATE usuarios
        SET partidas  = partidas + 1,
            max_score = MAX(max_score, ?)
        WHERE user_id = ?
    """, (score, user_id))
    con.commit()
    con.close()


def tiempo_proxima_vida(usuario: dict) -> str:
    """Devuelve un string con el tiempo restante para la siguiente vida."""
    ultima_perdida = usuario["ultima_perdida"]
    if not ultima_perdida:
        return ""

    entonces      = datetime.fromisoformat(ultima_perdida)
    ahora         = datetime.utcnow()
    horas_pasadas = (ahora - entonces).total_seconds() / 3600
    horas_hasta_proxima = HORAS_POR_VIDA - (horas_pasadas % HORAS_POR_VIDA)

    horas   = int(horas_hasta_proxima)
    minutos = int((horas_hasta_proxima - horas) * 60)

    if horas > 0:
        return f"{horas}h {minutos}min"
    return f"{minutos}min"


# ════════════════════════════════════════════════════════════════════════════
#  HELPERS DE UI
# ════════════════════════════════════════════════════════════════════════════

def teclado_jugar(tiene_vidas: bool):
    if tiene_vidas:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("🎮 Jugar Tetris", web_app=WebAppInfo(url=TETRIS_URL))
        ]])
    else:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 Jugar Tetris", web_app=WebAppInfo(url=TETRIS_URL))],
            [InlineKeyboardButton("⭐ 1 vida  — 10 Stars", callback_data="comprar_vidas_1")],
            [InlineKeyboardButton("⭐ 3 vidas — 25 Stars", callback_data="comprar_vidas_3")],
            [InlineKeyboardButton("⭐ 5 vidas — 40 Stars", callback_data="comprar_vidas_5")],
        ])


def teclado_sin_vidas():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⭐ 1 vida  — 10 Stars", callback_data="comprar_vidas_1")],
        [InlineKeyboardButton("⭐ 3 vidas — 25 Stars", callback_data="comprar_vidas_3")],
        [InlineKeyboardButton("⭐ 5 vidas — 40 Stars", callback_data="comprar_vidas_5")],
    ])


# ════════════════════════════════════════════════════════════════════════════
#  HANDLERS
# ════════════════════════════════════════════════════════════════════════════

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    usuario = get_usuario(user.id, user.username or user.first_name)
    vidas   = usuario["vidas"]

    # Mostrar tiempo para próxima vida si no tiene el máximo
    info_regeneracion = ""
    if 0 < vidas < VIDAS_MAX and usuario["ultima_perdida"]:
        tiempo = tiempo_proxima_vida(usuario)
        info_regeneracion = f"\n⏳ Próxima vida en: *{tiempo}*"
    elif vidas == 0 and usuario["ultima_perdida"]:
        tiempo = tiempo_proxima_vida(usuario)
        info_regeneracion = f"\n⏳ Primera vida en: *{tiempo}*"

    texto = (
        f"¡Hola, {user.first_name}! 🎮\n\n"
        f"❤️ Vidas disponibles: *{vidas}*"
        f"{info_regeneracion}\n"
        f"🏆 Mejor score: *{usuario['max_score']}*\n"
        f"🎯 Partidas jugadas: *{usuario['partidas']}*"
    )

    await update.message.reply_text(
        texto,
        parse_mode="Markdown",
        reply_markup=teclado_jugar(vidas > 0)
    )


async def jugar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def vidas_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user    = update.effective_user
    usuario = get_usuario(user.id, user.username or user.first_name)
    vidas   = usuario["vidas"]

    info_regeneracion = ""
    if vidas < VIDAS_MAX and usuario["ultima_perdida"]:
        tiempo = tiempo_proxima_vida(usuario)
        info_regeneracion = f"\n⏳ Próxima vida en: *{tiempo}*"

    await update.message.reply_text(
        f"❤️ Tienes *{vidas}* vida(s).{info_regeneracion}\n\n"
        f"Puedes comprar más con Stars:",
        parse_mode="Markdown",
        reply_markup=teclado_sin_vidas()
    )


# ── Resultado del juego (MiniApp) ────────────────────────────────────────────

async def resultado_tetris(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        data   = json.loads(update.message.web_app_data.data)
        score  = data.get("score", 0)
        tiempo = data.get("tiempo", "00:00")
        nivel  = data.get("nivel", 1)
        lineas = data.get("lineas", 0)
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Error procesando datos del juego: {e}")
        await update.message.reply_text("❌ Error al recibir los datos del juego.")
        return

    usuario     = get_usuario(user.id, user.username or user.first_name)
    vidas_antes = usuario["vidas"]
    nuevas_vidas = max(0, vidas_antes - 1)

    # Guardar timestamp solo si se pierde una vida (para la regeneración)
    set_vidas(user.id, nuevas_vidas, guardar_timestamp=(nuevas_vidas < VIDAS_MAX))
    registrar_partida(user.id, score)

    logger.info(f"Game over de {user.first_name} | score={score} | vidas {vidas_antes}→{nuevas_vidas}")

    # Info de regeneración
    info_regeneracion = ""
    if nuevas_vidas < VIDAS_MAX:
        usuario_actualizado = get_usuario(user.id)
        tiempo_regen = tiempo_proxima_vida(usuario_actualizado)
        info_regeneracion = f"\n⏳ Próxima vida en: *{tiempo_regen}*"

    texto = (
        f"🎮 *Partida de {user.first_name}*\n"
        f"━━━━━━━━━━━━━━\n"
        f"⭐ Score:  `{score}`\n"
        f"⏱️ Tiempo: `{tiempo}`\n"
        f"📈 Nivel:  `{nivel}`\n"
        f"✅ Líneas: `{lineas}`\n"
        f"━━━━━━━━━━━━━━\n"
        f"❤️ Vidas restantes: *{nuevas_vidas}*"
        f"{info_regeneracion}"
    )

    if nuevas_vidas > 0:
        await update.message.reply_text(
            texto,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Jugar de nuevo", web_app=WebAppInfo(url=TETRIS_URL))
            ]])
        )
    else:
        await update.message.reply_text(
            texto + "\n\n💀 *¡Sin vidas!* Compra más o espera a que se regeneren:",
            parse_mode="Markdown",
            reply_markup=teclado_sin_vidas()
        )


# ── Botón de compra pulsado ───────────────────────────────────────────────────

async def boton_comprar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key = query.data.replace("comprar_", "")

    if key not in PAQUETES:
        await query.answer("Paquete no encontrado.", show_alert=True)
        return

    paquete = PAQUETES[key]

    await context.bot.send_invoice(
        chat_id=query.from_user.id,
        title=f"Pack de {paquete['label']}",
        description=f"Añade {paquete['vidas']} vida(s) a tu cuenta del Tetris Bot.",
        payload=key,
        currency="XTR",
        prices=[LabeledPrice(label=paquete["label"], amount=paquete["stars"])]
    )


# ── PreCheckoutQuery ─────────────────────────────────────────────────────────

async def precheckout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload in PAQUETES:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Paquete no reconocido.")


# ── SuccessfulPayment ────────────────────────────────────────────────────────

async def pago_exitoso(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user    = update.effective_user
    key     = payment.invoice_payload

    if key not in PAQUETES:
        logger.error(f"Payload desconocido en pago: {key}")
        return

    paquete = PAQUETES[key]
    add_vidas(user.id, paquete["vidas"])

    usuario       = get_usuario(user.id)
    vidas_totales = usuario["vidas"]

    logger.info(
        f"Compra de {user.first_name} | {paquete['label']} "
        f"({paquete['stars']} Stars) | vidas ahora: {vidas_totales}"
    )

    await update.message.reply_text(
        f"⭐ ¡Gracias por tu compra!\n\n"
        f"❤️ +{paquete['vidas']} vida(s) añadida(s).\n"
        f"❤️ Total de vidas: *{vidas_totales}*\n\n"
        f"¡A jugar!",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🎮 Jugar ahora", web_app=WebAppInfo(url=TETRIS_URL))
        ]])
    )

# ════════════════════════════════════════════════════════════════════════════
#  DEBUG
# ════════════════════════════════════════════════════════════════════════════
async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"UPDATE COMPLETO: {update.to_dict()}")
# ════════════════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════════════════

def main():
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("jugar", jugar))
    app.add_handler(CommandHandler("vidas", vidas_cmd))

    app.add_handler(CallbackQueryHandler(boton_comprar, pattern="^comprar_"))

    app.add_handler(PreCheckoutQueryHandler(precheckout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, pago_exitoso))
    app.add_handler(MessageHandler(filters.StatusUpdate.WEB_APP_DATA, resultado_tetris))

    app.add_handler(MessageHandler(filters.ALL, debug), group=1)

    logger.info("Bot iniciado con sistema de vidas, regeneración y Stars ⭐")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()