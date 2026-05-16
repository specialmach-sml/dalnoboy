import logging
import asyncpg

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters
)

TOKEN = "8634997756:AAEQziuv7zogJZYk3ZLk85JPKvvHYmU9UMQ"
DB_DSN = "postgresql://postgres:postgres@127.0.0.1:5432/dalnoboy"

DB = None

CARGO_FROM = 1
CARGO_TO = 2
CARGO_DESC = 3
CARGO_PRICE = 4



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





async def truck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text(
            "🚚 Используй так:\n"
            "/truck Москва тент\n\n"
            "Первое слово — город, дальше — тип кузова."
        )
        return

    current_city = context.args[0]
    body_type = " ".join(context.args[1:])

    existing = await DB.fetchrow("""
        SELECT id FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if existing:
        await DB.execute("""
            UPDATE trucks
            SET current_city=$1, body_type=$2, status='active'
            WHERE id=$3
        """, current_city, body_type, existing["id"])

        await update.message.reply_text(
            f"✅ Машина обновлена\n"
            f"📍 Город: {current_city}\n"
            f"📦 Кузов: {body_type}"
        )
        return

    row = await DB.fetchrow("""
        INSERT INTO trucks (
            company_id,
            driver_id,
            current_city,
            body_type,
            status
        )
        VALUES (1,$1,$2,$3,'active')
        RETURNING id
    """, user_id, current_city, body_type)

    await update.message.reply_text(
        f"✅ Машина добавлена #{row['id']}\n"
        f"📍 Город: {current_city}\n"
        f"📦 Кузов: {body_type}"
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    stats = await DB.fetchrow("""
        SELECT
            COUNT(*) AS reviews_count,
            ROUND(AVG(overall_score)::numeric, 2) AS avg_score
        FROM reviews
        WHERE to_user_id=$1
          AND deleted_at IS NULL
    """, user_id)

    deals_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deals d
        JOIN responses r ON r.id = d.response_id
        WHERE r.driver_id=$1
          AND d.status='done'
    """, user_id)

    truck = await DB.fetchrow("""
        SELECT id, current_city, body_type
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    text = (
        f"👤 Профиль перевозчика\n\n"
        f"⭐ Рейтинг: {stats['avg_score'] or 'нет оценок'}\n"
        f"💬 Отзывов: {stats['reviews_count']}\n"
        f"✅ Завершённых сделок: {deals_count}\n"
    )

    if truck:
        text += (
            f"\n🚚 Машина #{truck['id']}\n"
            f"📍 Город: {truck['current_city']}\n"
            f"📦 Кузов: {truck['body_type']}"
        )
    else:
        text += "\n🚚 Машина не добавлена"

    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚛 Dalnoboy PRO ACTIVE\n\n"
        "Быстрые команды:\n"
        "/newcargo — создать груз\n"
        "/cargo — список грузов\n"
        "/mycargo — мои грузы\n"
        "/truck — добавить/обновить машину\n"
        "/findtruck — найти машину\n"
        "/deals — сделки\n"
        "/profile — мой профиль\n"
        "/help — все команды"
    )

async def cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, status
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
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"📊 Статус: {r['status']}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")]
        ])

        await update.message.reply_text(text, reply_markup=kb)








async def mycargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, status
        FROM cargo
        WHERE created_by=$1
          AND status <> 'deleted'
        ORDER BY id DESC
        LIMIT 20
    """, user_id)

    if not rows:
        await update.message.reply_text("📭 У вас пока нет грузов")
        return

    for r in rows:
        if r["status"] == "cancelled":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Опубликовать снова", callback_data=f"cargo_open_{r['id']}")],
                [InlineKeyboardButton("🗑 Удалить груз", callback_data=f"cargo_delete_{r['id']}")]
            ])
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Снять груз", callback_data=f"cargo_cancel_{r['id']}")]
            ])

        await update.message.reply_text(
            f"📦 Мой груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"📊 Статус: {r['status']}",
            reply_markup=kb
        )




