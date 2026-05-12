import asyncpg

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
from telegram import ReplyKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters

TOKEN = "8634997756:AAEQziuv7zogJZYk3ZLk85JPKvvHYmU9UMQ"

MENU = ReplyKeyboardMarkup(
    [
        ["🚛 Свободная машина"],
        ["📦 Разместить груз"],
        ["👤 Профиль", "💳 Подписка"],
    ],
    resize_keyboard=True,
)

user_state = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_state[update.effective_user.id] = ""
    await update.message.reply_text("Выбери действие", reply_markup=MENU)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    state = user_state.get(uid, "")

    if text == "🚛 Свободная машина":
        user_state[uid] = "truck_city"
        await update.message.reply_text("Укажи город:")
        return

    if text == "📦 Разместить груз":
        user_state[uid] = "load_from"
        await update.message.reply_text("Откуда груз:")
        return

    if state == "truck_city":
        user_state[uid] = "truck_direction"
        await update.message.reply_text(f"Город: {text}\n\nТеперь укажи направление:")
        return

    if state == "truck_direction":
        user_state[uid] = ""
        await update.message.reply_text(f"Направление: {text}\n\n✅ Машина добавлена", reply_markup=MENU)
        return

    await update.message.reply_text("Выбери действие", reply_markup=MENU)

async def post_init(app):
    await init_db()

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



if __name__ == "__main__":
    main()
