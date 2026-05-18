import logging
import shutil
import subprocess
from datetime import datetime
import asyncpg

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ApplicationHandlerStop
)

from config import TOKEN, DB_DSN

DB = None
BOT_VERSION = datetime.now().strftime("%Y.%m.%d.%H%M")
START_TIME = datetime.now()

CARGO_FROM = 1
CARGO_TO = 2
CARGO_DESC = 3
CARGO_PRICE = 4

TRUCK_CITY = 20
TRUCK_BODY = 21
TRUCK_TONS = 22
TRUCK_VOLUME = 23
TRUCK_COMMENT = 24







def main_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📦 Грузы", "🚚 Машина"],
            ["🤝 Сделки", "👤 Профиль"],
            ["🏠 Меню", "🆘 Помощь"]
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True
    )


def format_price(v):
    if v is None:
        return "-"
    try:
        return f"{int(float(v)):,}".replace(",", " ")
    except:
        return str(v)

def human_status(v):
    mapping = {
        "open": "🟢 Открыт",
        "pending": "🟡 Ожидает",
        "active": "🚚 В пути",
        "done": "✅ Завершён",
        "closed": "❌ Закрыт",
        "cancelled": "❌ Отменён"
    }
    return mapping.get(v, v)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

async def init_db():
    global DB
    DB = await asyncpg.create_pool(DB_DSN)
    print("✅ DB CONNECTED")

async def ensure_user(tg_user):
    if not tg_user or getattr(tg_user, "is_bot", False):
        return None

    row = await DB.fetchrow("""
        SELECT id, banned FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    if row:
        await DB.execute("""
            UPDATE users
            SET full_name=$1
            WHERE telegram_id=$2
        """, tg_user.full_name, tg_user.id)

        return row["id"]

    new_user = await DB.fetchrow("""
        INSERT INTO users (telegram_id, full_name)
        VALUES ($1, $2)
        RETURNING id
    """, tg_user.id, tg_user.full_name)

    return new_user["id"]







async def ban_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if not tg_user:
        return

    row = await DB.fetchrow("""
        SELECT id, banned
        FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    if row and row["banned"]:
        if update.message:
            await update.message.reply_text("⛔ Ваш аккаунт заблокирован")
        elif update.callback_query:
            await update.callback_query.answer("⛔ Аккаунт заблокирован", show_alert=True)
        raise ApplicationHandlerStop














async def subroute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    cargo_id = int(q.data.split("_")[1])

    cargo = await DB.fetchrow("""
        SELECT from_city, to_city
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    existing = await DB.fetchrow("""
        SELECT id
        FROM route_subscriptions
        WHERE user_id=$1
          AND from_city ILIKE $2
          AND to_city ILIKE $3
    """, user_id, cargo["from_city"], cargo["to_city"])

    if existing:
        await q.message.reply_text(
            f"⚠️ Подписка уже есть:\n"
            f"{cargo['from_city']} → {cargo['to_city']}"
        )
        return

    await DB.execute("""
        INSERT INTO route_subscriptions (
            user_id,
            from_city,
            to_city
        )
        VALUES ($1,$2,$3)
    """, user_id, cargo["from_city"], cargo["to_city"])

    await q.message.reply_text(
        f"🔔 Подписка добавлена:\n"
        f"{cargo['from_city']} → {cargo['to_city']}"
    )


async def sub_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)
    sub_id = int(q.data.split("_")[2])

    sub = await DB.fetchrow("""
        SELECT id, user_id
        FROM route_subscriptions
        WHERE id=$1
    """, sub_id)

    if not sub:
        await q.message.reply_text("❌ Подписка не найдена")
        return

    if sub["user_id"] != user_id:
        await q.message.reply_text("⛔ Можно удалить только свою подписку")
        return

    await DB.execute("""
        DELETE FROM route_subscriptions
        WHERE id=$1
    """, sub_id)

    await q.message.reply_text(f"🗑 Подписка #{sub_id} удалена")


async def sub_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)
    sub_id = int(q.data.split("_")[2])

    sub = await DB.fetchrow("""
        SELECT id, user_id
        FROM route_subscriptions
        WHERE id=$1
    """, sub_id)

    if not sub:
        await q.message.reply_text("❌ Подписка не найдена")
        return

    if sub["user_id"] != user_id:
        await q.message.reply_text("⛔ Можно включить только свою подписку")
        return

    await DB.execute("""
        UPDATE route_subscriptions
        SET active=true
        WHERE id=$1
    """, sub_id)

    await q.message.reply_text(f"🔔 Подписка #{sub_id} включена")


async def sub_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)
    sub_id = int(q.data.split("_")[2])

    sub = await DB.fetchrow("""
        SELECT id, user_id
        FROM route_subscriptions
        WHERE id=$1
    """, sub_id)

    if not sub:
        await q.message.reply_text("❌ Подписка не найдена")
        return

    if sub["user_id"] != user_id:
        await q.message.reply_text("⛔ Можно отключить только свою подписку")
        return

    await DB.execute("""
        UPDATE route_subscriptions
        SET active=false
        WHERE id=$1
    """, sub_id)

    await q.message.reply_text(f"🔕 Подписка #{sub_id} отключена")


async def mysubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, active
        FROM route_subscriptions
        WHERE user_id=$1
        ORDER BY id DESC
        LIMIT 20
    """, user_id)

    if not rows:
        await update.message.reply_text("🔕 Подписок пока нет")
        return

    for r in rows:
        status = "активна" if r["active"] else "выключена"

        if r["active"]:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Отключить", callback_data=f"sub_off_{r['id']}")],
                [InlineKeyboardButton("🗑 Удалить подписку", callback_data=f"sub_delete_{r['id']}")]
            ])
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Включить", callback_data=f"sub_on_{r['id']}")],
                [InlineKeyboardButton("🗑 Удалить подписку", callback_data=f"sub_delete_{r['id']}")]
            ])

        await update.message.reply_text(
            f"🔔 Подписка #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"Статус: {status}",
            reply_markup=kb
        )


USER_LAST_ACTION = {}

async def rate_limit_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if not tg_user:
        return

    now = datetime.now().timestamp()
    last = USER_LAST_ACTION.get(tg_user.id, 0)

    if now - last < 2:
        if update.message:
            await update.message.reply_text("⏳ Слишком часто. Подождите пару секунд.")
        elif update.callback_query:
            await update.callback_query.answer("⏳ Слишком часто", show_alert=False)
        raise ApplicationHandlerStop

    USER_LAST_ACTION[tg_user.id] = now


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text(
            "Используй:\n"
            "/subscribe Москва Смоленск\n"
            "/subscribe Москва * — из Москвы в любой город\n"
            "/subscribe * Смоленск — из любого города в Смоленск"
        )
        return

    from_city = context.args[0].strip()
    to_city = context.args[1].strip()

    existing = await DB.fetchrow("""
        SELECT id, active
        FROM route_subscriptions
        WHERE user_id=$1
          AND from_city ILIKE $2
          AND to_city ILIKE $3
        LIMIT 1
    """, user_id, from_city, to_city)

    if existing:
        if not existing["active"]:
            await DB.execute("""
                UPDATE route_subscriptions
                SET active=true
                WHERE id=$1
            """, existing["id"])

            await update.message.reply_text(
                f"🔔 Подписка снова включена:\n{from_city} → {to_city}"
            )
            return

        await update.message.reply_text(
            f"⚠️ Такая подписка уже есть:\n{from_city} → {to_city}"
        )
        return

    user_plan = await DB.fetchrow("""
        SELECT COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    plan = user_plan["plan_type"] if user_plan else "free"

    active_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM route_subscriptions
        WHERE user_id=$1
          AND active=true
    """, user_id)

    limits = {
        "free": 2,
        "pro": 10,
        "dispatcher": 999999,
        "company": 999999
    }

    limit = limits.get(plan, 2)

    if active_count >= limit:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💼 Улучшить тариф", callback_data="menu_plans")]
        ])

        await update.message.reply_text(
            f"⛔ Лимит подписок для тарифа {plan.upper()}: {limit}\n\n"
            "🔥 PRO: до 10 маршрутов\n"
            "⭐ COMPANY/DISPATCHER: без ограничений",
            reply_markup=kb
        )
        return

    await DB.execute("""
        INSERT INTO route_subscriptions (
            user_id,
            from_city,
            to_city
        )
        VALUES ($1,$2,$3)
    """, user_id, from_city, to_city)

    await update.message.reply_text(
        f"🔔 Подписка сохранена:\n{from_city} → {to_city}"
    )


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



async def truck_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"] = {}

    await update.message.reply_text(
        "🚚 Добавление машины\n\n"
        "📍 Введите текущий город:"
    )

    return TRUCK_CITY


async def truck_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["current_city"] = update.message.text.strip()

    await update.message.reply_text(
        "📦 Введите тип кузова:\n\n"
        "Например: тент, реф, контейнер, сцепка"
    )

    return TRUCK_BODY


async def truck_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["truck"]["body_type"] = update.message.text.strip()

    await update.message.reply_text(
        "⚖️ Введите грузоподъёмность в тоннах:\n\n"
        "Например: 20"
    )

    return TRUCK_TONS


async def truck_tons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(",", ".")

    try:
        tons = float(raw)
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: 20")
        return TRUCK_TONS

    context.user_data["truck"]["capacity_tons"] = tons

    await update.message.reply_text(
        "📦 Введите объём кузова м³:\n\n"
        "Например: 82"
    )

    return TRUCK_VOLUME