async def cargo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT id, created_by, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно снять только свой груз")
        return

    if cargo["status"] in ("done", "in_progress", "booked"):
        await q.message.reply_text("⛔ Нельзя снять груз в активной сделке")
        return

    await DB.execute("""
        UPDATE cargo
        SET status='cancelled'
        WHERE id=$1
    """, cargo_id)

    await q.message.reply_text(
        f"❌ Груз #{cargo_id} снят с публикации"
    )




async def cargo_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT id, created_by, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно публиковать только свой груз")
        return

    if cargo["status"] != "cancelled":
        await q.message.reply_text("⛔ Повторно можно публиковать только снятый груз")
        return

    await DB.execute("""
        UPDATE cargo
        SET status='open'
        WHERE id=$1
    """, cargo_id)

    await q.message.reply_text(f"✅ Груз #{cargo_id} снова опубликован")




async def cargo_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT id, created_by, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно удалить только свой груз")
        return

    if cargo["status"] != "cancelled":
        await q.message.reply_text("⛔ Удалять можно только снятый груз")
        return

    await DB.execute("""
        UPDATE cargo
        SET status='deleted'
        WHERE id=$1
    """, cargo_id)

    await q.message.reply_text(f"🗑 Груз #{cargo_id} удалён из списка")












async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
    cargo_count = await DB.fetchval("SELECT COUNT(*) FROM cargo")
    open_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
    deals_count = await DB.fetchval("SELECT COUNT(*) FROM deals")
    active_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status IN ('active','in_progress')")
    trucks_count = await DB.fetchval("SELECT COUNT(*) FROM trucks")

    await update.message.reply_text(
        f"🛠 Админ-панель\n\n"
        f"👤 Ваш ID: {user_id}\n"
        f"👥 Пользователей: {users_count}\n"
        f"📦 Грузов всего: {cargo_count}\n"
        f"🟢 Открытых грузов: {open_cargo}\n"
        f"🤝 Сделок всего: {deals_count}\n"
        f"🚚 Активных сделок: {active_deals}\n"
        f"🚛 Машин: {trucks_count}"
    )




async def adminusers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT id, telegram_id, full_name, role, created_at
        FROM users
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("👥 Пользователей нет")
        return

    text = "👥 Последние пользователи\n\n"

    for r in rows:
        text += (
            f"ID: {r['id']}\n"
            f"TG: {r['telegram_id']}\n"
            f"Имя: {r['full_name'] or '-'}\n"
            f"Роль: {r['role'] or '-'}\n\n"
        )

    await update.message.reply_text(text)




async def admincargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT id, created_by, from_city, to_city, price_amount, price_currency, status
        FROM cargo
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("📦 Грузов нет")
        return

    text = "📦 Последние грузы\n\n"

    for r in rows:
        text += (
            f"#{r['id']} | user {r['created_by']}\n"
            f"{r['from_city']} → {r['to_city']}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"Статус: {r['status']}\n\n"
        )

    await update.message.reply_text(text)




async def admindeals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            d.created_at,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        ORDER BY d.id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("🤝 Сделок нет")
        return

    text = "🤝 Последние сделки\n\n"

    for r in rows:
        text += (
            f"#{r['id']} | driver {r['driver_id']}\n"
            f"{r['from_city']} → {r['to_city']}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"Статус: {r['status']}\n\n"
        )

    await update.message.reply_text(text)




async def adminreviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT id, deal_id, from_user_id, to_user_id, review_type, overall_score, created_at
        FROM reviews
        WHERE deleted_at IS NULL
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("⭐ Отзывов нет")
        return

    text = "⭐ Последние отзывы\n\n"

    for r in rows:
        text += (
            f"#{r['id']} | сделка {r['deal_id']}\n"
            f"От user {r['from_user_id']} → user {r['to_user_id']}\n"
            f"Тип: {r['review_type']}\n"
            f"Оценка: {r['overall_score']}⭐\n\n"
        )

    await update.message.reply_text(text)




