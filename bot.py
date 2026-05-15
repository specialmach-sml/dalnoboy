import logging
import asyncpg

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

TOKEN = "8634997756:AAEQziuv7zogJZYk3ZLk85JPKvvHYmU9UMQ"
DB_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/dalnoboy"

DB = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

async def init_db():
    global DB
    DB = await asyncpg.create_pool(DB_DSN)
    print("✅ DB CONNECTED")

async def ensure_user(tg_user):
    row = await DB.fetchrow("""
        SELECT id FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    if row:
        return row["id"]

    new_user = await DB.fetchrow("""
        INSERT INTO users (telegram_id, full_name)
        VALUES ($1, $2)
        RETURNING id
    """, tg_user.id, tg_user.full_name)

    return new_user["id"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚛 Dalnoboy PRO ACTIVE\n\n"
        "/cargo — список грузов\n"
        "/responses — список откликов\n"
        "/deals — список сделок"
    )

async def cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT id, from_city, to_city, status
        FROM cargo
        ORDER BY id DESC
        LIMIT 10
    """)

    if not rows:
        await update.message.reply_text("📦 Нет грузов")
        return

    for r in rows:
        text = (
            f"📦 Груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📊 Статус: {r['status']}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")]
        ])

        await update.message.reply_text(text, reply_markup=kb)

async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[1])
    tg_user = q.from_user
    user_id = await ensure_user(tg_user)

    cargo_exists = await DB.fetchrow("SELECT id FROM cargo WHERE id=$1", cargo_id)

    if not cargo_exists:
        await q.message.reply_text("❌ Груз не найден")
        return

    truck = await DB.fetchrow("""
        SELECT id FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if not truck:
        await q.message.reply_text("❌ Нет машины у водителя")
        return

    existing = await DB.fetchrow("""
        SELECT id FROM responses
        WHERE cargo_id=$1 AND driver_id=$2
    """, cargo_id, user_id)

    if existing:
        await q.message.reply_text("⚠️ Уже откликались")
        return

    await DB.execute("""
        INSERT INTO responses (
            cargo_id,
            truck_id,
            driver_id,
            message,
            status
        )
        VALUES ($1,$2,$3,$4,'pending')
    """,
        cargo_id,
        truck["id"],
        user_id,
        f"Отклик от {tg_user.full_name}"
    )

    await q.message.reply_text("✅ Отклик отправлен")

async def responses_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            r.id,
            r.status,
            r.message,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            u.full_name,
            t.id AS truck_id,
            t.current_city,
            t.body_type
        FROM responses r
        JOIN cargo c ON c.id = r.cargo_id
        JOIN users u ON u.id = r.driver_id
        JOIN trucks t ON t.id = r.truck_id
        ORDER BY r.id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("📭 Откликов нет")
        return

    for r in rows:
        text = (
            f"🚛 Отклик #{r['id']}\n"
            f"📦 Груз #{r['cargo_id']}: {r['from_city']} → {r['to_city']}\n"
            f"👤 Водитель: {r['full_name']}\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"📊 Статус: {r['status']}\n"
            f"💬 {r['message']}"
        )

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Принять", callback_data=f"accept_{r['id']}"),
                InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{r['id']}")
            ]
        ])

        await update.message.reply_text(text, reply_markup=kb)

async def response_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    action, response_id_raw = q.data.split("_")
    response_id = int(response_id_raw)

    response = await DB.fetchrow("""
        SELECT id, status, cargo_id, truck_id
        FROM responses
        WHERE id=$1
    """, response_id)

    if not response:
        await q.message.reply_text("❌ Отклик не найден")
        return

    if action == "accept":
        await DB.execute("""
            UPDATE responses
            SET status='accepted'
            WHERE id=$1
        """, response_id)

        existing_deal = await DB.fetchrow("""
            SELECT id FROM deals
            WHERE response_id=$1
        """, response_id)

        if not existing_deal:
            await DB.execute("""
                INSERT INTO deals (
                    response_id,
                    cargo_id,
                    truck_id,
                    status
                )
                VALUES ($1,$2,$3,'active')
            """,
                response["id"],
                response["cargo_id"],
                response["truck_id"]
            )

        await q.message.reply_text(f"✅ Отклик #{response_id} принят. Сделка создана.")
        return

    if action == "reject":
        await DB.execute("""
            UPDATE responses
            SET status='rejected'
            WHERE id=$1
        """, response_id)

        await q.message.reply_text(f"❌ Отклик #{response_id} отклонён")
        return

async def deals_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            d.created_at,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            t.id AS truck_id,
            t.current_city,
            t.body_type
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        ORDER BY d.id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("📭 Сделок нет")
        return

    for r in rows:
        text = (
            f"🤝 Сделка #{r['id']}\n"
            f"📦 Груз #{r['cargo_id']}: {r['from_city']} → {r['to_city']}\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"📊 Статус: {r['status']}"
        )

        await update.message.reply_text(text)

async def post_init(app: Application):
    await init_db()

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cargo", cargo))
    app.add_handler(CommandHandler("responses", responses_list))
    app.add_handler(CommandHandler("deals", deals_list))

    app.add_handler(CallbackQueryHandler(respond, pattern="^cargo_"))
    app.add_handler(CallbackQueryHandler(response_action, pattern="^(accept|reject)_"))

    print("🚛 BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