async def truck_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(",", ".")

    try:
        volume = float(raw)
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: 82")
        return TRUCK_VOLUME

    context.user_data["truck"]["volume_m3"] = volume

    await update.message.reply_text(
        "📝 Комментарий к машине:\n\n"
        "Например: ADR, верхняя загрузка, ремни\n\n"
        "Или отправьте: -"
    )

    return TRUCK_COMMENT


async def truck_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment = update.message.text.strip()

    if comment == "-":
        comment = ""

    context.user_data["truck"]["comment"] = comment

    data = context.user_data["truck"]

    user_id = await ensure_user(update.effective_user)

    existing = await DB.fetchrow("""
        SELECT id
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if existing:
        await DB.execute("""
            UPDATE trucks
            SET
                current_city=$1,
                body_type=$2,
                capacity_tons=$3,
                volume_m3=$4,
                comment=$5,
                status='active'
            WHERE id=$6
        """,
            data["current_city"],
            data["body_type"],
            data["capacity_tons"],
            data["volume_m3"],
            data["comment"],
            existing["id"]
        )

        truck_id = existing["id"]

    else:
        row = await DB.fetchrow("""
            INSERT INTO trucks (
                company_id,
                driver_id,
                current_city,
                body_type,
                capacity_tons,
                volume_m3,
                comment,
                status
            )
            VALUES (1,$1,$2,$3,$4,$5,$6,'active')
            RETURNING id
        """,
            user_id,
            data["current_city"],
            data["body_type"],
            data["capacity_tons"],
            data["volume_m3"],
            data["comment"]
        )

        truck_id = row["id"]

    await update.message.reply_text(
        f"✅ Машина сохранена #{truck_id}\n\n"
        f"📍 Город: {data['current_city']}\n"
        f"📦 Кузов: {data['body_type']}\n"
        f"⚖️ Тоннаж: {data['capacity_tons']} т\n"
        f"📦 Объём: {data['volume_m3']} м³\n"
        f"📝 Комментарий: {data['comment'] or '-'}"
    )

    cargos = await DB.fetch("""
        SELECT
            c.id,
            c.from_city,
            c.to_city,
            u.telegram_id
        FROM cargo c
        JOIN users u ON u.id = c.created_by
        WHERE c.status='open'
          AND (
            c.from_city ILIKE $1
            OR c.to_city ILIKE $1
          )
    """, data["current_city"])

    for c in cargos:
        try:
            await context.bot.send_message(
                chat_id=c["telegram_id"],
                text=(
                    f"🚚 Появилась новая машина\n\n"
                    f"📍 {data['current_city']}\n"
                    f"📦 {data['body_type']}\n"
                    f"⚖️ {data['capacity_tons']} т\n"
                    f"📦 {data['volume_m3']} м³\n\n"
                    f"Для поиска: /findtruck {data['current_city']}"
                )
            )
        except Exception:
            pass

    matched = await DB.fetch("""
        SELECT
            id,
            from_city,
            to_city,
            price_amount,
            price_currency
        FROM cargo
        WHERE status='open'
          AND (
            from_city ILIKE $1
            OR to_city ILIKE $1
          )
        ORDER BY id DESC
        LIMIT 5
    """, data["current_city"])

    if matched:
        text = "📦 Подходящие грузы:\n\n"

        for m in matched:
            text += (
                f"#{m['id']} "
                f"{m['from_city']} → {m['to_city']}\n"
                f"💰 {format_price(m['price_amount'])} {m['price_currency'] or ''}\n\n"
            )

        text += f"🔎 Открыть: /find {data['current_city']}"

        await update.message.reply_text(text)

    subs = await DB.fetch("""
        SELECT DISTINCT u.telegram_id
        FROM route_subscriptions rs
        JOIN users u ON u.id = rs.user_id
        WHERE rs.active=true
          AND (
            rs.from_city ILIKE $1
            OR rs.to_city ILIKE $1
            OR rs.from_city='*'
            OR rs.to_city='*'
          )
          AND rs.user_id <> $2
    """, data["current_city"], user_id)

    for sub in subs:
        try:
            await context.bot.send_message(
                chat_id=sub["telegram_id"],
                text=(
                    f"🚚 Новая машина по вашей подписке\n\n"
                    f"📍 {data['current_city']}\n"
                    f"📦 {data['body_type']}\n"
                    f"⚖️ {data['capacity_tons']} т\n"
                    f"📦 {data['volume_m3']} м³\n\n"
                    f"🔎 Поиск: /findtruck {data['current_city']}"
                )
            )
        except Exception:
            pass

    return ConversationHandler.END


async def truck_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Добавление машины отменено")
    return ConversationHandler.END




async def mytruck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    truck = await DB.fetchrow("""
        SELECT
            id,
            current_city,
            body_type,
            capacity_tons,
            volume_m3,
            comment,
            status,
            created_at
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if not truck:
        await update.message.reply_text(
            "🚚 У вас пока нет машины\\n\\n"
            "Добавить: /truck"
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔁 Обновить в поиске", callback_data=f"truck_refresh_{truck['id']}")],
        [InlineKeyboardButton("❌ Убрать из поиска", callback_data=f"truck_hide_{truck['id']}")]
    ])

    await update.message.reply_text(
        f"🚚 Моя машина #{truck['id']}\\n\\n"
        f"📍 Город: {truck['current_city'] or '-'}\\n"
        f"📦 Кузов: {truck['body_type'] or '-'}\\n"
        f"⚖️ Тоннаж: {truck['capacity_tons'] or '-'} т\\n"
        f"📦 Объём: {truck['volume_m3'] or '-'} м³\\n"
        f"📝 Комментарий: {truck['comment'] or '-'}\\n"
        f"📊 Статус: {human_status(truck['status'])}",
        reply_markup=kb
    )




async def truck_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    truck_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    truck = await DB.fetchrow("""
        SELECT
            t.id,
            t.driver_id,
            t.refreshed_at,
            COALESCE(u.plan_type, 'free') AS plan_type
        FROM trucks t
        JOIN users u ON u.id = t.driver_id
        WHERE t.id=$1
    """, truck_id)

    if not truck:
        await q.message.reply_text("❌ Машина не найдена")
        return

    if truck["driver_id"] != user_id:
        await q.message.reply_text("⛔ Можно обновить только свою машину")
        return

    plan = truck["plan_type"] or "free"

    if plan == "free":
        can_refresh = await DB.fetchval("""
            SELECT $1::timestamp IS NULL OR $1::timestamp < now() - interval '12 hours'
        """, truck["refreshed_at"])

        if not can_refresh:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💼 Улучшить тариф", callback_data="menu_plans")]
            ])

            await q.message.reply_text(
                "⛔ На тарифе FREE можно поднимать машину раз в 12 часов.\n\n"
                "🔥 PRO: 1 раз в час\n"
                "⭐ COMPANY: без ограничений",
                reply_markup=kb
            )
            return

    elif plan == "pro":
        can_refresh = await DB.fetchval("""
            SELECT $1::timestamp IS NULL OR $1::timestamp < now() - interval '1 hour'
        """, truck["refreshed_at"])

        if not can_refresh:
            await q.message.reply_text(
                "⛔ На тарифе PRO можно поднимать машину раз в 1 час.\n\n"
                "Для безлимита нужен COMPANY."
            )
            return

    await DB.execute("""
        UPDATE trucks
        SET status='active', created_at=now(), refreshed_at=now()
        WHERE id=$1
    """, truck_id)

    await q.message.reply_text(f"🔁 Машина #{truck_id} обновлена в поиске")



async def truck_hide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    truck_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    truck = await DB.fetchrow("""
        SELECT id, driver_id
        FROM trucks
        WHERE id=$1
    """, truck_id)

    if not truck:
        await q.message.reply_text("❌ Машина не найдена")
        return

    if truck["driver_id"] != user_id:
        await q.message.reply_text("⛔ Можно убрать только свою машину")
        return

    await DB.execute("""
        UPDATE trucks
        SET status='hidden'
        WHERE id=$1
    """, truck_id)

    await q.message.reply_text(f"❌ Машина #{truck_id} убрана из поиска")