async def adminhelp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    await update.message.reply_text(
        "🛠 Админ-команды\n\n"
        "/admin — обзор системы\n"
        "/adminusers — пользователи\n"
        "/admincargo — грузы\n"
        "/admindeals — сделки\n"
        "/adminreviews — отзывы"
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚛 Dalnoboy PRO — команды\n\n"
        "📦 Грузы:\n"
        "/newcargo — создать груз\n"
        "/cargo — все грузы\n"
        "/mycargo — мои грузы\n"
        "/deletedcargo — удалённые грузы\n"
        "/find — поиск груза по городу\n"
        "/findprice — поиск груза от цены\n\n"
        "🚚 Перевозчики:\n"
        "/truck — добавить/обновить машину\n"
        "/profile — мой профиль\n"
        "/findtruck — найти машину\n"
        "/topcarriers — топ перевозчиков\n\n"
        "🤝 Сделки:\n"
        "/responses — отклики\n"
        "/deals — сделки\n\n"
        "📊 Сервис:\n"
        "/stats — статистика\n"
        "/help — помощь"
    )


async def topcarriers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            u.id,
            u.full_name,
            COALESCE(ROUND(AVG(rv.overall_score)::numeric, 2), 0) AS avg_score,
            COUNT(rv.id) AS reviews_count,
            (
                SELECT COUNT(*)
                FROM deals d
                JOIN responses r ON r.id = d.response_id
                WHERE r.driver_id = u.id
                  AND d.status='done'
            ) AS done_deals
        FROM users u
        LEFT JOIN reviews rv ON rv.to_user_id = u.id AND rv.deleted_at IS NULL
        GROUP BY u.id, u.full_name
        ORDER BY avg_score DESC, done_deals DESC
        LIMIT 10
    """)

    if not rows:
        await update.message.reply_text("📭 Рейтинга пока нет")
        return

    text = "🏆 Топ перевозчиков\n\n"

    for i, r in enumerate(rows, start=1):
        text += (
            f"{i}. 👤 {r['full_name'] or 'Без имени'}\n"
            f"   ⭐ {r['avg_score']} ({r['reviews_count']} отзывов)\n"
            f"   ✅ Сделок: {r['done_deals']}\n"
        )

    await update.message.reply_text(text)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
    cargo_open = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
    cargo_done = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='done'")
    deals_done = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status='done'")
    trucks_active = await DB.fetchval("SELECT COUNT(*) FROM trucks WHERE status='active'")
    reviews_count = await DB.fetchval("SELECT COUNT(*) FROM reviews WHERE deleted_at IS NULL")

    await update.message.reply_text(
        f"📊 Статистика Dalnoboy\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"🚚 Активных машин: {trucks_active}\n"
        f"📦 Открытых грузов: {cargo_open}\n"
        f"✅ Завершённых грузов: {cargo_done}\n"
        f"🤝 Завершённых сделок: {deals_done}\n"
        f"⭐ Отзывов: {reviews_count}"
    )


async def deletedcargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, status
        FROM cargo
        WHERE created_by=$1
          AND status='deleted'
        ORDER BY id DESC
        LIMIT 20
    """, user_id)

    if not rows:
        await update.message.reply_text("🗑 Удалённых грузов нет")
        return

    for r in rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("♻️ Восстановить", callback_data=f"cargo_restore_{r['id']}")]
        ])

        await update.message.reply_text(
            f"🗑 Удалённый груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}",
            reply_markup=kb
        )


async def cargo_restore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT id, created_by, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно восстановить только свой груз")
        return

    if cargo["status"] != "deleted":
        await q.message.reply_text("⛔ Восстановить можно только удалённый груз")
        return

    await DB.execute("""
        UPDATE cargo
        SET status='cancelled'
        WHERE id=$1
    """, cargo_id)

    await q.message.reply_text(f"♻️ Груз #{cargo_id} восстановлен как снятый")


