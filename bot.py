import logging
import asyncpg

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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# ================= DB =================
DB = None

async def init_db():
    global DB
    DB = await asyncpg.connect(
        user="dalno",
        password="123456789",
        database="dalnoboy",
        host="127.0.0.1"
    )
    print("DB CONNECTED")

# ================= TOKEN =================
TOKEN = "8634997756:AAEQziuv7zogJZYk3ZLk85JPKvvHYmU9UMQ"

# ================= MENU =================
MENU = ReplyKeyboardMarkup(
    [
        ["🚛 Машина"],
        ["📦 Груз"],
        ["🔍 Грузы"]
    ],
    resize_keyboard=True
)

# ================= STATE =================
user_state = {}
user_data = {}

# ================= USER =================
async def get_or_create_user(uid, name):
    user = await DB.fetchrow("""
        SELECT id FROM users WHERE telegram_id=$1
    """, uid)

    if user:
        return user["id"]

    return await DB.fetchval("""
        INSERT INTO users(telegram_id, full_name)
        VALUES($1,$2)
        RETURNING id
    """, uid, name)

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    name = update.effective_user.full_name

    await get_or_create_user(uid, name)

    user_state[uid] = ""

    await update.message.reply_text("🚀 Готово", reply_markup=MENU)

# ================= CALLBACK =================
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    data = query.data
    print("CALLBACK:", data)

    uid = query.from_user.id
    user_id = await get_or_create_user(uid, query.from_user.full_name)

    # ================= RESPOND =================
    if data.startswith("respond_"):

        cargo_id = int(data.split("_")[1])

        truck = await DB.fetchrow("""
            SELECT id FROM trucks WHERE driver_id=$1 LIMIT 1
        """, user_id)

        if not truck:
            await query.message.reply_text("❌ Нет машины")
            return

        resp_id = await DB.fetchval("""
            INSERT INTO responses(
                cargo_id,
                truck_id,
                driver_id,
                message,
                status
            )
            VALUES($1,$2,$3,$4,'pending')
            RETURNING id
        """,
        cargo_id,
        truck["id"],
        user_id,
        "Отклик")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🤝 MATCH", callback_data=f"match_{resp_id}")]
        ])

        await query.message.reply_text(f"📨 Отклик #{resp_id}", reply_markup=kb)
        return

    # ================= MATCH =================
    if data.startswith("match_"):

        resp_id = int(data.split("_")[1])

        resp = await DB.fetchrow("""
            SELECT cargo_id, truck_id, driver_id
            FROM responses
            WHERE id=$1
        """, resp_id)

        if not resp:
            await query.message.reply_text("❌ response not found")
            return

        deal_id = await DB.fetchval("""
            INSERT INTO deals(
                response_id,
                cargo_id,
                truck_id,
                status
            )
            VALUES($1,$2,$3,'agreed')
            RETURNING id
        """,
        resp_id,
        resp["cargo_id"],
        resp["truck_id"])

        await DB.execute("""
            UPDATE cargo SET status='matched'
            WHERE id=$1
        """, resp["cargo_id"])

        await context.bot.send_message(
            chat_id=resp["driver_id"],
            text=f"🚛 Назначен груз #{resp['cargo_id']} | Deal #{deal_id}"
        )

        await query.message.reply_text(f"✅ Deal #{deal_id}")
        return

    await query.message.reply_text("⚠️ неизвестная кнопка")

# ================= TEXT =================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    uid = update.effective_user.id
    text = update.message.text

    state = user_state.get(uid, "")

    user_id = await get_or_create_user(uid, update.effective_user.full_name)

    # ================= TRUCK =================
    if text == "🚛 Машина":
        user_state[uid] = "truck_city"
        await update.message.reply_text("Город?")
        return

    if state == "truck_city":
        user_data[uid] = {"city": text}
        user_state[uid] = "truck_body"
        await update.message.reply_text("Кузов?")
        return

    if state == "truck_body":

        await DB.execute("""
            INSERT INTO trucks(
                company_id,
                driver_id,
                current_city,
                body_type,
                status
            )
            VALUES(1,$1,$2,$3,'active')
        """,
        user_id,
        user_data[uid]["city"],
        text)

        user_state[uid] = ""
        await update.message.reply_text("🚛 Машина добавлена", reply_markup=MENU)
        return

    # ================= CARGO =================
    if text == "📦 Груз":
        user_state[uid] = "cargo_from"
        await update.message.reply_text("Откуда?")
        return

    if state == "cargo_from":
        user_data[uid] = {"from": text}
        user_state[uid] = "cargo_to"
        await update.message.reply_text("Куда?")
        return

    if state == "cargo_to":
        user_data[uid]["to"] = text
        user_state[uid] = "cargo_desc"
        await update.message.reply_text("Описание?")
        return

    if state == "cargo_desc":

        cargo_id = await DB.fetchval("""
            INSERT INTO cargo(
                company_id,
                created_by,
                from_city,
                to_city,
                description,
                status
            )
            VALUES(1,$1,$2,$3,$4,'open')
            RETURNING id
        """,
        user_id,
        user_data[uid]["from"],
        user_data[uid]["to"],
        text)

        user_state[uid] = ""

        await update.message.reply_text(f"📦 Груз #{cargo_id}", reply_markup=MENU)
        return

    # ================= FIND =================
    if text == "🔍 Грузы":

        rows = await DB.fetch("""
            SELECT id, from_city, to_city, description
            FROM cargo
            WHERE status='open'
            ORDER BY id DESC
            LIMIT 10
        """)

        for r in rows:

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Отклик", callback_data=f"respond_{r['id']}")]
            ])

            await update.message.reply_text(
                f"📦 #{r['id']}\n{r['from_city']} → {r['to_city']}\n{r['description']}",
                reply_markup=kb
            )

        return

    await update.message.reply_text("Выбери кнопку", reply_markup=MENU)

# ================= INIT =================
async def post_init(app):
    await init_db()

# ================= MAIN =================
def main():

    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(CallbackQueryHandler(handle_callback))

    print("BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