async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Подключить тариф", callback_data="buy_plan")]
    ])

    await update.message.reply_text(
        "💼 Тарифы Dalnoboy\n\n"
        "🆓 FREE\n"
        "• 1 активный груз\n"
        "• обычный показ в списке\n\n"
        "🔥 PRO\n"
        "• до 5 активных грузов\n"
        "• выше FREE в поиске\n"
        "• бейдж PRO\n\n"
        "📡 DISPATCHER\n"
        "• до 20 активных грузов\n"
        "• для диспетчеров\n"
        "• бейдж DISPATCHER\n\n"
        "⭐ COMPANY\n"
        "• без лимита грузов\n"
        "• самый высокий приоритет\n"
        "• бейдж COMPANY\n\n"
        "Для подключения тарифа нажмите кнопку ниже.",
        reply_markup=kb
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT full_name, role, verified, plan_type, plan_expires_at, created_at
        FROM users
        WHERE id=$1
    """, user_id)

    full_name = user["full_name"] if user and user["full_name"] else update.effective_user.full_name
    role = user["role"] if user and user["role"] else "driver"
    user_verified = user["verified"] if user else False
    plan_type = user["plan_type"] if user and user["plan_type"] else "free"
    plan_expires_at = user["plan_expires_at"] if user else None
    created_at = user["created_at"] if user else None

    plan_badges = {
        "company": "⭐ COMPANY",
        "pro": "🔥 PRO",
        "dispatcher": "📡 DISPATCHER",
        "free": "🆓 FREE"
    }

    role_badges = {
        "admin": "🛠 Админ",
        "driver": "🚚 Водитель",
        "company": "🏢 Компания",
        "dispatcher": "📡 Диспетчер"
    }

    stats = await DB.fetchrow("""
        SELECT
            COUNT(*) AS reviews_count,
            ROUND(AVG(overall_score)::numeric, 2) AS avg_score
        FROM reviews
        WHERE to_user_id=$1
          AND deleted_at IS NULL
    """, user_id)

    trucks_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM trucks
        WHERE driver_id=$1
    """, user_id)

    deals_total = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1
    """, user_id)

    deals_done = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE (c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1)
          AND d.status='done'
    """, user_id)

    deals_active = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE (c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1)
          AND d.status IN ('pending','active','in_progress')
    """, user_id)

    truck = await DB.fetchrow("""
        SELECT id, current_city, body_type, capacity_tons, volume_m3
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    text = (
        f"👤 {full_name}\n"
        f"{'✅ Проверен' if user_verified else '⚠️ Не проверен'}\n"
        f"Роль: {role_badges.get(role, role)}\n"
        f"Тариф: {plan_badges.get(plan_type, plan_type.upper())}\n"
        + (f"⏳ Тариф до: {plan_expires_at}\n" if plan_expires_at else "")
        + (f"📅 На платформе с: {created_at.date()}\n" if created_at else "")
        + "\n"
        f"⭐ Рейтинг: {stats['avg_score'] or 'нет оценок'}\n"
        f"💬 Отзывов: {stats['reviews_count']}\n"
        f"🚚 Машин: {trucks_count}\n"
        f"🤝 Сделок всего: {deals_total}\n"
        f"🚚 Активных сделок: {deals_active}\n"
        f"✅ Выполнено: {deals_done}\n"
    )

    if truck:
        text += (
            f"\n🚚 Основная машина #{truck['id']}\n"
            f"📍 {truck['current_city']}\n"
            f"📦 {truck['body_type']}\n"
            f"⚖️ {truck['capacity_tons']} т | {truck['volume_m3']} м³"
        )
    else:
        text += "\n🚚 Машина не добавлена"

    await update.message.reply_text(text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await ensure_user(update.effective_user)
    await update.message.reply_text(
        "🚛 Добро пожаловать в Dalnoboy Bros!",
        reply_markup=main_reply_keyboard()
    )
    await menu(update, context)

async def cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            c.id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.price_currency,
            c.status,
            COALESCE(u.plan_type, 'free') AS plan_type
        FROM cargo c
        LEFT JOIN users u ON u.id = c.created_by
        WHERE c.status='open'
        ORDER BY
            CASE
                WHEN COALESCE(u.plan_type, 'free')='company' THEN 1
                WHEN COALESCE(u.plan_type, 'free')='pro' THEN 2
                WHEN COALESCE(u.plan_type, 'free')='dispatcher' THEN 3
                ELSE 4
            END,
            c.id DESC
        LIMIT 10
    """)

    if not rows:
        await update.message.reply_text("📦 Нет грузов")
        return

    for r in rows:
        plan_type = r["plan_type"] if "plan_type" in r and r["plan_type"] else "free"

        if plan_type == "company":
            badge = "⭐ COMPANY"
        elif plan_type == "pro":
            badge = "🔥 PRO"
        elif plan_type == "dispatcher":
            badge = "📡 DISPATCHER"
        else:
            badge = "FREE"

        text = (
            f"📦 Груз #{r['id']}\n"
            f"🏷 Тариф: {badge}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📊 Статус: {human_status(r['status'])}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")],
            [InlineKeyboardButton("🚩 Жалоба", callback_data=f"report_{r['id']}")]
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
                [InlineKeyboardButton("❌ Снять груз", callback_data=f"cargo_cancel_{r['id']}")],
                [InlineKeyboardButton("🔁 Повторить", callback_data=f"cargo_clone_{r['id']}")],
                [InlineKeyboardButton("🔝 Поднять груз", callback_data=f"cargo_refresh_{r['id']}")]
            ])

        await update.message.reply_text(
            f"📦 Мой груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📊 Статус: {human_status(r['status'])}",
            reply_markup=kb
        )





async def cargo_clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT
            created_by,
            from_city,
            to_city,
            description,
            price_amount,
            price_currency
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно повторить только свой груз")
        return

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
        VALUES ($1,$2,$3,$4,$5,$6,'open')
        RETURNING id
    """,
        user_id,
        cargo["from_city"],
        cargo["to_city"],
        cargo["description"],
        cargo["price_amount"],
        cargo["price_currency"] or "RUB"
    )

    await q.message.reply_text(
        f"🔁 Груз повторён #{row['id']}\n\n"
        f"📍 {cargo['from_city']} → {cargo['to_city']}\n"
        f"💰 {format_price(cargo['price_amount'])} {cargo['price_currency'] or ''}"
    )




async def cargo_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[2])
    user_id = await ensure_user(q.from_user)

    cargo = await DB.fetchrow("""
        SELECT
            c.id,
            c.created_by,
            c.refreshed_at,
            COALESCE(u.plan_type, 'free') AS plan_type
        FROM cargo c
        JOIN users u ON u.id = c.created_by
        WHERE c.id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно поднимать только свой груз")
        return

    plan = cargo["plan_type"] or "free"

    if plan == "free":
        can_refresh = await DB.fetchval("""
            SELECT $1::timestamp IS NULL
            OR $1::timestamp < now() - interval '12 hours'
        """, cargo["refreshed_at"])

        if not can_refresh:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💼 Улучшить тариф", callback_data="menu_plans")]
            ])

            await q.message.reply_text(
                "⛔ FREE: поднятие груза раз в 12 часов\n\n"
                "🔥 PRO: раз в 1 час\n"
                "⭐ COMPANY: без ограничений",
                reply_markup=kb
            )
            return

    elif plan == "pro":
        can_refresh = await DB.fetchval("""
            SELECT $1::timestamp IS NULL
            OR $1::timestamp < now() - interval '1 hour'
        """, cargo["refreshed_at"])

        if not can_refresh:
            await q.message.reply_text(
                "⛔ PRO: поднятие груза раз в 1 час\n\n"
                "⭐ COMPANY — без ограничений"
            )
            return

    await DB.execute("""
        UPDATE cargo
        SET refreshed_at=now(), created_at=now()
        WHERE id=$1
    """, cargo_id)

    await q.message.reply_text(
        f"🔝 Груз #{cargo_id} поднят в поиске"
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






async def cargo_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    cargo_id = int(q.data.split("_")[1])

    user_id = await ensure_user(q.from_user)

    exists = await DB.fetchval("""
        SELECT id
        FROM cargo_reports
        WHERE cargo_id=$1
          AND user_id=$2
    """, cargo_id, user_id)

    if exists:
        await q.message.reply_text("⚠️ Жалоба уже отправлена")
        return

    await DB.execute("""
        INSERT INTO cargo_reports (
            cargo_id,
            user_id,
            reason
        )
        VALUES ($1,$2,'telegram_report')
    """, cargo_id, user_id)

    await q.message.reply_text(
        f"🚩 Жалоба на груз #{cargo_id} отправлена"
    )


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
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"Статус: {human_status(r['status'])}\n\n"
        )

    await update.message.reply_text(text)










async def adminnotes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /adminnotes DEAL_ID")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    rows = await DB.fetch("""
        SELECT n.id, n.note, n.created_at, u.full_name
        FROM deal_admin_notes n
        JOIN users u ON u.id = n.admin_user_id
        WHERE n.deal_id=$1
        ORDER BY n.id DESC
        LIMIT 20
    """, deal_id)

    if not rows:
        await update.message.reply_text(f"📝 По сделке #{deal_id} заметок нет")
        return

    text = f"📝 Заметки по сделке #{deal_id}\n\n"

    for r in reversed(rows):
        text += (
            f"#{r['id']} | {r['full_name'] or 'Админ'}\n"
            f"{r['note']}\n\n"
        )

    await update.message.reply_text(text[-3500:])


async def adminnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Используй: /adminnote DEAL_ID текст заметки")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    note = " ".join(context.args[1:]).strip()

    await DB.execute("""
        INSERT INTO deal_admin_notes (
            deal_id,
            admin_user_id,
            note
        )
        VALUES ($1,$2,$3)
    """, deal_id, admin_id, note)

    await update.message.reply_text(f"📝 Заметка по сделке #{deal_id} сохранена")


async def admindealchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /admindealchat DEAL_ID")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    rows = await DB.fetch("""
        SELECT
            dm.message_text,
            dm.created_at,
            u.full_name,
            u.id AS user_id
        FROM deal_messages dm
        JOIN users u ON u.id = dm.from_user_id
        WHERE dm.deal_id=$1
        ORDER BY dm.id DESC
        LIMIT 30
    """, deal_id)

    if not rows:
        await update.message.reply_text(f"💬 В сделке #{deal_id} сообщений нет")
        return

    text = f"🛠 Чат сделки #{deal_id}\n\n"

    for r in reversed(rows):
        text += (
            f"👤 user {r['user_id']} | {r['full_name'] or 'Без имени'}\n"
            f"{r['message_text']}\n\n"
        )

    await update.message.reply_text(text[-3500:])






async def closedispute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /closedispute DEAL_ID")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=false
        WHERE id=$1
    """, deal_id)

    await update.message.reply_text(f"✅ Спор по сделке #{deal_id} закрыт")


async def admindisputes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            c.created_by,
            r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.dispute=true
        ORDER BY d.id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("✅ Открытых споров нет")
        return

    text = "⚠️ Открытые споры\n\n"

    for r in rows:
        text += (
            f"Сделка #{r['id']} | статус: {human_status(r['status'])}\n"
            f"{r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"Заказчик user {r['created_by']} | водитель user {r['driver_id']}\n\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Чат первой сделки", callback_data=f"admin_dealchat_{rows[0]['id']}")],
        [InlineKeyboardButton("📝 Заметки первой сделки", callback_data=f"admin_notes_{rows[0]['id']}")],
        [InlineKeyboardButton("✅ Закрыть первый спор", callback_data=f"admin_close_dispute_{rows[0]['id']}")]
    ])

    await update.message.reply_text(text[-3500:], reply_markup=kb)