async def findprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("💰 Используй так: /findprice 50000")
        return

    try:
        min_price = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Цена должна быть числом, например: /findprice 50000")
        return

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, status
        FROM cargo
        WHERE status='open'
          AND price_amount >= $1
        ORDER BY price_amount DESC
        LIMIT 20
    """, min_price)

    if not rows:
        await update.message.reply_text(f"📭 Грузы от {min_price} не найдены")
        return

    for r in rows:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")]
        ])

        await update.message.reply_text(
            f"💰 Груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"📊 Статус: {r['status']}",
            reply_markup=kb
        )


async def findtruck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔎 Используй так: /findtruck Москва")
        return

    query = " ".join(context.args).strip()

    rows = await DB.fetch("""
        SELECT
            t.id AS truck_id,
            t.current_city,
            t.body_type,
            u.full_name,
            COALESCE(ROUND(AVG(rv.overall_score)::numeric, 2), 0) AS avg_score,
            COUNT(rv.id) AS reviews_count,
            (
                SELECT COUNT(*)
                FROM deals d
                JOIN responses r2 ON r2.id = d.response_id
                WHERE r2.driver_id = t.driver_id
                  AND d.status='done'
            ) AS done_deals
        FROM trucks t
        JOIN users u ON u.id = t.driver_id
        LEFT JOIN reviews rv ON rv.to_user_id = u.id AND rv.deleted_at IS NULL
        WHERE t.status='active'
          AND (
            t.current_city ILIKE $1
            OR t.body_type ILIKE $1
            OR u.full_name ILIKE $1
          )
        GROUP BY t.id, t.current_city, t.body_type, u.full_name, t.driver_id
        ORDER BY avg_score DESC, done_deals DESC
        LIMIT 20
    """, f"%{query}%")

    if not rows:
        await update.message.reply_text(f"📭 Машины по запросу «{query}» не найдены")
        return

    for r in rows:
        await update.message.reply_text(
            f"🚚 Машина #{r['truck_id']}\n"
            f"👤 Водитель: {r['full_name']}\n"
            f"📍 Город: {r['current_city']}\n"
            f"📦 Кузов: {r['body_type']}\n"
            f"⭐ Рейтинг: {r['avg_score'] or 'нет'} ({r['reviews_count']} отзывов)\n"
            f"✅ Завершённых сделок: {r['done_deals']}"
        )


async def find_cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("🔎 Используй так: /find Москва")
        return

    query = " ".join(context.args).strip()

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, status
        FROM cargo
        WHERE status='open'
          AND (
            from_city ILIKE $1
            OR to_city ILIKE $1
            OR description ILIKE $1
          )
        ORDER BY id DESC
        LIMIT 20
    """, f"%{query}%")

    if not rows:
        await update.message.reply_text(f"📭 Грузы по запросу «{query}» не найдены")
        return

    for r in rows:
        text = (
            f"🔎 Найден груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
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
    user_id = await ensure_user(update.effective_user)

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
        WHERE c.created_by=$1
        ORDER BY r.id DESC
        LIMIT 20
    """, user_id)

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
        SELECT
            r.id,
            r.status,
            r.cargo_id,
            r.truck_id,
            u.telegram_id,
            c.from_city,
            c.to_city
        FROM responses r
        JOIN users u ON u.id = r.driver_id
        JOIN cargo c ON c.id = r.cargo_id
        WHERE r.id=$1
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

        await context.bot.send_message(
            chat_id=response["telegram_id"],
            text=(
                f"✅ Ваш отклик принят!\n"
                f"📦 Груз #{response['cargo_id']}: {response['from_city']} → {response['to_city']}\n"
                f"🤝 Сделка создана"
            )
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
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            t.id AS truck_id,
            t.current_city,
            t.body_type
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        JOIN responses r ON r.id = d.response_id
        WHERE c.created_by=$1 OR r.driver_id=$1
        ORDER BY d.id DESC
        LIMIT 20
    """, user_id)

    if not rows:
        await update.message.reply_text("📭 Сделок нет")
        return

    for r in rows:
        text = (
            f"🤝 Сделка #{r['id']}\n"
            f"📦 Груз #{r['cargo_id']}: {r['from_city']} → {r['to_city']}\n"
            f"💰 {r['price_amount'] or '-'} {r['price_currency'] or ''}\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"📊 Статус: {r['status']}"
        )

        buttons = [
            [
                InlineKeyboardButton("🚚 В пути", callback_data=f"deal_in_progress_{r['id']}"),
                InlineKeyboardButton("✅ Доставлено", callback_data=f"deal_done_{r['id']}")
            ],
            [
                InlineKeyboardButton("❌ Отменить", callback_data=f"deal_cancelled_{r['id']}")
            ]
        ]

        if r["status"] == "done":
            buttons.append([
                InlineKeyboardButton("⭐ Оценить", callback_data=f"review_{r['id']}")
            ])

        kb = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(text, reply_markup=kb)

