import logging
import asyncpg
import asyncio

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

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

# ================= BOT =================
TOKEN = "YOUR_BOT_TOKEN"

MENU = ReplyKeyboardMarkup(
    [
        ["🚛 Свободная машина"],
        ["📦 Разместить груз"],
        ["🔍 Найти груз"],
        ["👤 Профиль", "💳 Подписка"],
    ],
    resize_keyboard=True,
)

user_state = {}
user_data = {}

# ================= CALLBACK =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("respond_"):
        load_id = int(data.split("_")[1])
        user = query.from_user

        try:
            await DB.execute(
                """
                INSERT INTO responses(load_id, driver_id, driver_name)
                VALUES($1, $2, $3)
                """,
                load_id,
                user.id,
                user.full_name
            )
        except Exception as e:
            print("DB ERROR:", e)
            await query.message.reply_text("⚠️ Ошибка сохранения отклика")
            return

        await query.message.reply_text(
            f"✅ Отклик отправлен\n"
            f"🚛 Груз №{load_id}\n"
            f"👤 Водитель: {user.full_name}"
        )

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_user.id] = ""
    await update.message.reply_text("Выбери действие", reply_markup=MENU)

# ================= MAIN HANDLER =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    state = user_state.get(uid, "")

    # 🔍 Найти груз
    if text == "🔍 Найти груз":
        rows = await DB.fetch("""
            SELECT id, from_city, to_city, cargo, created_at
            FROM loads
            ORDER BY created_at DESC
            LIMIT 10
        """)

        if not rows:
            await update.message.reply_text("📭 Пока нет грузов")
            return

        for r in rows:
            text_msg = (
                f"📦 Груз #{r['id']}\n"
                f"🚚 {r['from_city']} → {r['to_city']}\n"
                f"📦 {r['cargo']}\n"
                f"🕒 {r['created_at']}"
            )

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Откликнуться", callback_data=f"respond_{r['id']}")]
            ])

            await update.message.reply_text(text_msg, reply_markup=keyboard)

        return

    # 🚛 машина
    if text == "🚛 Свободная машина":
        user_state[uid] = "truck_city"
        await update.message.reply_text("Укажи город:")
        return

    # 📦 груз
    if text == "📦 Разместить груз":
        user_state[uid] = "load_from"
        await update.message.reply_text("Откуда груз:")
        return

    # ================= LOAD FLOW =================
    if state == "load_from":
        user_data[uid] = {"from_city": text}
        user_state[uid] = "load_to"
        await update.message.reply_text("Куда груз:")
        return

    if state == "load_to":
        user_data[uid]["to_city"] = text
        user_state[uid] = "load_cargo"
        await update.message.reply_text("Какой груз:")
        return

    if state == "load_cargo":
        from_city = user_data[uid]["from_city"]
        to_city = user_data[uid]["to_city"]

        try:
            await DB.execute(
                """
                INSERT INTO loads(user_id, from_city, to_city, cargo)
                VALUES($1, $2, $3, $4)
                """,
                uid, from_city, to_city, text
            )
        except Exception as e:
            print("DB ERROR:", e)
            await update.message.reply_text("⚠️ Ошибка сохранения груза")
            return

        user_state[uid] = ""
        user_data[uid] = {}

        await update.message.reply_text("✅ Груз добавлен", reply_markup=MENU)
        return

    await update.message.reply_text("Выбери действие", reply_markup=MENU)

# ================= INIT =================
async def post_init(app):
    await init_db()

# ================= MAIN =================
def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("Бот запущен")
    app.run_polling()

# ================= RUN =================
if __name__ == "__main__":
    main()