async def admin_dispute_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(q.from_user)
    if admin_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    data = q.data.split("_")
    deal_id = int(data[-1])

    if q.data.startswith("admin_dealchat_"):
        await q.message.reply_text(f"💬 Смотреть чат: /admindealchat {deal_id}")
        return

    if q.data.startswith("admin_notes_"):
        await q.message.reply_text(f"📝 Смотреть заметки: /adminnotes {deal_id}\nДобавить: /adminnote {deal_id} текст")
        return

    if q.data.startswith("admin_close_dispute_"):
        await DB.execute("""
            UPDATE deals
            SET dispute=false
            WHERE id=$1
        """, deal_id)

        await q.message.reply_text(f"✅ Спор по сделке #{deal_id} закрыт")
        return


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
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"Статус: {human_status(r['status'])}\n\n"
        )

    await update.message.reply_text(text)






async def adminreports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            cr.id,
            cr.cargo_id,
            cr.user_id,
            cr.reason,
            cr.status,
            cr.created_at,
            c.from_city,
            c.to_city
        FROM cargo_reports cr
        LEFT JOIN cargo c ON c.id = cr.cargo_id
        ORDER BY cr.id DESC
        LIMIT 20
    """)

    if not rows:
        await update.message.reply_text("🚩 Жалоб нет")
        return

    text = "🚩 Последние жалобы\n\n"

    for r in rows:
        text += (
            f"#{r['id']} | груз {r['cargo_id']} | user {r['user_id']}\n"
            f"{r['from_city']} → {r['to_city']}\n"
            f"Причина: {r['reason']}\n"
            f"Статус: {human_status(r['status'])}\n\n"
        )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Закрыть последнюю жалобу", callback_data=f"report_close_{rows[0]['id']}")]
    ])

    await update.message.reply_text(text, reply_markup=kb)




async def report_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    if user_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    report_id = int(q.data.split("_")[2])

    await DB.execute("""
        UPDATE cargo_reports
        SET status='closed'
        WHERE id=$1
    """, report_id)

    await q.message.reply_text(f"✅ Жалоба #{report_id} закрыта")


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










async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db_ok = await DB.fetchval("SELECT 1")
        db_status = "OK" if db_ok == 1 else "ERROR"
    except Exception:
        db_status = "ERROR"

    await update.message.reply_text(
        f"🚛 Dalnoboy PRO\n"
        f"Версия: {BOT_VERSION}\n"
        f"PostgreSQL: {db_status}\n"
        f"Режим: MVP"
    )








async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db_ok = await DB.fetchval("SELECT 1")
        open_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
        active_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status IN ('active','in_progress')")

        await update.message.reply_text(
            f"🟢 Dalnoboy работает\n\n"
            f"Версия: {BOT_VERSION}\n"
            f"База: {'OK' if db_ok == 1 else 'ERROR'}\n"
            f"Открытых грузов: {open_cargo}\n"
            f"Активных сделок: {active_deals}"
        )
    except Exception as e:
        await update.message.reply_text(f"🔴 Ошибка статуса: {e}")


async def errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    result = subprocess.run(
        ["/usr/bin/journalctl", "-u", "dalnoboy", "-n", "100", "--no-pager"],
        capture_output=True,
        text=True
    )

    lines = [
        line for line in result.stdout.splitlines()
        if "ERROR" in line or "Traceback" in line or "Exception" in line
    ]

    text = "\n".join(lines[-20:]) or "✅ Ошибок не найдено"

    await update.message.reply_text(
        f"🚨 Ошибки:\n\n{text[-3000:]}"
    )


async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    result = subprocess.run(
        ["/usr/bin/journalctl", "-u", "dalnoboy", "-n", "10", "--no-pager"],
        capture_output=True,
        text=True
    )

    text = result.stdout[-1800:] or "Лог пуст"

    await update.message.reply_text(
        f"📜 Последние логи:\n\n{text}"
    )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        db_ok = await DB.fetchval("SELECT 1")
        await update.message.reply_text(
            "✅ Health OK\n"
            f"🤖 Бот: работает\n"
            f"🗄 PostgreSQL: {'OK' if db_ok == 1 else 'ERROR'}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Health ERROR: {e}")


async def backupbot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    backup_dir = "/root/dalnoboy/backups"
    filename = f"bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"

    shutil.copy(
        "/root/dalnoboy/bot.py",
        f"{backup_dir}/{filename}"
    )

    await update.message.reply_text(
        f"💾 Бэкап создан:\n{filename}"
    )




async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    verified = await DB.fetchval("""
        SELECT verified FROM users WHERE id=$1
    """, user_id)

    if verified:
        await update.message.reply_text("✅ Ваш профиль уже проверен")
        return

    await update.message.reply_text(
        "📩 Заявка на верификацию отправлена.\n"
        "Админ проверит профиль и подтвердит статус."
    )


async def adminverify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /adminverify USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    await DB.execute("""
        UPDATE users
        SET verified=true
        WHERE id=$1
    """, target_id)

    await update.message.reply_text(f"✅ User #{target_id} верифицирован")




async def unverify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /unverify USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    await DB.execute("""
        UPDATE users
        SET verified=false
        WHERE id=$1
    """, target_id)

    await update.message.reply_text(f"⚠️ User #{target_id} больше не верифицирован")




async def ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /ban USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    await DB.execute("""
        UPDATE users
        SET banned=true
        WHERE id=$1
    """, target_id)

    await update.message.reply_text(f"⛔ User #{target_id} заблокирован")


async def unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if not context.args:
        await update.message.reply_text("Используй: /unban USER_ID")
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    await DB.execute("""
        UPDATE users
        SET banned=false
        WHERE id=$1
    """, target_id)

    await update.message.reply_text(f"✅ User #{target_id} разблокирован")




async def adminsubs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            rs.id,
            rs.user_id,
            rs.from_city,
            rs.to_city,
            rs.active,
            u.full_name
        FROM route_subscriptions rs
        JOIN users u ON u.id = rs.user_id
        ORDER BY rs.id DESC
        LIMIT 30
    """)

    if not rows:
        await update.message.reply_text("🔕 Подписок нет")
        return

    text = "🔔 Подписки пользователей\n\n"

    for r in rows:
        status = "active" if r["active"] else "off"
        text += (
            f"#{r['id']} | user {r['user_id']} | {r['full_name'] or '-'}\n"
            f"{r['from_city']} → {r['to_city']} | {status}\n\n"
        )

    await update.message.reply_text(text[-3500:])






async def today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    new_users = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
    new_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at::date = CURRENT_DATE")
    new_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE created_at::date = CURRENT_DATE")
    new_reviews = await DB.fetchval("SELECT COUNT(*) FROM reviews WHERE created_at::date = CURRENT_DATE")
    new_subs = await DB.fetchval("SELECT COUNT(*) FROM route_subscriptions WHERE created_at::date = CURRENT_DATE")

    await update.message.reply_text(
        f"📅 Сегодня\n\n"
        f"👥 Новых пользователей: {new_users}\n"
        f"📦 Новых грузов: {new_cargo}\n"
        f"🤝 Новых сделок: {new_deals}\n"
        f"⭐ Новых отзывов: {new_reviews}\n"
        f"🔔 Новых подписок: {new_subs}"
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
    verified_count = await DB.fetchval("SELECT COUNT(*) FROM users WHERE verified=true")
    banned_count = await DB.fetchval("SELECT COUNT(*) FROM users WHERE banned=true")
    cargo_open = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
    cargo_done = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='done'")
    deals_total = await DB.fetchval("SELECT COUNT(*) FROM deals")
    disputes_open = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE dispute=true")
    subs_active = await DB.fetchval("SELECT COUNT(*) FROM route_subscriptions WHERE active=true")
    new_users = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
    new_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at::date = CURRENT_DATE")
    new_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE created_at::date = CURRENT_DATE")
    subs_active = await DB.fetchval("SELECT COUNT(*) FROM route_subscriptions WHERE active=true")

    await update.message.reply_text(
        f"📄 Отчёт Dalnoboy\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"✅ Проверенных: {verified_count}\n"
        f"⛔ Забаненных: {banned_count}\n"
        f"📦 Открытых грузов: {cargo_open}\n"
        f"🏁 Завершённых грузов: {cargo_done}\n"
        f"🤝 Сделок всего: {deals_total}\n"
        f"⚠️ Открытых споров: {disputes_open}\n"
        f"🔔 Активных подписок: {subs_active}"
    )


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
        "/docs — документы\n"
        "/rules — правила\n"
        "/support — поддержка\n"
        "/faq — частые вопросы\n"
        "/help — помощь"
    )


