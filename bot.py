import logging
import asyncpg

from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ================= DB =================
DB = None

async def init_db():
    global DB
    DB = await asyncpg.connect(
        user='dalno',
        password='123456789',
        database='dalnoboy',
        host='127.0.0.1'
    )
    print("База подключена")

# ================= TELEGRAM =================
TOKEN = "YOUR_TOKEN_HERE"

MENU = ReplyKeyboardMarkup(
    [
        ["🚛 Свободная машина"],
        ["📦 Разместить груз"],
        ["👤 Профиль", "💳 Подписка"],
    ],
    resize_keyboard=True,
)

user_state = {}
user_data = {}

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_user.id] = ""
    await update.message.reply_text("Выбери действие", reply_markup=MENU)

# ================= MAIN HANDLER =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    state = user_state.get(uid, "")

    # 🚛 free truck
    if text == "🚛 Свободная машина":
        user_state[uid] = "truck_city"
        await update.message.reply_text("Укажи город:")
        return

    # 📦 load
    if text == "📦 Разместить груз":
        user_state[uid] = "load_from"
        await update.message.reply_text("Откуда груз:")
        return

    # ================= TRUCK FLOW =================
    if state == "truck_city":
        user_data[uid] = {"city": text}
        user_state[uid] = "truck_direction"

        await update.message.reply_text(
            f"Город: {text}\n\nТеперь укажи направление:"
        )
        return

    if state == "truck_direction":
        city = user_data[uid].get("city", "")
        direction = text

        print("SAVE START")

        await DB.execute(
            """
            INSERT INTO trucks(
                created_by_user_id,
                comment
            )
            VALUES($1, $2)
            """,
            uid,
            f"{city} -> {direction}"
        )

        print("SAVE OK")

        user_state[uid] = ""
        user_data[uid] = {}

        await update.message.reply_text(
            f"✅ Машина добавлена\n\n"
            f"Город: {city}\n"
            f"Направление: {direction}",
            reply_markup=MENU
        )
        return

    await update.message.reply_text("Выбери действие", reply_markup=MENU)

# ================= ERROR HANDLER =================
async def error_handler(update, context):
    logger.error("BOT ERROR", exc_info=context.error)
    try:
        if update and update.effective_message:
            await update.effective_message.reply_text("⚠️ Ошибка, попробуйте позже")
    except:
        pass

# ================= POST INIT =================
async def post_init(app):
    await init_db()

# ================= MAIN =================
def main():
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    print("Бот запущен")
    app.run_polling()

# ================= RUN =================
if __name__ == "__main__":
    main()