async def deal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    parts = q.data.split("_")

    if len(parts) == 4 and parts[1] == "in" and parts[2] == "progress":
        status = "in_progress"
        deal_id = int(parts[3])
    else:
        status = parts[1]
        deal_id = int(parts[2])

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            d.cargo_id,
            c.from_city,
            c.to_city,
            owner.telegram_id AS owner_tg,
            driver.telegram_id AS driver_tg
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN users owner ON owner.id = c.created_by
        JOIN responses r ON r.id = d.response_id
        JOIN users driver ON driver.id = r.driver_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await q.message.reply_text("❌ Сделка не найдена")
        return

    await DB.execute("""
        UPDATE deals
        SET status=$1, updated_at=now()
        WHERE id=$2
    """, status, deal_id)

    cargo_status = {
        "active": "booked",
        "in_progress": "in_progress",
        "done": "done",
        "cancelled": "open"
    }.get(status)

    if cargo_status:
        await DB.execute("""
            UPDATE cargo
            SET status=$1
            WHERE id=$2
        """, cargo_status, deal["cargo_id"])

    labels = {
        "active": "🟢 Активная",
        "in_progress": "🚚 В пути",
        "done": "✅ Доставлено",
        "cancelled": "❌ Отменено"
    }

    status_text = labels.get(status, status)

    notify_text = (
        f"🔔 Обновление сделки #{deal_id}\n"
        f"📦 {deal['from_city']} → {deal['to_city']}\n"
        f"📊 Новый статус: {status_text}"
    )

    for chat_id in {deal["owner_tg"], deal["driver_tg"]}:
        try:
            await context.bot.send_message(chat_id=chat_id, text=notify_text)
        except Exception as e:
            logging.warning(f"Notify failed for {chat_id}: {e}")

    await q.message.reply_text(
        f"🤝 Сделка #{deal_id}: статус изменён на {status_text}"
    )




async def review_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    deal_id = int(q.data.split("_")[1])

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1⭐", callback_data=f"rate_1_{deal_id}"),
            InlineKeyboardButton("2⭐", callback_data=f"rate_2_{deal_id}"),
            InlineKeyboardButton("3⭐", callback_data=f"rate_3_{deal_id}")
        ],
        [
            InlineKeyboardButton("4⭐", callback_data=f"rate_4_{deal_id}"),
            InlineKeyboardButton("5⭐", callback_data=f"rate_5_{deal_id}")
        ]
    ])

    await q.message.reply_text(
        f"⭐ Оцените сделку #{deal_id}",
        reply_markup=kb
    )