async def topcarriers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            u.id,
            u.full_name,
            u.verified,
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
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}",
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
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")],
            [InlineKeyboardButton("🚩 Жалоба", callback_data=f"report_{r['id']}")]
        ])

        await update.message.reply_text(
            f"💰 Груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📊 Статус: {human_status(r['status'])}",
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
            t.capacity_tons,
            t.volume_m3,
            t.comment,
            u.full_name,
            u.verified,
            COALESCE(u.plan_type, 'free') AS plan_type,
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
        GROUP BY
            t.id,
            t.current_city,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            t.comment,
            u.full_name,
            u.verified,
            u.plan_type,
            t.driver_id
        ORDER BY
            CASE
                WHEN COALESCE(u.plan_type, 'free')='company' THEN 1
                WHEN COALESCE(u.plan_type, 'free')='dispatcher' THEN 2
                WHEN COALESCE(u.plan_type, 'free')='pro' THEN 3
                ELSE 4
            END,
            COALESCE(t.refreshed_at, t.created_at) DESC,
            avg_score DESC,
            done_deals DESC
        LIMIT 20
    """, f"%{query}%")

    if not rows:
        await update.message.reply_text(f"📭 Машины по запросу «{query}» не найдены")
        return

    for r in rows:
        plan_type = r["plan_type"] or "free"

        if plan_type == "company":
            badge = "⭐ COMPANY"
        elif plan_type == "dispatcher":
            badge = "📡 DISPATCHER"
        elif plan_type == "pro":
            badge = "🔥 PRO"
        else:
            badge = "FREE"

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "👤 Профиль",
                    callback_data=f"driver_profile_{r['truck_id']}"
                ),
                InlineKeyboardButton(
                    "🤝 Сделка",
                    callback_data=f"truck_deal_{r['truck_id']}"
                )
            ]
        ])

        await update.message.reply_text(
            f"🚚 Машина #{r['truck_id']}\n"
            f"🏷 Тариф: {badge}\n"
            f"👤 Водитель: {r['full_name']} {'✅' if r['verified'] else '⚠️'}\n"
            f"📍 Город: {r['current_city']}\n"
            f"📦 Кузов: {r['body_type']}\n"
            f"⚖️ Тоннаж: {r['capacity_tons'] or '-'} т\n"
            f"📦 Объём: {r['volume_m3'] or '-'} м³\n"
            f"📝 Комментарий: {r['comment'] or '-'}\n"
            f"⭐ Рейтинг: {r['avg_score'] or 'нет'} ({r['reviews_count']} отзывов)\n"
            f"✅ Завершённых сделок: {r['done_deals']}",
            reply_markup=kb
        )







async def driver_profile_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    truck_id = int(q.data.split("_")[2])

    row = await DB.fetchrow("""
        SELECT
            t.id AS truck_id,
            u.id AS user_id,
            u.full_name,
            u.verified,
            COALESCE(u.plan_type, 'free') AS plan_type,
            t.current_city,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            t.comment,
            COALESCE(ROUND(AVG(rv.overall_score)::numeric, 2), 0) AS avg_score,
            COUNT(rv.id) AS reviews_count
        FROM trucks t
        JOIN users u ON u.id = t.driver_id
        LEFT JOIN reviews rv ON rv.to_user_id = u.id AND rv.deleted_at IS NULL
        WHERE t.id=$1
        GROUP BY
            t.id, u.id, u.full_name, u.verified, u.plan_type,
            t.current_city, t.body_type, t.capacity_tons, t.volume_m3, t.comment
    """, truck_id)

    if not row:
        await q.message.reply_text("❌ Машина не найдена")
        return

    await q.message.reply_text(
        f"👤 Профиль водителя #{row['user_id']}\n\n"
        f"Имя: {row['full_name'] or '-'} {'✅' if row['verified'] else '⚠️'}\n"
        f"Тариф: {row['plan_type'].upper()}\n"
        f"⭐ Рейтинг: {row['avg_score'] or 'нет'} ({row['reviews_count']} отзывов)\n\n"
        f"🚚 Машина #{row['truck_id']}\n"
        f"📍 Город: {row['current_city'] or '-'}\n"
        f"📦 Кузов: {row['body_type'] or '-'}\n"
        f"⚖️ Тоннаж: {row['capacity_tons'] or '-'} т\n"
        f"📦 Объём: {row['volume_m3'] or '-'} м³\n"
        f"📝 Комментарий: {row['comment'] or '-'}"
    )




async def truck_deal_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    truck_id = int(q.data.split("_")[2])

    cargos = await DB.fetch("""
        SELECT
            id,
            from_city,
            to_city,
            description,
            price_amount
        FROM cargo
        WHERE created_by=$1
          AND status='open'
        ORDER BY id DESC
        LIMIT 10
    """, user_id)

    if not cargos:
        await q.message.reply_text(
            "📦 У вас нет открытых грузов\\n\\n"
            "Создать груз: /newcargo"
        )
        return

    buttons = []

    for c in cargos:
        buttons.append([
            InlineKeyboardButton(
                f"📦 #{c['id']} {c['from_city']} → {c['to_city']}",
                callback_data=f"create_deal_{truck_id}_{c['id']}"
            )
        ])

    kb = InlineKeyboardMarkup(buttons)

    await q.message.reply_text(
        "🤝 Выберите груз для сделки:",
        reply_markup=kb
    )




async def create_deal_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    parts = q.data.split("_")
    truck_id = int(parts[2])
    cargo_id = int(parts[3])

    cargo = await DB.fetchrow("""
        SELECT id, created_by, from_city, to_city
        FROM cargo
        WHERE id=$1
          AND status='open'
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден или уже закрыт")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Это не ваш груз")
        return

    truck = await DB.fetchrow("""
        SELECT
            t.id,
            t.driver_id,
            u.telegram_id,
            u.full_name
        FROM trucks t
        JOIN users u ON u.id = t.driver_id
        WHERE t.id=$1
          AND t.status='active'
    """, truck_id)

    if not truck:
        await q.message.reply_text("❌ Машина не найдена")
        return

    existing = await DB.fetchrow("""
        SELECT id
        FROM deals
        WHERE cargo_id=$1
          AND truck_id=$2
          AND status IN ('pending','active')
        LIMIT 1
    """, cargo_id, truck_id)

    if existing:
        await q.message.reply_text(f"⚠️ Сделка уже создана #{existing['id']}")
        return

    row = await DB.fetchrow("""
        INSERT INTO deals (
            cargo_id,
            truck_id,
            status,
            driver_chat_id
        )
        VALUES ($1,$2,'pending',$3)
        RETURNING id
    """, cargo_id, truck_id, truck["telegram_id"])

    await q.message.reply_text(
        f"✅ Сделка создана #{row['id']}\n\n"
        f"📦 Груз #{cargo_id}: {cargo['from_city']} → {cargo['to_city']}\n"
        f"🚚 Машина #{truck_id}\n"
        f"👤 Водитель: {truck['full_name'] or '-'}"
    )

    try:
        await context.bot.send_message(
            chat_id=truck["telegram_id"],
            text=(
                f"🤝 Вам предложили сделку #{row['id']}\n\n"
                f"📦 Груз: {cargo['from_city']} → {cargo['to_city']}\n"
                f"Откройте /deals"
            )
        )
    except Exception:
        pass



async def subroutes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT from_city, to_city, COUNT(*) AS cnt
        FROM route_subscriptions
        WHERE active=true
        GROUP BY from_city, to_city
        ORDER BY cnt DESC
        LIMIT 10
    """)

    if not rows:
        await update.message.reply_text("🔕 Активных подписок нет")
        return

    text = "🔔 Популярные подписки\n\n"

    for r in rows:
        text += f"🚩 {r['from_city']} → {r['to_city']} — {r['cnt']} подписок\n"

    await update.message.reply_text(text)


async def routes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT from_city, to_city, COUNT(*) AS cnt
        FROM cargo
        WHERE status='open'
        GROUP BY from_city, to_city
        ORDER BY cnt DESC
        LIMIT 10
    """)

    if not rows:
        await update.message.reply_text("📭 Открытых направлений нет")
        return

    text = "🛣 Популярные направления\n\n"

    for r in rows:
        text += f"🚩 {r['from_city']} → {r['to_city']} — {r['cnt']} грузов\n"

    await update.message.reply_text(text)


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
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📊 Статус: {human_status(r['status'])}"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")],
            [InlineKeyboardButton("🚩 Жалоба", callback_data=f"report_{r['id']}")]
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







async def replydeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if "last_deal_id" not in context.user_data:
        await update.message.reply_text("Сначала откройте чат сделки через /dealchat DEAL_ID")
        return

    if not context.args:
        await update.message.reply_text("Используй: /replydeal текст")
        return

    deal_id = context.user_data["last_deal_id"]
    text = " ".join(context.args).strip()

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, text)

    other_tg = await DB.fetchval("""
        SELECT u.telegram_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        JOIN users u ON u.id = CASE
            WHEN $2 = c.created_by THEN r.driver_id
            ELSE c.created_by
        END
        WHERE d.id=$1
    """, deal_id, user_id)

    if other_tg:
        await context.bot.send_message(
            chat_id=other_tg,
            text=f"💬 Новое сообщение в сделке #{deal_id}\n\n{text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Ответить", callback_data=f"deal_chat_{deal_id}")],
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_chat_{deal_id}")]
            ])
        )

    await update.message.reply_text(f"💬 Ответ отправлен в сделку #{deal_id}")




async def searchdeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text("Используй: /searchdeal DEAL_ID слово")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    query = " ".join(context.args[1:]).strip()

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    rows = await DB.fetch("""
        SELECT dm.message_text, u.full_name
        FROM deal_messages dm
        JOIN users u ON u.id = dm.from_user_id
        WHERE dm.deal_id=$1
          AND dm.message_text ILIKE $2
        ORDER BY dm.id DESC
        LIMIT 10
    """, deal_id, f"%{query}%")

    if not rows:
        await update.message.reply_text("📭 Ничего не найдено")
        return

    text = f"🔎 Поиск в сделке #{deal_id}: {query}\n\n"

    for r in reversed(rows):
        text += f"👤 {r['full_name'] or 'Пользователь'}: {r['message_text']}\n\n"

    await update.message.reply_text(text[-3500:])






async def disputereason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text("Используй: /disputereason DEAL_ID причина")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    reason = " ".join(context.args[1:]).strip()

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=true
        WHERE id=$1
    """, deal_id)

    await DB.execute("""
        INSERT INTO deal_messages (deal_id, from_user_id, message_text)
        VALUES ($1,$2,$3)
    """, deal_id, user_id, f"⚠️ Причина спора: {reason}")

    await update.message.reply_text(f"⚠️ Причина спора по сделке #{deal_id} сохранена")


async def dispute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not context.args:
        await update.message.reply_text("Используй: /dispute DEAL_ID")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=true
        WHERE id=$1
    """, deal_id)

    msg = "⚠️ Спор открыт пользователем"

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, msg)

    await update.message.reply_text(f"⚠️ Спор по сделке #{deal_id} открыт")


async def dealchat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not context.args:
        await update.message.reply_text("Используй: /dealchat DEAL_ID")
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

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
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    context.user_data["last_deal_id"] = deal_id

    rows = await DB.fetch("""
        SELECT
            dm.message_text,
            dm.created_at,
            u.full_name
        FROM deal_messages dm
        JOIN users u ON u.id = dm.from_user_id
        WHERE dm.deal_id=$1
        ORDER BY dm.id DESC
        LIMIT 20
    """, deal_id)

    if not rows:
        await update.message.reply_text(f"💬 В сделке #{deal_id} сообщений пока нет")
        return

    text = (
        f"💬 Чат сделки #{deal_id}\n"
        f"Написать: /dealmsg {deal_id} текст\n"
        f"Ответить сюда: /replydeal текст\n"
        f"Спор: /disputereason {deal_id} причина\n\n"
    )

    for r in reversed(rows):
        text += (
            f"👤 {r['full_name'] or 'Пользователь'}:\n"
            f"{r['message_text']}\n\n"
        )

    await update.message.reply_text(text[-3500:])


async def dealmsg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text(
            "Используй: /dealmsg DEAL_ID текст"
        )
        return

    try:
        deal_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("DEAL_ID должен быть числом")
        return

    text = " ".join(context.args[1:]).strip()

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
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, text)

    other_tg = await DB.fetchval("""
        SELECT u.telegram_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        JOIN users u ON u.id = CASE
            WHEN $2 = c.created_by THEN r.driver_id
            ELSE c.created_by
        END
        WHERE d.id=$1
    """, deal_id, user_id)

    if other_tg:
        await context.bot.send_message(
            chat_id=other_tg,
            text=f"💬 Новое сообщение в сделке #{deal_id}\n\n{text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Ответить", callback_data=f"deal_chat_{deal_id}")],
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_chat_{deal_id}")]
            ])
        )

    await update.message.reply_text(
        f"💬 Сообщение отправлено в сделку #{deal_id}"
    )


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
            u.verified,
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
            f"👤 Водитель: {r['full_name']} {'✅' if r['verified'] else '⚠️'}\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"📊 Статус: {human_status(r['status'])}\n"
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

        try:
            await context.bot.send_message(
                chat_id=response["telegram_id"],
                text=(
                    f"✅ Ваш отклик принят!\n"
                    f"📦 Груз #{response['cargo_id']}: {response['from_city']} → {response['to_city']}\n"
                    f"🤝 Сделка создана"
                )
            )
        except Exception as e:
            logging.error(f"accept notify failed: {e}")

        await q.message.reply_text(
            f"✅ Отклик #{response_id} принят. Сделка создана."
        )
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
            d.dispute,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            t.id AS truck_id,
            t.current_city,
            t.body_type,
            (
                SELECT COUNT(*)
                FROM deal_messages dm
                WHERE dm.deal_id = d.id
            ) AS messages_count
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1
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
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"💬 Сообщений: {r['messages_count']}\n"
            + ("⚠️ Спор открыт\n" if r["dispute"] else "")
            + f"📊 Статус: {human_status(r['status'])}"
        )

        buttons = [
            [
                InlineKeyboardButton("🚚 В пути", callback_data=f"deal_in_progress_{r['id']}"),
                InlineKeyboardButton("✅ Доставлено", callback_data=f"deal_done_{r['id']}")
            ],
            [
                InlineKeyboardButton("❌ Отменить", callback_data=f"deal_cancelled_{r['id']}")
            ],
            [
                InlineKeyboardButton("💬 Чат", callback_data=f"deal_chat_{r['id']}")
            ],
            [
                InlineKeyboardButton(
                    "✅ Закрыть спор" if r["dispute"] else "⚠️ Открыть спор",
                    callback_data=f"deal_closedispute_{r['id']}" if r["dispute"] else f"deal_dispute_{r['id']}"
                )
            ]
        ]

        if r["status"] == "done":
            buttons.append([
                InlineKeyboardButton("⭐ Оценить", callback_data=f"review_{r['id']}")
            ])

        kb = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(text, reply_markup=kb)







async def deal_closedispute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)
    deal_id = int(q.data.split("_")[2])

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await q.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await q.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=false
        WHERE id=$1
    """, deal_id)

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,'✅ Спор закрыт')
    """, deal_id, user_id)

    await q.message.reply_text(f"✅ Спор по сделке #{deal_id} закрыт")