async def rate_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    _, score_raw, deal_raw = q.data.split("_")

    score = int(score_raw)
    deal_id = int(deal_raw)

    tg_user = q.from_user

    author = await DB.fetchrow("""
        SELECT id FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            c.created_by,
            r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await q.message.reply_text("❌ Сделка не найдена")
        return

    from_user_id = author["id"]

    if from_user_id == deal["created_by"]:
        to_user_id = deal["driver_id"]
        review_type = "carrier"
    else:
        to_user_id = deal["created_by"]
        review_type = "customer"

    existing = await DB.fetchrow("""
        SELECT id FROM reviews
        WHERE deal_id=$1
          AND from_user_id=$2
    """, deal_id, from_user_id)

    if existing:
        await q.message.reply_text("⚠️ Вы уже оценили эту сделку")
        return

    await DB.execute("""
        INSERT INTO reviews (
            deal_id,
            from_company_id,
            to_company_id,
            from_user_id,
            to_user_id,
            review_type,
            overall_score
        )
        VALUES ($1,1,1,$2,$3,$4,$5)
    """,
        deal_id,
        from_user_id,
        to_user_id,
        review_type,
        score
    )

    await q.message.reply_text(
        f"✅ Оценка {score}⭐ сохранена"
    )


async def newcargo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["newcargo"] = {}
    await update.message.reply_text("📍 Введите город загрузки:")
    return CARGO_FROM


async def newcargo_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["newcargo"]["from_city"] = update.message.text.strip()
    await update.message.reply_text("🏁 Введите город выгрузки:")
    return CARGO_TO


async def newcargo_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["newcargo"]["to_city"] = update.message.text.strip()
    await update.message.reply_text("📝 Введите описание груза:")
    return CARGO_DESC


async def newcargo_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["newcargo"]["description"] = update.message.text.strip()
    await update.message.reply_text("💰 Введите цену, например: 50000")
    return CARGO_PRICE


async def newcargo_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_price = update.message.text.strip().replace(" ", "").replace(",", ".")

    try:
        price_amount = float(raw_price)
    except ValueError:
        await update.message.reply_text("❌ Введите цену числом, например: 50000")
        return CARGO_PRICE

    context.user_data["newcargo"]["price_amount"] = price_amount

    tg_user = update.effective_user
    user_id = await ensure_user(tg_user)

    data = context.user_data["newcargo"]

    row = await DB.fetchrow("""
        INSERT INTO cargo (
            created_by,
            from_city,
            to_city,
            description,
            price_amount,
            price_currency,
            status
        )
        VALUES ($1,$2,$3,$4,$5,'RUB','open')
        RETURNING id
    """,
        user_id,
        data["from_city"],
        data["to_city"],
        data["description"],
        data["price_amount"]
    )

    await update.message.reply_text(
        f"✅ Груз создан #{row['id']}\n"
        f"📍 {data['from_city']} → {data['to_city']}\n"
        f"📝 {data['description']}\n"
        f"💰 Цена: {data['price_amount']} RUB"
    )

    context.user_data.pop("newcargo", None)
    return ConversationHandler.END


async def newcargo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("newcargo", None)
    await update.message.reply_text("❌ Создание груза отменено")
    return ConversationHandler.END



async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("BOT ERROR", exc_info=context.error)


async def post_init(app: Application):
    await init_db()

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    newcargo_handler = ConversationHandler(
        entry_points=[CommandHandler("newcargo", newcargo_start)],
        states={
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_to)],
            CARGO_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_desc)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_price)],
        },
        fallbacks=[CommandHandler("cancel", newcargo_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cargo", cargo))
    app.add_handler(CommandHandler("find", find_cargo))
    app.add_handler(CommandHandler("findtruck", findtruck))
    app.add_handler(CommandHandler("findprice", findprice))
    app.add_handler(CommandHandler("mycargo", mycargo))
    app.add_handler(CommandHandler("deletedcargo", deletedcargo))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("topcarriers", topcarriers))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("adminusers", adminusers))
    app.add_handler(CommandHandler("admincargo", admincargo))
    app.add_handler(CommandHandler("admindeals", admindeals))
    app.add_handler(CommandHandler("adminreviews", adminreviews))
    app.add_handler(CommandHandler("adminhelp", adminhelp))
    app.add_handler(CommandHandler("responses", responses_list))
    app.add_handler(CommandHandler("deals", deals_list))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("truck", truck))
    app.add_handler(newcargo_handler)

    app.add_handler(CallbackQueryHandler(cargo_cancel, pattern="^cargo_cancel_"))
    app.add_handler(CallbackQueryHandler(cargo_open, pattern="^cargo_open_"))
    app.add_handler(CallbackQueryHandler(cargo_delete, pattern="^cargo_delete_"))
    app.add_handler(CallbackQueryHandler(cargo_restore, pattern="^cargo_restore_"))
    app.add_handler(CallbackQueryHandler(respond, pattern="^cargo_"))
    app.add_handler(CallbackQueryHandler(response_action, pattern="^(accept|reject)_"))
    app.add_handler(CallbackQueryHandler(deal_action, pattern="^deal_"))
    app.add_handler(CallbackQueryHandler(review_action, pattern="^review_"))
    app.add_handler(CallbackQueryHandler(rate_action, pattern="^rate_"))

    app.add_error_handler(error_handler)

    print("🚛 BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