async def deal_dispute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)
    deal_id = int(q.data.split("_")[2])

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await q.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await q.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=true
        WHERE id=$1
    """, deal_id)

    msg = "⚠️ Спор открыт пользователем"

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, msg)

    admin_tg = await DB.fetchval("SELECT telegram_id FROM users WHERE id=1")

    other_tg = await DB.fetchval("""
        SELECT u.telegram_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        JOIN users u ON u.id = CASE
            WHEN $2 = c.created_by THEN r.driver_id
            ELSE c.created_by
        END
        WHERE d.id=$1
    """, deal_id, user_id)

    if admin_tg:
        await context.bot.send_message(
            chat_id=admin_tg,
            text=f"⚠️ Открыт спор по сделке #{deal_id}\nПроверить: /admindisputes"
        )

    if other_tg:
        await context.bot.send_message(
            chat_id=other_tg,
            text=f"⚠️ По сделке #{deal_id} открыт спор\nОткрыть: /dealchat {deal_id}"
        )

    await q.message.reply_text(
        f"⚠️ Спор по сделке #{deal_id} открыт\n\n"
        f"Чтобы добавить причину, напишите:\n"
        f"/dispute {deal_id} причина спора"
    )






async def deal_reason_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    deal_id = int(q.data.split("_")[3])

    await q.message.reply_text(
        f"⚠️ Чтобы добавить причину спора по сделке #{deal_id}:\n\n"
        f"/disputereason {deal_id} ваша причина"
    )


async def deal_write_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    deal_id = int(q.data.split("_")[3])

    await q.message.reply_text(
        f"✍️ Чтобы написать в чат сделки #{deal_id}:\n\n"
        f"/dealmsg {deal_id} ваш текст"
    )


async def deal_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    deal_id = int(q.data.split("_")[2])
    context.user_data["chat_deal_id"] = deal_id

    await q.message.reply_text(
        f"💬 Чат сделки #{deal_id}\n\n"
        f"Напишите сообщение обычным текстом.\n"
        f"История: /dealchat {deal_id}\n"
        f"Отмена: /cancel"
    )


async def deal_chat_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deal_id = context.user_data.get("chat_deal_id")

    if not deal_id:
        return

    user_id = await ensure_user(update.effective_user)
    text = update.message.text.strip()

    if text == "/cancel":
        context.user_data.pop("chat_deal_id", None)
        await update.message.reply_text("❌ Отменено")
        return

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            c.created_by,
            COALESCE(r.driver_id, t.driver_id) AS driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        context.user_data.pop("chat_deal_id", None)
        await update.message.reply_text("❌ Сделка не найдена")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        context.user_data.pop("chat_deal_id", None)
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, text)

    other_tg = await DB.fetchval("""
        SELECT u.telegram_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        JOIN users u ON u.id = CASE
            WHEN $2 = c.created_by THEN COALESCE(r.driver_id, t.driver_id)
            ELSE c.created_by
        END
        WHERE d.id=$1
    """, deal_id, user_id)

    if other_tg:
        await context.bot.send_message(
            chat_id=other_tg,
            text=f"💬 Новое сообщение в сделке #{deal_id}\n\n{text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Ответить", callback_data=f"deal_chat_{deal_id}")],
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_chat_{deal_id}")]
            ])
        )

    context.user_data.pop("chat_deal_id", None)
    await update.message.reply_text(f"✅ Сообщение отправлено в сделку #{deal_id}")


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
        LEFT JOIN responses r ON r.id = d.response_id
        JOIN trucks t ON t.id = d.truck_id
        JOIN users driver ON driver.id = COALESCE(r.driver_id, t.driver_id)
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
        SELECT id, banned FROM users
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
    tg_user = update.effective_user
    user_id = await ensure_user(tg_user)

    user = await DB.fetchrow("""
        SELECT COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    plan_type = user["plan_type"]

    active_cargo = await DB.fetchval("""
        SELECT COUNT(*)
        FROM cargo
        WHERE created_by=$1
          AND status='open'
    """, user_id)

    limits = {
        "free": 1,
        "pro": 5,
        "dispatcher": 20,
        "company": 999999
    }

    limit = limits.get(plan_type, 1)

    if active_cargo >= limit:
        await update.message.reply_text(
            f"⛔ Лимит активных грузов для тарифа {plan_type.upper()}: {limit}"
        )
        return ConversationHandler.END

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

    recent_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM cargo
        WHERE created_by=$1
          AND created_at > now() - interval '10 minutes'
    """, user_id)

    if recent_count >= 5:
        await update.message.reply_text(
            "⛔ Слишком много грузов. Подождите 10 минут."
        )
        return ConversationHandler.END

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

    subs = await DB.fetch("""
        SELECT u.id AS user_id, u.telegram_id
        FROM route_subscriptions rs
        JOIN users u ON u.id = rs.user_id
        WHERE rs.active=true
          AND (rs.from_city ILIKE $1 OR rs.from_city='*')
          AND (rs.to_city ILIKE $2 OR rs.to_city='*')
          AND rs.user_id <> $3
    """, data["from_city"], data["to_city"], user_id)

    for sub in subs:
        inserted = await DB.fetchrow("""
            INSERT INTO notification_log (
                user_id,
                entity_type,
                entity_id
            )
            VALUES ($1,'cargo',$2)
            ON CONFLICT (user_id, entity_type, entity_id) DO NOTHING
            RETURNING id
        """, sub["user_id"], row["id"])

        if not inserted:
            continue

        try:
            await context.bot.send_message(
                chat_id=sub["telegram_id"],
                text=(
                    f"🔔 Новый груз по вашей подписке\n"
                    f"📦 #{row['id']}\n"
                    f"🚩 {data['from_city']} → {data['to_city']}\n"
                    f"💰 {data['price_amount']} RUB\n"
                    f"Открыть список: /cargo"
                )
            )
        except Exception as e:
            logging.warning(f"Subscription notify failed: {e}")

    context.user_data.pop("newcargo", None)
    return ConversationHandler.END


async def newcargo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("newcargo", None)
    await update.message.reply_text("❌ Создание груза отменено")
    return ConversationHandler.END



async def reply_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📦 Грузы":
        return await cargo(update, context)
    if text == "🚚 Машина":
        return await truck(update, context)
    if text == "🤝 Сделки":
        return await deals_list(update, context)
    if text == "👤 Профиль":
        return await profile(update, context)
    if text == "🏠 Меню":
        return await menu(update, context)
    if text == "🆘 Помощь":
        return await help_cmd(update, context)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("BOT ERROR", exc_info=context.error)


async def post_init(app: Application):
    await init_db()


async def admin_setplan_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(update.effective_user)
    if admin_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    parts = q.data.split("_")
    target_id = int(parts[2])
    plan = parts[3]

    allowed = ["free", "pro", "dispatcher", "company"]
    if plan not in allowed:
        await q.message.reply_text("❌ Неизвестный тариф")
        return

    row = await DB.fetchrow("""
        UPDATE users
        SET plan_type=$1
        WHERE id=$2
        RETURNING telegram_id, full_name
    """, plan, target_id)

    if not row:
        await q.message.reply_text("❌ Пользователь не найден")
        return

    await q.message.reply_text(
        f"✅ Пользователь #{target_id} переведён на тариф {plan.upper()}"
    )

    try:
        await context.bot.send_message(
            chat_id=row["telegram_id"],
            text=f"✅ Вам подключён тариф {plan.upper()}"
        )
    except Exception:
        pass


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📦 Грузы", callback_data="menu_cargo"),
            InlineKeyboardButton("➕ Создать груз", callback_data="menu_newcargo"),
        ],
        [
            InlineKeyboardButton("🚚 Моя машина", callback_data="menu_truck"),
            InlineKeyboardButton("🔍 Найти машину", callback_data="menu_findtruck"),
        ],
        [
            InlineKeyboardButton("🤝 Сделки", callback_data="menu_deals"),
            InlineKeyboardButton("📨 Отклики", callback_data="menu_responses"),
        ],
        [
            InlineKeyboardButton("👤 Профиль", callback_data="menu_profile"),
            InlineKeyboardButton("💳 Тарифы", callback_data="menu_plans"),
        ],
        [
            InlineKeyboardButton("ℹ️ Помощь", callback_data="menu_help"),
            InlineKeyboardButton("🛟 Поддержка", callback_data="menu_support"),
        ],
    ]

    await update.message.reply_text(
        "🏠 Главное меню\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    await update.message.reply_text(
        "Нижнее меню включено 👇",
        reply_markup=main_reply_keyboard()
    )


async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    fake_update = Update(update.update_id, message=q.message)

    if q.data == "menu_today":
        return await today(fake_update, context)
    if q.data == "menu_deals":
        return await deals_list(fake_update, context)
    if q.data == "menu_responses":
        return await responses_list(fake_update, context)
    if q.data == "menu_profile":
        return await profile(fake_update, context)
    if q.data == "menu_plans":
        return await plans(fake_update, context)

    if q.data == "buy_plan":
        user_id = await ensure_user(update.effective_user)
        tg_user = update.effective_user

        await q.message.reply_text(
            f"✅ Заявка отправлена администратору.\n\n"
            f"🆔 Ваш ID: {user_id}"
        )

        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔥 PRO", callback_data=f"admin_setplan_{user_id}_pro"),
                InlineKeyboardButton("📡 DISPATCHER", callback_data=f"admin_setplan_{user_id}_dispatcher"),
            ],
            [
                InlineKeyboardButton("⭐ COMPANY", callback_data=f"admin_setplan_{user_id}_company"),
                InlineKeyboardButton("🆓 FREE", callback_data=f"admin_setplan_{user_id}_free"),
            ],
        ])

        username = f"@{tg_user.username}" if tg_user.username else "-"

        try:
            await context.bot.send_message(
                chat_id=439871270,
                text=(
                    "💳 Новая заявка на тариф\n\n"
                    f"User ID: {user_id}\n"
                    f"Telegram ID: {tg_user.id}\n"
                    f"Имя: {tg_user.full_name or '-'}\n"
                    f"Username: {username}"
                ),
                reply_markup=admin_kb
            )
        except Exception:
            pass

        return

    if q.data == "menu_truck":
        await q.message.reply_text(
            "🚚 Добавление машины\n\n"
            "Для запуска анкеты отправьте команду:\n"
            "/truck"
        )
        return
    if q.data == "menu_health":
        return await health(fake_update, context)
    if q.data == "menu_status":
        return await status_cmd(fake_update, context)
    if q.data == "menu_help":
        return await help_cmd(fake_update, context)
    if q.data == "menu_docs":
        return await docs(fake_update, context)
    if q.data == "menu_rules":
        return await rules(fake_update, context)
    if q.data == "menu_support":
        return await support(fake_update, context)

    if q.data == "menu_adminhelp":
        return await adminhelp(fake_update, context)

    if q.data == "menu_dashboard":
        user_id = await ensure_user(q.from_user)

        if user_id != 1:
            await q.message.reply_text("⛔ Нет доступа")
            return

        users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
        cargo_open = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
        cargo_total = await DB.fetchval("SELECT COUNT(*) FROM cargo")
        trucks_active = await DB.fetchval("SELECT COUNT(*) FROM trucks WHERE status='active'")
        deals_active = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status IN ('active','in_progress')")
        disputes_open = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE dispute=true")
        subs_active = await DB.fetchval("SELECT COUNT(*) FROM route_subscriptions WHERE active=true")
        new_users = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
        new_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at::date = CURRENT_DATE")
        new_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE created_at::date = CURRENT_DATE")

        await q.message.reply_text(
            "📊 Dashboard\n\n"
            f"👥 Пользователей: {users_count}\n"
            f"📦 Грузов всего: {cargo_total}\n"
            f"🟢 Открытых грузов: {cargo_open}\n"
            f"🚚 Активных машин: {trucks_active}\n"
            f"🤝 Активных сделок: {deals_active}\n"
            f"⚠️ Открытых споров: {disputes_open}"
        )
        return

    if q.data == "menu_cargo":
        return await cargo(fake_update, context)
    if q.data == "menu_mycargo":
        return await mycargo(fake_update, context)
    if q.data == "menu_newcargo":
        await q.message.reply_text("➕ Чтобы создать груз, отправь команду /newcargo")
        return
    if q.data == "menu_findcargo":
        await q.message.reply_text("🔎 Поиск груза:\n/find Москва")
        return
    if q.data == "menu_findtruck":
        await q.message.reply_text("🔍 Поиск машины:\n/findtruck Москва")
        return

    await q.message.reply_text("Неизвестный пункт меню")



async def dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
    cargo_open = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
    cargo_total = await DB.fetchval("SELECT COUNT(*) FROM cargo")
    trucks_active = await DB.fetchval("SELECT COUNT(*) FROM trucks WHERE status='active'")
    deals_active = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status IN ('active','in_progress')")
    disputes_open = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE dispute=true")
    subs_active = await DB.fetchval("SELECT COUNT(*) FROM route_subscriptions WHERE active=true")
    new_users = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
    new_cargo = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at::date = CURRENT_DATE")
    new_deals = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE created_at::date = CURRENT_DATE")

    await update.message.reply_text(
        "📊 Dashboard\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"📦 Грузов всего: {cargo_total}\n"
        f"🟢 Открытых грузов: {cargo_open}\n"
        f"🚚 Активных машин: {trucks_active}\n"
        f"🤝 Активных сделок: {deals_active}\n"
        f"⚠️ Открытых споров: {disputes_open}\n"
        f"🔔 Активных подписок: {subs_active}\n\n"
        f"📅 Сегодня:\n"
        f"👥 Новых пользователей: {new_users}\n"
        f"📦 Новых грузов: {new_cargo}\n"
        f"🤝 Новых сделок: {new_deals}"
    )


async def docs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 Документы для перевозки\n\n"
        "1. Договор-заявка на перевозку\n"
        "2. CMR / ТТН\n"
        "3. Акт приёма-передачи груза\n"
        "4. Правила безопасной сделки\n\n"
        "Скоро добавим шаблоны прямо в бота."
    )


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📜 Правила платформы\n\n"
        "1. Запрещён спам\n"
        "2. Запрещён фейковый груз\n"
        "3. Уважайте участников платформы\n"
        "4. Проверяйте документы перед сделкой\n"
        "5. Не переводите деньги вне сделки\n\n"
        "Нарушения могут привести к блокировке."
    )


async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛠 Поддержка платформы\n\n"
        "Если возникла проблема:\n"
        "• спор по сделке\n"
        "• подозрение на мошенничество\n"
        "• баг платформы\n"
        "• ошибка груза\n\n"
        "Напишите администратору:\n"
        "@your_support_username"
    )


async def faq(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "❓ FAQ Dalnoboy\n\n"
        "🚛 Как добавить машину?\n"
        "/truck Москва тент\n\n"
        "📦 Как создать груз?\n"
        "/newcargo\n\n"
        "🔎 Как найти груз?\n"
        "/find Москва\n\n"
        "🤝 Где мои сделки?\n"
        "/deals\n\n"
        "📄 Где документы?\n"
        "/docs\n\n"
        "🛠 Поддержка:\n"
        "/support"
    )


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 pong")


async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    delta = datetime.now() - START_TIME

    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    await update.message.reply_text(
        f"⏱ Uptime\\n\\n"
        f"{days}d {hours}h {minutes}m {seconds}s"
    )


async def monetization(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT COALESCE(plan_type, 'free') AS plan_type, COUNT(*) AS cnt
        FROM users
        GROUP BY COALESCE(plan_type, 'free')
        ORDER BY cnt DESC
    """)

    active_subs = await DB.fetchval("""
        SELECT COUNT(*)
        FROM user_subscriptions
        WHERE status='active'
          AND (expires_at IS NULL OR expires_at > now())
    """)

    payments_count = await DB.fetchval("SELECT COUNT(*) FROM payments")

    payments_sum = await DB.fetchval("""
        SELECT COALESCE(SUM(amount_minor), 0)
        FROM payments
        WHERE deleted_at IS NULL
    """)

    text = "💰 Монетизация\n\n"

    text += "👥 Пользователи по тарифам:\n"
    for r in rows:
        text += f"• {r['plan_type']}: {r['cnt']}\n"

    text += (
        f"\n🔁 Активных подписок: {active_subs}\n"
        f"💳 Платежей всего: {payments_count}\n"
        f"💵 Сумма платежей: {payments_sum / 100:.2f} RUB\n\n"
        "Текущий режим: MVP, оплаты ещё не включены."
    )

    await update.message.reply_text(text)


async def setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Используй:\n"
            "/setplan USER_ID free\n"
            "/setplan USER_ID pro\n"
            "/setplan USER_ID dispatcher\n"
            "/setplan USER_ID company"
        )
        return

    try:
        target_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("USER_ID должен быть числом")
        return

    plan = context.args[1].lower()

    allowed = ["free", "pro", "dispatcher", "company"]
    if plan not in allowed:
        await update.message.reply_text("Тариф должен быть: free, pro, dispatcher, company")
        return

    await DB.execute("""
        UPDATE users
        SET plan_type=$1
        WHERE id=$2
    """, plan, target_id)

    await update.message.reply_text(
        f"✅ User #{target_id} переведён на тариф {plan.upper()}"
    )

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

    truck_handler = ConversationHandler(
        entry_points=[CommandHandler("truck", truck_start)],
        states={
            TRUCK_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_city)],
            TRUCK_BODY: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_body)],
            TRUCK_TONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_tons)],
            TRUCK_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_volume)],
            TRUCK_COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, truck_comment)],
        },
        fallbacks=[CommandHandler("cancel", truck_cancel)],
    )

    app.add_handler(newcargo_handler)
    app.add_handler(truck_handler)

    app.add_handler(MessageHandler(filters.ALL, ban_guard), group=-2)
    app.add_handler(MessageHandler(filters.ALL, rate_limit_guard), group=-1)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cargo", cargo))
    app.add_handler(CommandHandler("find", find_cargo))
    app.add_handler(CommandHandler("routes", routes))
    app.add_handler(CommandHandler("subroutes", subroutes))
    app.add_handler(CommandHandler("findtruck", findtruck))
    app.add_handler(CommandHandler("mytruck", mytruck))
    app.add_handler(CommandHandler("findprice", findprice))
    app.add_handler(CommandHandler("mycargo", mycargo))
    app.add_handler(CommandHandler("deletedcargo", deletedcargo))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("topcarriers", topcarriers))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("rules", rules))
    app.add_handler(CommandHandler("support", support))
    app.add_handler(CommandHandler("faq", faq))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("adminusers", adminusers))
    app.add_handler(CommandHandler("admincargo", admincargo))
    app.add_handler(CommandHandler("admindeals", admindeals))
    app.add_handler(CommandHandler("admindisputes", admindisputes))
    app.add_handler(CommandHandler("closedispute", closedispute))
    app.add_handler(CommandHandler("admindealchat", admindealchat))
    app.add_handler(CommandHandler("adminnote", adminnote))
    app.add_handler(CommandHandler("adminnotes", adminnotes))
    app.add_handler(CommandHandler("adminreviews", adminreviews))
    app.add_handler(CommandHandler("adminreports", adminreports))
    app.add_handler(CommandHandler("adminhelp", adminhelp))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("today", today))
    app.add_handler(CommandHandler("adminsubs", adminsubs))
    app.add_handler(CommandHandler("verify", verify))
    app.add_handler(CommandHandler("adminverify", adminverify))
    app.add_handler(CommandHandler("unverify", unverify))
    app.add_handler(CommandHandler("ban", ban_user))
    app.add_handler(CommandHandler("unban", unban_user))
    app.add_handler(CommandHandler("backupbot", backupbot))
    app.add_handler(CommandHandler("health", health))
    app.add_handler(CommandHandler("version", version))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("logs", logs))
    app.add_handler(CommandHandler("errors", errors))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("responses", responses_list))
    app.add_handler(CommandHandler("dealmsg", dealmsg))
    app.add_handler(CommandHandler("dealchat", dealchat))
    app.add_handler(CommandHandler("dispute", dispute))
    app.add_handler(CommandHandler("disputereason", disputereason))
    app.add_handler(CommandHandler("replydeal", replydeal))
    app.add_handler(CommandHandler("searchdeal", searchdeal))
    app.add_handler(CommandHandler("deals", deals_list))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_chat_text))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("truck", truck))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("sub", subscribe))
    app.add_handler(CommandHandler("mysubs", mysubs))
    app.add_handler(newcargo_handler)

    app.add_handler(CallbackQueryHandler(cargo_refresh, pattern="^cargo_refresh_"))
    app.add_handler(CallbackQueryHandler(cargo_clone, pattern="^cargo_clone_"))
    app.add_handler(CallbackQueryHandler(cargo_cancel, pattern="^cargo_cancel_"))
    app.add_handler(CallbackQueryHandler(cargo_open, pattern="^cargo_open_"))
    app.add_handler(CallbackQueryHandler(cargo_delete, pattern="^cargo_delete_"))
    app.add_handler(CallbackQueryHandler(report_close, pattern="^report_close_"))
    app.add_handler(CallbackQueryHandler(cargo_report, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(cargo_restore, pattern="^cargo_restore_"))
    app.add_handler(CallbackQueryHandler(respond, pattern="^cargo_"))
    app.add_handler(CallbackQueryHandler(response_action, pattern="^(accept|reject)_"))
    app.add_handler(CallbackQueryHandler(deal_closedispute_button, pattern="^deal_closedispute_"))
    app.add_handler(CallbackQueryHandler(deal_dispute_button, pattern="^deal_dispute_"))
    app.add_handler(CallbackQueryHandler(admin_setplan_button, pattern="^admin_setplan_"))
    app.add_handler(CallbackQueryHandler(admin_dispute_buttons, pattern="^admin_(dealchat|notes|close_dispute)_"))
    app.add_handler(CallbackQueryHandler(deal_reason_help, pattern="^deal_reason_help_"))
    app.add_handler(CallbackQueryHandler(deal_write_help, pattern="^deal_write_help_"))
    app.add_handler(CallbackQueryHandler(deal_chat_button, pattern="^deal_chat_"))
    app.add_handler(CallbackQueryHandler(deal_action, pattern="^deal_"))
    app.add_handler(CallbackQueryHandler(review_action, pattern="^review_"))
    app.add_handler(CallbackQueryHandler(subroute, pattern="^subroute_"))
    app.add_handler(CallbackQueryHandler(sub_delete, pattern="^sub_delete_"))
    app.add_handler(CallbackQueryHandler(sub_on, pattern="^sub_on_"))
    app.add_handler(CallbackQueryHandler(sub_off, pattern="^sub_off_"))
    app.add_handler(CallbackQueryHandler(driver_profile_button, pattern="^driver_profile_"))
    app.add_handler(CallbackQueryHandler(truck_refresh, pattern="^truck_refresh_"))
    app.add_handler(CallbackQueryHandler(truck_hide, pattern="^truck_hide_"))
    app.add_handler(CallbackQueryHandler(truck_deal_button, pattern="^truck_deal_"))
    app.add_handler(CallbackQueryHandler(create_deal_button, pattern="^create_deal_"))
    app.add_handler(CallbackQueryHandler(rate_action, pattern="^rate_"))

    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Грузы|🚚 Машина|🤝 Сделки|👤 Профиль|🏠 Меню|🆘 Помощь)$"), reply_menu_handler))
    app.add_handler(CallbackQueryHandler(menu_button, pattern="^(menu_|buy_plan)"))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("monetization", monetization))
    app.add_handler(CommandHandler("setplan", setplan))

    app.add_handler(CommandHandler("docs", docs))

    app.add_error_handler(error_handler)

    print("🚛 BOT RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
