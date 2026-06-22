import aiohttp
import asyncio
import logging
import json
import shutil
import os
import qrcode
import subprocess
from datetime import datetime
import asyncpg

from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont


from math import radians, sin, cos, sqrt, atan2

def distance_km(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
        r = 6371
        dlat = radians(lat2 - lat1)
        dlon = radians(lon2 - lon1)
        a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return round(r * c)
    except Exception:
        return None


from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, WebAppInfo
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
CARGO_WEIGHT = 5
CARGO_VOLUME = 6
CARGO_PLACES = 7
CARGO_TYPE = 8
CARGO_DISTANCE = 9

TRUCK_CITY = 20
TRUCK_BODY = 21
TRUCK_TONS = 22
TRUCK_VOLUME = 23
TRUCK_COMMENT = 24









async def get_user_roles(tg_id):
    user = await DB.fetchrow("""
        SELECT id, role, verified, banned
        FROM users
        WHERE telegram_id=$1
    """, tg_id)

    if not user:
        return {
            "user_id": None,
            "roles": [],
            "primary_role": "carrier",
            "verified": False,
            "banned": False
        }

    rows = await DB.fetch("""
        SELECT role
        FROM user_roles
        WHERE user_id=$1
          AND verified=true
          AND active=true
          AND (expires_at IS NULL OR expires_at > now())
    """, user["id"])

    roles = [r["role"] for r in rows]

    # Совместимость со старым users.role
    if user["verified"] and user["role"] not in roles:
        roles.append(user["role"])

    # Админ видит все рабочие разделы
    if "admin" in roles:
        for r in ["carrier", "shipper", "dispatcher"]:
            if r not in roles:
                roles.append(r)

    return {
        "user_id": user["id"],
        "roles": roles,
        "primary_role": user["role"] or "carrier",
        "verified": bool(user["verified"]),
        "banned": bool(user["banned"])
    }


def main_reply_keyboard(role="carrier", verified=True, roles=None):
    roles = roles or ([role] if role else [])

    if not verified:
        return ReplyKeyboardMarkup(
            [
                ["📝 Подать заявку"],
                ["👤 Профиль"]
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
            is_persistent=True
        )

    rows = []

    # Админ видит всё
    if role == "admin" or "admin" in roles:
        rows = [
            ["🗺 Карта"],
            ["➕ Груз", "📦 Грузы"],
            ["📋 Мои грузы", "🚚 Машина"],
            ["📍 Рядом", "🧩 Догрузы"],
            ["🟢 Выгодные", "📨 Отклики"],
            ["🤝 Сделки", "🛡 Админ"],
            ["💳 Тарифы", "⚙️ Настройки"],
            ["👤 Профиль"]
        ]

    # Грузоотправитель
    elif role == "shipper" or "shipper" in roles:
        rows = [
            ["🗺 Карта"],
            ["➕ Груз", "📦 Грузы"],
            ["📋 Мои грузы", "📨 Отклики"],
            ["🤝 Сделки", "📁 Архив сделок"],
            ["👤 Профиль", "⚙️ Настройки"],
            ["💳 Тарифы"]
        ]

    # Диспетчер
    elif role == "dispatcher" or "dispatcher" in roles:
        rows = [
            ["🗺 Карта"],
            ["➕ Груз", "📦 Грузы"],
            ["📋 Мои грузы", "🚚 Машина"],
            ["📨 Отклики", "🤝 Сделки"],
            ["📁 Архив сделок"],
            ["👤 Профиль", "⚙️ Настройки"],
            ["💳 Тарифы"]
        ]

    # Перевозчик
    elif role == "carrier" or role == "driver" or "carrier" in roles or "driver" in roles:
        rows = [
            ["🗺 Карта"],
            ["📦 Грузы", "🚚 Машина"],
            ["📍 Рядом", "🧩 Догрузы"],
            ["🟢 Выгодные"],
            ["📨 Отклики", "🤝 Сделки"],
            ["📁 Архив сделок"],
            ["👤 Профиль", "⚙️ Настройки"],
            ["💳 Тарифы"]
        ]

    else:
        rows = [
            ["👤 Профиль", "⚙️ Настройки"],
            ["💳 Тарифы", "➕ Запросить роль"]
        ]

    clean = []
    seen = set()
    for row in rows:
        key = tuple(row)
        if key not in seen:
            clean.append(row)
            seen.add(key)

    return ReplyKeyboardMarkup(
        clean,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True
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
            SET full_name=$1,
                telegram_username=$2
            WHERE telegram_id=$3
        """, tg_user.full_name, tg_user.username, tg_user.id)

        return row["id"]

    new_user = await DB.fetchrow("""
        INSERT INTO users (telegram_id, full_name, telegram_username)
        VALUES ($1, $2, $3)
        RETURNING id
    """, tg_user.id, tg_user.full_name, tg_user.username)

    return new_user["id"]









async def is_admin_user(user_id):
    row = await DB.fetchrow("""
        SELECT role
        FROM users
        WHERE id=$1
    """, user_id)

    return bool(row and row["role"] == "admin")


async def setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_user_id = await ensure_user(update.effective_user)

    if not await is_admin_user(admin_user_id):
        await update.message.reply_text("⛔ Доступ только для администратора")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/setrole USER_ID carrier\n"
            "/setrole USER_ID shipper\n"
            "/setrole USER_ID dispatcher\n"
            "/setrole USER_ID admin"
        )
        return

    try:
        target_user_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ USER_ID должен быть числом")
        return

    role = context.args[1].lower().strip()

    allowed = ["carrier", "shipper", "dispatcher", "admin"]
    if role not in allowed:
        await update.message.reply_text(
            "❌ Роль должна быть одной из:\n"
            "carrier — перевозчик\n"
            "shipper — грузовладелец\n"
            "dispatcher — диспетчер\n"
            "admin — админ"
        )
        return

    target = await DB.fetchrow("""
        SELECT id, telegram_id, full_name, role
        FROM users
        WHERE id=$1
    """, target_user_id)

    if not target:
        await update.message.reply_text(f"❌ Пользователь #{target_user_id} не найден")
        return

    old_role = target["role"]

    await DB.execute("""
        UPDATE users
        SET role=$1,
            verified=true,
            banned=false
        WHERE id=$2
    """, role, target_user_id)

    await DB.execute("""
        INSERT INTO user_roles (user_id, role, verified, active, paid)
        VALUES ($1, $2, true, true, true)
        ON CONFLICT (user_id, role)
        DO UPDATE SET
            verified=true,
            active=true,
            paid=true,
            expires_at=NULL
    """, target_user_id, role)

    await audit(
        admin_user_id,
        "user_role_set",
        payload={
            "target_user_id": target_user_id,
            "telegram_id": target["telegram_id"],
            "old_role": old_role,
            "new_role": role
        }
    )

    role_names = {
        "carrier": "🚚 Перевозчик",
        "shipper": "📦 Грузовладелец",
        "dispatcher": "📡 Диспетчер",
        "admin": "🛠 Админ"
    }

    await update.message.reply_text(
        f"✅ Роль обновлена\n\n"
        f"👤 User #{target_user_id}\n"
        f"Имя: {target['full_name'] or '-'}\n"
        f"Было: {old_role or '-'}\n"
        f"Стало: {role_names.get(role, role)}\n\n"
        f"Пользователю надо отправить /menu"
    )



async def auditcargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not await is_admin_user(user_id):
        await update.message.reply_text("⛔ Доступ только для администратора")
        return

    if not context.args:
        await update.message.reply_text("Использование: /auditcargo 14")
        return

    try:
        cargo_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ ID груза должен быть числом")
        return

    rows = await DB.fetch("""
        SELECT id, user_id, action, deal_id, cargo_id, payload, created_at
        FROM audit_log
        WHERE cargo_id=$1
        ORDER BY id DESC
        LIMIT 20
    """, cargo_id)

    if not rows:
        await update.message.reply_text(f"📭 По грузу #{cargo_id} записей аудита нет")
        return

    lines = [f"⚖️ Аудит груза #{cargo_id}\n"]

    for r in rows:
        lines.append(
            f"#{r['id']} — {r['action']}\n"
            f"👤 user_id: {r['user_id'] or '-'}\n"
            f"🤝 deal_id: {r['deal_id'] or '-'}\n"
            f"🕒 {r['created_at']}\n"
            f"📌 {r['payload'] or {}}\n"
        )

    text = "\n".join(lines)

    if len(text) > 3900:
        text = text[:3900] + "\n...обрезано"

    await update.message.reply_text(text)


async def auditdeal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not await is_admin_user(user_id):
        await update.message.reply_text("⛔ Доступ только для администратора")
        return

    if not context.args:
        await update.message.reply_text("Использование: /auditdeal 10")
        return

    try:
        deal_id = int(context.args[0])
    except Exception:
        await update.message.reply_text("❌ ID сделки должен быть числом")
        return

    rows = await DB.fetch("""
        SELECT id, user_id, action, deal_id, cargo_id, payload, created_at
        FROM audit_log
        WHERE deal_id=$1
        ORDER BY id DESC
        LIMIT 20
    """, deal_id)

    if not rows:
        await update.message.reply_text(f"📭 По сделке #{deal_id} записей аудита нет")
        return

    lines = [f"⚖️ Аудит сделки #{deal_id}\n"]

    for r in rows:
        lines.append(
            f"#{r['id']} — {r['action']}\n"
            f"👤 user_id: {r['user_id'] or '-'}\n"
            f"📦 cargo_id: {r['cargo_id'] or '-'}\n"
            f"🕒 {r['created_at']}\n"
            f"📌 {r['payload'] or {}}\n"
        )

    text = "\n".join(lines)

    if len(text) > 3900:
        text = text[:3900] + "\n...обрезано"

    await update.message.reply_text(text)



async def audit(user_id, action, deal_id=None, cargo_id=None, payload=None):
    """
    Юридический журнал действий.
    Пишем только технический факт действия, без лишних персональных данных.
    Ошибка аудита не должна ломать работу бота.
    """
    if payload is None:
        payload = {}

    try:
        await DB.execute("""
            INSERT INTO audit_log (
                user_id,
                deal_id,
                cargo_id,
                action,
                payload
            )
            VALUES ($1, $2, $3, $4, $5::jsonb)
        """, user_id, deal_id, cargo_id, action, json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logging.warning(f"audit_log failed: {e}")



async def audit_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    await audit(
        user_id,
        "audit_test",
        payload={
            "source": "telegram_command",
            "telegram_id": update.effective_user.id
        }
    )

    await update.message.reply_text("✅ audit_log test записан")



async def private_only(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat and chat.type != "private":
        raise ApplicationHandlerStop


async def ban_guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    if not tg_user:
        return

    row = await DB.fetchrow("""
        SELECT id, role, banned
        FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    # Защита: админ не может быть заблокирован в боте
    if row and row["role"] == "admin" and row["banned"]:
        await DB.execute("""
            UPDATE users
            SET banned=false
            WHERE telegram_id=$1
        """, tg_user.id)
        return

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
        f"✅ Подписка активна\n\n"
        f"Будем присылать новые грузы по маршруту:\n"
        f"🚩 {cargo['from_city']} → {cargo['to_city']}\n\n"
        f"Вы получите уведомление, когда появится подходящий груз.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Мои подписки", callback_data="menu_mysubs")],
            [InlineKeyboardButton("📦 Смотреть грузы", callback_data="menu_cargo")]
        ])
    )


async def sub_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

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


async def settruck_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text(
            "🚚 Используй так:\n"
            "/settruck Москва тент\n\n"
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




async def apply_radius_limit(user_id: int, radius: int):
    user = await DB.fetchrow("""
        SELECT
            COALESCE(role, 'carrier') AS role,
            COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"] if user else "carrier"
    plan_type = user["plan_type"] if user else "free"

    if role in ("admin", "dispatcher") or plan_type == "company":
        return radius, None

    if plan_type == "pro":
        max_radius = 500
    else:
        max_radius = 150

    if radius > max_radius:
        return max_radius, max_radius

    return radius, None


async def truck_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT
            COALESCE(role, 'carrier') AS role,
            COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"]
    plan_type = user["plan_type"]

    truck_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM trucks
        WHERE driver_id=$1
          AND COALESCE(status, 'active') <> 'deleted'
    """, user_id)

    # Коммерческая модель v1:
    # перевозчик FREE = 1 машина
    # перевозчик PRO = до 3 машин
    # admin/dispatcher/company пока без ограничений
    if role in ("admin", "dispatcher") or plan_type == "company":
        limit = 999999
    elif plan_type == "pro":
        limit = 3
    else:
        limit = 1

    if truck_count >= limit:
        if limit == 1:
            await update.message.reply_text(
                "🚫 Лимит FREE достигнут\n\n"
                "На тарифе FREE доступна только 1 машина.\n\n"
                "🚚 PRO — 490 ₽/месяц\n"
                "✓ до 3 машин\n"
                "✓ радиус поиска до 500 км\n"
                "✓ раздел «🟢 Выгодные грузы»\n"
                "✓ приоритет в выдаче\n\n"
                "Нажмите кнопку ниже, чтобы отправить заявку.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚚 Подключить PRO", callback_data="plan_request_pro")]
                ])
            )
        else:
            await update.message.reply_text(
                f"🚫 Лимит машин для тарифа {plan_type.upper()}: {limit}.\n\n"
                "Чтобы увеличить лимит, обратитесь к администратору."
            )
        return ConversationHandler.END

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
                free_weight_kg=$3::numeric * 1000,
                free_volume_m3=$4,
                allow_partial_load=true,
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
                free_weight_kg,
                free_volume_m3,
                allow_partial_load,
                comment,
                status
            )
            VALUES ($1,$2,$3,$4,$5,$4::numeric * 1000,$5,true,$6,'active')
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
        f"🧩 Догрузы: разрешены\n"
        f"⚖️ Свободно: {data['capacity_tons'] * 1000} кг\n"
        f"📦 Свободный объём: {data['volume_m3']} м³\n"
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

        await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📞 Добавить / изменить телефон", callback_data="profile_phone")]
        ])
    )

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
    source_user = update.effective_user
    reply_message = update.message

    if update.callback_query:
        source_user = update.callback_query.from_user
        reply_message = update.callback_query.message

    user_id = await ensure_user(source_user)
    active_truck_id = context.user_data.get("active_truck_id")

    truck = await DB.fetchrow("""
        SELECT
            id,
            current_city,
            body_type,
            capacity_tons,
            volume_m3,
            comment,
            status,
            created_at,
            brand,
            model,
            plate_number,
            latitude,
            longitude,
            location_updated_at,
            photo_file_id,
            min_rate_per_km,
            search_radius_km,
            free_weight_kg,
            free_volume_m3,
            allow_partial_load
        FROM trucks
        WHERE driver_id=$1
          AND ($2::bigint IS NULL OR id=$2)
        ORDER BY id DESC
        LIMIT 1
    """, user_id, active_truck_id)

    if not truck:
        if update.callback_query:
            await reply_message.reply_text("❌ Машина не найдена или не принадлежит вам")
            return
        return await truck_start(update, context)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Марка", callback_data="truck_edit_brand"),
            InlineKeyboardButton("✏️ Модель", callback_data="truck_edit_model")
        ],
        [
            InlineKeyboardButton("✏️ Номер", callback_data="truck_edit_plate_number"),
            InlineKeyboardButton("📍 Город", callback_data="truck_edit_current_city")
        ],
        [
            InlineKeyboardButton("📦 Кузов", callback_data="truck_edit_body_type"),
            InlineKeyboardButton("⚖️ Тоннаж", callback_data="truck_edit_capacity_tons")
        ],
        [
            InlineKeyboardButton("📦 Объём", callback_data="truck_edit_volume_m3"),
            InlineKeyboardButton("📝 Описание", callback_data="truck_edit_comment")
        ],
        [InlineKeyboardButton(
            "🧩 Догрузы: вкл" if truck["allow_partial_load"] else "🧩 Догрузы: выкл",
            callback_data=f"truck_partial_{truck['id']}"
        )],
        [InlineKeyboardButton("📷 Фото машины", callback_data="truck_photo")],
        [InlineKeyboardButton("🔁 Обновить в поиске", callback_data=f"truck_refresh_{truck['id']}")],
        [InlineKeyboardButton("📍 Обновить GEO вручную", callback_data="truck_geo")],
        [InlineKeyboardButton("📍 Грузы рядом", callback_data="menu_nearby")],
        [
            InlineKeyboardButton("💰 Ставка ₽/км", callback_data="settings_rate"),
            InlineKeyboardButton("🛣 Радиус", callback_data="settings_radius")
        ],
        [InlineKeyboardButton("🙈 Скрыть из поиска", callback_data=f"truck_hide_{truck['id']}")]
    ])

    text = (
        f"🚚 Моя машина #{truck['id']}\n\n"
        f"🚛 Марка: {truck['brand'] or 'Не указана'}\n"
        f"🔤 Модель: {truck['model'] or 'Не указана'}\n"
        f"🔢 Номер: {truck['plate_number'] or '-'}\n"
        f"📍 Город: {truck['current_city'] or 'Не указан'}\n"
        f"📦 Кузов: {truck['body_type'] or 'Не указан'}\n"
        f"⚖️ Тоннаж: {truck['capacity_tons'] or '-'} т\n"
        f"📦 Объём: {truck['volume_m3'] or '-'} м³\n"
        f"🧩 Догрузы: {'разрешены' if truck['allow_partial_load'] else 'выключены'}\n"
        f"⚖️ Свободно: {truck['free_weight_kg'] or 0} кг\n"
        f"📦 Свободный объём: {truck['free_volume_m3'] or 0} м³\n"
        f"💰 Мин. ставка: {truck['min_rate_per_km'] or '-'} ₽/км\n"
        f"🛣 Радиус поиска: {truck['search_radius_km'] or '-'} км\n"
        f"🌐 GEO: {(str(round(float(truck['latitude']), 4)) + ', ' + str(round(float(truck['longitude']), 4))) if truck['latitude'] and truck['longitude'] else 'не найдено'}\n"
        f"🟢 Гео активно\n"
        f"📝 {truck['comment'] or 'Комментариев нет'}\n"
        f"{human_status(truck['status'])}"
    )

    if truck.get("photo_file_id"):
        await reply_message.reply_photo(
            photo=truck["photo_file_id"],
            caption=text,
            reply_markup=kb
        )
    else:
        await reply_message.reply_text(
            text,
            reply_markup=kb
        )




async def mytrucks_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT
            id,
            current_city,
            body_type,
            capacity_tons,
            volume_m3,
            free_weight_kg,
            free_volume_m3,
            allow_partial_load,
            status,
            min_rate_per_km,
            search_radius_km,
            location_updated_at
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id
    """, user_id)

    if not rows:
        await update.message.reply_text(
            "🚚 У вас пока нет машин.\n\n"
            "Чтобы добавить машину, отправьте команду:\n"
            "/newtruck"
        )
        return

    text = f"🚚 Мои машины: {len(rows)}\n\n"

    for r in rows:
        text += (
            f"#{r['id']} — {r['current_city'] or 'город не указан'}\n"
            f"📦 {r['body_type'] or '-'} | ⚖️ {r['capacity_tons'] or '-'} т | "
            f"{r['volume_m3'] or '-'} м³\n"
            f"🧩 Догрузы: {'включены' if r['allow_partial_load'] else 'выключены'}\n"
            f"⚖️ Свободно: {r['free_weight_kg'] or 0} кг | "
            f"📦 {r['free_volume_m3'] or 0} м³\n"
            f"💰 Ставка: {r['min_rate_per_km'] or '-'} ₽/км | "
            f"🛣 Радиус: {r['search_radius_km'] or '-'} км\n"
            f"Статус: {human_status(r['status'])}\n\n"
        )

    text += "Выберите машину для управления:"

    buttons = []
    for r in rows:
        buttons.append([
            InlineKeyboardButton(
                f"🚚 Машина #{r['id']}",
                callback_data=f"truck_open_{r['id']}"
            )
        ])

    buttons.append([InlineKeyboardButton("➕ Добавить машину", callback_data="truck_add")])

    await update.message.reply_text(
        text.strip(),
        reply_markup=InlineKeyboardMarkup(buttons)
    )






async def truckcomment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text(
            "📝 Укажите ID машины и комментарий.\n\n"
            "Пример:\n"
            "/truckcomment 1 свободен завтра"
        )
        return

    try:
        truck_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID машины должен быть числом. Например: /truckcomment 1 свободен завтра")
        return

    comment = " ".join(context.args[1:]).strip()

    owner_ok = await DB.fetchval("""
        SELECT EXISTS(
            SELECT 1 FROM trucks
            WHERE id=$1 AND driver_id=$2
        )
    """, truck_id, user_id)

    if not owner_ok:
        await update.message.reply_text("❌ Машина не найдена или не принадлежит вам")
        return

    await DB.execute("""
        UPDATE trucks
        SET comment=$1
        WHERE id=$2 AND driver_id=$3
    """, comment, truck_id, user_id)

    await update.message.reply_text(f"✅ Комментарий машины #{truck_id} обновлён")

    context.args = [str(truck_id)]
    await truckinfo(update, context)


async def truckinfo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not context.args:
        await update.message.reply_text(
            "🚚 Укажите номер машины.\n\n"
            "Пример:\n"
            "/truckinfo 1"
        )
        return

    try:
        truck_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Номер машины должен быть числом. Например: /truckinfo 1")
        return

    truck = await DB.fetchrow("""
        SELECT
            id,
            current_city,
            body_type,
            capacity_tons,
            volume_m3,
            comment,
            status,
            created_at,
            brand,
            model,
            plate_number,
            latitude,
            longitude,
            location_updated_at,
            photo_file_id,
            min_rate_per_km,
            search_radius_km,
            free_weight_kg,
            free_volume_m3,
            allow_partial_load
        FROM trucks
        WHERE id=$1 AND driver_id=$2
    """, truck_id, user_id)

    if not truck:
        await update.message.reply_text("❌ Машина не найдена или не принадлежит вам")
        return

    geo = "не найдено"
    if truck["latitude"] and truck["longitude"]:
        geo = f"{round(float(truck['latitude']), 4)}, {round(float(truck['longitude']), 4)}"

    text = (
        f"🚚 Машина #{truck['id']}\n\n"
        f"🚛 Марка: {truck['brand'] or 'Не указана'}\n"
        f"🔤 Модель: {truck['model'] or 'Не указана'}\n"
        f"🔢 Номер: {truck['plate_number'] or '-'}\n"
        f"📍 Город: {truck['current_city'] or 'Не указан'}\n"
        f"📦 Кузов: {truck['body_type'] or 'Не указан'}\n"
        f"⚖️ Тоннаж: {truck['capacity_tons'] or '-'} т\n"
        f"📦 Объём: {truck['volume_m3'] or '-'} м³\n"
        f"🧩 Догрузы: {'разрешены' if truck['allow_partial_load'] else 'выключены'}\n"
        f"⚖️ Свободно: {truck['free_weight_kg'] or 0} кг\n"
        f"📦 Свободный объём: {truck['free_volume_m3'] or 0} м³\n"
        f"💰 Мин. ставка: {truck['min_rate_per_km'] or '-'} ₽/км\n"
        f"🛣 Радиус поиска: {truck['search_radius_km'] or '-'} км\n"
        f"🌐 GEO: {geo}\n"
        f"📝 {truck['comment'] or 'Комментариев нет'}\n"
        f"{human_status(truck['status'])}"
    )

    if truck["photo_file_id"]:
        await update.message.reply_photo(
            photo=truck["photo_file_id"],
            caption=text
        )
    else:
        await update.message.reply_text(text)



async def truck_open_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    try:
        truck_id = int(q.data.split("_")[2])
    except Exception:
        await q.message.reply_text("❌ Неверный номер машины")
        return

    user_id = await ensure_user(q.from_user)

    owner_ok = await DB.fetchval("""
        SELECT EXISTS(
            SELECT 1
            FROM trucks
            WHERE id=$1 AND driver_id=$2
        )
    """, truck_id, user_id)

    if not owner_ok:
        await q.message.reply_text("❌ Машина не найдена или не принадлежит вам")
        return

    context.user_data["active_truck_id"] = truck_id

    await mytruck(update, context)


async def truck_partial_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    user_id = await ensure_user(q.from_user)
    truck_id = int(q.data.split("_")[2])

    truck = await DB.fetchrow("""
        SELECT id, driver_id, allow_partial_load
        FROM trucks
        WHERE id=$1
    """, truck_id)

    if not truck:
        await q.message.reply_text("❌ Машина не найдена")
        return

    if truck["driver_id"] != user_id:
        await q.message.reply_text("⛔ Это не ваша машина")
        return

    new_value = not bool(truck["allow_partial_load"])

    await DB.execute("""
        UPDATE trucks
        SET allow_partial_load=$1
        WHERE id=$2
    """, new_value, truck_id)

    await q.message.reply_text(
        "✅ Догрузы включены" if new_value else "✅ Догрузы выключены"
    )


async def truck_add_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    user_id = await ensure_user(q.from_user)

    user = await DB.fetchrow("""
        SELECT COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    plan_type = user["plan_type"] if user else "free"

    trucks_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM trucks
        WHERE driver_id=$1
    """, user_id)

    limits = {
        "free": 1,
        "pro": 3,
        "dispatcher": 10,
        "company": 999999
    }

    limit = limits.get(plan_type, 1)

    if trucks_count >= limit:
        await q.message.reply_text(
            f"⛔ Лимит машин для тарифа {plan_type.upper()}: {limit}\n\n"
            f"У вас уже добавлено машин: {trucks_count}\n\n"
            "Чтобы добавить больше машин, подключите PRO или COMPANY."
        )
        return

    context.user_data["truck"] = {}

    await q.message.reply_text(
        "🚚 Добавление машины\n\n"
        "📍 Введите текущий город:"
    )

    return TRUCK_CITY




async def truck_photo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    context.user_data["awaiting_truck_photo"] = True

    await q.message.reply_text(
        "📷 Отправьте фото машины."
    )


async def truck_photo_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_truck_photo"):
        return

    if not update.message.photo:
        await update.message.reply_text("❌ Отправьте фото.")
        return

    context.user_data["awaiting_truck_photo"] = False

    user_id = await ensure_user(update.effective_user)

    file_id = update.message.photo[-1].file_id

    truck = await DB.fetchrow("""
        SELECT id
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if not truck:
        await update.message.reply_text("❌ Машина не найдена")
        return

    truck_id = truck["id"]

    photo_url = None

    try:
        import os
        os.makedirs("/root/dalnoboy/web/uploads/trucks", exist_ok=True)
        os.makedirs("/var/www/dalnoboy/uploads/trucks", exist_ok=True)

        tg_file = await context.bot.get_file(file_id)

        local_path = f"/root/dalnoboy/web/uploads/trucks/truck_{truck_id}.jpg"
        public_path = f"/var/www/dalnoboy/uploads/trucks/truck_{truck_id}.jpg"

        await tg_file.download_to_drive(local_path)

        import shutil
        shutil.copyfile(local_path, public_path)

        photo_url = f"/uploads/trucks/truck_{truck_id}.jpg"

    except Exception as e:
        logging.warning(f"Truck photo web save failed: {e}")

    await DB.execute("""
        UPDATE trucks
        SET photo_file_id=$1,
            photo_url=COALESCE($3, photo_url)
        WHERE id=$2
    """, file_id, truck_id, photo_url)

    await update.message.reply_text("✅ Фото машины сохранено")
    await mytruck(update, context)


async def truck_edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    field = q.data.replace("truck_edit_", "")

    labels = {
        "brand": "марку машины",
        "model": "модель машины",
        "plate_number": "госномер",
        "current_city": "текущий город",
        "body_type": "тип кузова (например: тент, фургон, реф, борт, эвакуатор)",
        "capacity_tons": "тоннаж в тоннах (например: 1.5)",
        "volume_m3": "объём в м³ (например: 12)",
        "comment": "описание машины"
    }

    if field not in labels:
        await q.message.reply_text("❌ Неизвестное поле")
        return

    context.user_data["truck_edit_field"] = field

    await q.message.reply_text(
        f"✏️ Введите {labels[field]}:"
    )


async def truck_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_dispute_reason"):
        deal_id = context.user_data.pop("awaiting_dispute_reason")
        reason = text.strip()

        if len(reason) < 3:
            context.user_data["awaiting_dispute_reason"] = deal_id
            await update.message.reply_text("❌ Опишите причину подробнее.")
            return

        user_id = await ensure_user(update.effective_user)

        await DB.execute("""
            INSERT INTO deal_messages (
                deal_id,
                from_user_id,
                message_text
            )
            VALUES ($1,$2,$3)
        """, deal_id, user_id, f"⚠️ Причина жалобы: {reason}")

        await update.message.reply_text(
            f"✅ Причина жалобы сохранена по сделке #{deal_id}\n\n"
            f"Причина: {reason}"
        )
        return

    if context.user_data.get("awaiting_rate"):
        context.user_data["awaiting_rate"] = False

        try:
            rate = float(update.message.text.strip().replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введите число, например: 35")
            context.user_data["awaiting_rate"] = True
            return

        user_id = await ensure_user(update.effective_user)

        await DB.execute("""
            UPDATE trucks
            SET min_rate_per_km=$1
            WHERE id=(
                SELECT id
                FROM trucks
                WHERE driver_id=$2
                ORDER BY id DESC
                LIMIT 1
            )
        """, rate, user_id)

        await update.message.reply_text(f"✅ Минимальная ставка сохранена: {rate} ₽/км")
        await mytruck(update, context)
        return

    if context.user_data.get("awaiting_radius"):
        context.user_data["awaiting_radius"] = False

        try:
            radius = int(float(update.message.text.strip().replace(",", ".")))
        except ValueError:
            await update.message.reply_text("❌ Введите число, например: 100")
            context.user_data["awaiting_radius"] = True
            return

        user_id = await ensure_user(update.effective_user)

        radius, clipped_to = await apply_radius_limit(user_id, radius)

        await DB.execute("""
            UPDATE trucks
            SET search_radius_km=$1
            WHERE id=(
                SELECT id
                FROM trucks
                WHERE driver_id=$2
                ORDER BY id DESC
                LIMIT 1
            )
        """, radius, user_id)

        if clipped_to:
            await update.message.reply_text(
                f"ℹ️ Для вашего тарифа максимальный радиус поиска: {clipped_to} км.\n"
                f"Сохранил радиус: {radius} км."
            )
        else:
            await update.message.reply_text(f"✅ Радиус поиска сохранён: {radius} км")

        await mytruck(update, context)
        return

    field = context.user_data.get("truck_edit_field")

    if not field:
        return

    value = update.message.text.strip()

    if not value:
        await update.message.reply_text("❌ Значение не может быть пустым")
        return

    context.user_data["truck_edit_field"] = None

    user_id = await ensure_user(update.effective_user)

    allowed = {
        "brand": "brand",
        "model": "model",
        "plate_number": "plate_number",
        "current_city": "current_city",
        "body_type": "body_type",
        "capacity_tons": "capacity_tons",
        "volume_m3": "volume_m3",
        "comment": "comment"
    }

    column = allowed.get(field)

    if not column:
        await update.message.reply_text("❌ Поле не разрешено")
        return

    await DB.execute(f"""
        UPDATE trucks
        SET {column}=$1
        WHERE id=(
            SELECT id
            FROM trucks
            WHERE driver_id=$2
            ORDER BY id DESC
            LIMIT 1
        )
    """, value, user_id)

    await update.message.reply_text("✅ Данные обновлены")
    await mytruck(update, context)



async def truck_geo_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    kb = ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Отправить GEO", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await q.message.reply_text(
        "📍 Отправьте текущее местоположение машины.",
        reply_markup=kb
    )


async def truck_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

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
        [InlineKeyboardButton("🚚 Подключить PRO перевозчика", callback_data="plan_request_pro")],
        [InlineKeyboardButton("📦 Подключить COMPANY грузоотправителя", callback_data="plan_request_company")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile")]
    ])

    text = (
        "💳 Тарифы Dalnoboy Bros\n\n"

        "🆓 FREE\n"
        "• 1 активный груз\n"
        "• 1 машина\n"
        "• радиус поиска до 150 км\n"
        "• обычная выдача\n\n"

        "🚚 PRO для перевозчика\n"
        "490 ₽ / месяц\n"
        "• до 3 машин\n"
        "• радиус поиска до 500 км\n"
        "• раздел «🟢 Выгодные грузы»\n"
        "• уведомления и приоритет\n\n"

        "📦 COMPANY для грузоотправителя\n"
        "990 ₽ / месяц\n"
        "• до 20 активных грузов\n"
        "• VIP и поднятие грузов\n"
        "• приоритет в выдаче\n\n"

        "👨‍💼 Диспетчер\n"
        "• бесплатно на этапе роста платформы\n\n"

        "Оплата пока вручную через администратора.\n"
        "Нажмите кнопку ниже — админ получит заявку."
    )

    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=kb)


async def plan_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    tg_user = q.from_user
    user_id = await ensure_user(tg_user)

    plan = q.data.replace("plan_request_", "")

    if plan == "pro":
        plan_title = "🚚 PRO перевозчика"
        price = "490 ₽ / месяц"
    elif plan == "company":
        plan_title = "📦 COMPANY грузоотправителя"
        price = "990 ₽ / месяц"
    else:
        plan_title = plan.upper()
        price = "-"

    user = await DB.fetchrow("""
        SELECT
            id,
            telegram_id,
            full_name,
            telegram_username,
            role,
            plan_type,
            phone
        FROM users
        WHERE id=$1
    """, user_id)

    username = user["telegram_username"] if user and user["telegram_username"] else None
    username_text = f"@{username}" if username else "-"

    await q.message.reply_text(
        "✅ Заявка отправлена администратору.\n\n"
        f"Тариф: {plan_title}\n"
        f"Стоимость: {price}\n\n"
        "Администратор свяжется с вами для подключения."
    )

    admins = await DB.fetch("""
        SELECT telegram_id
        FROM users
        WHERE role='admin'
          AND COALESCE(banned,false)=false
    """)

    admin_text = (
        "💳 Новая заявка на тариф\n\n"
        f"Тариф: {plan_title}\n"
        f"Цена: {price}\n\n"
        f"Пользователь: {user['full_name'] if user and user['full_name'] else tg_user.full_name}\n"
        f"Telegram: {username_text}\n"
        f"Телефон: {user['phone'] if user and user['phone'] else '-'}\n"
        f"Telegram ID: {tg_user.id}\n"
        f"User ID в базе: {user_id}\n"
        f"Текущая роль: {user['role'] if user else '-'}\n"
        f"Текущий тариф: {user['plan_type'] if user else '-'}\n\n"
        f"Для подключения:\n"
        f"/setplan {user_id} {plan} 30"
    )

    for admin in admins:
        try:
            await context.bot.send_message(
                chat_id=admin["telegram_id"],
                text=admin_text,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "💬 Написать клиенту",
                            url=f"tg://user?id={tg_user.id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "✅ Подключить тариф",
                            callback_data=f"plan_activate_{user_id}_{plan}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "❌ Отклонить заявку",
                            callback_data=f"plan_reject_{user_id}_{plan}"
                        )
                    ]
                ])
            )
        except Exception as e:
            logging.warning(f"plan_request notify admin failed: {e}")


async def plan_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_access = await get_user_roles(q.from_user.id)
    if "admin" not in admin_access["roles"] and admin_access["primary_role"] != "admin":
        await q.answer("⛔ Только администратор", show_alert=True)
        return

    parts = q.data.split("_")
    # plan_activate_USERID_PLAN
    # plan_reject_USERID_PLAN
    action = parts[1]
    user_id = int(parts[2])
    plan = parts[3]

    user = await DB.fetchrow("""
        SELECT id, telegram_id, full_name, plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    if not user:
        await q.message.reply_text("❌ Пользователь не найден")
        return

    if action == "activate":
        await DB.execute("""
            UPDATE users
            SET plan_type=$1,
                plan_expires_at=now() + interval '30 days'
            WHERE id=$2
        """, plan, user_id)

        await q.message.reply_text(
            f"✅ Тариф подключён\n\n"
            f"Пользователь: {user['full_name'] or user_id}\n"
            f"Тариф: {plan.upper()}\n"
            f"Срок: 30 дней"
        )

        try:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=(
                    f"✅ Ваш тариф {plan.upper()} подключён на 30 дней.\n\n"
                    "Спасибо за оплату и доверие к Dalnoboy Bros."
                )
            )
        except Exception as e:
            logging.warning(f"plan activate notify user failed: {e}")

    elif action == "reject":
        await q.message.reply_text(
            f"❌ Заявка отклонена\n\n"
            f"Пользователь: {user['full_name'] or user_id}\n"
            f"Тариф: {plan.upper()}"
        )

        try:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text=(
                    f"❌ Заявка на тариф {plan.upper()} отклонена.\n\n"
                    "Если это ошибка, напишите администратору."
                )
            )
        except Exception as e:
            logging.warning(f"plan reject notify user failed: {e}")


async def tariffs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await get_user_roles(update.effective_user.id)

    if access["primary_role"] != "admin" and "admin" not in access["roles"]:
        await update.message.reply_text("⛔ Только администратор")
        return

    rows = await DB.fetch("""
        SELECT code, title, price_month, active
        FROM tariff_settings
        ORDER BY
            CASE code
                WHEN 'free' THEN 1
                WHEN 'pro' THEN 2
                WHEN 'company' THEN 3
                WHEN 'dispatcher' THEN 4
                ELSE 9
            END
    """)

    if not rows:
        await update.message.reply_text("❌ Таблица tariff_settings пустая")
        return

    text = "💳 Управление тарифами\n\n"
    buttons = []

    for r in rows:
        status = "✅" if r["active"] else "⛔"
        text += (
            f"{status} {r['code'].upper()} — {r['title']}\n"
            f"Цена: {r['price_month']} ₽/мес\n\n"
        )

        if r["code"] in ("pro", "company"):
            buttons.append([
                InlineKeyboardButton(
                    f"✏️ Изменить {r['code'].upper()}",
                    callback_data=f"tariff_edit_{r['code']}"
                )
            ])

    buttons.append([InlineKeyboardButton("🛡 Открыть админку: нажмите нижнюю кнопку Админ", callback_data="noop")])

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await get_user_roles(update.effective_user.id)

    if access["primary_role"] != "admin" and "admin" not in access["roles"]:
        await update.message.reply_text("⛔ Только администратор")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Использование:\n"
            "/setprice pro 590\n"
            "/setprice company 1490"
        )
        return

    code = context.args[0].lower().strip()

    try:
        price = int(float(context.args[1].replace(",", ".")))
    except Exception:
        await update.message.reply_text("❌ Цена должна быть числом")
        return

    row = await DB.fetchrow("""
        UPDATE tariff_settings
        SET price_month=$1,
            updated_at=now()
        WHERE code=$2
        RETURNING code, title, price_month
    """, price, code)

    if not row:
        await update.message.reply_text("❌ Тариф не найден. Доступны: free, pro, company, dispatcher")
        return

    await update.message.reply_text(
        f"✅ Цена тарифа обновлена\n\n"
        f"{row['code'].upper()} — {row['title']}\n"
        f"Новая цена: {row['price_month']} ₽/мес"
    )


async def noop_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Нажмите нижнюю кнопку 🛡 Админ", show_alert=False)


async def matching(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:5000/api/matching/open-cargo") as resp:
                data = await resp.json()

        items = data.get("items", [])

        if not items:
            await update.message.reply_text("❌ Совпадений нет")
            return

        for m in items[:10]:
            txt = (
                f"📦 Груз #{m['cargo_id']}\n"
                f"🚩 {m['from_city']} → {m['to_city']}\n"
                f"🚚 Машина #{m['truck_id']}\n"
                f"👤 {m['full_name']}\n"
                f"🔥 Match: {m['match_score']}%"
            )

            await update.message.reply_text(txt)

    except Exception as e:
        await update.message.reply_text(f"❌ Matching error: {e}")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT full_name, role, verified, plan_type, plan_expires_at, created_at, phone
        FROM users
        WHERE id=$1
    """, user_id)

    full_name = user["full_name"] if user and user["full_name"] else update.effective_user.full_name
    role = user["role"] if user and user["role"] else "driver"
    user_verified = user["verified"] if user else False
    plan_type = user["plan_type"] if user and user["plan_type"] else "free"
    plan_expires_at = user["plan_expires_at"] if user else None
    created_at = user["created_at"] if user else None
    phone = user["phone"] if user and user["phone"] else None

    plan_badges = {
        "company": "⭐ COMPANY",
        "pro": "🔥 PRO",
        "dispatcher": "📡 DISPATCHER",
        "free": "🆓 FREE"
    }

    role_badges = {
        "admin": "🛠 Админ",
        "driver": "🚚 Водитель",
        "carrier": "🚚 Перевозчик",
        "shipper": "📦 Грузоотправитель",
        "company": "🏢 Компания",
        "dispatcher": "📡 Диспетчер"
    }

    role_rows = await DB.fetch("""
        SELECT role, verified, active, paid, expires_at
        FROM user_roles
        WHERE user_id=$1
        ORDER BY role
    """, user_id)

    if role_rows:
        roles_text = "\n".join(
            [
                f"{'✅' if r['verified'] and r['active'] else '⏳'} "
                f"{role_badges.get(r['role'], r['role'])}"
                f"{' 💳' if r['paid'] else ''}"
                f"{' до ' + str(r['expires_at'].date()) if r['expires_at'] else ''}"
                for r in role_rows
            ]
        )
    else:
        roles_text = role_badges.get(role, role)

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
        f"Роли:\n{roles_text}\n"
        f"Тариф: {plan_badges.get(plan_type, plan_type.upper())}\n"
        + (f"⏳ Тариф до: {plan_expires_at}\n" if plan_expires_at else "")
        + (f"📅 На платформе с: {created_at.date()}\n" if created_at else "")
        + "\n"
        f"⭐ Рейтинг: {stats['avg_score'] or 'нет оценок'}\n"
        f"💬 Отзывов: {stats['reviews_count']}\n"
        f"📞 Телефон: {phone or 'не указан'}\n"
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

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Мои отзывы", callback_data=f"profile_reviews_{user_id}")]
        ])
    )






async def user_profile_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = int(q.data.split("_")[2])

    user = await DB.fetchrow("""
        SELECT id, full_name, role, verified, plan_type, created_at
        FROM users
        WHERE id=$1
    """, user_id)

    if not user:
        await q.message.reply_text("❌ Пользователь не найден")
        return

    stats = await DB.fetchrow("""
        SELECT
            COUNT(*) AS reviews_count,
            ROUND(AVG(overall_score)::numeric, 2) AS avg_score,
            COUNT(*) FILTER (WHERE is_complaint=true OR overall_score <= 2) AS complaints_count
        FROM reviews
        WHERE to_user_id=$1
          AND deleted_at IS NULL
    """, user_id)

    deals_done = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE (c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1)
          AND d.status IN ('done','delivered','closed')
    """, user_id)

    last_reviews = await DB.fetch("""
        SELECT overall_score, comment, is_complaint, created_at
        FROM reviews
        WHERE to_user_id=$1
          AND deleted_at IS NULL
        ORDER BY id DESC
        LIMIT 3
    """, user_id)

    role_names = {
        "admin": "🛠 Админ",
        "driver": "🚚 Водитель",
        "carrier": "🚚 Перевозчик",
        "shipper": "📦 Грузоотправитель",
        "dispatcher": "📡 Диспетчер",
        "company": "🏢 Компания"
    }

    text = (
        f"👤 Профиль пользователя\n\n"
        f"Имя: {user['full_name'] or '-'}\n"
        f"Роль: {role_names.get(user['role'], user['role'] or '-')}\n"
        f"{'✅ Проверен' if user['verified'] else '⚠️ Не проверен'}\n"
        f"Тариф: {(user['plan_type'] or 'free').upper()}\n"
        f"📅 На платформе с: {user['created_at'].date() if user['created_at'] else '-'}\n\n"
        f"⭐ Рейтинг: {stats['avg_score'] or 'нет оценок'}\n"
        f"💬 Отзывов: {stats['reviews_count']}\n"
        f"🚩 Низких оценок/жалоб: {stats['complaints_count']}\n"
        f"✅ Выполнено сделок: {deals_done}\n"
    )

    if last_reviews:
        text += "\n💬 Последние отзывы:\n"
        for r in last_reviews:
            mark = "🚩" if r["is_complaint"] or r["overall_score"] <= 2 else "⭐"
            comment = r["comment"] or "Без комментария"
            if len(comment) > 120:
                comment = comment[:120] + "..."
            text += f"\n{mark} {r['overall_score']}⭐ — {comment}"

    await q.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 Все отзывы", callback_data=f"profile_reviews_{user_id}")]
        ])
    )


async def profile_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = int(q.data.split("_")[2])

    rows = await DB.fetch("""
        SELECT
            r.overall_score,
            r.comment,
            r.is_complaint,
            r.created_at,
            u.full_name AS from_name
        FROM reviews r
        LEFT JOIN users u ON u.id = r.from_user_id
        WHERE r.to_user_id=$1
          AND r.deleted_at IS NULL
        ORDER BY r.id DESC
        LIMIT 10
    """, user_id)

    if not rows:
        await q.message.reply_text("💬 Отзывов пока нет")
        return

    text = "💬 Последние отзывы\n\n"

    for r in rows:
        mark = "🚩" if r["is_complaint"] else "⭐"
        comment = r["comment"] or "Без комментария"
        if len(comment) > 300:
            comment = comment[:300] + "..."

        text += (
            f"{mark} {r['overall_score']}⭐ от {r['from_name'] or 'Пользователь'}\n"
            f"📝 {comment}\n"
            f"📅 {r['created_at'].strftime('%d.%m.%Y')}\n\n"
        )

    await q.message.reply_text(text.strip())


async def profile_phone_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    context.user_data["awaiting_phone"] = True

    await q.message.reply_text(
        "📞 Введите телефон для связи\n\nНапример: +79991234567"
    )


async def profile_phone_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_phone"):
        return

    phone = update.message.text.strip()

    context.user_data["awaiting_phone"] = False

    user_id = await ensure_user(update.effective_user)

    await DB.execute("""
        UPDATE users
        SET phone=$1
        WHERE id=$2
    """, phone, user_id)

    await update.message.reply_text("✅ Телефон сохранён")

    await profile(update, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info(f"start called args={context.args}, user={update.effective_user.id if update.effective_user else None}")
    user_id = await ensure_user(update.effective_user)

    if context.args and context.args[0].startswith("cargo_"):
        try:
            cargo_id = int(context.args[0].replace("cargo_", ""))
        except ValueError:
            cargo_id = None

        if cargo_id:
            cargo = await DB.fetchrow("""
                SELECT
                    id,
                    from_city,
                    to_city,
                    description,
                    price_amount,
                    price_currency,
                    distance_km,
                    rate_per_km,
                    weight_kg,
                    volume_m3,
                    places_count,
                    cargo_type,
                    status,
                    vip_until,
                    boost_until
                FROM cargo
                WHERE id=$1
            """, cargo_id)

            if not cargo:
                await update.message.reply_text("❌ Груз не найден")
                return

            if user_id:
                await DB.execute("""
                    INSERT INTO cargo_views (cargo_id, user_id)
                    VALUES ($1, $2)
                    ON CONFLICT (cargo_id, user_id)
                    DO UPDATE SET viewed_at=now()
                """, cargo_id, user_id)

            views_count = await DB.fetchval("""
                SELECT COUNT(*)
                FROM cargo_views
                WHERE cargo_id=$1
            """, cargo_id)

            responses_count = await DB.fetchval("""
                SELECT COUNT(*)
                FROM responses
                WHERE cargo_id=$1
            """, cargo_id)

            deals_count = await DB.fetchval("""
                SELECT COUNT(*)
                FROM deals
                WHERE cargo_id=$1
            """, cargo_id)

            badges = ""
            if cargo["vip_until"]:
                badges += f"⭐ VIP груз до {cargo['vip_until'].strftime('%d.%m.%Y')}\n"
            if cargo["boost_until"]:
                badges += f"🚀 Поднят в ТОП до {cargo['boost_until'].strftime('%d.%m.%Y')}\n"
            if badges:
                badges += "\n"

            await update.message.reply_text(
                (
                    f"{badges}"
                    f"📦 Груз #{cargo['id']}\n"
                    f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
                    f"💰 {format_price(cargo['price_amount'])} {cargo['price_currency'] or 'RUB'}\n"
                    f"📏 {cargo['distance_km'] or '-'} км\n"
                    f"💵 {round(float(cargo['rate_per_km']), 2) if cargo['rate_per_km'] else 'нет'} ₽/км\n"
                    f"⚖️ Вес: {cargo['weight_kg'] or 0} кг\n"
                    f"📦 Объём: {cargo['volume_m3'] or 0} м³\n"
                    f"🔢 Мест: {cargo['places_count'] or 0}\n"
                    f"🚚 Тип: {cargo_type_name(cargo['cargo_type'])}\n"
                    f"📊 {human_status(cargo['status'])}\n"
                    f"👁 Просмотров: {views_count}\n"
                    f"📨 Откликов: {responses_count}\n"
                    f"🤝 Сделок: {deals_count}\n"
                    f"📝 {cargo['description'] or '-'}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{cargo_id}")],
                    [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{cargo_id}")],
                    [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{cargo_id}")],
                    [InlineKeyboardButton("🗺 Карта", web_app=WebAppInfo(url="https://dalnoboybros.ru/map.html?v=229"))]
                ])
            )
            return

    if context.args and context.args[0].startswith("user_"):
        try:
            target_user_id = int(context.args[0].replace("user_", ""))
        except ValueError:
            target_user_id = None

        if target_user_id:
            user = await DB.fetchrow("""
                SELECT id, full_name, verified, plan_type
                FROM users
                WHERE id=$1
            """, target_user_id)

            truck = await DB.fetchrow("""
                SELECT id, current_city, body_type, capacity_tons, volume_m3, min_rate_per_km, status, location_updated_at
                FROM trucks
                WHERE driver_id=$1
                ORDER BY id DESC
                LIMIT 1
            """, target_user_id)

            if not user:
                await update.message.reply_text("❌ Пользователь не найден")
                return

            text = (
                f"👤 Пользователь #{user['id']}\n"
                f"Имя: {user['full_name'] or '-'} {'✅' if user['verified'] else ''}\n"
                f"Тариф: {user['plan_type'] or 'free'}\n\n"
            )

            if truck:
                text += (
                    f"🚚 Машина #{truck['id']}\n"
                    f"📍 Город: {truck['current_city'] or 'Не указан'}\n"
                    f"📦 Кузов: {truck['body_type'] or 'Не указан'}\n"
                    f"⚖️ Тоннаж: {truck['capacity_tons'] or '-'} т\n"
                    f"📦 Объём: {truck['volume_m3'] or '-'} м³\n"
        f"💰 Мин. ставка: {truck['min_rate_per_km'] or '-'} ₽/км\n"
                    f"📊 Статус: {human_status(truck['status'])}\n"
                    f"🕒 GEO: {truck['location_updated_at'] or '-'}"
                )
            else:
                text += "🚚 Машина не добавлена"

            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🤝 Сделки", callback_data="menu_deals")],
                    [InlineKeyboardButton("📦 Грузы", callback_data="menu_cargo")]
                ])
            )
            return

    access = await get_user_roles(update.effective_user.id)

    role = access["primary_role"]
    roles = access["roles"]
    verified = access["verified"]
    banned = access["banned"]

    if banned:
        await update.message.reply_text("⛔ Ваш аккаунт заблокирован")
        return

    if not verified:
        await update.message.reply_text(
            "🚛 Добро пожаловать в Dalnoboy Bros!\n\n"
            "Ваш аккаунт пока не одобрен. Подайте заявку, админ проверит доступ.",
            reply_markup=main_reply_keyboard(role, verified, roles)
        )
        return

    await update.message.reply_text(
        "🚛 Добро пожаловать в Dalnoboy Bros!",
        reply_markup=main_reply_keyboard(role, verified, roles)
    )



LEGAL_DOC_VERSION = "2026-06-17-v1"
LEGAL_CONSENT_TYPES = [
    "user_agreement",
    "privacy_policy",
    "personal_data_consent",
    "geo_consent"
]


async def has_required_legal_consents(user_id: int) -> bool:
    count = await DB.fetchval("""
        SELECT COUNT(DISTINCT consent_type)
        FROM user_consents
        WHERE user_id=$1
          AND document_version=$2
          AND source='telegram_bot'
          AND revoked_at IS NULL
          AND consent_type = ANY($3::text[])
    """, user_id, LEGAL_DOC_VERSION, LEGAL_CONSENT_TYPES)

    return int(count or 0) >= len(LEGAL_CONSENT_TYPES)



async def require_legal_for_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    q = update.callback_query
    user_id = await ensure_user(q.from_user)

    if await has_required_legal_consents(user_id):
        return True

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принимаю условия", callback_data="legal_consent_accept")],
        [InlineKeyboardButton("❌ Не принимаю", callback_data="legal_consent_decline")]
    ])

    await q.message.reply_text(
        "⚖️ Перед использованием сервиса нужно принять условия.\n\n"
        "Отправьте /consent или нажмите кнопку ниже.",
        reply_markup=kb
    )
    return False



async def consent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if await has_required_legal_consents(user_id):
        await update.message.reply_text(
            f"✅ Юридические условия уже приняты.\nВерсия: {LEGAL_DOC_VERSION}"
        )
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принимаю условия", callback_data="legal_consent_accept")],
        [InlineKeyboardButton("❌ Не принимаю", callback_data="legal_consent_decline")]
    ])

    await update.message.reply_text(
        "⚖️ Перед использованием сервиса нужно принять условия.\n\n"
        "1. Пользовательское соглашение\n"
        "2. Политика обработки персональных данных\n"
        "3. Согласие на обработку персональных данных\n"
        "4. Согласие на использование геолокации\n\n"
        f"Версия документов: {LEGAL_DOC_VERSION}",
        reply_markup=kb
    )


async def legal_consent_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    if q.data == "legal_consent_decline":
        await q.message.reply_text(
            "❌ Условия не приняты.\nДоступ к сервису может быть ограничен.\nДля возврата отправьте /consent"
        )
        return

    tg_id = q.from_user.id

    for consent_type in LEGAL_CONSENT_TYPES:
        await DB.execute("""
            INSERT INTO user_consents (
                user_id, telegram_id, consent_type,
                document_version, source, text_hash, payload
            )
            VALUES ($1, $2, $3, $4, 'telegram_bot', 'draft-v1', '{}'::jsonb)
            ON CONFLICT (user_id, consent_type, document_version, source)
            DO UPDATE SET
                accepted_at=now(),
                revoked_at=NULL,
                text_hash='draft-v1'
        """, user_id, tg_id, consent_type, LEGAL_DOC_VERSION)

    await audit(
        user_id,
        "legal_consent_accepted",
        payload={"version": LEGAL_DOC_VERSION, "source": "telegram_bot"}
    )

    await q.message.reply_text("✅ Условия приняты.\nТеперь можно пользоваться сервисом. Отправьте /menu")




def format_price(value):
    """
    Красивый формат цены:
    15000 -> 15 000
    None -> 0
    """
    try:
        if value is None:
            return "0"

        n = float(value)

        if n.is_integer():
            return f"{int(n):,}".replace(",", " ")

        return f"{n:,.2f}".replace(",", " ").replace(".00", "")
    except Exception:
        return str(value or "0")




def cargo_type_name(value):
    """
    Человекочитаемый тип груза.
    """
    value = (value or "full").strip().lower()

    names = {
        "full": "Полная загрузка",
        "partial": "Догруз",
        "parcel": "Посылка",
        "mail": "Документы",
    }

    return names.get(value, value)




def human_status(value):
    """
    Человекочитаемый статус груза/сделки.
    """
    mapping = {
        "open": "🟢 Открыт",
        "pending": "🟡 Ожидает",
        "active": "🤝 Сделка создана",
        "driver_assigned": "👤 Водитель назначен",
        "to_pickup": "🚚 Еду на загрузку",
        "loading": "📍 На загрузке",
        "loaded": "📦 Загружен",
        "in_progress": "🚚 В пути",
        "delivered": "🏁 Доставлен",
        "done": "✅ Завершён",
        "closed": "✅ Закрыт",
        "cancelled": "❌ Отменён",
        "deleted": "🗑 Удалён"
    }
    return mapping.get(value, value or "-")


async def cargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await DB.fetch("""
        SELECT
            c.id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.price_currency,
            c.weight_kg,
            c.volume_m3,
            c.places_count,
            c.cargo_type,
            c.status,
            c.vip_until,
            c.boost_until,
            c.load_latitude,
            c.load_longitude,
            COALESCE(u.plan_type, 'free') AS plan_type
        FROM cargo c
        LEFT JOIN users u ON u.id = c.created_by
        WHERE c.status='open'
        ORDER BY
            CASE WHEN c.boost_until > now() THEN 0 ELSE 1 END,
            CASE WHEN c.vip_until > now() THEN 0 ELSE 1 END,
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

    user_id = await ensure_user(update.effective_user)

    my_truck = await DB.fetchrow("""
        SELECT latitude, longitude
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    items = []
    for r in rows:
        dist = None
        if my_truck and my_truck["latitude"] and my_truck["longitude"] and r["load_latitude"] and r["load_longitude"]:
            dist = distance_km(my_truck["latitude"], my_truck["longitude"], r["load_latitude"], r["load_longitude"])
        items.append((dist, r))

    items.sort(
        key=lambda x: (
            0 if x[1]["boost_until"] and x[1]["boost_until"] > datetime.now() else 1,
            0 if x[1]["vip_until"] and x[1]["vip_until"] > datetime.now() else 1,
            x[0] is None,
            x[0] if x[0] is not None else 999999
        )
    )

    await update.message.reply_text("📦 Последние грузы:")

    for dist, r in items:
        flags = ""
        if r["boost_until"] and r["boost_until"] > datetime.now():
            flags += "🚀"
        if r["vip_until"] and r["vip_until"] > datetime.now():
            flags += "⭐"

        distance_text = f"\n📍 До загрузки: {dist} км" if dist is not None else ""
        desc = r["description"] or "Без описания"

        if len(desc) > 120:
            desc = desc[:120] + "..."

        await update.message.reply_text(
            f"{flags}📦 Груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {desc}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or 'RUB'}\n"
            f"⚖️ {r['weight_kg'] or 0} кг | 📦 {r['volume_m3'] or 0} м³ | 🔢 {r['places_count'] or 0}\n"
            f"🚚 {cargo_type_name(r['cargo_type'])}"
            f"{distance_text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")]
            ])
        )




async def mycargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT id, from_city, to_city, description, price_amount, price_currency,
               weight_kg, volume_m3, places_count, cargo_type, status
        FROM cargo
        WHERE created_by=$1
          AND status <> 'deleted'
        ORDER BY
            CASE
                WHEN status='open' THEN 2
                WHEN status='cancelled' THEN 1
                ELSE 0
            END ASC,
            id ASC
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
                [InlineKeyboardButton("🔝 Поднять груз", callback_data=f"cargo_refresh_{r['id']}")],
                [InlineKeyboardButton("🚀 Заявка: ТОП", callback_data=f"cargo_promo_boost_{r['id']}")],
                [InlineKeyboardButton("⭐ Заявка: VIP", callback_data=f"cargo_promo_vip_{r['id']}")]
            ])

        await update.message.reply_text(
            f"📦 Мой груз #{r['id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"📝 {r['description'] or 'Без описания'}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"⚖️ Вес: {r['weight_kg'] or 0} кг\n"
            f"📦 Объём: {r['volume_m3'] or 0} м³\n"
            f"🔢 Мест: {r['places_count'] or 0}\n"
            f"🚚 Тип: {cargo_type_name(r['cargo_type'])}\n"
            f"📊 Статус: {human_status(r['status'])}",
            reply_markup=kb
        )







async def cargo_promo_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    user_id = await ensure_user(q.from_user)

    parts = q.data.split("_")
    promo_type = parts[2]
    cargo_id = int(parts[3])

    cargo = await DB.fetchrow("""
        SELECT id, created_by, from_city, to_city, price_amount, price_currency
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await q.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await q.message.reply_text("⛔ Можно продвигать только свой груз")
        return

    promo_text = "🚀 Поднять в ТОП" if promo_type == "boost" else "⭐ VIP груз"

    await q.message.reply_text(
        f"✅ Заявка отправлена администратору\n\n"
        f"{promo_text}\n"
        f"📦 Груз #{cargo['id']}\n"
        f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
        f"💰 {format_price(cargo['price_amount'])} {cargo['price_currency'] or 'RUB'}"
    )

    admin_text = (
        f"💰 Новая заявка на продвижение\n\n"
        f"{promo_text}\n"
        f"📦 Груз #{cargo['id']}\n"
        f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
        f"💰 {format_price(cargo['price_amount'])} {cargo['price_currency'] or 'RUB'}\n\n"
        f"Пользователь: {q.from_user.full_name}\n"
        f"TG ID: {q.from_user.id}\n"
        f"Username: @{q.from_user.username or '-'}\n\n"
        f"Команды после оплаты:\n"
        f"/boostcargo {cargo['id']} 7\n"
        f"/vipcargo {cargo['id']} 30"
    )

    try:
        await context.bot.send_message(chat_id=439871270, text=admin_text)
    except Exception as e:
        logging.warning(f"promo request notify admin failed: {e}")


async def cargo_clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

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

    await audit(user_id, "cargo_refreshed", cargo_id=cargo_id, payload={"plan_type": plan})

    await q.message.reply_text(
        f"🔝 Груз #{cargo_id} поднят в поиске"
    )



async def cargo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    await audit(user_id, "cargo_cancelled", cargo_id=cargo_id, payload={"previous_status": cargo["status"]})

    await q.message.reply_text(
        f"❌ Груз #{cargo_id} снят с публикации"
    )

    # fresh_card_after_cargo_cancel
    fresh = await DB.fetchrow("""
        SELECT id, from_city, to_city, description, price_amount, price_currency,
               weight_kg, volume_m3, places_count, cargo_type, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if fresh:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Опубликовать снова", callback_data=f"cargo_open_{fresh['id']}")],
            [InlineKeyboardButton("🗑 Удалить груз", callback_data=f"cargo_delete_{fresh['id']}")]
        ])

        await q.message.reply_text(
            f"📦 Мой груз #{fresh['id']}\n"
            f"🚩 {fresh['from_city']} → {fresh['to_city']}\n"
            f"📝 {fresh['description'] or 'Без описания'}\n"
            f"💰 {format_price(fresh['price_amount'])} {fresh['price_currency'] or ''}\n"
            f"⚖️ Вес: {fresh['weight_kg'] or 0} кг\n"
            f"📦 Объём: {fresh['volume_m3'] or 0} м³\n"
            f"🔢 Мест: {fresh['places_count'] or 0}\n"
            f"🚚 Тип: {cargo_type_name(fresh['cargo_type'])}\n"
            f"📊 Статус: {human_status(fresh['status'])}",
            reply_markup=kb
        )





async def cargo_open(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    await audit(user_id, "cargo_opened", cargo_id=cargo_id, payload={"previous_status": cargo["status"]})

    await q.message.reply_text(f"✅ Груз #{cargo_id} снова опубликован")

    # fresh_card_after_cargo_open
    fresh = await DB.fetchrow("""
        SELECT id, from_city, to_city, description, price_amount, price_currency,
               weight_kg, volume_m3, places_count, cargo_type, status
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if fresh:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Снять груз", callback_data=f"cargo_cancel_{fresh['id']}")],
            [InlineKeyboardButton("🔁 Повторить", callback_data=f"cargo_clone_{fresh['id']}")],
            [InlineKeyboardButton("🔝 Поднять груз", callback_data=f"cargo_refresh_{fresh['id']}")],
            [InlineKeyboardButton("🚀 Заявка: ТОП", callback_data=f"cargo_promo_boost_{fresh['id']}")],
            [InlineKeyboardButton("⭐ Заявка: VIP", callback_data=f"cargo_promo_vip_{fresh['id']}")]
        ])

        await q.message.reply_text(
            f"📦 Мой груз #{fresh['id']}\n"
            f"🚩 {fresh['from_city']} → {fresh['to_city']}\n"
            f"📝 {fresh['description'] or 'Без описания'}\n"
            f"💰 {format_price(fresh['price_amount'])} {fresh['price_currency'] or ''}\n"
            f"⚖️ Вес: {fresh['weight_kg'] or 0} кг\n"
            f"📦 Объём: {fresh['volume_m3'] or 0} м³\n"
            f"🔢 Мест: {fresh['places_count'] or 0}\n"
            f"🚚 Тип: {cargo_type_name(fresh['cargo_type'])}\n"
            f"📊 Статус: {human_status(fresh['status'])}",
            reply_markup=kb
        )







async def cargo_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

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

    await audit(user_id, "cargo_deleted", cargo_id=cargo_id, payload={"previous_status": cargo["status"]})

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
    pending_access = await DB.fetchval("SELECT COUNT(*) FROM access_requests WHERE status='pending'")

    text = (
        f"🛡 Админ-панель Dalnoboy Bros\n\n"
        f"👤 Ваш ID: {user_id}\n\n"
        f"👥 Пользователей: {users_count}\n"
        f"📦 Грузов всего: {cargo_count}\n"
        f"🟢 Открытых грузов: {open_cargo}\n"
        f"🤝 Сделок всего: {deals_count}\n"
        f"🚚 Активных сделок: {active_deals}\n"
        f"🚛 Машин: {trucks_count}\n"
        f"📝 Заявок на роли: {pending_access}\n\n"
        f"Выберите раздел:"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_panel_stats"),
         InlineKeyboardButton("📄 Отчёт", callback_data="admin_panel_report")],
        [InlineKeyboardButton("📅 Сегодня", callback_data="admin_panel_today"),
         InlineKeyboardButton("👥 Пользователи", callback_data="admin_panel_users")],
        [InlineKeyboardButton("📦 Грузы", callback_data="admin_panel_cargo"),
         InlineKeyboardButton("🤝 Сделки", callback_data="admin_panel_deals")],
        [InlineKeyboardButton("⚠️ Жалобаы", callback_data="admin_panel_disputes"),
         InlineKeyboardButton("💳 Тарифы", callback_data="admin_tariffs")],
        [InlineKeyboardButton("🚩 Жалобы", callback_data="admin_panel_reports"),
         InlineKeyboardButton("🔔 Маршруты", callback_data="admin_panel_routes")],
        [InlineKeyboardButton("⚖️ Аудит", callback_data="admin_panel_audit"),
         InlineKeyboardButton("👥 Роли", callback_data="admin_panel_roles")]
    ])

    await update.message.reply_text(text, reply_markup=kb)




async def admin_tariffs_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(q.from_user)
    if admin_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT code, title, price_month, active
        FROM tariff_settings
        ORDER BY
            CASE code
                WHEN 'free' THEN 1
                WHEN 'pro' THEN 2
                WHEN 'company' THEN 3
                WHEN 'dispatcher' THEN 4
                ELSE 9
            END
    """)

    text = "💳 Управление тарифами\n\n"
    buttons = []

    for r in rows:
        status = "✅" if r["active"] else "⛔"
        text += f"{status} {r['code'].upper()} — {r['title']}\nЦена: {r['price_month']} ₽/мес\n\n"

        if r["code"] in ("pro", "company"):
            buttons.append([
                InlineKeyboardButton(
                    f"✏️ Изменить {r['code'].upper()}",
                    callback_data=f"tariff_edit_{r['code']}"
                )
            ])

    buttons.append([InlineKeyboardButton("⬅️ Назад в админку", callback_data="admin_panel")])

    await q.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def tariff_edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(q.from_user)
    if admin_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    code = q.data.replace("tariff_edit_", "").strip()

    row = await DB.fetchrow("""
        SELECT code, title, price_month
        FROM tariff_settings
        WHERE code=$1
    """, code)

    if not row:
        await q.message.reply_text("❌ Тариф не найден")
        return

    context.user_data["awaiting_tariff_price"] = code

    await q.message.reply_text(
        f"✏️ Изменение цены тарифа\n\n"
        f"{row['code'].upper()} — {row['title']}\n"
        f"Текущая цена: {row['price_month']} ₽/мес\n\n"
        f"Введите новую цену числом.\n"
        f"Например: 590"
    )


async def tariff_price_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = context.user_data.get("awaiting_tariff_price")
    if not code:
        return

    admin_id = await ensure_user(update.effective_user)
    if admin_id != 1:
        context.user_data.pop("awaiting_tariff_price", None)
        await update.message.reply_text("⛔ Нет доступа")
        return

    raw = update.message.text.strip().replace(" ", "").replace(",", ".")

    try:
        price = int(float(raw))
    except Exception:
        await update.message.reply_text("❌ Введите цену числом, например: 590")
        return

    context.user_data.pop("awaiting_tariff_price", None)

    row = await DB.fetchrow("""
        UPDATE tariff_settings
        SET price_month=$1,
            updated_at=now()
        WHERE code=$2
        RETURNING code, title, price_month
    """, price, code)

    if not row:
        await update.message.reply_text("❌ Тариф не найден")
        return

    await update.message.reply_text(
        f"✅ Цена сохранена\n\n"
        f"{row['code'].upper()} — {row['title']}\n"
        f"Новая цена: {row['price_month']} ₽/мес"
    )


async def admin_audit_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user_id = await ensure_user(q.from_user)

    if not await is_admin_user(user_id):
        await q.message.reply_text("⛔ Доступ только для администратора")
        return

    rows = await DB.fetch("""
        SELECT id, user_id, action, deal_id, cargo_id, payload, created_at
        FROM audit_log
        ORDER BY id DESC
        LIMIT 15
    """)

    if not rows:
        await q.message.reply_text("📭 Журнал аудита пока пуст")
        return

    lines = [
        "⚖️ Последние записи audit_log\n",
        "Команды для подробного просмотра:",
        "/auditcargo 14",
        "/auditdeal 10\n",
    ]

    for r in rows:
        lines.append(
            f"#{r['id']} — {r['action']}\n"
            f"👤 user_id: {r['user_id'] or '-'}\n"
            f"📦 cargo_id: {r['cargo_id'] or '-'}\n"
            f"🤝 deal_id: {r['deal_id'] or '-'}\n"
            f"🕒 {r['created_at']}\n"
            f"📌 {r['payload'] or {}}\n"
        )

    text = "\n".join(lines)

    if len(text) > 3900:
        text = text[:3900] + "\n...обрезано"

    await q.message.reply_text(text)



async def admin_roles_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    admin_user_id = await ensure_user(q.from_user)

    if not await is_admin_user(admin_user_id):
        await q.message.reply_text("⛔ Доступ только для администратора")
        return

    rows = await DB.fetch("""
        SELECT id, telegram_id, full_name, role, verified, plan_type
        FROM users
        ORDER BY id DESC
        LIMIT 20
    """)

    if not rows:
        await q.message.reply_text("📭 Пользователей пока нет")
        return

    role_names = {
        "admin": "🛠 Админ",
        "carrier": "🚚 Перевозчик",
        "driver": "🚚 Водитель",
        "shipper": "📦 Грузовладелец",
        "dispatcher": "📡 Диспетчер"
    }

    lines = [
        "👥 Роли пользователей\n",
        "Команды:",
        "/setrole USER_ID carrier",
        "/setrole USER_ID shipper",
        "/setrole USER_ID dispatcher",
        "/setrole USER_ID admin\n"
    ]

    for r in rows:
        verified = "✅" if r["verified"] else "⏳"
        role = role_names.get(r["role"], r["role"] or "-")
        lines.append(
            f"#{r['id']} | {r['full_name'] or '-'} | {role} | {verified} | {r['plan_type'] or 'free'}"
        )

    text = "\n".join(lines)

    if len(text) > 3900:
        text = text[:3900] + "\n...обрезано"

    await q.message.reply_text(text)



async def admin_panel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(q.from_user)

    if admin_id != 1:
        await q.message.reply_text("⛔ Нет доступа")
        return

    class FakeUpdate:
        def __init__(self, q):
            self.message = q.message
            self.effective_user = q.from_user

    fake_update = FakeUpdate(q)

    data = q.data

    if data == "admin_panel":
        return await admin(fake_update, context)

    if data == "admin_panel_stats":
        return await dashboard(fake_update, context)

    if data == "admin_panel_report":
        return await dealreport(fake_update, context)

    if data == "admin_panel_today":
        return await today(fake_update, context)

    if data == "admin_panel_users":
        return await adminusers(fake_update, context)

    if data == "admin_panel_cargo":
        return await admincargo(fake_update, context)

    if data == "admin_panel_deals":
        return await admindeals(fake_update, context)

    if data == "admin_panel_disputes":
        return await admindisputes(fake_update, context)

    if data == "admin_panel_subs":
        return await adminsubs(fake_update, context)

    if data == "admin_panel_reports":
        return await adminreports(fake_update, context)

    if data == "admin_panel_audit":
        return await admin_audit_panel(update, context)

    if data == "admin_panel_roles":
        return await admin_roles_panel(update, context)

    if data == "admin_panel_routes":
        return await mysubs(fake_update, context)

    await q.message.reply_text("❌ Неизвестный раздел админки")


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

    text = f"💬 Чат по грузу #{deal_id}\n\n"

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

    deal = await DB.fetchrow("""
        SELECT id, cargo_id
        FROM deals
        WHERE id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=false
        WHERE id=$1
    """, deal_id)

    await audit(
        admin_id,
        "dispute_closed",
        deal_id=deal_id,
        cargo_id=deal["cargo_id"],
        payload={"source": "command"}
    )

    await update.message.reply_text(f"✅ Жалоба по сделке #{deal_id} закрыт")


async def admindisputes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admin_id = await ensure_user(update.effective_user)

    if admin_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            d.client_price,
            d.carrier_price,
            d.dispatcher_profit,
            d.safe_deal_status,
            d.shipper_confirmed,
            d.carrier_confirmed,
            d.dispatcher_confirmed,
            d.payment_status,
            d.dispute,
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
        await update.message.reply_text("✅ Открытых жалобуов нет")
        return

    text = "⚠️ Открытые жалобуы\n\n"

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
        [InlineKeyboardButton("✅ Закрыть первый жалобу", callback_data=f"admin_close_dispute_{rows[0]['id']}")]
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

        await q.message.reply_text(f"✅ Жалоба по сделке #{deal_id} закрыт")
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
        f"⚠️ Открытых жалобуов: {disputes_open}\n"
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
        LIMIT 3
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
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    users_count = await DB.fetchval("SELECT COUNT(*) FROM users")
    verified_count = await DB.fetchval("SELECT COUNT(*) FROM users WHERE verified=true")
    banned_count = await DB.fetchval("SELECT COUNT(*) FROM users WHERE banned=true")

    carriers = await DB.fetchval("SELECT COUNT(DISTINCT user_id) FROM user_roles WHERE role='carrier' AND active=true")
    shippers = await DB.fetchval("SELECT COUNT(DISTINCT user_id) FROM user_roles WHERE role='shipper' AND active=true")
    dispatchers = await DB.fetchval("SELECT COUNT(DISTINCT user_id) FROM user_roles WHERE role='dispatcher' AND active=true")

    trucks_total = await DB.fetchval("SELECT COUNT(*) FROM trucks")
    trucks_active = await DB.fetchval("SELECT COUNT(*) FROM trucks WHERE status='active'")
    trucks_geo = await DB.fetchval("SELECT COUNT(*) FROM trucks WHERE latitude IS NOT NULL AND longitude IS NOT NULL")

    cargo_total = await DB.fetchval("SELECT COUNT(*) FROM cargo")
    cargo_open = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='open'")
    cargo_done = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE status='done'")
    cargo_today = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at::date = CURRENT_DATE")

    responses_total = await DB.fetchval("SELECT COUNT(*) FROM responses")
    responses_today = await DB.fetchval("SELECT COUNT(*) FROM responses WHERE created_at::date = CURRENT_DATE")

    deals_total = await DB.fetchval("SELECT COUNT(*) FROM deals")
    deals_active = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status IN ('pending','active','in_progress')")
    deals_done = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE status='done'")
    deals_today = await DB.fetchval("SELECT COUNT(*) FROM deals WHERE created_at::date = CURRENT_DATE")

    reviews_count = await DB.fetchval("SELECT COUNT(*) FROM reviews WHERE deleted_at IS NULL")

    cargo_turnover_total = await DB.fetchval("""
        SELECT COALESCE(SUM(price_amount), 0)
        FROM cargo
        WHERE price_currency='RUB' OR price_currency IS NULL
    """)

    cargo_turnover_open = await DB.fetchval("""
        SELECT COALESCE(SUM(price_amount), 0)
        FROM cargo
        WHERE status='open'
          AND (price_currency='RUB' OR price_currency IS NULL)
    """)

    cargo_turnover_7d = await DB.fetchval("""
        SELECT COALESCE(SUM(price_amount), 0)
        FROM cargo
        WHERE created_at > now() - interval '7 days'
          AND (price_currency='RUB' OR price_currency IS NULL)
    """)

    avg_rate = await DB.fetchval("""
        SELECT ROUND(AVG(rate_per_km)::numeric, 2)
        FROM cargo
        WHERE rate_per_km IS NOT NULL
    """)

    access_pending = await DB.fetchval("SELECT COUNT(*) FROM access_requests WHERE status='pending'")
    dispatcher_clients = await DB.fetchval("SELECT COUNT(*) FROM dispatcher_clients WHERE status='active'")

    new_users_today = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at::date = CURRENT_DATE")
    new_users_7d = await DB.fetchval("SELECT COUNT(*) FROM users WHERE created_at > now() - interval '7 days'")
    cargo_7d = await DB.fetchval("SELECT COUNT(*) FROM cargo WHERE created_at > now() - interval '7 days'")
    responses_7d = await DB.fetchval("SELECT COUNT(*) FROM responses WHERE created_at > now() - interval '7 days'")

    await update.message.reply_text(
        f"📊 Dalnoboy Bros — статистика\n\n"
        f"👥 Пользователи\n"
        f"Всего: {users_count}\n"
        f"✅ Проверенных: {verified_count}\n"
        f"⛔ Забаненных: {banned_count}\n"
        f"🆕 Сегодня: {new_users_today}\n"
        f"📅 За 7 дней: {new_users_7d}\n\n"

        f"🎭 Роли\n"
        f"🚛 Перевозчики: {carriers}\n"
        f"📦 Грузоотправители: {shippers}\n"
        f"👨‍💼 Диспетчеры: {dispatchers}\n"
        f"📝 Заявок на доступ: {access_pending}\n\n"

        f"🚚 Машины\n"
        f"Всего: {trucks_total}\n"
        f"🟢 Активных: {trucks_active}\n"
        f"📍 С геолокацией: {trucks_geo}\n\n"

        f"📦 Грузы\n"
        f"Всего: {cargo_total}\n"
        f"🟢 Открытых: {cargo_open}\n"
        f"✅ Завершённых: {cargo_done}\n"
        f"🆕 Сегодня: {cargo_today}\n"
        f"📅 За 7 дней: {cargo_7d}\n\n"

        f"💰 Деньги по грузам\n"
        f"Оборот всего: {format_price(cargo_turnover_total)} ₽\n"
        f"Открытые грузы: {format_price(cargo_turnover_open)} ₽\n"
        f"За 7 дней: {format_price(cargo_turnover_7d)} ₽\n"
        f"Средняя ставка: {avg_rate or '-'} ₽/км\n\n"

        f"📨 Отклики\n"
        f"Всего: {responses_total}\n"
        f"🆕 Сегодня: {responses_today}\n"
        f"📅 За 7 дней: {responses_7d}\n\n"

        f"🤝 Сделки\n"
        f"Всего: {deals_total}\n"
        f"🚚 Активных: {deals_active}\n"
        f"✅ Завершённых: {deals_done}\n"
        f"🆕 Сегодня: {deals_today}\n\n"

        f"👨‍💼 CRM диспетчера\n"
        f"Активных клиентов: {dispatcher_clients}\n\n"

        f"⭐ Отзывов: {reviews_count}"
    )




async def topcargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            c.id,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            c.status,
            COUNT(DISTINCT cv.user_id) AS views_count,
            COUNT(DISTINCT r.id) AS responses_count,
            COUNT(DISTINCT d.id) AS deals_count
        FROM cargo c
        LEFT JOIN cargo_views cv ON cv.cargo_id = c.id
        LEFT JOIN responses r ON r.cargo_id = c.id
        LEFT JOIN deals d ON d.cargo_id = c.id
        GROUP BY c.id
        ORDER BY views_count DESC, responses_count DESC, c.id DESC
        LIMIT 3
    """)

    if not rows:
        await update.message.reply_text("🔥 Популярных грузов пока нет")
        return

    text = "🔥 ТОП грузов по просмотрам\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for i, r in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        text += (
            f"{medal} Груз #{r['id']} — {human_status(r['status'])}\n"
            f"📍 {r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or 'RUB'}\n"
            f"👁 Просмотров: {r['views_count']}\n"
            f"📨 Откликов: {r['responses_count']}\n"
            f"🤝 Сделок: {r['deals_count']}\n"
            f"🔗 https://t.me/dalnoboybros_bot?start=cargo_{r['id']}\n\n"
        )

    await update.message.reply_text(text)




async def toproutes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            c.from_city,
            c.to_city,
            COUNT(DISTINCT c.id) AS cargo_count,
            COUNT(DISTINCT cv.user_id) AS views_count,
            COUNT(DISTINCT r.id) AS responses_count,
            COUNT(DISTINCT d.id) AS deals_count,
            COALESCE(SUM(c.price_amount), 0) AS turnover
        FROM cargo c
        LEFT JOIN cargo_views cv ON cv.cargo_id = c.id
        LEFT JOIN responses r ON r.cargo_id = c.id
        LEFT JOIN deals d ON d.cargo_id = c.id
        WHERE c.from_city IS NOT NULL
          AND c.to_city IS NOT NULL
        GROUP BY c.from_city, c.to_city
        ORDER BY views_count DESC, cargo_count DESC, responses_count DESC
        LIMIT 3
    """)

    if not rows:
        await update.message.reply_text("🔥 Популярных направлений пока нет")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "🔥 Популярные направления\n\n"

    for i, r in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"{i}."

        text += (
            f"{medal} {r['from_city']} → {r['to_city']}\n"
            f"📦 Грузов: {r['cargo_count']}\n"
            f"👁 Просмотров: {r['views_count']}\n"
            f"📨 Откликов: {r['responses_count']}\n"
            f"🤝 Сделок: {r['deals_count']}\n"
            f"💰 Оборот: {format_price(r['turnover'])} ₽\n\n"
        )

    await update.message.reply_text(text)




async def topdispatchers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    rows = await DB.fetch("""
        SELECT
            dc.dispatcher_user_id,
            u.full_name,
            u.telegram_id,
            COUNT(DISTINCT dc.client_user_id) AS clients_count,
            COUNT(DISTINCT d.id) AS deals_count,
            COUNT(DISTINCT d.id) FILTER (WHERE d.status IN ('pending','active','in_progress')) AS active_deals,
            COUNT(DISTINCT d.id) FILTER (WHERE d.status IN ('done','completed')) AS done_deals,
            COALESCE(SUM(
                CASE
                    WHEN d.status IN ('done','completed')
                    THEN c.price_amount * COALESCE(dc.commission_percent, 0) / 100
                    ELSE 0
                END
            ), 0) AS commission_income,
            COALESCE(SUM(
                CASE
                    WHEN d.status IN ('done','completed')
                    THEN c.price_amount
                    ELSE 0
                END
            ), 0) AS turnover_done
        FROM dispatcher_clients dc
        JOIN users u ON u.id = dc.dispatcher_user_id
        LEFT JOIN cargo c ON c.created_by = dc.client_user_id
        LEFT JOIN trucks t ON t.driver_id = dc.client_user_id
        LEFT JOIN deals d ON d.cargo_id = c.id OR d.truck_id = t.id
        WHERE dc.status='active'
        GROUP BY dc.dispatcher_user_id, u.full_name, u.telegram_id
        ORDER BY clients_count DESC, deals_count DESC, turnover_done DESC
        LIMIT 3
    """)

    if not rows:
        await update.message.reply_text("👨‍💼 Диспетчеров с клиентами пока нет")
        return

    medals = ["🥇", "🥈", "🥉"]
    text = "👨‍💼 ТОП диспетчеров\n\n"

    for i, r in enumerate(rows, start=1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        text += (
            f"{medal} {r['full_name'] or 'Без имени'}\n"
            f"TG ID: {r['telegram_id']}\n"
            f"👥 Клиентов: {r['clients_count']}\n"
            f"🤝 Сделок всего: {r['deals_count']}\n"
            f"🚚 Активных: {r['active_deals']}\n"
            f"✅ Завершено: {r['done_deals']}\n"
            f"💰 Оборот завершённых: {format_price(r['turnover_done'])} ₽\n"
            f"💼 Потенц. комиссия: {format_price(r['commission_income'])} ₽\n\n"
        )

    await update.message.reply_text(text)




async def boostcargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Формат: /boostcargo CARGO_ID DAYS\nПример: /boostcargo 21 7")
        return

    try:
        cargo_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ ID груза и дни должны быть числами")
        return

    row = await DB.fetchrow("""
        UPDATE cargo
        SET boost_until = now() + ($1::int * interval '1 day')
        WHERE id=$2
        RETURNING id, from_city, to_city, boost_until
    """, days, cargo_id)

    if not row:
        await update.message.reply_text("❌ Груз не найден")
        return

    await audit(user_id, "cargo_boosted", cargo_id=row["id"], payload={"days": days, "boost_until": str(row["boost_until"])})

    await update.message.reply_text(
        f"🚀 Груз #{row['id']} поднят в ТОП\n"
        f"{row['from_city']} → {row['to_city']}\n"
        f"До: {row['boost_until']}"
    )


async def vipcargo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if user_id != 1:
        await update.message.reply_text("⛔ Нет доступа")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Формат: /vipcargo CARGO_ID DAYS\nПример: /vipcargo 21 30")
        return

    try:
        cargo_id = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("❌ ID груза и дни должны быть числами")
        return

    row = await DB.fetchrow("""
        UPDATE cargo
        SET vip_until = now() + ($1::int * interval '1 day')
        WHERE id=$2
        RETURNING id, from_city, to_city, vip_until
    """, days, cargo_id)

    if not row:
        await update.message.reply_text("❌ Груз не найден")
        return

    await audit(user_id, "cargo_vip_enabled", cargo_id=row["id"], payload={"days": days, "vip_until": str(row["vip_until"])})

    await update.message.reply_text(
        f"⭐ Груз #{row['id']} получил VIP\n"
        f"{row['from_city']} → {row['to_city']}\n"
        f"До: {row['vip_until']}"
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

    if not await require_legal_for_callback(update, context):
        return

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

    await audit(user_id, "cargo_restored", cargo_id=cargo_id, payload={"previous_status": cargo["status"], "new_status": "cancelled"})

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
                [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}")],
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")]
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
            c.created_by AS owner_id,
            COALESCE(r.driver_id, t.driver_id) AS driver_id,
            t.capacity_tons,
            t.volume_m3,
            t.photo_url,
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
                    "💬 Переговоры",
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

    if not await require_legal_for_callback(update, context):
        return

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
        LIMIT 3
    """, user_id)

    if not cargos:
        await q.message.reply_text(
            "📦 У вас нет открытых грузов\n\n"
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

    if not await require_legal_for_callback(update, context):
        return

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
        LIMIT 3
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
        LIMIT 3
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
                [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}")],
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")]
        ])

        await update.message.reply_text(text, reply_markup=kb)





async def cargo_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    cargo_id = int(q.data.split("_")[-1])
    link = f"https://t.me/dalnoboybros_bot?start=cargo_{cargo_id}"

    await q.message.reply_text(
        f"🔗 Ссылка на груз #{cargo_id}:\n\n{link}"
    )


async def cargo_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    cargo_id = int(q.data.split("_")[-1])

    c = await DB.fetchrow("""
        SELECT id, from_city, to_city, description, price_amount, price_currency, distance_km, rate_per_km
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not c:
        await q.message.reply_text("❌ Груз не найден")
        return

    currency = "₽" if (c["price_currency"] or "RUB") == "RUB" else (c["price_currency"] or "RUB")

    text = (
        f"🚛 DALNOBOY BROS\n\n"
        f"📦 Груз #{c['id']}\n\n"
        f"📍 {c['from_city']} → {c['to_city']}\n\n"
        f"💰 {format_price(c['price_amount'])} {currency}\n"
        f"📏 {c['distance_km'] or '-'} км\n"
        f"💵 {round(float(c['rate_per_km']), 2) if c['rate_per_km'] else '-'} ₽/км\n\n"
        f"📦 Груз:\n"
        f"{c['description'] or '-'}\n\n"
        f"👇 Открыть груз и откликнуться:\n"
        f"https://t.me/dalnoboybros_bot?start=cargo_{c['id']}"
    )

    await q.message.reply_text(
        "📤 Текст для пересылки:\n\n" + text
    )


async def respond(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    selected_truck_id = None

    if q.data.startswith("truckrespond_"):
        parts = q.data.split("_")
        cargo_id = int(parts[1])
        selected_truck_id = int(parts[2])
    else:
        cargo_id = int(q.data.split("_")[1])

    tg_user = q.from_user
    user_id = await ensure_user(tg_user)

    cargo_exists = await DB.fetchrow("SELECT id FROM cargo WHERE id=$1", cargo_id)

    if not cargo_exists:
        await q.message.reply_text("❌ Груз не найден")
        return

    if selected_truck_id is not None:
        truck = await DB.fetchrow("""
            SELECT id
            FROM trucks
            WHERE id=$1 AND driver_id=$2
        """, selected_truck_id, user_id)

        if not truck:
            await q.message.reply_text("❌ Машина не найдена или не ваша")
            return
    else:
        trucks = await DB.fetch("""
            SELECT id, current_city, body_type, capacity_tons, volume_m3
            FROM trucks
            WHERE driver_id=$1
            ORDER BY id
        """, user_id)

        if not trucks:
            await q.message.reply_text("🚚 Сначала добавьте машину через /newtruck")
            return

        if len(trucks) > 1:
            buttons = []
            for t in trucks:
                buttons.append([
                    InlineKeyboardButton(
                        f"🚚 #{t['id']} — {t['body_type'] or '-'} / {t['capacity_tons'] or '-'} т / {t['volume_m3'] or '-'} м³",
                        callback_data=f"truckrespond_{cargo_id}_{t['id']}"
                    )
                ])

            await q.message.reply_text(
                "🚚 Выберите машину для отклика:",
                reply_markup=InlineKeyboardMarkup(buttons)
            )
            return

        truck = trucks[0]

    existing = await DB.fetchrow("""
        SELECT id FROM responses
        WHERE cargo_id=$1 AND driver_id=$2
    """, cargo_id, user_id)

    if existing:
        await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📨 Мой отклик / статус", callback_data="menu_myresponses")],
                [InlineKeyboardButton("✅ Вы уже откликнулись", callback_data="menu_myresponses")],
                [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{cargo_id}")]
            ])
        )
        return

    response_id = await DB.fetchval("""
        INSERT INTO responses (
            cargo_id,
            truck_id,
            driver_id,
            message,
            status
        )
        VALUES ($1,$2,$3,$4,'pending')
        RETURNING id
    """,
        cargo_id,
        truck["id"],
        user_id,
        f"Отклик от {tg_user.full_name}"
    )


    await audit(
        user_id,
        "response_created",
        cargo_id=cargo_id,
        payload={
            "response_id": response_id,
            "truck_id": truck["id"]
        }
    )

    owner = await DB.fetchrow("""
        SELECT
            u.telegram_id,
            c.from_city,
            c.to_city
        FROM cargo c
        JOIN users u ON u.id = c.created_by
        WHERE c.id=$1
    """, cargo_id)

    if owner and owner["telegram_id"]:
        try:
            await context.bot.send_message(
                chat_id=owner["telegram_id"],
                text=(
                    f"📨 Новый отклик на ваш груз\n\n"
                    f"📦 Груз #{cargo_id}\n"
                    f"🚩 {owner['from_city']} → {owner['to_city']}\n"
                    f"👤 Водитель: {tg_user.full_name}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("✅ Принять", callback_data=f"accept_{response_id}"),
                        InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{response_id}")
                    ],
                    [InlineKeyboardButton("📨 Все отклики", callback_data="menu_responses")]
                ])
            )
        except Exception as e:
            logging.warning(f"Response notify failed: {e}")

    await q.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Мой отклик / статус", callback_data="menu_myresponses")],
            [InlineKeyboardButton("✅ Вы откликнулись", callback_data="menu_myresponses")],
            [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{cargo_id}")]
        ])
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
            text=f"💬 Новое сообщение в сделке #{deal_id}\n"
                 f"От user_id: {user_id}\n"
                 f"Кому telegram_id: {other_tg}\n\n"
                 f"{text}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Ответить", callback_data=f"deal_chat_{deal_id}")],
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_history_{deal_id}")]
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
        await update.message.reply_text("❌ Чат не найден")
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
        LIMIT 3
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
        await update.message.reply_text("❌ Чат не найден")
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
    """, deal_id, user_id, f"⚠️ Причина жалобы: {reason}")

    await update.message.reply_text(f"⚠️ Причина жалобы по сделке #{deal_id} сохранена")


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
        SELECT d.id, d.cargo_id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Чат не найден")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=true
        WHERE id=$1
    """, deal_id)

    msg = "🚩 Жалоба отправлена пользователем"

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, msg)


    await audit(
        user_id,
        "dispute_opened",
        deal_id=deal_id,
        cargo_id=deal["cargo_id"],
        payload={
            "source": "command"
        }
    )

    await update.message.reply_text(f"🚩 Жалоба по сделке #{deal_id} открыт")


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
        await update.message.reply_text("❌ Чат не найден")
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
        f"💬 Чат по грузу #{deal_id}\n"
        f"Написать: /dealmsg {deal_id} текст\n"
        f"Ответить сюда: /replydeal текст\n"
        f"Жалоба: /disputereason {deal_id} причина\n\n"
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
        await update.message.reply_text("❌ Чат не найден")
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
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_history_{deal_id}")]
            ])
        )

    await update.message.reply_text(
        f"💬 Сообщение отправлено в сделку #{deal_id}"
    )




async def myresponses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    rows = await DB.fetch("""
        SELECT
            r.id,
            r.status,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency
        FROM responses r
        JOIN cargo c ON c.id = r.cargo_id
        WHERE r.driver_id=$1
        ORDER BY r.id DESC
        LIMIT 20
    """, user_id)

    if not rows:
        await update.message.reply_text("📭 Вы ещё не откликались")
        return

    for r in rows:
        await update.message.reply_text(
            f"📨 Мой отклик #{r['id']}\n"
            f"📦 Груз #{r['cargo_id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📊 Статус: {human_status(r['status'])}"
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


async def emit_response_status(response_id, status, cargo_id=None, truck_id=None, deal_id=None):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                "http://localhost:5000/api/realtime/response-status",
                json={
                    "response_id": response_id,
                    "status": status,
                    "cargo_id": cargo_id,
                    "truck_id": truck_id,
                    "deal_id": deal_id
                },
                timeout=5
            )
    except Exception as e:
        logging.warning(f"emit_response_status failed: {e}")


async def response_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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
            c.to_city,
            c.price_amount,
            c.price_currency,
            c.weight_kg,
            c.volume_m3,
            c.places_count,
            c.cargo_type,
            c.distance_km,
            c.rate_per_km
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

        deal_id = await DB.fetchval("""
            SELECT id
            FROM deals
            WHERE response_id=$1
            LIMIT 1
        """, response_id)

        actor = await DB.fetchrow("""
            SELECT id
            FROM users
            WHERE telegram_id=$1
            LIMIT 1
        """, q.from_user.id)


        await audit(
            actor["id"] if actor else None,
            "response_accepted",
            deal_id=deal_id,
            cargo_id=response["cargo_id"],
            payload={
                "response_id": response_id,
                "truck_id": response["truck_id"]
            }
        )

        if not existing_deal:
            await audit(
                actor["id"] if actor else None,
                "deal_created",
                deal_id=deal_id,
                cargo_id=response["cargo_id"],
                payload={
                    "response_id": response_id,
                    "truck_id": response["truck_id"]
                }
            )

        history_exists = await DB.fetchval("""
            SELECT COUNT(*)
            FROM deal_status_history
            WHERE deal_id=$1
        """, deal_id)

        if history_exists == 0:
            await DB.execute("""
                INSERT INTO deal_status_history (deal_id, status, created_by)
                VALUES
                    ($1, 'active', $2),
                    ($1, 'driver_assigned', $2)
            """, deal_id, actor["id"] if actor else None)

        try:
            await context.bot.send_message(
                chat_id=response["telegram_id"],
                text=(
                    f"✅ Ваш отклик принят!\n\n"
                    f"📦 Груз #{response['cargo_id']}\n"
                    f"🚩 {response['from_city']} → {response['to_city']}\n"
                    f"⚖️ {response['weight_kg'] or 0} кг\n"
                    f"📦 {response['volume_m3'] or 0} м³\n"
                    f"🔢 {response['places_count'] or 0} мест\n"
                    f"💰 {format_price(response['price_amount'])} {response['price_currency'] or 'RUB'}\n"
                    f"💵 {response['rate_per_km'] or '-'} ₽/км\n\n"
                    f"🤝 Сделка #{deal_id} создана"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🤝 Открыть сделку", callback_data="menu_deals")],
                    [InlineKeyboardButton("💬 Чат сделки", callback_data=f"deal_chat_{deal_id}")],
                    [InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{deal_id}")]
                ])
            )
        except Exception as e:
            logging.error(f"accept notify failed: {e}")

        await emit_response_status(
            response_id,
            "accepted",
            cargo_id=response["cargo_id"],
            truck_id=response["truck_id"],
            deal_id=deal_id
        )

        await q.edit_message_reply_markup(reply_markup=None)

        await q.message.reply_text(
            (
                f"✅ Отклик #{response_id} принят\n\n"
                f"📦 Груз #{response['cargo_id']}\n"
                f"🚩 {response['from_city']} → {response['to_city']}\n"
                f"⚖️ {response['weight_kg'] or 0} кг\n"
                f"📦 {response['volume_m3'] or 0} м³\n"
                f"🔢 {response['places_count'] or 0} мест\n"
                f"💰 {format_price(response['price_amount'])} {response['price_currency'] or 'RUB'}\n"
                f"💵 {response['rate_per_km'] or '-'} ₽/км\n\n"
                f"💬 Переговоры #{deal_id} создана"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤝 Открыть сделку", callback_data="menu_deals")],
                [InlineKeyboardButton("💬 Чат сделки", callback_data=f"deal_chat_{deal_id}")],
                [InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{deal_id}")]
            ])
        )
        return

    if action == "reject":
        await DB.execute("""
            UPDATE responses
            SET status='rejected'
            WHERE id=$1
        """, response_id)

        await emit_response_status(
            response_id,
            "rejected",
            cargo_id=response["cargo_id"],
            truck_id=response["truck_id"]
        )

        await q.message.reply_text(f"❌ Отклик #{response_id} отклонён")
        return

async def deals_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    forced_user = context.user_data.pop("_forced_effective_user", None)
    only_deal_id = context.user_data.pop("_deal_only_id", None)
    archive_mode = context.user_data.pop("_deals_archive_mode", False)
    user_id = await ensure_user(forced_user or update.effective_user)

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
            c.weight_kg,
            c.volume_m3,
            c.places_count,
            c.distance_km,
            c.rate_per_km,
            c.created_by AS owner_id,
            t.id AS truck_id,
            t.driver_id AS driver_id,
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
        WHERE (c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1)
          AND (
              ($2::bigint IS NOT NULL AND d.id=$2)
              OR
              ($2::bigint IS NULL AND $3::boolean=false AND d.status NOT IN ('closed', 'cancelled'))
              OR
              ($2::bigint IS NULL AND $3::boolean=true AND d.status IN ('closed', 'cancelled'))
          )
        ORDER BY d.id DESC
        LIMIT 20
    """, user_id, only_deal_id, archive_mode)

    if not rows:
        if archive_mode:
            await update.message.reply_text("📁 Архив сделок пуст")
        else:
            await update.message.reply_text("📭 Активных сделок нет")
        return

    for r in rows:
        text = (
            f"🤝 Сделка #{r['id']}\n\n"
            f"📦 Груз #{r['cargo_id']}\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"⚖️ {r['weight_kg'] or 0} кг\n"
            f"📦 {r['volume_m3'] or 0} м³\n"
            f"🔢 {r['places_count'] or 0} мест\n"
            f"💰 {format_price(r['price_amount'])} {r['price_currency'] or ''}\n"
            f"📏 {r['distance_km'] or '-'} км\n"
            f"💵 {r['rate_per_km'] or '-'} ₽/км\n\n"
            f"🚚 Машина #{r['truck_id']}: {r['current_city']}, {r['body_type']}\n"
            f"💬 Сообщений: {r['messages_count']}\n"
            + ("🚩 Жалоба отправлена\n" if r["dispute"] else "")
            + f"📊 Статус: {human_status(r['status'])}"
        )

        is_driver = user_id == r["driver_id"]
        is_owner = user_id == r["owner_id"]

        if is_driver:
            text += "\n\n🚚 Вы в этой сделке: перевозчик. Вам доступны кнопки рейса ниже."
        elif is_owner:
            text += "\n\n📦 Вы в этой сделке: заказчик. Статусы рейса меняет перевозчик; вам доступны чат и таймлайн."
        else:
            text += "\n\n👁 Вы участник сделки. Доступны чат и таймлайн."

        if is_driver:
            next_buttons = {
                "active": [InlineKeyboardButton("🚚 Еду на загрузку", callback_data=f"deal_to_pickup_{r['id']}")],
                "to_pickup": [InlineKeyboardButton("📍 На загрузке", callback_data=f"deal_loading_{r['id']}")],
                "loading": [InlineKeyboardButton("📦 Загружен", callback_data=f"deal_loaded_{r['id']}")],
                "loaded": [InlineKeyboardButton("🚛 В пути", callback_data=f"deal_in_progress_{r['id']}")],
                "in_progress": [InlineKeyboardButton("🏁 Доставлен", callback_data=f"deal_delivered_{r['id']}")],
                "breakdown": [InlineKeyboardButton("🚛 Продолжить движение", callback_data=f"deal_resume_movement_{r['id']}")],
                "resume_movement": [InlineKeyboardButton("🏁 Доставлен", callback_data=f"deal_delivered_{r['id']}")],
                "delivered": [InlineKeyboardButton("⏳ Ожидаем принятия заказчиком", callback_data="noop")],
                "done": [InlineKeyboardButton("⏳ Ожидаем принятия заказчиком", callback_data="noop")]
            }

            buttons = []

            if r["status"] in next_buttons:
                buttons.append(next_buttons[r["status"]])
            elif r["status"] == "closed":
                buttons.append([InlineKeyboardButton("✅ Рейс закрыт", callback_data="noop")])
            elif r["status"] == "cancelled":
                buttons.append([InlineKeyboardButton("❌ Сделка отменена", callback_data="noop")])
            else:
                buttons.append([InlineKeyboardButton("🚚 Еду на загрузку", callback_data=f"deal_to_pickup_{r['id']}")])

            if r["status"] not in ("closed", "cancelled", "done", "delivered", "breakdown"):
                buttons.append([InlineKeyboardButton("⚠️ Поломка", callback_data=f"deal_breakdown_{r['id']}")])

            if r["status"] not in ("closed", "cancelled", "done", "delivered"):
                buttons.append([InlineKeyboardButton("❌ Отменить", callback_data=f"deal_cancelled_{r['id']}")])

            buttons.append([
                InlineKeyboardButton("💬 Чат", callback_data=f"deal_chat_{r['id']}"),
                InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{r['id']}")
            ])
        elif is_owner:
            buttons = []

            if r["status"] in ("delivered", "done"):
                buttons.append([InlineKeyboardButton("✅ Принять доставку / закрыть рейс", callback_data=f"deal_closed_{r['id']}")])
                buttons.append([InlineKeyboardButton("⭐ Оценить перевозчика", callback_data=f"review_{r['id']}")])
            elif r["status"] == "closed":
                buttons.append([InlineKeyboardButton("✅ Рейс закрыт", callback_data="noop")])
                buttons.append([InlineKeyboardButton("⭐ Оценить перевозчика", callback_data=f"review_{r['id']}")])
            elif r["status"] == "cancelled":
                buttons.append([InlineKeyboardButton("❌ Сделка отменена", callback_data="noop")])
            else:
                buttons.append([InlineKeyboardButton("💬 Написать перевозчику", callback_data=f"deal_chat_{r['id']}")])
                buttons.append([InlineKeyboardButton("📍 Где груз / таймлайн", callback_data=f"deal_timeline_{r['id']}")])
                buttons.append([InlineKeyboardButton("ℹ️ Статусы рейса ведёт перевозчик", callback_data="noop")])

            buttons.append([
                InlineKeyboardButton("💬 Чат", callback_data=f"deal_chat_{r['id']}"),
                InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{r['id']}")
            ])
        else:
            buttons = [
                [InlineKeyboardButton("ℹ️ Статусы рейса у перевозчика", callback_data="noop")],
                [
                    InlineKeyboardButton("💬 Чат", callback_data=f"deal_chat_{r['id']}"),
                    InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{r['id']}")
                ]
            ]

        buttons.append([
            InlineKeyboardButton("👤 Профиль заказчика", callback_data=f"user_profile_{r['owner_id']}"),
            InlineKeyboardButton("👤 Профиль перевозчика", callback_data=f"user_profile_{r['driver_id']}")
        ])

        if r["status"] in ("done", "delivered", "closed") and not is_owner:
            buttons.append([
                InlineKeyboardButton("⭐ Оценить", callback_data=f"review_{r['id']}")
            ])

        kb = InlineKeyboardMarkup(buttons)

        await update.message.reply_text(text, reply_markup=kb)








async def deals_archive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_deals_archive_mode"] = True
    return await deals_list(update, context)


async def deal_closedispute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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
        await q.message.reply_text("❌ Чат не найден")
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
        VALUES ($1,$2,'✅ Жалоба закрыт')
    """, deal_id, user_id)

    await audit(
        user_id,
        "dispute_closed",
        deal_id=deal_id,
        cargo_id=deal["cargo_id"],
        payload={"source": "button"}
    )

    await q.message.reply_text(f"✅ Жалоба по сделке #{deal_id} закрыт")


async def deal_dispute_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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
        await q.message.reply_text("❌ Чат не найден")
        return

    if user_id not in [deal["created_by"], deal["driver_id"]]:
        await q.message.reply_text("⛔ Нет доступа к этой сделке")
        return

    await DB.execute("""
        UPDATE deals
        SET dispute=true
        WHERE id=$1
    """, deal_id)

    msg = "🚩 Жалоба отправлена пользователем"

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
            text=f"⚠️ Открыт жалобу по сделке #{deal_id}\nПроверить: /admindisputes"
        )

    if other_tg:
        await context.bot.send_message(
            chat_id=other_tg,
            text=f"⚠️ По сделке #{deal_id} открыт жалобу\nОткрыть: /dealchat {deal_id}"
        )

    context.user_data["awaiting_dispute_reason"] = deal_id

    await q.message.reply_text(
        f"🚩 Жалоба по сделке #{deal_id} открыт\n\n"
        f"Опишите причину жалобы одним сообщением.\n"
        f"Например: груз повреждён, оплата задержана, условия не совпали.\n\n"
        f"Отмена: /cancel"
    )






async def deal_reason_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[3])

    await q.message.reply_text(
        f"⚠️ Чтобы добавить причину жалобы по сделке #{deal_id}:\n\n"
        f"/disputereason {deal_id} ваша причина"
    )


async def deal_write_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[3])

    await q.message.reply_text(
        f"✍️ Чтобы написать в чат сделки #{deal_id}:\n\n"
        f"/dealmsg {deal_id} ваш текст"
    )




async def deal_docs_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    parts = q.data.split("_")

    mode = parts[1]
    deal_id = int(parts[2])

    doc_type = "document"

    if mode == "adddoc":
        doc_type = "document"

    if mode == "loadphoto":
        doc_type = "load_photo"

    if mode == "unloadphoto":
        doc_type = "unload_photo"

    if doc_type == "document":
        rows = await DB.fetch("""
            SELECT
                id,
                doc_type,
                file_id,
                file_name,
                created_at
            FROM deal_documents
            WHERE deal_id=$1
            ORDER BY id DESC
            LIMIT 3
        """, deal_id)

        text = f"📄 Документы сделки #{deal_id}\n\n"

        if rows:
            for d in rows:
                text += f"• {d['doc_type']} — {d['file_name'] or 'file'} — {d['created_at'].strftime('%d.%m %H:%M')}\n"
        else:
            text += "Пока документов нет.\n"

        buttons = []

        for d in rows:
            buttons.append([
                InlineKeyboardButton(
                    f"📎 {d['file_name'] or d['doc_type']} #{d['id']}",
                    callback_data=f"deal_opendoc_{d['id']}"
                )
            ])

        buttons.append([InlineKeyboardButton("➕ Добавить файл", callback_data=f"deal_adddoc_{deal_id}")])

        await q.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    context.user_data["deal_upload_id"] = deal_id
    context.user_data["deal_upload_type"] = doc_type

    if doc_type == "load_photo":
        title = "📸 Фото загрузки"
        hint = "Отправьте фото с места загрузки следующим сообщением."
    elif doc_type == "unload_photo":
        title = "📸 Фото выгрузки"
        hint = "Отправьте фото с места выгрузки следующим сообщением."
    else:
        title = "📄 Документ сделки"
        hint = "Отправьте файл или фото следующим сообщением."

    await q.message.reply_text(
        f"{title} для сделки #{deal_id}\n\n"
        f"{hint}\n\n"
        f"Отмена: /cancel"
    )


async def deal_open_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    doc_id = int(q.data.split("_")[-1])

    doc = await DB.fetchrow("""
        SELECT
            id,
            deal_id,
            doc_type,
            file_id,
            file_name
        FROM deal_documents
        WHERE id=$1
    """, doc_id)

    if not doc:
        await q.message.reply_text("❌ Документ не найден")
        return

    caption = f"📎 {doc['file_name'] or doc['doc_type']}\\nСделка #{doc['deal_id']}"

    if doc["doc_type"] in ["load_photo", "unload_photo"] or (doc["file_name"] or "").lower().endswith((".jpg", ".jpeg", ".png")):
        await context.bot.send_photo(
            chat_id=q.message.chat_id,
            photo=doc["file_id"],
            caption=caption
        )
    else:
        await context.bot.send_document(
            chat_id=q.message.chat_id,
            document=doc["file_id"],
            caption=caption
        )



async def dispute_reason_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deal_id = context.user_data.get("awaiting_dispute_reason")

    if not deal_id:
        return

    text = (update.message.text or "").strip()

    if len(text) < 3:
        await update.message.reply_text("❌ Опишите причину жалобы подробнее.")
        raise ApplicationHandlerStop

    user_id = await ensure_user(update.effective_user)

    deal = await DB.fetchrow("""
        SELECT d.id, c.created_by, r.driver_id
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN responses r ON r.id = d.response_id
        WHERE d.id=$1
    """, deal_id)

    if not deal or user_id not in [deal["created_by"], deal["driver_id"]]:
        context.user_data.pop("awaiting_dispute_reason", None)
        await update.message.reply_text("⛔ Нет доступа к этой сделке")
        raise ApplicationHandlerStop

    await DB.execute("""
        INSERT INTO deal_messages (
            deal_id,
            from_user_id,
            message_text
        )
        VALUES ($1,$2,$3)
    """, deal_id, user_id, f"⚠️ Причина жалобы: {text}")

    context.user_data.pop("awaiting_dispute_reason", None)

    await update.message.reply_text(
        f"✅ Причина жалобы сохранена по сделке #{deal_id}\n\n"
        f"Причина: {text}"
    )

    raise ApplicationHandlerStop


async def deal_document_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deal_id = context.user_data.get("deal_upload_id")

    if not deal_id:
        return

    doc_type = context.user_data.get("deal_upload_type", "document")

    user_id = await ensure_user(update.effective_user)

    file_id = None
    file_name = None

    if update.message.document:
        file_id = update.message.document.file_id
        file_name = update.message.document.file_name

    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_name = "photo.jpg"

    else:
        return

    await DB.execute("""
        INSERT INTO deal_documents (
            deal_id,
            uploaded_by,
            doc_type,
            file_id,
            file_name
        )
        VALUES ($1,$2,$3,$4,$5)
    """,
        deal_id,
        user_id,
        doc_type,
        file_id,
        file_name
    )

    context.user_data.pop("deal_upload_id", None)
    context.user_data.pop("deal_upload_type", None)

    if doc_type == "load_photo":
        saved_text = f"✅ Фото загрузки сохранено в сделке #{deal_id}"
    elif doc_type == "unload_photo":
        saved_text = f"✅ Фото выгрузки сохранено в сделке #{deal_id}"
    else:
        saved_text = f"✅ Документ сохранён в сделке #{deal_id}"

    await update.message.reply_text(saved_text)
    raise ApplicationHandlerStop


async def deal_history_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[2])
    fake_update = Update(update.update_id, message=q.message)
    context.args = [str(deal_id)]

    return await dealchat(fake_update, context)

async def deal_chat_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[2])
    context.user_data["chat_deal_id"] = deal_id

    await q.message.reply_text(
        f"💬 Чат по грузу #{deal_id}\n\n"
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
        await update.message.reply_text("❌ Чат не найден")
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


    await audit(
        user_id,
        "deal_chat_message",
        deal_id=deal_id,
        cargo_id=deal["cargo_id"],
        payload={
            "message_len": len(text)
        }
    )

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
                [InlineKeyboardButton("📖 История чата", callback_data=f"deal_history_{deal_id}")]
            ])
        )

    context.user_data.pop("chat_deal_id", None)

    await update.message.reply_text("✅ Сообщение отправлено")
    raise ApplicationHandlerStop







async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))




async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user = await DB.fetchrow(
        "SELECT id, telegram_id, full_name, role, plan_type FROM users WHERE telegram_id=$1",
        tg_id
    )

    if not user:
        await update.message.reply_text(f"Telegram ID: {tg_id}\nПользователь в БД не найден")
        return

    await update.message.reply_text(
        f"Telegram ID: {tg_id}\n"
        f"DB user_id: {user['id']}\n"
        f"Имя: {user['full_name']}\n"
        f"Роль: {user['role']}\n"
        f"Тариф: {user['plan_type']}"
    )




async def dealdebug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deal_id = int(context.args[0]) if context.args else 13

    rows = await DB.fetch("""
        SELECT
            d.id AS deal_id,
            c.created_by AS owner_user_id,
            COALESCE(r.driver_id, t.driver_id) AS driver_user_id,
            owner.telegram_id AS owner_tg,
            owner.full_name AS owner_name,
            driver.telegram_id AS driver_tg,
            driver.full_name AS driver_name,
            ts.score AS driver_trust_score
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        LEFT JOIN users owner ON owner.id = c.created_by
        LEFT JOIN users driver ON driver.id = COALESCE(r.driver_id, t.driver_id)
        WHERE d.id=$1
    """, deal_id)

    if not rows:
        await update.message.reply_text("Сделка не найдена")
        return

    r = rows[0]
    await update.message.reply_text(
        f"Сделка #{r['deal_id']}\n\n"
        f"Владелец груза:\n"
        f"user_id={r['owner_user_id']}\n"
        f"tg={r['owner_tg']}\n"
        f"name={r['owner_name']}\n\n"
        f"Водитель:\n"
        f"user_id={r['driver_user_id']}\n"
        f"tg={r['driver_tg']}\n"
        f"name={r['driver_name']}"
    )


async def pingtg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=1723796022,
        text="✅ TEST DELIVERY"
    )

    await update.message.reply_text("sent")


async def emit_deal_status(deal_id, status, status_text=None, cargo_id=None):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                "http://localhost:5000/api/realtime/deal-status",
                json={
                    "deal_id": deal_id,
                    "status": status,
                    "status_text": status_text or status,
                    "cargo_id": cargo_id
                },
                timeout=5
            )
    except Exception as e:
        logging.warning(f"emit_deal_status failed: {e}")


async def deal_report_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[-1])

    fake_update = Update(update.update_id, message=q.message)
    context.args = [str(deal_id)]

    return await dealreport(fake_update, context)


async def deal_timeline_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[-1])

    rows = await DB.fetch("""
        SELECT
            h.status,
            h.created_at,
            u.full_name
        FROM deal_status_history h
        LEFT JOIN users u ON u.id = h.created_by
        WHERE h.deal_id=$1
        ORDER BY h.created_at ASC
    """, deal_id)

    if not rows:
        await q.message.reply_text(f"📭 Таймлайн сделки #{deal_id} пока пуст")
        return

    labels = {
        "active": "🟢 Сделка создана",
        "driver_assigned": "👤 Водитель назначен",
        "to_pickup": "🚚 Еду на загрузку",
        "loading": "📍 На загрузке",
        "loaded": "📦 Загружен",
        "in_progress": "🚚 В пути",
        "breakdown": "⚠️ Поломка",
        "resume_movement": "🚛 Продолжил движение",
        "done": "✅ Доставлено",
        "delivered": "🏁 Доставлен",
        "closed": "✅ Рейс закрыт",
        "cancelled": "❌ Отменено"
    }

    text = f"📍 Таймлайн сделки #{deal_id}\\n\\n"

    for r in rows:
        label = labels.get(r["status"], r["status"])
        who = r["full_name"] or "Пользователь"
        dt = r["created_at"].strftime("%d.%m %H:%M")
        text += f"{dt} — {label}\n👤 {who}\n\n"

    await q.message.reply_text(text)


async def dealact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /dealact ID")
        return

    try:
        deal_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            d.status,
            d.created_at,
            d.updated_at,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.price_currency,
            c.distance_km,
            c.rate_per_km,
            t.id AS truck_id,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            owner.full_name AS owner_name,
            driver.full_name AS driver_name
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        JOIN users owner ON owner.id = c.created_by
        LEFT JOIN responses r ON r.id = d.response_id
        JOIN users driver ON driver.id = COALESCE(r.driver_id, t.driver_id)
        LEFT JOIN trust_scores ts ON ts.user_id = driver.id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))

    path = f"/tmp/dalnoboy_deal_{deal_id}_act.pdf"

    styles = getSampleStyleSheet()
    for st in styles.byName.values():
        st.fontName = "DejaVu"

    style = styles["Normal"]
    title = styles["Title"]

    doc = SimpleDocTemplate(path, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []

    story.append(Paragraph(f"DALNOBOY BROS — АКТ ВЫПОЛНЕННОЙ ПЕРЕВОЗКИ №{deal_id}", title))
    story.append(Spacer(1, 16))

    rows = [
        ["Параметр", "Значение"],
        ["Сделка", f"#{deal['id']}"],
        ["Груз", f"#{deal['cargo_id']}"],
        ["Маршрут", f"{deal['from_city']} → {deal['to_city']}"],
        ["Описание", deal["description"] or "-"],
        ["Цена", f"{format_price(deal['price_amount'])} {deal['price_currency'] or ''}"],
        ["Дистанция", f"{deal['distance_km'] or '-'} км"],
        ["Ставка", f"{deal['rate_per_km'] or '-'} ₽/км"],
        ["Машина", f"#{deal['truck_id']} {deal['body_type'] or ''}"],
        ["Параметры", f"{deal['capacity_tons'] or '-'} т / {deal['volume_m3'] or '-'} м³"],
        ["Заказчик", deal["owner_name"] or "-"],
        ["Перевозчик", deal["driver_name"] or "-"],
        ["Статус", "Рейс закрыт / перевозка выполнена"],
    ]

    table = Table(rows, colWidths=[150, 360])
    table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    story.append(table)

    story.append(Spacer(1, 18))
    story.append(Paragraph(
        "Стороны подтверждают, что перевозка выполнена. "
        "Претензии по факту выполнения перевозки фиксируются отдельно в переписке сделки.",
        style
    ))

    docs_count = await DB.fetchval("SELECT COUNT(*) FROM deal_documents WHERE deal_id=$1", deal_id)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Документов в архиве сделки: {docs_count}", style))

    story.append(Spacer(1, 28))
    sign_table = Table([
        ["Заказчик", "Перевозчик"],
        ["____________________", "____________________"],
        ["подпись", "подпись"],
    ], colWidths=[250, 250])
    sign_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("PADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(sign_table)

    doc.build(story)

    sent = await update.message.reply_document(
        document=open(path, "rb"),
        filename=f"deal_{deal_id}_act.pdf",
        caption=f"📄 Акт выполненной перевозки по сделке #{deal_id}"
    )

    try:
        await DB.execute("""
            INSERT INTO generated_documents (
                deal_id,
                doc_type,
                telegram_file_id
            )
            VALUES ($1,'deal_act_pdf',$2)
        """, deal_id, sent.document.file_id)
    except Exception as e:
        logging.warning(f"save generated act failed: {e}")


async def deal_act_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    deal_id = int(q.data.split("_")[-1])
    fake_update = Update(update.update_id, message=q.message)
    context.args = [str(deal_id)]

    return await dealact(fake_update, context)


async def dealpdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /dealpdf ID")
        return

    try:
        deal_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            d.status,
            d.client_price,
            d.carrier_price,
            d.dispatcher_profit,
            d.safe_deal_status,
            d.shipper_confirmed,
            d.carrier_confirmed,
            d.dispatcher_confirmed,
            d.payment_status,
            d.dispute,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.price_currency,
            c.distance_km,
            c.rate_per_km,
            t.id AS truck_id,
            t.current_city,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            t.photo_url,
            owner.full_name AS owner_name,
            driver.full_name AS driver_name,
            ts.score AS driver_trust_score
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        JOIN users owner ON owner.id = c.created_by
        LEFT JOIN responses r ON r.id = d.response_id
        JOIN users driver ON driver.id = COALESCE(r.driver_id, t.driver_id)
        LEFT JOIN trust_scores ts ON ts.user_id = driver.id
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    pdfmetrics.registerFont(TTFont("DejaVu", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))

    path = f"/tmp/dalnoboy_deal_{deal_id}.pdf"

    styles = getSampleStyleSheet()
    style = styles["Normal"]
    style.fontName = "DejaVu"
    style.fontSize = 10
    style.leading = 14

    title = styles["Title"]
    title.fontName = "DejaVu"

    doc = SimpleDocTemplate(path, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    story = []

    verify_url = f"https://dalnoboybros.ru/deal/{deal_id}"
    qr_path = f"/tmp/dalnoboy_deal_{deal_id}_qr.png"
    qrcode.make(verify_url).save(qr_path)

    story.append(Paragraph(f"DALNOBOY BROS — ПАСПОРТ БЕЗОПАСНОЙ СДЕЛКИ №{deal_id}", title))
    story.append(Spacer(1, 10))

    logo_path = "/root/dalnoboy/web/assets/logo.png"
    if not os.path.exists(logo_path):
        logo_path = "/var/www/dalnoboy/assets/logo.png"

    left_block = Table([
        [
            Image(logo_path, width=90, height=90)
        ],
        [
            Paragraph(
                "Цифровая заявка на перевозку<br/>"
                f"Проверка документа: {verify_url}",
                style
            )
        ]
    ])

    header_table = Table([
        [
            left_block,
            Image(qr_path, width=80, height=80)
        ]
    ], colWidths=[400, 100])

    header_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("ALIGN", (1,0), (1,0), "RIGHT"),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ]))

    story.append(header_table)
    story.append(Spacer(1, 14))

    main_rows = [
        ["Параметр", "Значение"],
        ["Груз", f"#{deal['cargo_id']}"],
        ["Маршрут", f"{deal['from_city']} → {deal['to_city']}"],
        ["Описание", deal["description"] or "-"],
        ["Цена", f"{format_price(deal['price_amount'])} {deal['price_currency'] or ''}"],
        ["Дистанция", f"{deal['distance_km'] or '-'} км"],
        ["Ставка", f"{deal['rate_per_km'] or '-'} ₽/км"],
        ["Статус", human_status(deal["status"])],
    ]

    main_table = Table(main_rows, colWidths=[150, 360])
    main_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    story.append(main_table)
    story.append(Spacer(1, 14))

    safe_rows = [
        ["Безопасная сделка", deal["safe_deal_status"] or "draft"],
        ["Заказчик", "Подтвержден" if deal["shipper_confirmed"] else "Нет"],
        ["Перевозчик", "Подтвержден" if deal["carrier_confirmed"] else "Нет"],
        ["Диспетчер", "Подтвержден" if deal["dispatcher_confirmed"] else "Нет"],
        ["Оплата", deal["payment_status"] or "pending"],
        ["Жалоба", "Да" if deal["dispute"] else "Нет"],
    ]

    safe_table = Table(safe_rows, colWidths=[150, 360])
    safe_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))

    story.append(Paragraph("Безопасная сделка", style))
    story.append(Spacer(1, 8))
    story.append(safe_table)
    story.append(Spacer(1, 14))

    finance_rows = [
        ["Клиент платит", format_price(deal["client_price"])],
        ["Перевозчику", format_price(deal["carrier_price"])],
        ["Доход диспетчера", format_price(deal["dispatcher_profit"])],
    ]

    finance_table = Table(finance_rows, colWidths=[150, 360])
    finance_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))

    story.append(Paragraph("Финансы сделки", style))
    story.append(Spacer(1, 8))
    story.append(finance_table)
    story.append(Spacer(1, 16))

    truck_rows = [
        ["Машина", f"#{deal['truck_id']} {deal['body_type'] or ''}"],
        ["Грузоподъёмность", f"{deal['capacity_tons'] or '-'} т / {deal['volume_m3'] or '-'} м³"],
        ["Заказчик", deal["owner_name"] or "-"],
        ["Водитель", deal["driver_name"] or "-"],
        ["Индекс доверия водителя", f"{deal['driver_trust_score'] or 50}/100"],
    ]

    truck_table = Table(truck_rows, colWidths=[150, 360])
    truck_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("PADDING", (0,0), (-1,-1), 7),
    ]))
    story.append(Paragraph("Участники и транжалобут", style))
    story.append(Spacer(1, 8))
    story.append(truck_table)
    story.append(Spacer(1, 12))

    if deal["photo_url"]:
        local_photo = "/var/www/dalnoboy" + deal["photo_url"]
        if os.path.exists(local_photo):
            story.append(Paragraph("Фото транжалобута", style))
            story.append(Spacer(1, 8))
            story.append(Image(local_photo, width=300, height=180))
            story.append(Spacer(1, 16))

    history = await DB.fetch("""
        SELECT status, created_at
        FROM deal_status_history
        WHERE deal_id=$1
        ORDER BY created_at ASC
    """, deal_id)

    story.append(Paragraph("Таймлайн рейса", style))
    story.append(Spacer(1, 8))

    if history:
        hist_rows = [["Время", "Статус"]]
        for h in history:
            hist_rows.append([h["created_at"].strftime("%d.%m.%Y %H:%M"), human_status(h["status"])])
        hist_table = Table(hist_rows, colWidths=[150, 360])
        hist_table.setStyle(TableStyle([
            ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
            ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),
            ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
            ("PADDING", (0,0), (-1,-1), 7),
        ]))
        story.append(hist_table)
    else:
        story.append(Paragraph("Событий пока нет", style))

    docs_count = await DB.fetchval("SELECT COUNT(*) FROM deal_documents WHERE deal_id=$1", deal_id)
    disputes_count = await DB.fetchval("SELECT COUNT(*) FROM disputes WHERE deal_id=$1", deal_id)

    story.append(Spacer(1, 16))
    story.append(Paragraph(f"Документы в сделке: {docs_count}", style))
    story.append(Paragraph(f"Жалобаов по сделке: {disputes_count}", style))

    story.append(Spacer(1, 28))
    sign_table = Table([
        ["Заказчик", "Перевозчик", "Диспетчер"],
        ["____________________", "____________________", "____________________"],
        ["подпись", "подпись", "подпись"],
    ], colWidths=[165, 165, 165])
    sign_table.setStyle(TableStyle([
        ("FONTNAME", (0,0), (-1,-1), "DejaVu"),
        ("ALIGN", (0,0), (-1,-1), "CENTER"),
        ("PADDING", (0,0), (-1,-1), 8),
    ]))
    story.append(sign_table)

    doc.build(story)

    sent = await update.message.reply_document(
        document=open(path, "rb"),
        filename=f"deal_{deal_id}_safe_passport.pdf",
        caption=f"📄 Пажалобут безопасной сделки #{deal_id}"
    )

    try:
        file_id = sent.document.file_id
        await DB.execute("""
            INSERT INTO generated_documents (
                deal_id,
                doc_type,
                telegram_file_id
            )
            VALUES ($1,'deal_safe_passport_pdf',$2)
        """, deal_id, file_id)
    except Exception as e:
        logging.warning(f"save generated pdf failed: {e}")


async def dealreport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /dealreport ID")
        return

    try:
        deal_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return

    deal = await DB.fetchrow("""
        SELECT
            d.id,
            d.status,
            d.created_at,
            d.updated_at,
            c.id AS cargo_id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.price_currency,
            c.distance_km,
            c.rate_per_km,
            t.id AS truck_id,
            t.current_city,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            owner.full_name AS owner_name,
            driver.full_name AS driver_name
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        JOIN users owner ON owner.id = c.created_by
        LEFT JOIN responses r ON r.id = d.response_id
        JOIN users driver ON driver.id = COALESCE(r.driver_id, t.driver_id)
        WHERE d.id=$1
    """, deal_id)

    if not deal:
        await update.message.reply_text("❌ Сделка не найдена")
        return

    docs_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM deal_documents
        WHERE deal_id=$1
    """, deal_id)

    history = await DB.fetch("""
        SELECT status, created_at
        FROM deal_status_history
        WHERE deal_id=$1
        ORDER BY created_at ASC
    """, deal_id)

    labels = {
        "active": "Активная",
        "to_pickup": "Еду на загрузку",
        "loading": "На загрузке",
        "loaded": "Загружен",
        "in_progress": "В пути",
        "breakdown": "Поломка",
        "resume_movement": "Продолжил движение",
        "done": "Доставлено",
        "delivered": "Доставлен",
        "cancelled": "Отменено"
    }

    text = (
        f"📄 Отчёт по сделке #{deal['id']}\\n\\n"
        f"📦 Груз #{deal['cargo_id']}\\n"
        f"🚩 Маршрут: {deal['from_city']} → {deal['to_city']}\\n"
        f"📝 Описание: {deal['description'] or '-'}\\n"
        f"💰 Цена: {format_price(deal['price_amount'])} {deal['price_currency'] or ''}\\n"
        f"📏 Дистанция: {deal['distance_km'] or '-'} км\\n"
        f"💵 Ставка: {deal['rate_per_km'] or '-'} ₽/км\\n\\n"
        f"🚚 Машина #{deal['truck_id']}\\n"
        f"📍 Город: {deal['current_city'] or '-'}\\n"
        f"🚛 Тип: {deal['body_type'] or '-'}\\n"
        f"⚖️ {deal['capacity_tons'] or '-'} т / {deal['volume_m3'] or '-'} м³\\n\\n"
        f"👤 Заказчик: {deal['owner_name'] or '-'}\\n"
        f"👤 Водитель: {deal['driver_name'] or '-'}\\n"
        f"📊 Статус: {labels.get(deal['status'], deal['status'])}\\n"
        f"📎 Документов: {docs_count}\\n\\n"
        f"📍 Таймлайн:\\n"
    )

    if history:
        for h in history:
            text += f"• {h['created_at'].strftime('%d.%m %H:%M')} — {labels.get(h['status'], h['status'])}\\n"
    else:
        text += "• Пока нет событий\\n"

    await update.message.reply_text(text)


async def dealtimeline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /dealtimeline ID")
        return

    try:
        deal_id = int(context.args[0])
    except:
        await update.message.reply_text("❌ Неверный ID")
        return

    rows = await DB.fetch("""
        SELECT
            h.status,
            h.created_at,
            u.full_name
        FROM deal_status_history h
        LEFT JOIN users u ON u.id = h.created_by
        WHERE h.deal_id=$1
        ORDER BY h.created_at ASC
    """, deal_id)

    if not rows:
        await update.message.reply_text(f"📭 Таймлайн сделки #{deal_id} пока пуст")
        return

    labels = {
        "active": "🟢 Сделка создана",
        "driver_assigned": "👤 Водитель назначен",
        "to_pickup": "🚚 Еду на загрузку",
        "loading": "📍 На загрузке",
        "loaded": "📦 Загружен",
        "in_progress": "🚚 В пути",
        "breakdown": "⚠️ Поломка",
        "resume_movement": "🚛 Продолжил движение",
        "done": "✅ Доставлено",
        "delivered": "🏁 Доставлен",
        "cancelled": "❌ Отменено"
    }

    text = f"📍 Таймлайн сделки #{deal_id}\\n\\n"

    for r in rows:
        label = labels.get(r["status"], r["status"])
        who = r["full_name"] or "Пользователь"
        dt = r["created_at"].strftime("%d.%m %H:%M")
        text += f"{dt} — {label}\n👤 {who}\n\n"

    await update.message.reply_text(text)


async def deal_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    raw = q.data.replace("deal_", "", 1)
    status, deal_id_raw = raw.rsplit("_", 1)
    deal_id = int(deal_id_raw)

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
        await q.message.reply_text("❌ Чат не найден")
        return

    await DB.execute("""
        UPDATE deals
        SET status=$1, updated_at=now()
        WHERE id=$2
    """, status, deal_id)

    actor = await DB.fetchrow("""
        SELECT id
        FROM users
        WHERE telegram_id=$1
        LIMIT 1
    """, q.from_user.id)

    if actor:
        await DB.execute("""
            INSERT INTO deal_status_history (
                deal_id,
                status,
                created_by
            )
            VALUES ($1,$2,$3)
        """,
            deal_id,
            status,
            actor["id"]
        )


    await audit(
        actor["id"] if actor else None,
        "deal_status_changed",
        deal_id=deal_id,
        cargo_id=deal["cargo_id"],
        payload={
            "status": status
        }
    )

    cargo_status = {
        "active": "booked",
        "to_pickup": "in_progress",
        "loading": "in_progress",
        "loaded": "in_progress",
        "in_progress": "in_progress",
        "breakdown": "in_progress",
        "resume_movement": "in_progress",
        "done": "done",
        "delivered": "done",
        "closed": "done",
        "cancelled": "open"
    }.get(status)

    if cargo_status:
        await DB.execute("""
            UPDATE cargo
            SET status=$1
            WHERE id=$2
        """, cargo_status, deal["cargo_id"])

    labels = {
        "active": "🟢 Сделка создана",
        "driver_assigned": "👤 Водитель назначен",
        "to_pickup": "🚚 Еду на загрузку",
        "loading": "📍 На загрузке",
        "loaded": "📦 Загружен",
        "in_progress": "🚚 В пути",
        "breakdown": "⚠️ Поломка",
        "resume_movement": "🚛 Продолжил движение",
        "done": "✅ Доставлено",
        "delivered": "🏁 Доставлен",
        "closed": "✅ Рейс закрыт",
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
            notify_kb = None

            # Когда перевозчик отметил доставку, грузоотправитель сразу получает кнопку принятия.
            if chat_id == deal["owner_tg"]:
                owner_buttons = []

                if status in ("delivered", "done"):
                    owner_buttons.append([
                        InlineKeyboardButton("✅ Принять доставку / закрыть рейс", callback_data=f"deal_closed_{deal_id}")
                    ])
                    owner_buttons.append([
                        InlineKeyboardButton("⭐ Оценить перевозчика", callback_data=f"review_{deal_id}")
                    ])

                owner_buttons.append([
                    InlineKeyboardButton("💬 Чат", callback_data=f"deal_chat_{deal_id}"),
                    InlineKeyboardButton("📍 Таймлайн", callback_data=f"deal_timeline_{deal_id}")
                ])

                notify_kb = InlineKeyboardMarkup(owner_buttons)

            await context.bot.send_message(
                chat_id=chat_id,
                text=notify_text,
                reply_markup=notify_kb
            )
        except Exception as e:
            logging.warning(f"Notify failed for {chat_id}: {e}")

    await emit_deal_status(
        deal_id,
        status,
        status_text=status_text,
        cargo_id=deal["cargo_id"]
    )

    await q.message.reply_text(
        f"✅ Сделка #{deal_id}: статус изменён на {status_text}\n"
        f"Ниже свежая карточка с актуальными кнопками."
    )

    context.user_data["_forced_effective_user"] = q.from_user
    context.user_data["_deal_only_id"] = deal_id
    fake_update = Update(update.update_id, message=q.message)
    return await deals_list(fake_update, context)




async def review_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

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

    if not await require_legal_for_callback(update, context):
        return

    parts = q.data.split("_")

    if len(parts) < 3:
        await q.message.reply_text("❌ Ошибка оценки")
        return

    score = int(parts[1])
    deal_id = int(parts[2])

    tg_user = q.from_user

    author = await DB.fetchrow("""
        SELECT id, banned
        FROM users
        WHERE telegram_id=$1
    """, tg_user.id)

    if not author:
        await q.message.reply_text("❌ Пользователь не найден")
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
          AND deleted_at IS NULL
    """, deal_id, from_user_id)

    if existing:
        await q.message.reply_text("⚠️ Вы уже оставили отзыв по этой сделке")
        return

    context.user_data["pending_review"] = {
        "deal_id": deal_id,
        "score": score,
        "from_user_id": from_user_id,
        "to_user_id": to_user_id,
        "review_type": review_type,
        "is_complaint": score <= 2
    }

    await q.edit_message_reply_markup(reply_markup=None)

    await q.message.reply_text(
        f"⭐ Оценка {score}⭐ выбрана\n\n"
        f"Теперь напишите текст отзыва одним сообщением. "
        f"Он будет виден другим пользователям в профиле."
    )




async def review_comment_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pending = context.user_data.get("pending_review")

    if not pending:
        return

    comment = (update.message.text or "").strip()

    if len(comment) < 3:
        await update.message.reply_text("❌ Напишите отзыв подробнее.")
        raise ApplicationHandlerStop

    deal_id = pending["deal_id"]
    score = pending["score"]
    from_user_id = pending["from_user_id"]
    to_user_id = pending["to_user_id"]
    review_type = pending["review_type"]
    is_complaint = pending.get("is_complaint", False)

    await DB.execute("""
        INSERT INTO reviews (
            deal_id,
            from_company_id,
            to_company_id,
            from_user_id,
            to_user_id,
            review_type,
            overall_score,
            comment,
            is_complaint
        )
        VALUES ($1,1,1,$2,$3,$4,$5,$6,$7)
    """,
        deal_id,
        from_user_id,
        to_user_id,
        review_type,
        score,
        comment,
        is_complaint
    )



    cargo_id = await DB.fetchval("""
        SELECT cargo_id
        FROM deals
        WHERE id=$1
    """, deal_id)

    await audit(
        from_user_id,
        "review_created",
        deal_id=deal_id,
        cargo_id=cargo_id,
        payload={
            "to_user_id": to_user_id,
            "score": score,
            "review_type": review_type,
            "is_complaint": is_complaint,
            "comment_len": len(comment)
        }
    )

    context.user_data.pop("pending_review", None)

    await update.message.reply_text(
        f"✅ Отзыв сохранён\n\n"
        f"Оценка: {score}⭐\n"
        f"Текст: {comment}"
    )

    other_tg = await DB.fetchval("""
        SELECT telegram_id
        FROM users
        WHERE id=$1
    """, to_user_id)

    if other_tg:
        try:
            await context.bot.send_message(
                chat_id=other_tg,
                text=(
                    f"⭐ Вам оставили отзыв\n\n"
                    f"Оценка: {score}⭐\n"
                    f"Комментарий: {comment}\n"
                    f"💬 Сделка #{deal_id}"
                )
            )
        except Exception as e:
            logging.warning(f"review notify failed: {e}")

    raise ApplicationHandlerStop


async def skip_cargo_geo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cargo_id = context.user_data.pop("awaiting_cargo_geo", None)

    if cargo_id:
        await update.message.reply_text(
            f"⏭ Гео загрузки для груза #{cargo_id} пропущено.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "⏭ Гео сейчас не ожидается.",
            reply_markup=ReplyKeyboardRemove()
        )

    return await menu(update, context)


async def newcargo_button_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    tg_user = q.from_user
    user_id = await ensure_user(tg_user)

    user = await DB.fetchrow("""
        SELECT
            COALESCE(role, 'carrier') AS role,
            COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"]
    plan_type = user["plan_type"]

    active_cargo = await DB.fetchval("""
        SELECT COUNT(*)
        FROM cargo
        WHERE created_by=$1
          AND status='open'
    """, user_id)

    if role in ("admin", "dispatcher") or plan_type == "company":
        limit = 999999
    else:
        limit = 1

    if active_cargo >= limit:
        await q.message.reply_text(
            "🚫 Лимит активных грузов достигнут. Сначала снимите старый груз или улучшите тариф."
        )
        return ConversationHandler.END

    context.user_data["newcargo"] = {}

    await q.message.reply_text("📍 Введите город загрузки:")
    return CARGO_FROM


async def newcargo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    user_id = await ensure_user(tg_user)

    user = await DB.fetchrow("""
        SELECT
            COALESCE(role, 'carrier') AS role,
            COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"]
    plan_type = user["plan_type"]

    active_cargo = await DB.fetchval("""
        SELECT COUNT(*)
        FROM cargo
        WHERE created_by=$1
          AND status='open'
    """, user_id)

    # Коммерческая модель v1:
    # грузоотправитель FREE = 1 активный груз
    # грузоотправитель COMPANY = до 20 активных грузов
    # dispatcher/admin/company пока без ограничений
    if role in ("admin", "dispatcher") or plan_type == "company":
        limit = 999999
    elif plan_type == "company":
        limit = 20
    else:
        limit = 1

    if active_cargo >= limit:
        if limit == 1:
            await update.message.reply_text(
                "🚫 Лимит FREE достигнут\n\n"
                "На тарифе FREE доступен только 1 активный груз.\n\n"
                "🏢 COMPANY — 990 ₽/месяц\n"
                "✓ до 20 активных грузов\n"
                "✓ VIP размещение\n"
                "✓ поднятие грузов\n"
                "✓ приоритет в выдаче\n\n"
                "Нажмите кнопку ниже, чтобы отправить заявку.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📦 Подключить COMPANY", callback_data="plan_request_company")]
                ])
            )
        else:
            await update.message.reply_text(
                f"🚫 Лимит активных грузов для тарифа {plan_type.upper()}: {limit}.\n\n"
                "Чтобы увеличить лимит, обратитесь к администратору."
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

    await update.message.reply_text(
        "⚖️ Введите вес груза в кг\n\n"
        "Например: 350\n"
        "Если вес неизвестен — введите 0"
    )

    return CARGO_WEIGHT



async def newcargo_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace(",", ".")

    try:
        weight_kg = float(raw)
    except ValueError:
        await update.message.reply_text("❌ Введите вес числом, например: 350")
        return CARGO_WEIGHT

    if weight_kg < 0:
        await update.message.reply_text("❌ Вес не может быть отрицательным")
        return CARGO_WEIGHT

    context.user_data["newcargo"]["weight_kg"] = weight_kg

    await update.message.reply_text(
        "📦 Введите объём груза в м³\n\n"
        "Например: 2.5\n"
        "Если объём неизвестен — введите 0"
    )

    return CARGO_VOLUME


async def newcargo_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "").replace(",", ".")

    try:
        volume_m3 = float(raw)
    except ValueError:
        await update.message.reply_text("❌ Введите объём числом, например: 2.5")
        return CARGO_VOLUME

    if volume_m3 < 0:
        await update.message.reply_text("❌ Объём не может быть отрицательным")
        return CARGO_VOLUME

    context.user_data["newcargo"]["volume_m3"] = volume_m3

    await update.message.reply_text(
        "🔢 Введите количество мест\n\n"
        "Например: 5\n"
        "Если неизвестно — введите 0"
    )

    return CARGO_PLACES


async def newcargo_places(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip().replace(" ", "")

    try:
        places_count = int(raw)
    except ValueError:
        await update.message.reply_text("❌ Введите количество мест числом, например: 5")
        return CARGO_PLACES

    if places_count < 0:
        await update.message.reply_text("❌ Количество мест не может быть отрицательным")
        return CARGO_PLACES

    context.user_data["newcargo"]["places_count"] = places_count

    kb = ReplyKeyboardMarkup(
        [
            ["🚛 Полная загрузка", "📦 Догруз"],
            ["📬 Посылка", "📄 Документы"]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

    await update.message.reply_text(
        "🚚 Выберите тип перевозки:",
        reply_markup=kb
    )

    return CARGO_TYPE


async def newcargo_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()

    mapping = {
        "🚛 полная загрузка": "full",
        "полная загрузка": "full",
        "полный груз": "full",
        "full": "full",

        "📦 догруз": "partial",
        "догруз": "partial",
        "partial": "partial",

        "📬 посылка": "parcel",
        "посылка": "parcel",
        "коробка": "parcel",
        "parcel": "parcel",

        "📄 документы": "mail",
        "документы": "mail",
        "почта": "mail",
        "mail": "mail",
    }

    cargo_type = mapping.get(text)

    if not cargo_type:
        await update.message.reply_text(
            "❌ Выберите тип кнопкой или напишите: полный груз, догруз, посылка, документы"
        )
        return CARGO_TYPE

    context.user_data["newcargo"]["cargo_type"] = cargo_type

    await update.message.reply_text(
        "📏 Введите расстояние маршрута в км\n\nНапример: 850",
        reply_markup=main_reply_keyboard("shipper", True, [])
    )

    return CARGO_DISTANCE


async def newcargo_distance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_distance = update.message.text.strip().replace(",", ".")

    try:
        distance_km = float(raw_distance)
    except ValueError:
        await update.message.reply_text("❌ Введите расстояние числом, например: 850")
        return CARGO_DISTANCE

    context.user_data["newcargo"]["distance_km"] = distance_km

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
            weight_kg,
            volume_m3,
            places_count,
            cargo_type,
            distance_km,
            rate_per_km,
            status
        )
        VALUES ($1,$2,$3,$4,$5,'RUB',$6,$7,$8,$9,$10,$11,'open')
        RETURNING id
    """,
        user_id,
        data["from_city"],
        data["to_city"],
        data["description"],
        data["price_amount"],
        data.get("weight_kg", 0),
        data.get("volume_m3", 0),
        data.get("places_count", 0),
        data.get("cargo_type", "full"),
        data["distance_km"],
        round(data["price_amount"] / data["distance_km"], 2) if data["distance_km"] else None
    )

    context.user_data["awaiting_cargo_geo"] = row["id"]

    await audit(
        user_id,
        "cargo_created",
        cargo_id=row["id"],
        payload={
            "from_city": data.get("from_city"),
            "to_city": data.get("to_city"),
            "price_amount": data.get("price_amount"),
            "distance_km": data.get("distance_km"),
            "cargo_type": data.get("cargo_type", "full")
        }
    )

    kb = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📍 Отправить гео загрузки", request_location=True)],
            ["⏭ Пропустить гео"],
            ["📋 Мои грузы", "🏠 Меню"]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

    type_names = {
        "full": "🚛 Полная загрузка",
        "partial": "📦 Догруз",
        "parcel": "📬 Посылка",
        "mail": "📄 Документы/почта",
        "pallet": "📦 Паллеты"
    }

    await update.message.reply_text(
        f"✅ Груз создан #{row['id']}\n"
        f"📍 {data['from_city']} → {data['to_city']}\n"
        f"📝 {data['description']}\n"
        f"💰 Цена: {data['price_amount']} RUB\n"
        f"⚖️ Вес: {data.get('weight_kg', 0)} кг\n"
        f"📦 Объём: {data.get('volume_m3', 0)} м³\n"
        f"🔢 Мест: {data.get('places_count', 0)}\n"
        f"🚚 Тип: {type_names.get(data.get('cargo_type', 'full'), data.get('cargo_type', 'full'))}\n"
        f"📏 Расстояние: {data['distance_km']} км\n"
        f"💵 Ставка: {round(data['price_amount'] / data['distance_km'], 2)} ₽/км\n\n"
        f"📌 Отправьте геолокацию места загрузки кнопкой ниже.",
        reply_markup=kb
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
                    f"🔔 Новый груз по вашей подписке\n\n"
                    f"📦 Груз #{row['id']}\n"
                    f"🚩 {data['from_city']} → {data['to_city']}\n"
                    f"📝 {data['description']}\n"
                    f"💰 {format_price(data['price_amount'])} RUB\n"
                    f"🏷 COMPANY"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{row['id']}")],
                    [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{row['id']}")],
                    [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{row['id']}")],
                    [InlineKeyboardButton("📦 Смотреть грузы", callback_data="menu_cargo")],
                    [InlineKeyboardButton("📋 Мои подписки", callback_data="menu_mysubs")]
                ])
            )
        except Exception as e:
            logging.warning(f"Subscription notify failed: {e}")

    matched_trucks = await DB.fetch("""
        SELECT DISTINCT
            t.id AS truck_id,
            t.driver_id,
            t.current_city,
            t.body_type,
            t.capacity_tons,
            t.volume_m3,
            u.telegram_id
        FROM trucks t
        JOIN users u ON u.id = t.driver_id
        WHERE t.status='active'
          AND t.driver_id <> $3
          AND (
            t.current_city ILIKE $1
            OR t.current_city ILIKE $2
            OR COALESCE(t.preferred_from, '') ILIKE $1
            OR COALESCE(t.preferred_to, '') ILIKE $2
          )
        LIMIT 20
    """, data["from_city"], data["to_city"], user_id)

    for mt in matched_trucks:
        inserted = await DB.fetchrow("""
            INSERT INTO notification_log (
                user_id,
                entity_type,
                entity_id
            )
            VALUES ($1,'truck_match_cargo',$2)
            ON CONFLICT (user_id, entity_type, entity_id) DO NOTHING
            RETURNING id
        """, mt["driver_id"], row["id"])

        if not inserted:
            continue

        try:
            await context.bot.send_message(
                chat_id=mt["telegram_id"],
                text=(
                    f"🎯 Подходящий груз для вашей машины\n\n"
                    f"📦 Груз #{row['id']}\n"
                    f"🚩 {data['from_city']} → {data['to_city']}\n"
                    f"📝 {data['description']}\n"
                    f"💰 {format_price(data['price_amount'])} RUB\n\n"
                    f"🚚 Ваша машина #{mt['truck_id']}: {mt['current_city']}, {mt['body_type']}\n"
                    f"⚖️ {mt['capacity_tons']} т | {mt['volume_m3']} м³"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{row['id']}")],
                    [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{row['id']}")],
                    [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{row['id']}")],
                    [InlineKeyboardButton("📦 Смотреть грузы", callback_data="menu_cargo")]
                ])
            )
        except Exception as e:
            logging.warning(f"Truck match notify failed: {e}")

    context.user_data.pop("newcargo", None)
    return ConversationHandler.END


async def newcargo_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("newcargo", None)
    await update.message.reply_text("❌ Создание груза отменено")
    return ConversationHandler.END











async def settings_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    fake_update = Update(update.update_id, message=q.message)

    if q.data == "settings_rate":
        context.user_data["awaiting_rate"] = True
        await q.message.reply_text("💰 Введите минимальную ставку ₽/км\n\nНапример: 35")
        return

    if q.data == "settings_geo":
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Отправить геолокацию", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await q.message.reply_text(
            "📍 Нажмите кнопку ниже, чтобы отправить текущую геолокацию",
            reply_markup=kb
        )
        return

    if q.data == "settings_radius":
        await q.message.reply_text("🛣 Введите радиус поиска в км\n\nНапример: 100")
        context.user_data["awaiting_radius"] = True
        return



async def settings_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if not await require_legal_for_callback(update, context):
        return

    if q.data == "settings_rate":
        context.user_data["awaiting_rate"] = True
        await q.message.reply_text("💰 Введите минимальную ставку ₽/км\n\nНапример: 35")
        return

    if q.data == "settings_geo":
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Отправить геолокацию", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await q.message.reply_text("📍 Нажмите кнопку ниже, чтобы отправить текущую геолокацию", reply_markup=kb)
        return

    if q.data == "settings_radius":
        context.user_data["awaiting_radius"] = True
        await q.message.reply_text("🛣 Введите радиус поиска в км\n\nНапример: 100")
        return

    if q.data == "settings_profit":
        fake_update = Update(update.update_id, message=q.message)
        return await nearby_profit(fake_update, context)

    if q.data == "settings_nearby":
        fake_update = Update(update.update_id, message=q.message)
        return await nearby(fake_update, context)

    if q.data == "settings_notify_toggle":
        user_id = await ensure_user(q.from_user)

        row = await DB.fetchrow("""
            UPDATE trucks
            SET notifications_enabled = NOT COALESCE(notifications_enabled, true)
            WHERE driver_id=$1
            RETURNING notifications_enabled
        """, user_id)

        if not row:
            await q.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
            return

        status = "включены" if row["notifications_enabled"] else "выключены"
        await q.message.reply_text(f"🔔 Уведомления {status}")
        return

    if q.data == "settings_profitable_notify_toggle":
        user_id = await ensure_user(q.from_user)

        row = await DB.fetchrow("""
            UPDATE trucks
            SET notify_profitable_only = NOT COALESCE(notify_profitable_only, true)
            WHERE driver_id=$1
            RETURNING notify_profitable_only
        """, user_id)

        if not row:
            await q.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
            return

        status = "только выгодные" if row["notify_profitable_only"] else "все рядом"
        await q.message.reply_text(f"🟢 Режим уведомлений: {status}")
        return


async def truck_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT COALESCE(role, 'carrier') AS role
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"] if user else "carrier"

    if role == "shipper":
        text = (
            "⚙️ Настройки грузоотправителя\n\n"
            "🔔 Уведомления об откликах: включены\n"
            "🤝 Уведомления по сделкам: включены\n"
            "💬 Уведомления чата: включены"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔔 Уведомления ON/OFF", callback_data="settings_shipper_notify")],
            [InlineKeyboardButton("📦 Мои грузы", callback_data="menu_mycargo")],
            [InlineKeyboardButton("🤝 Сделки", callback_data="menu_deals")]
        ])

        await update.message.reply_text(text, reply_markup=kb)
        return

    truck = await DB.fetchrow("""
        SELECT
            search_radius_km,
            min_rate_per_km,
            notifications_enabled,
            notify_profitable_only,
            location_updated_at
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if truck:
        notify_status = "включены" if truck["notifications_enabled"] else "выключены"
        notify_mode = "только выгодные" if truck["notify_profitable_only"] else "все рядом"
        radius = truck["search_radius_km"] or 50
        rate = truck["min_rate_per_km"] or "-"
        geo = truck["location_updated_at"] or "не обновлялась"
    else:
        notify_status = "не настроены"
        notify_mode = "-"
        radius = "-"
        rate = "-"
        geo = "машина не добавлена"

    text = (
        "⚙️ Настройки машины\n\n"
        f"🔔 Уведомления: {notify_status}\n"
        f"🟢 Режим: {notify_mode}\n"
        f"🛣 Радиус поиска: {radius} км\n"
        f"💰 Мин. ставка: {rate} ₽/км\n"
        f"📍 GEO: {geo}"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Ставка ₽/км", callback_data="settings_rate")],
        [InlineKeyboardButton("📍 Обновить гео", callback_data="settings_geo")],
        [InlineKeyboardButton("🛣 Радиус поиска", callback_data="settings_radius")],
        [InlineKeyboardButton("🟢 Только выгодные", callback_data="settings_profit")],
        [InlineKeyboardButton("📍 Грузы рядом", callback_data="settings_nearby")],
        [InlineKeyboardButton("🔔 Уведомления ON/OFF", callback_data="settings_notify_toggle")],
        [InlineKeyboardButton("🟢 Уведомлять только выгодные ON/OFF", callback_data="settings_profitable_notify_toggle")]
    ])

    await update.message.reply_text(text, reply_markup=kb)


async def setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not context.args:
        await update.message.reply_text("Используй: /setrate 35")
        return

    try:
        rate = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: /setrate 35")
        return

    row = await DB.fetchrow("""
        UPDATE trucks
        SET min_rate_per_km=$1
        WHERE driver_id=$2
        RETURNING id
    """, rate, user_id)

    if not row:
        await update.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
        return

    await update.message.reply_text(
        f"✅ Минимальная ставка сохранена: {rate} ₽/км"
    )










async def automatches(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:5000/api/matching/open-cargo") as resp:
                data = await resp.json()

        items = data.get("items", [])
        sent = 0
        skipped = 0

        for m in items[:50]:
            cargo_id = int(m["cargo_id"])
            truck_id = int(m["truck_id"])

            exists = await DB.fetchrow("""
                SELECT id
                FROM match_notifications
                WHERE cargo_id=$1 AND truck_id=$2
            """, cargo_id, truck_id)

            if exists:
                skipped += 1
                continue

            await update.message.reply_text(
                f"🤖 Новое совпадение\n\n"
                f"📦 Груз #{cargo_id}\n"
                f"🚩 {m['from_city']} → {m['to_city']}\n"
                f"💰 {m['price_amount']} ₽\n"
                f"💵 {round(float(m['rate_per_km']), 2) if m['rate_per_km'] else 'ставка не указана'} ₽/км\n\n"
                f"🚚 Машина #{truck_id}\n"
                f"👤 {m['full_name']}\n"
                f"📦 Свободно: {m['available_tons']} т / {m['available_volume_m3']} м³\n"
                f"🔥 Match: {m['match_score']}%"
            )

            await DB.execute("""
                INSERT INTO match_notifications (cargo_id, truck_id)
                VALUES ($1,$2)
                ON CONFLICT (cargo_id, truck_id) DO NOTHING
            """, cargo_id, truck_id)

            sent += 1

        await update.message.reply_text(
            f"✅ Автоподбор завершён\n"
            f"Отправлено новых: {sent}\n"
            f"Пропущено дублей: {skipped}"
        )

    except Exception as e:
        await update.message.reply_text(f"❌ Automatches error: {e}")


async def pushmatches(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://localhost:5000/api/matching/open-cargo") as resp:
                data = await resp.json()

        items = data.get("items", [])

        if not items:
            await update.message.reply_text("❌ Совпадений для отправки нет")
            return

        sent = 0

        for m in items[:20]:
            text = (
                f"🤖 Найдено совпадение\n\n"
                f"📦 Груз #{m['cargo_id']}\n"
                f"🚩 {m['from_city']} → {m['to_city']}\n"
                f"💰 {m['price_amount']} ₽\n"
                f"💵 {round(float(m['rate_per_km']), 2) if m['rate_per_km'] else 'ставка не указана'} ₽/км\n\n"
                f"🚚 Машина #{m['truck_id']}\n"
                f"👤 {m['full_name']}\n"
                f"📦 Свободно: {m['available_tons']} т / {m['available_volume_m3']} м³\n"
                f"🔥 Match: {m['match_score']}%"
            )

            await update.message.reply_text(text)
            sent += 1

        await update.message.reply_text(f"✅ Отправлено совпадений: {sent}")

    except Exception as e:
        await update.message.reply_text(f"❌ Push matches error: {e}")

async def push_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    context.args = ["500"]

    url = f"http://localhost:5000/api/nearby?telegram_id={update.effective_user.id}&radius=500&profitability=profitable"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()

    sent = 0

    for r in data.get("items", []):
        exists = await DB.fetchrow("""
            SELECT id
            FROM profit_notifications
            WHERE user_id=$1 AND cargo_id=$2
        """, user_id, int(r["id"]))

        if exists:
            continue

        await update.message.reply_text(
            f"🟢 Новый груз рядом\n\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} ₽\n"
            f"📍 {r['distance_km']} км до загрузки\n"
            f"💵 {round(float(r['rate_per_km']), 2) if r['rate_per_km'] else 'ставка не указана'} ₽/км",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
                [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}")],
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")]
            ])
        )

        await DB.execute("""
            INSERT INTO profit_notifications (user_id, cargo_id)
            VALUES ($1,$2)
            ON CONFLICT (user_id, cargo_id) DO NOTHING
        """, user_id, int(r["id"]))

        sent += 1

    if sent == 0:
        await update.message.reply_text("📭 Новых выгодных грузов нет")


async def check_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.args = ["500"]
    return await nearby_profit(update, context)


async def nearby_profit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT
            COALESCE(role,'carrier') AS role,
            COALESCE(plan_type,'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    role = user["role"] if user else "carrier"
    plan_type = user["plan_type"] if user else "free"

    # Выгодные грузы только для PRO
    if role not in ("admin", "dispatcher") and plan_type not in ("pro", "company"):
        await update.message.reply_text(
            "🟢 Раздел «Выгодные грузы» доступен только на тарифе PRO.\n\n"
            "Подключите PRO для поиска самых прибыльных рейсов."
        )
        return

    radius = None
    if context.args:
        try:
            radius = int(context.args[0])
        except Exception:
            pass

    truck = await DB.fetchrow("""
        SELECT search_radius_km
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if radius is None:
        radius = int(truck["search_radius_km"] or 50) if truck else 50

    logging.info(f"nearby_profit radius={radius}, user_id={user_id}, tg={update.effective_user.id}")

    url = f"http://localhost:5000/api/nearby?telegram_id={update.effective_user.id}&radius={radius}&profitability=profitable"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            data = await resp.json()

    if not data.get("items"):
        await update.message.reply_text(f"📭 Выгодных грузов рядом ({radius} км) не найдено")
        return

    for r in data["items"][:20]:
        await update.message.reply_text(
            f"🟢 Выгодный груз\n\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} ₽\n"
            f"📍 {r['distance_km']} км до загрузки\n"
            f"💵 {round(float(r['rate_per_km']), 2) if r['rate_per_km'] else 'ставка не указана'} ₽/км",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
                [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}")],
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")]
            ])
        )


async def nearby(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    user = await DB.fetchrow("""
        SELECT COALESCE(plan_type, 'free') AS plan_type
        FROM users
        WHERE id=$1
    """, user_id)

    plan_type = user["plan_type"] if user else "free"

    radius = None

    if context.args:
        try:
            radius = int(context.args[0])
        except:
            pass

    truck = await DB.fetchrow("""
        SELECT
            latitude,
            longitude,
            min_rate_per_km,
            search_radius_km,
            free_weight_kg,
            free_volume_m3,
            allow_partial_load
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if radius is None:
        radius = int(truck["search_radius_km"] or 50) if truck else 50

    max_radius = None
    if plan_type == "free":
        max_radius = 150
    elif plan_type in ["pro", "dispatcher"]:
        max_radius = 500

    if max_radius is not None and radius > max_radius:
        radius = max_radius
        await update.message.reply_text(
            f"ℹ️ Для тарифа {plan_type.upper()} максимальный радиус поиска: {max_radius} км.\n"
            f"Показываю грузы в радиусе {max_radius} км."
        )

    if not truck or not truck["latitude"] or not truck["longitude"]:
        kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📍 Отправить геолокацию", request_location=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )

        await update.message.reply_text(
            "📍 Сначала отправьте геолокацию",
            reply_markup=kb
        )
        return

    rows = await DB.fetch("""
        SELECT
            c.id,
            c.from_city,
            c.to_city,
            c.description,
            c.price_amount,
            c.distance_km,
            c.rate_per_km,
            c.weight_kg,
            c.volume_m3,
            c.places_count,
            c.cargo_type,
            c.load_latitude,
            c.load_longitude,
            COALESCE(u.plan_type, 'free') AS owner_plan
        FROM cargo c
        JOIN users u ON u.id = c.created_by
        WHERE c.status='open'
          AND c.load_latitude IS NOT NULL
          AND c.load_longitude IS NOT NULL
        ORDER BY c.id DESC
        LIMIT 30
    """)

    found = []

    for r in rows:
        dist = distance_km(
            truck["latitude"],
            truck["longitude"],
            r["load_latitude"],
            r["load_longitude"]
        )

        if dist is not None and dist <= radius:
            cargo_type = r["cargo_type"] or "full"

            if cargo_type in ("partial", "parcel", "mail", "pallet"):
                if not truck["allow_partial_load"]:
                    continue

                cargo_weight = float(r["weight_kg"] or 0)
                cargo_volume = float(r["volume_m3"] or 0)
                free_weight = float(truck["free_weight_kg"] or 0)
                free_volume = float(truck["free_volume_m3"] or 0)

                if cargo_weight > 0 and free_weight > 0 and cargo_weight > free_weight:
                    continue

                if cargo_volume > 0 and free_volume > 0 and cargo_volume > free_volume:
                    continue

            found.append((dist, r))

    plan_priority = {
        "company": 0,
        "dispatcher": 1,
        "pro": 2,
        "free": 3
    }

    found.sort(
        key=lambda x: (
            plan_priority.get((x[1]["owner_plan"] or "free"), 3),
            x[0]
        )
    )

    if not found:
        await update.message.reply_text(
            f"📭 Грузов рядом ({radius} км) не найдено"
        )
        return

    for dist, r in found[:20]:
        rate = r["rate_per_km"]
        min_rate = truck["min_rate_per_km"]

        econ = "⚪ Рентабельность: нет данных"
        if rate and min_rate:
            delta = round(float(rate) - float(min_rate), 2)
            if delta >= 0:
                econ = f"🟢 Выгодно: {rate} ₽/км (+{delta} ₽/км)"
            else:
                econ = f"🔴 Ниже минималки: {rate} ₽/км ({delta} ₽/км)"

        owner_plan = r["owner_plan"] or "free"
        owner_badge = {
            "company": "⭐ COMPANY",
            "dispatcher": "📡 DISPATCHER",
            "pro": "🔥 PRO",
            "free": "🆓 FREE"
        }.get(owner_plan, owner_plan.upper())

        await update.message.reply_text(
            f"📦 Груз рядом\n\n"
            f"🚩 {r['from_city']} → {r['to_city']}\n"
            f"💰 {format_price(r['price_amount'])} ₽\n"
            f"⚖️ {r['weight_kg'] or 0} кг | 📦 {r['volume_m3'] or 0} м³ | 🔢 {r['places_count'] or 0}\n"
            f"🚚 {cargo_type_name(r['cargo_type'])}\n"
            f"📍 {dist} км до загрузки\n"
            f"{econ}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
                [InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}")],
                [InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")]
            ])
        )


async def cargogeo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 3:
        await update.message.reply_text("Используй: /cargogeo CARGO_ID LAT LON")
        return

    try:
        cargo_id = int(context.args[0])
        lat = float(context.args[1].replace(",", "."))
        lon = float(context.args[2].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Пример: /cargogeo 1 55.7558 37.6173")
        return

    cargo = await DB.fetchrow("""
        SELECT id, created_by, from_city, to_city
        FROM cargo
        WHERE id=$1
    """, cargo_id)

    if not cargo:
        await update.message.reply_text("❌ Груз не найден")
        return

    if cargo["created_by"] != user_id:
        await update.message.reply_text("⛔ Можно обновить гео только своего груза")
        return

    await DB.execute("""
        UPDATE cargo
        SET load_latitude=$1,
            load_longitude=$2
        WHERE id=$3
    """, lat, lon, cargo_id)

    await update.message.reply_text(
        f"📍 Гео загрузки сохранено\n\n"
        f"📦 Груз #{cargo_id}\n"
        f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
        f"🌐 {round(lat, 4)}, {round(lon, 4)}"
    )


async def location_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if len(context.args) < 2:
        await update.message.reply_text("Используй: /location LAT LON")
        return

    try:
        lat = float(context.args[0].replace(",", "."))
        lon = float(context.args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Координаты должны быть числами")
        return

    truck = await DB.fetchrow("""
        SELECT id
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if not truck:
        await update.message.reply_text("🚚 Сначала добавьте машину через 🚚 Машина")
        return

    await DB.execute("""
        UPDATE trucks
        SET latitude=$1,
            longitude=$2,
            location_updated_at=now(),
            status='active'
        WHERE id=$3
    """, lat, lon, truck["id"])

    await update.message.reply_text(
        f"📍 Геолокация сохранена\n\n"
        f"🚚 Машина #{truck['id']}\n"
        f"Широта: {round(lat, 4)}\n"
        f"Долгота: {round(lon, 4)}\n\n"
        f"Сейчас покажу грузы рядом 👇"
    )

    context.args = ["50"]
    await nearby(update, context)


async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = await ensure_user(update.effective_user)

    if not update.message.location:
        return

    lat = update.message.location.latitude
    lon = update.message.location.longitude

    cargo_id = context.user_data.pop("awaiting_cargo_geo", None)

    if cargo_id:
        cargo = await DB.fetchrow("""
            SELECT
                id,
                created_by,
                from_city,
                to_city,
                price_amount,
                price_currency,
                weight_kg,
                volume_m3,
                places_count,
                distance_km,
                rate_per_km
            FROM cargo
            WHERE id=$1
        """, cargo_id)

        if not cargo or cargo["created_by"] != user_id:
            await update.message.reply_text("❌ Груз не найден или не ваш")
            return

        await DB.execute("""
            UPDATE cargo
            SET load_latitude=$1,
                load_longitude=$2
            WHERE id=$3
        """, lat, lon, cargo_id)

        auto_items = []
        sent_count = 0

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://localhost:5000/api/cargo/{cargo_id}/available-trucks",
                    timeout=8
                ) as resp:
                    auto_data = await resp.json()
                    auto_items = auto_data.get("items", []) if auto_data.get("success") else []

            for m in auto_items[:10]:
                driver_id = int(m.get("driver_id"))
                driver_tg = await DB.fetchval(
                    "SELECT telegram_id FROM users WHERE id=$1",
                    driver_id
                )

                if not driver_tg or driver_tg == update.effective_user.id:
                    continue

                await context.bot.send_message(
                    chat_id=driver_tg,
                    text=(
                        f"🔥 Новый груз рядом\n\n"
                        f"📦 Груз #{cargo_id}\n"
                        f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
                        f"⚖️ {cargo['weight_kg'] or 0} кг\n"
                        f"📦 {cargo['volume_m3'] or 0} м³\n"
                        f"🔢 {cargo['places_count'] or 0} мест\n"
                        f"💰 {format_price(cargo['price_amount'])} {cargo['price_currency'] or 'RUB'}\n"
                        f"📏 {cargo['distance_km'] or '-'} км\n"
                        f"💵 {cargo['rate_per_km'] or '-'} ₽/км\n\n"
                        f"🚚 До загрузки: {m.get('distance_km', '-')} км\n"
                        f"🔥 Совпадение: {m.get('match_score', 0)}%"
                    ),
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{cargo_id}")]
                    ])
                )

                sent_count += 1

        except Exception as e:
            logging.warning(f"auto matching after cargo geo failed: {e}")

        access = await get_user_roles(update.effective_user.id)

        await update.message.reply_text(
            f"✅ Геолокация загрузки сохранена для груза #{cargo_id}\n"
            f"Широта: {round(lat, 4)}\n"
            f"Долгота: {round(lon, 4)}\n\n"
            f"🎯 Подходящих машин найдено: {len(auto_items)}\n"
            f"🔔 Уведомлений отправлено: {sent_count}",
            reply_markup=main_reply_keyboard(
                access["primary_role"],
                access["verified"],
                access["roles"]
            )
        )
        return

    truck = await DB.fetchrow("""
        SELECT id
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if not truck:
        await update.message.reply_text(
            "🚚 Сначала добавьте машину через кнопку 🚚 Машина"
        )
        return

    await DB.execute("""
        UPDATE trucks
        SET latitude=$1,
            longitude=$2,
            location_updated_at=now(),
            status='active'
        WHERE id=$3
    """, lat, lon, truck["id"])

    await update.message.reply_text(
        f"📍 Геолокация сохранена\n\n"
        f"🚚 Машина #{truck['id']}\n"
        f"Широта: {round(lat, 4)}\n"
        f"Долгота: {round(lon, 4)}",
        reply_markup=main_reply_keyboard()
    )


async def rate_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_radius"):
        context.user_data["awaiting_radius"] = False

        text = update.message.text.strip()

        try:
            radius = int(float(text.replace(",", ".")))
        except ValueError:
            await update.message.reply_text("❌ Введите число, например: 100")
            context.user_data["awaiting_radius"] = True
            return

        user_id = await ensure_user(update.effective_user)

        radius, clipped_to = await apply_radius_limit(user_id, radius)

        row = await DB.fetchrow("""
            UPDATE trucks
            SET search_radius_km=$1
            WHERE driver_id=$2
            RETURNING id
        """, radius, user_id)

        if not row:
            await update.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
            return

        if clipped_to:
            await update.message.reply_text(
                f"ℹ️ Для вашего тарифа максимальный радиус поиска: {clipped_to} км.\n"
                f"Сохранил радиус: {radius} км."
            )
        else:
            await update.message.reply_text(f"✅ Радиус поиска сохранён: {radius} км")

        return

    if not context.user_data.get("awaiting_rate"):
        return

    text = update.message.text.strip()
    context.user_data["awaiting_rate"] = False

    try:
        rate = float(text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: 35")
        context.user_data["awaiting_rate"] = True
        return

    user_id = await ensure_user(update.effective_user)

    row = await DB.fetchrow("""
        UPDATE trucks
        SET min_rate_per_km=$1
        WHERE driver_id=$2
        RETURNING id
    """, rate, user_id)

    if not row:
        await update.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
        return

    await update.message.reply_text(f"✅ Минимальная ставка сохранена: {rate} ₽/км")


async def reply_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    logging.info(f"reply_menu_handler text={repr(text)}")

    user_id = await ensure_user(update.effective_user)

    if not await has_required_legal_consents(user_id):
        return await consent_command(update, context)

    if text == "📝 Подать заявку":
        return await access_request_start(update, context)

    if context.user_data.get("awaiting_rate"):
        context.user_data["awaiting_rate"] = False

        try:
            rate = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введите число, например: 35")
            context.user_data["awaiting_rate"] = True
            return

        user_id = await ensure_user(update.effective_user)

        row = await DB.fetchrow("""
            UPDATE trucks
            SET min_rate_per_km=$1
            WHERE driver_id=$2
            RETURNING id
        """, rate, user_id)

        if not row:
            await update.message.reply_text("🚚 Сначала добавьте машину через кнопку 🚚 Машина")
            return

        await update.message.reply_text(f"✅ Минимальная ставка сохранена: {rate} ₽/км")
        return

    if text == "📦 Грузы":
        return await cargo(update, context)

    if text == "📋 Мои грузы":
        return await mycargo(update, context)
    if text == "📍 Рядом":
        context.args = ["50"]
        return await nearby(update, context)
    if text == "🟢 Выгодные":
        context.args = ["500"]
        return await nearby_profit(update, context)
    if text == "🤝 Сделки":
        return await deals_list(update, context)
    if text == "📁 Архив сделок":
        return await deals_archive(update, context)
    if text == "⚙️ Настройки":
        return await truck_settings(update, context)
    if text == "🗺 Карта":
        from telegram import WebAppInfo

        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🗺 Открыть карту",
                    web_app=WebAppInfo(url="https://dalnoboybros.ru/map.html?v=229")
                )
            ]
        ])

        await update.message.reply_text(
            "🗺 Карта Dalnoboy",
            reply_markup=kb
        )
        return
    if text == "➕ Груз":
        return await newcargo_start(update, context)
    if text == "🚚 Машина":
        user_id = await ensure_user(update.effective_user)

        truck = await DB.fetchrow("""
            SELECT id
            FROM trucks
            WHERE driver_id=$1
            ORDER BY id DESC
            LIMIT 1
        """, user_id)

        if not truck:
            return await truck_start(update, context)

        return await mytrucks_list(update, context)
    if text == "📨 Отклики":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📨 Мои отклики", callback_data="menu_myresponses")],
            [InlineKeyboardButton("🤝 Сделки", callback_data="menu_deals")]
        ])

        await update.message.reply_text(
            "📨 Центр откликов",
            reply_markup=kb
        )
        return
    if text == "👤 Профиль":
        return await profile(update, context)
    if text == "💳 Тарифы":
        access = await get_user_roles(update.effective_user.id)

        if access["primary_role"] == "admin" or "admin" in access["roles"]:
            return await tariffs_cmd(update, context)

        return await plans(update, context)
    if text == "🏠 Меню":
        return await menu(update, context)
    if text == "🛡 Админ":
        return await admin(update, context)
    if text == "🆘 Помощь":
        return await help_cmd(update, context)


async def auto_profit_loop(app: Application):
    await asyncio.sleep(20)

    while True:
        try:
            rows = await DB.fetch("""
                SELECT DISTINCT ON (u.id)
                    u.id AS user_id,
                    u.telegram_id,
                    COALESCE(t.search_radius_km, 500) AS radius
                FROM users u
                JOIN trucks t ON t.driver_id = u.id
                WHERE
                    u.telegram_id IS NOT NULL
                    AND t.status='active'
                    AND COALESCE(t.notifications_enabled, true)=true
                    AND t.latitude IS NOT NULL
                    AND t.longitude IS NOT NULL
                    AND t.location_updated_at > now() - interval '24 hours'
                ORDER BY u.id, t.id DESC
                LIMIT 200
            """)

            total_sent = 0

            async with aiohttp.ClientSession() as session:
                for user in rows:
                    try:
                        url = (
                            f"http://localhost:5000/api/nearby"
                            f"?telegram_id={user['telegram_id']}"
                            f"&radius={int(user['radius'] or 500)}"
                            f"&profitability=profitable"
                        )

                        async with session.get(url, timeout=15) as resp:
                            data = await resp.json()

                        sent_for_user = 0

                        for r in data.get("items", [])[:5]:
                            exists = await DB.fetchrow("""
                                SELECT id
                                FROM profit_notifications
                                WHERE user_id=$1 AND cargo_id=$2
                            """, user["user_id"], int(r["id"]))

                            if exists:
                                continue

                            price_amount = float(r["price_amount"]) if r.get("price_amount") is not None else 0
                            rate_per_km = float(r["rate_per_km"]) if r.get("rate_per_km") is not None else None

                            text = (
                                f"🔔 Новый выгодный груз\n\n"
                                f"📦 Груз #{r['id']}\n"
                                f"📍 {r['from_city']} → {r['to_city']}\n\n"
                                f"💰 {format_price(price_amount)} ₽\n"
                                f"📏 {r.get('distance_km') or '-'} км до загрузки\n"
                                f"💵 {round(rate_per_km, 2) if rate_per_km else 'ставка не указана'} ₽/км"
                            )

                            await app.bot.send_message(
                                chat_id=user["telegram_id"],
                                text=text,
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🚛 Откликнуться", callback_data=f"cargo_{r['id']}")],
                                    [InlineKeyboardButton("🔔 Следить за маршрутом", callback_data=f"subroute_{r['id']}")],
                                    [
                                        InlineKeyboardButton("📤 Поделиться", callback_data=f"cargo_share_{r['id']}"),
                                        InlineKeyboardButton("🔗 Получить ссылку", callback_data=f"cargo_link_{r['id']}")
                                    ]
                                ])
                            )

                            await DB.execute("""
                                INSERT INTO profit_notifications (user_id, cargo_id)
                                VALUES ($1,$2)
                                ON CONFLICT (user_id, cargo_id) DO NOTHING
                            """, user["user_id"], int(r["id"]))

                            sent_for_user += 1
                            total_sent += 1

                            if sent_for_user >= 3:
                                break

                    except Exception as e:
                        logging.warning(f"auto_profit_loop user failed: {e}")

            logging.info(f"auto_profit_loop sent: {total_sent}")

        except Exception as e:
            logging.warning(f"auto_profit_loop failed: {e}")

        await asyncio.sleep(600)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.exception("BOT ERROR", exc_info=context.error)


async def post_init(app: Application):
    await init_db()

    updated = await DB.execute("""
        UPDATE users
        SET
            plan_type='free',
            plan_expires_at=NULL
        WHERE
            plan_type != 'free'
            AND plan_expires_at IS NOT NULL
            AND plan_expires_at < now()
    """)

    logging.info(f"Expired plans cleanup: {updated}")

    try:
        await notify_expiring_subscriptions(app)
    except Exception as e:
        logging.warning(f"notify_expiring_subscriptions failed: {e}")

    app.create_task(auto_profit_loop(app))
    logging.info("auto_profit_loop started")


async def notify_expiring_subscriptions(app: Application):
    rows = await DB.fetch("""
        SELECT
            id,
            telegram_id,
            plan_type,
            plan_expires_at,
            EXTRACT(DAY FROM (plan_expires_at - now()))::int AS days_left
        FROM users
        WHERE
            plan_type != 'free'
            AND plan_expires_at IS NOT NULL
            AND plan_expires_at > now()
            AND (
                plan_expires_at <= now() + interval '3 days'
            )
    """)

    for r in rows:
        days_left = int(r["days_left"])

        if days_left <= 0:
            notify_type = "expire_0"
        elif days_left <= 1:
            notify_type = "expire_1"
        else:
            notify_type = "expire_3"

        already = await DB.fetchval("""
            SELECT id
            FROM subscription_notifications
            WHERE user_id=$1 AND notify_type=$2
        """, r["id"], notify_type)

        if already:
            continue

        try:
            await app.bot.send_message(
                chat_id=r["telegram_id"],
                text=(
                    f"⏳ Ваш тариф {r['plan_type'].upper()} скоро закончится.\n\n"
                    f"Осталось дней: {max(days_left,0)}\n\n"
                    "После окончания тарифа будут отключены:\n"
                    "• выгодные грузы\n"
                    "• приоритет в поиске\n"
                    "• расширенный nearby\n\n"
                    "💳 Продлите тариф заранее."
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💳 Продлить тариф", callback_data="buy_plan")]
                ])
            )

            await DB.execute("""
                INSERT INTO subscription_notifications (
                    user_id,
                    notify_type
                )
                VALUES ($1,$2)
            """, r["id"], notify_type)

        except Exception as e:
            logging.warning(f"subscription notify failed: {e}")


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




async def access_request_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🚚 Я перевозчик", callback_data="access_role_carrier")],
        [InlineKeyboardButton("📦 Я грузоотправитель", callback_data="access_role_shipper")],
        [InlineKeyboardButton("📡 Я диспетчер", callback_data="access_role_dispatcher")]
    ]

    if update.callback_query:
        q = update.callback_query
        await q.answer()
        await q.message.reply_text(
            "📝 Заявка на доступ\n\nВыберите вашу роль:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            "📝 Заявка на доступ\n\nВыберите вашу роль:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def access_request_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    requested_role = q.data.replace("access_role_", "")
    user_id = await ensure_user(q.from_user)

    existing = await DB.fetchrow("""
        SELECT id, status
        FROM access_requests
        WHERE user_id=$1 AND status='pending'
        ORDER BY id DESC
        LIMIT 1
    """, user_id)

    if existing:
        await q.message.reply_text("⏳ Ваша заявка уже отправлена и ожидает проверки.")
        return

    req = await DB.fetchrow("""
        INSERT INTO access_requests (user_id, requested_role, status)
        VALUES ($1,$2,'pending')
        RETURNING id
    """, user_id, requested_role)

    role_names = {
        "carrier": "перевозчик",
        "shipper": "грузоотправитель",
        "dispatcher": "диспетчер"
    }
    role_text = role_names.get(requested_role, requested_role)

    await q.message.reply_text(
        f"✅ Заявка отправлена.\n\n"
        f"Роль: {role_text}\n"
        f"Ожидайте одобрения админа."
    )

    admins = await DB.fetch("""
        SELECT telegram_id
        FROM users
        WHERE role='admin' AND verified=true AND banned=false
    """)

    for admin in admins:
        try:
            await context.bot.send_message(
                chat_id=admin["telegram_id"],
                text=(
                    f"📝 Новая заявка #{req['id']}\n\n"
                    f"Пользователь: {q.from_user.full_name}\n"
                    f"Telegram ID: {q.from_user.id}\n"
                    f"Username: @{q.from_user.username or '-'}\n"
                    f"Запрошенная роль: {role_text}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Одобрить перевозчика", callback_data=f"access_approve_carrier_{req['id']}")],
                    [InlineKeyboardButton("✅ Одобрить грузоотправителя", callback_data=f"access_approve_shipper_{req['id']}")],
                    [InlineKeyboardButton("✅ Одобрить диспетчера", callback_data=f"access_approve_dispatcher_{req['id']}")],
                    [InlineKeyboardButton("❌ Отклонить", callback_data=f"access_reject_{req['id']}")]
                ])
            )
        except Exception as e:
            logging.warning(f"access request notify admin failed: {e}")


async def access_request_admin_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    admin_id = await ensure_user(q.from_user)

    admin = await DB.fetchrow("""
        SELECT role, verified, banned
        FROM users
        WHERE id=$1
    """, admin_id)

    if not admin or admin["role"] != "admin" or not admin["verified"] or admin["banned"]:
        await q.answer("⛔ Только админ", show_alert=True)
        return

    parts = q.data.split("_")

    if parts[1] == "reject":
        req_id = int(parts[2])

        req = await DB.fetchrow("""
            UPDATE access_requests
            SET status='rejected', reviewed_by=$2, reviewed_at=now()
            WHERE id=$1 AND status='pending'
            RETURNING user_id
        """, req_id, admin_id)

        if not req:
            await q.message.reply_text("Заявка уже обработана.")
            return

        user = await DB.fetchrow("SELECT telegram_id FROM users WHERE id=$1", req["user_id"])

        if user:
            await context.bot.send_message(
                chat_id=user["telegram_id"],
                text="❌ Ваша заявка на доступ отклонена."
            )

        await q.message.reply_text(f"❌ Заявка #{req_id} отклонена.")
        return

    role = parts[2]
    req_id = int(parts[3])

    req = await DB.fetchrow("""
        UPDATE access_requests
        SET status='approved', reviewed_by=$2, reviewed_at=now()
        WHERE id=$1 AND status='pending'
        RETURNING user_id
    """, req_id, admin_id)

    if not req:
        await q.message.reply_text("Заявка уже обработана.")
        return

    await DB.execute("""
        UPDATE users
        SET role=$2,
            verified=true,
            banned=false
        WHERE id=$1
    """, req["user_id"], role)

    await DB.execute("""
        INSERT INTO user_roles (user_id, role, verified, active, paid)
        VALUES ($1, $2, true, true, false)
        ON CONFLICT (user_id, role) DO UPDATE
        SET verified=true,
            active=true
    """, req["user_id"], role)

    user = await DB.fetchrow("SELECT telegram_id FROM users WHERE id=$1", req["user_id"])

    role_names = {
        "carrier": "перевозчик",
        "shipper": "грузоотправитель",
        "dispatcher": "диспетчер"
    }
    role_text = role_names.get(role, role)

    if user:
        await context.bot.send_message(
            chat_id=user["telegram_id"],
            text=f"✅ Ваша заявка одобрена.\nРоль: {role_text}\n\nОтправьте /start"
        )

    await q.message.reply_text(f"✅ Заявка #{req_id} одобрена. Роль: {role_text}")






async def setcommission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dispatcher_id = await ensure_user(update.effective_user)

    access = await get_user_roles(update.effective_user.id)
    if "dispatcher" not in access["roles"] and "admin" not in access["roles"]:
        await update.message.reply_text("⛔ Нужна роль диспетчера")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат:\n"
            "/setcommission CLIENT_LINK_ID PERCENT\n\n"
            "Пример:\n"
            "/setcommission 1 7"
        )
        return

    try:
        link_id = int(context.args[0])
        percent = float(context.args[1].replace(",", "."))
    except ValueError:
        await update.message.reply_text("❌ ID и процент должны быть числами")
        return

    if percent < 0 or percent > 50:
        await update.message.reply_text("❌ Комиссия должна быть от 0 до 50%")
        return

    row = await DB.fetchrow("""
        UPDATE dispatcher_clients
        SET commission_percent=$1
        WHERE id=$2
          AND dispatcher_user_id=$3
        RETURNING id, client_user_id, client_type, commission_percent
    """, percent, link_id, dispatcher_id)

    if not row:
        await update.message.reply_text("❌ Клиент не найден или не принадлежит вам")
        return

    client = await DB.fetchrow("""
        SELECT full_name, telegram_id
        FROM users
        WHERE id=$1
    """, row["client_user_id"])

    await update.message.reply_text(
        f"✅ Комиссия обновлена\n\n"
        f"Клиент: {client['full_name'] or '-'}\n"
        f"Telegram ID: {client['telegram_id']}\n"
        f"Комиссия: {row['commission_percent']}%"
    )


async def dispatcher_clients_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)

    rows = await DB.fetch("""
        SELECT
            dc.id,
            dc.client_type,
            dc.status,
            dc.commission_percent,
            u.full_name,
            u.telegram_id
        FROM dispatcher_clients dc
        JOIN users u ON u.id = dc.client_user_id
        WHERE dc.dispatcher_user_id=$1
        ORDER BY dc.created_at DESC
        LIMIT 20
    """, dispatcher_id)

    if not rows:
        await q.message.reply_text("👥 У вас пока нет клиентов.")
        return

    keyboard = []
    for r in rows:
        kind = "🚚" if r["client_type"] == "carrier" else "📦"
        name = r["full_name"] or str(r["telegram_id"])
        keyboard.append([
            InlineKeyboardButton(
                f"#{r['id']} {kind} {name} — {r['commission_percent'] or 0}%",
                callback_data=f"dispatcher_client_{r['id']}"
            )
        ])

    await q.message.reply_text(
        "👥 Ваши клиенты\n\nВыберите клиента:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )




async def dispatcher_client_card_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)
    link_id = int(q.data.split("_")[-1])

    r = await DB.fetchrow("""
        SELECT
            dc.id,
            dc.client_user_id,
            dc.client_type,
            dc.status,
            dc.commission_percent,
            u.full_name,
            u.telegram_id,
            u.telegram_username
        FROM dispatcher_clients dc
        JOIN users u ON u.id = dc.client_user_id
        WHERE dc.id=$1
          AND dc.dispatcher_user_id=$2
    """, link_id, dispatcher_id)

    if not r:
        await q.message.reply_text("❌ Клиент не найден")
        return

    client_id = r["client_user_id"]

    trucks_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM trucks
        WHERE driver_id=$1
    """, client_id)

    cargo_count = await DB.fetchval("""
        SELECT COUNT(*)
        FROM cargo
        WHERE created_by=$1
    """, client_id)

    stats = await DB.fetchrow("""
        SELECT
            COUNT(*) AS deals_total,
            COUNT(*) FILTER (WHERE d.status IN ('pending','active','in_progress')) AS deals_active,
            COUNT(*) FILTER (WHERE d.status IN ('done','completed')) AS deals_done,
            COALESCE(SUM(CASE WHEN d.status IN ('done','completed') THEN c.price_amount ELSE 0 END), 0) AS turnover_done
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses resp ON resp.id = d.response_id
        WHERE c.created_by=$1 OR t.driver_id=$1 OR resp.driver_id=$1
    """, client_id)

    commission = float(r["commission_percent"] or 0)
    turnover_done = float(stats["turnover_done"] or 0)
    potential_commission = turnover_done * commission / 100

    kind = "🚚 Перевозчик" if r["client_type"] == "carrier" else "📦 Грузоотправитель"
    username = f"@{r['telegram_username']}" if r["telegram_username"] else "-"

    text = (
        f"👤 Клиент #{r['id']}\n\n"
        f"{kind}\n"
        f"Имя: {r['full_name'] or '-'}\n"
        f"Telegram ID: {r['telegram_id']}\n"
        f"Username: {username}\n"
        f"Статус: {r['status']}\n\n"

        f"📊 CRM-сводка\n"
        f"🚚 Машин: {trucks_count}\n"
        f"📦 Грузов: {cargo_count}\n"
        f"🤝 Сделок всего: {stats['deals_total'] or 0}\n"
        f"🚚 Активных сделок: {stats['deals_active'] or 0}\n"
        f"✅ Завершено: {stats['deals_done'] or 0}\n"
        f"💰 Оборот завершённых: {format_price(turnover_done)} ₽\n"
        f"💼 Комиссия: {commission}%\n"
        f"💵 Потенц. доход: {format_price(potential_commission)} ₽"
    )

    keyboard = [
        [InlineKeyboardButton("🚚 Машины клиента", callback_data=f"dispatcher_client_trucks_{r['id']}")],
        [InlineKeyboardButton("🤝 Сделки клиента", callback_data=f"dispatcher_client_deals_{r['id']}")],
        [InlineKeyboardButton("💰 Изменить комиссию", callback_data=f"dispatcher_client_commission_{r['id']}")],
        [InlineKeyboardButton("❌ Удалить клиента", callback_data=f"dispatcher_client_remove_{r['id']}")],
        [InlineKeyboardButton("⬅️ Назад к клиентам", callback_data="dispatcher_clients")]
    ]

    await q.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))




async def dispatcher_client_trucks_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)
    link_id = int(q.data.split("_")[-1])

    link = await DB.fetchrow("""
        SELECT client_user_id
        FROM dispatcher_clients
        WHERE id=$1
          AND dispatcher_user_id=$2
          AND status='active'
    """, link_id, dispatcher_id)

    if not link:
        await q.message.reply_text("❌ Клиент не найден")
        return

    rows = await DB.fetch("""
        SELECT id, current_city, body_type, capacity_tons, volume_m3, min_rate_per_km, status
        FROM trucks
        WHERE driver_id=$1
        ORDER BY id DESC
    """, link["client_user_id"])

    if not rows:
        await q.message.reply_text("🚚 У клиента пока нет машин.")
        return

    text = "🚚 Машины клиента\n\n"
    for t in rows:
        text += (
            f"🚚 Машина #{t['id']}\n"
            f"📍 {t['current_city'] or '-'}\n"
            f"📦 {t['body_type'] or '-'}\n"
            f"⚖️ {t['capacity_tons'] or '-'} т | {t['volume_m3'] or '-'} м³\n"
            f"💰 Мин ставка: {t['min_rate_per_km'] or '-'} ₽/км\n"
            f"Статус: {t['status'] or '-'}\n\n"
        )

    await q.message.reply_text(text)




async def dispatcher_client_deals_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)
    link_id = int(q.data.split("_")[-1])

    link = await DB.fetchrow("""
        SELECT dc.client_user_id, dc.client_type, dc.commission_percent, u.full_name
        FROM dispatcher_clients dc
        JOIN users u ON u.id = dc.client_user_id
        WHERE dc.id=$1
          AND dc.dispatcher_user_id=$2
          AND dc.status='active'
    """, link_id, dispatcher_id)

    if not link:
        await q.message.reply_text("❌ Клиент не найден")
        return

    client_id = link["client_user_id"]

    stats = await DB.fetchrow("""
        SELECT
            COUNT(*) FILTER (WHERE d.status IN ('pending','active','in_progress')) AS active_count,
            COUNT(*) FILTER (WHERE d.status IN ('done','completed')) AS done_count,
            COALESCE(SUM(CASE WHEN d.status IN ('done','completed') THEN c.price_amount ELSE 0 END), 0) AS turnover
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1
    """, client_id)

    rows = await DB.fetch("""
        SELECT
            d.id,
            d.status,
            c.from_city,
            c.to_city,
            c.price_amount,
            c.price_currency,
            d.created_at
        FROM deals d
        JOIN cargo c ON c.id = d.cargo_id
        JOIN trucks t ON t.id = d.truck_id
        LEFT JOIN responses r ON r.id = d.response_id
        WHERE c.created_by=$1 OR t.driver_id=$1 OR r.driver_id=$1
        ORDER BY d.created_at DESC
        LIMIT 3
    """, client_id)

    commission = float(link["commission_percent"] or 0)
    turnover = float(stats["turnover"] or 0)
    dispatcher_income = turnover * commission / 100

    text = (
        f"🤝 Сделки клиента\n\n"
        f"👤 {link['full_name'] or '-'}\n"
        f"Активных: {stats['active_count'] or 0}\n"
        f"Завершено: {stats['done_count'] or 0}\n"
        f"Оборот завершённых: {format_price(turnover)} ₽\n"
        f"Комиссия: {commission}%\n"
        f"Доход диспетчера: {format_price(dispatcher_income)} ₽\n\n"
    )

    if not rows:
        text += "Сделок пока нет."
    else:
        text += "Последние сделки:\n\n"
        for d in rows:
            text += (
                f"#{d['id']} — {human_status(d['status'])}\n"
                f"🚩 {d['from_city']} → {d['to_city']}\n"
                f"💰 {format_price(d['price_amount'])} {d['price_currency'] or 'RUB'}\n\n"
            )

    await q.message.reply_text(text)




async def dispatcher_client_commission_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)
    link_id = int(q.data.split("_")[-1])

    row = await DB.fetchrow("""
        SELECT id
        FROM dispatcher_clients
        WHERE id=$1
          AND dispatcher_user_id=$2
          AND status='active'
    """, link_id, dispatcher_id)

    if not row:
        await q.message.reply_text("❌ Клиент не найден")
        return

    context.user_data["awaiting_dispatcher_commission_link_id"] = link_id

    await q.message.reply_text(
        "💰 Введите комиссию диспетчера в процентах\n\n"
        "Например: 7 или 10"
    )


async def dispatcher_commission_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link_id = context.user_data.get("awaiting_dispatcher_commission_link_id")
    if not link_id:
        return

    dispatcher_id = await ensure_user(update.effective_user)

    text = update.message.text.strip().replace(",", ".")

    try:
        percent = float(text)
    except ValueError:
        await update.message.reply_text("❌ Введите число, например: 7")
        raise ApplicationHandlerStop

    if percent < 0 or percent > 50:
        await update.message.reply_text("❌ Комиссия должна быть от 0 до 50%")
        raise ApplicationHandlerStop

    row = await DB.fetchrow("""
        UPDATE dispatcher_clients
        SET commission_percent=$1
        WHERE id=$2
          AND dispatcher_user_id=$3
        RETURNING id, client_user_id, commission_percent
    """, percent, link_id, dispatcher_id)

    if not row:
        await update.message.reply_text("❌ Клиент не найден")
        context.user_data.pop("awaiting_dispatcher_commission_link_id", None)
        raise ApplicationHandlerStop

    client = await DB.fetchrow("""
        SELECT full_name, telegram_id
        FROM users
        WHERE id=$1
    """, row["client_user_id"])

    context.user_data.pop("awaiting_dispatcher_commission_link_id", None)

    await update.message.reply_text(
        f"✅ Комиссия обновлена\n\n"
        f"Клиент: {client['full_name'] or '-'}\n"
        f"Telegram ID: {client['telegram_id']}\n"
        f"Комиссия: {row['commission_percent']}%"
    )

    raise ApplicationHandlerStop


async def dispatcher_add_client_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    await q.message.reply_text(
        "➕ Добавить клиента\n\n"
        "Пока добавление делаем командой:\n\n"
        "/addclient TELEGRAM_ID carrier\n"
        "или\n"
        "/addclient TELEGRAM_ID shipper\n\n"
        "Пример:\n"
        "/addclient 1723796022 carrier"
    )


async def dispatcher_commission_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    dispatcher_id = await ensure_user(q.from_user)

    rows = await DB.fetch("""
        SELECT client_type, COUNT(*) AS cnt, AVG(commission_percent) AS avg_commission
        FROM dispatcher_clients
        WHERE dispatcher_user_id=$1 AND status='active'
        GROUP BY client_type
    """, dispatcher_id)

    if not rows:
        await q.message.reply_text("💰 Комиссия пока не настроена. Клиентов нет.")
        return

    text = "💰 Комиссия диспетчера\n\n"
    for r in rows:
        kind = "🚚 Перевозчики" if r["client_type"] == "carrier" else "📦 Грузоотправители"
        text += f"{kind}: {r['cnt']} клиентов, средняя комиссия {round(float(r['avg_commission'] or 0), 2)}%\n"

    await q.message.reply_text(text)


async def dispatcher_deals_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    await q.message.reply_text(
        "🤝 Сделки клиентов\n\n"
        "Раздел подготовлен. Далее подключим сделки клиентов диспетчера."
    )


async def addclient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dispatcher_id = await ensure_user(update.effective_user)

    access = await get_user_roles(update.effective_user.id)
    if "dispatcher" not in access["roles"] and "admin" not in access["roles"]:
        await update.message.reply_text("⛔ Нужна роль диспетчера")
        return

    if len(context.args) < 2:
        await update.message.reply_text(
            "Формат:\n/addclient TELEGRAM_ID carrier\nили\n/addclient TELEGRAM_ID shipper"
        )
        return

    try:
        client_tg = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ TELEGRAM_ID должен быть числом")
        return

    client_type = context.args[1]

    if client_type not in ["carrier", "shipper"]:
        await update.message.reply_text("❌ Тип клиента: carrier или shipper")
        return

    client = await DB.fetchrow("""
        SELECT id, full_name
        FROM users
        WHERE telegram_id=$1
    """, client_tg)

    if not client:
        await update.message.reply_text("❌ Пользователь не найден. Он должен сначала нажать /start в боте.")
        return

    await DB.execute("""
        INSERT INTO dispatcher_clients (
            dispatcher_user_id,
            client_user_id,
            client_type,
            status,
            commission_percent
        )
        VALUES ($1,$2,$3,'active',0)
        ON CONFLICT (dispatcher_user_id, client_user_id, client_type) DO UPDATE
        SET status='active'
    """, dispatcher_id, client["id"], client_type)

    await update.message.reply_text(
        f"✅ Клиент добавлен\n\n"
        f"Имя: {client['full_name'] or '-'}\n"
        f"Telegram ID: {client_tg}\n"
        f"Тип: {client_type}"
    )


async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    access = await get_user_roles(update.effective_user.id)

    role = access["primary_role"]
    roles = access["roles"]
    verified = access["verified"]
    banned = access["banned"]

    if banned:
        await update.message.reply_text("⛔ Ваш аккаунт заблокирован")
        return

    user_id = await ensure_user(update.effective_user)

    if not await has_required_legal_consents(user_id):
        return await consent_command(update, context)

    if not verified:
        keyboard = [
            [InlineKeyboardButton("📝 Подать заявку", callback_data="access_request")],
            [InlineKeyboardButton("👤 Профиль", callback_data="menu_profile")],
            [InlineKeyboardButton("🛟 Поддержка", callback_data="menu_support")]
        ]
        await update.message.reply_text(
            "🔒 Доступ к карте и грузам только после одобрения админом.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        await update.message.reply_text(
            "Нижнее меню включено 👇",
            reply_markup=main_reply_keyboard(role, verified, roles)
        )
        return

    keyboard = []

    if "admin" in roles:
        keyboard += [
            [InlineKeyboardButton("🛡 Админ", callback_data="admin_panel"),
             InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")]
        ]

    if "carrier" in roles:
        keyboard += [
            [InlineKeyboardButton("📦 Грузы", callback_data="menu_cargo"),
             InlineKeyboardButton("🚚 Моя машина", callback_data="menu_truck")],
            [InlineKeyboardButton("📍 Грузы рядом", callback_data="menu_nearby"),
             InlineKeyboardButton("🟢 Выгодные", callback_data="menu_profit")]
        ]

    if "shipper" in roles:
        keyboard += [
            [InlineKeyboardButton("➕ Создать груз", callback_data="menu_newcargo"),
             InlineKeyboardButton("📋 Мои грузы", callback_data="menu_mycargo")],
            [InlineKeyboardButton("📨 Отклики", callback_data="menu_responses"),
             InlineKeyboardButton("🤝 Сделки", callback_data="menu_deals")]
        ]

    if "dispatcher" in roles:
        keyboard += [
            [InlineKeyboardButton("👥 Клиенты", callback_data="dispatcher_clients"),
             InlineKeyboardButton("➕ Добавить клиента", callback_data="dispatcher_add_client")],
            [InlineKeyboardButton("💰 Комиссия", callback_data="dispatcher_commission"),
             InlineKeyboardButton("🤝 Сделки клиентов", callback_data="dispatcher_deals")]
        ]

    keyboard += [
        [InlineKeyboardButton("👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton("🛟 Поддержка", callback_data="menu_support")],
        [InlineKeyboardButton("➕ Запросить роль", callback_data="access_request")]
    ]

    # убрать дубли
    clean = []
    seen = set()
    for row in keyboard:
        key = tuple(btn.callback_data for btn in row)
        if key not in seen:
            clean.append(row)
            seen.add(key)

    await update.message.reply_text(
        "🏠 Главное меню\n\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(clean)
    )



async def menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    user_id = await ensure_user(q.from_user)

    if not await has_required_legal_consents(user_id):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принимаю условия", callback_data="legal_consent_accept")],
            [InlineKeyboardButton("❌ Не принимаю", callback_data="legal_consent_decline")]
        ])
        await q.message.reply_text(
            "⚖️ Перед использованием сервиса нужно принять условия.\n\n"
            "Отправьте /consent или нажмите кнопку ниже.",
            reply_markup=kb
        )
        return

    fake_update = Update(update.update_id, message=q.message)

    if q.data == "menu_today":
        return await today(fake_update, context)
    if q.data == "menu_deals":
        context.user_data["_forced_effective_user"] = q.from_user
        return await deals_list(fake_update, context)
    if q.data == "menu_responses":
        return await responses_list(fake_update, context)

    if q.data == "menu_myresponses":
        return await myresponses(fake_update, context)
    if q.data == "menu_mysubs":
        return await mysubs(fake_update, context)
    if q.data == "menu_nearby":
        fake_update = Update(update.update_id, message=q.message)
        return await nearby(fake_update, context)

    if q.data == "menu_mycargo":
        fake_update = Update(update.update_id, message=q.message)
        return await mycargo(fake_update, context)

    if q.data == "menu_profit":
        q.message._effective_user = q.from_user
        context.args = []
        fake_update = Update(update.update_id, message=q.message)
        return await nearby_profit(fake_update, context)

    if q.data == "menu_plans":
        return await plans(fake_update, context)

    if q.data == "buy_plan":
        user_id = await ensure_user(update.effective_user)
        tg_user = update.effective_user

        await q.message.reply_text(
            "💳 Оплата тарифа PRO\n\n"
            "🔥 PRO — 990 ₽ / 30 дней\n\n"
            "Оплата по СБП:\n"
            "+7XXXXXXXXXX\n\n"
            "После оплаты отправьте чек администратору.\n\n"
            f"🆔 Ваш ID: {user_id}"
        )

        payment_id = await DB.fetchval("""
            INSERT INTO payments (
                company_id,
                amount_minor,
                currency_code,
                operation_type,
                description,
                user_id,
                plan_type,
                status,
                provider,
                created_by_user_id
            )
            VALUES (
                1,
                99000,
                'RUB',
                'subscription',
                'PRO 30 days',
                $1,
                'pro',
                'pending',
                'manual_sbp',
                $1
            )
            RETURNING id
        """, user_id)

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
                    "💳 Новая заявка на оплату\n\n"
                    f"Payment ID: {payment_id}\n"
                    f"User ID: {user_id}\n"
                    f"Telegram ID: {tg_user.id}\n"
                    f"Имя: {tg_user.full_name or '-'}\n"
                    f"Username: {username}\n"
                    "Тариф: PRO\n"
                    "Сумма: 990 ₽"
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
            f"⚠️ Открытых жалобуов: {disputes_open}"
        )
        return

    if q.data == "menu_profile":
        return await profile(fake_update, context)

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
        f"⚠️ Открытых жалобуов: {disputes_open}\n"
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
        "• жалобу по сделке\n"
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
        f"⏱ Uptime\n\n"
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
            "/setplan USER_ID pro 30\n"
            "/setplan USER_ID dispatcher 30\n"
            "/setplan USER_ID company 30\n\n"
            "Где 30 — количество дней. Для free срок очищается."
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

    days = None
    if len(context.args) >= 3:
        try:
            days = int(context.args[2])
        except ValueError:
            await update.message.reply_text("Количество дней должно быть числом")
            return

        if days <= 0:
            await update.message.reply_text("Количество дней должно быть больше 0")
            return

    if plan == "free":
        await DB.execute("""
            UPDATE users
            SET plan_type='free',
                plan_expires_at=NULL
            WHERE id=$1
        """, target_id)

        await DB.execute("""
            DELETE FROM subscription_notifications
            WHERE user_id=$1
        """, target_id)

        await update.message.reply_text(
            f"✅ User #{target_id} переведён на FREE"
        )
        return

    if days is None:
        days = 30

    await DB.execute("""
        UPDATE users
        SET plan_type=$1,
            plan_expires_at=now() + ($2::int * interval '1 day')
        WHERE id=$3
    """, plan, days, target_id)

    await DB.execute("""
        DELETE FROM subscription_notifications
        WHERE user_id=$1
    """, target_id)

    expires_at = await DB.fetchval("""
        SELECT plan_expires_at
        FROM users
        WHERE id=$1
    """, target_id)

    await update.message.reply_text(
        f"✅ User #{target_id} переведён на тариф {plan.upper()}\n"
        f"⏳ Срок: {days} дней\n"
        f"Дата окончания: {expires_at}"
    )

def main():
    app = Application.builder().token(TOKEN).post_init(post_init).build()

    newcargo_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newcargo", newcargo_start),
            CallbackQueryHandler(newcargo_button_start, pattern="^menu_newcargo$"),
            MessageHandler(filters.Regex("^➕ Груз$"), newcargo_start),
        ],
        states={
            CARGO_FROM: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_from)],
            CARGO_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_to)],
            CARGO_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_desc)],
            CARGO_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_price)],
            CARGO_WEIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_weight)],
            CARGO_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_volume)],
            CARGO_PLACES: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_places)],
            CARGO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_type)],
            CARGO_DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, newcargo_distance)],
        },
        fallbacks=[CommandHandler("cancel", newcargo_cancel)],
    )

    truck_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newtruck", truck_start),
        ],
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
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))

    app.add_handler(MessageHandler(filters.ALL, private_only), group=-3)
    app.add_handler(MessageHandler(filters.ALL, ban_guard), group=-2)
    app.add_handler(MessageHandler(filters.ALL, rate_limit_guard), group=-1)

    app.add_handler(MessageHandler(filters.Regex("^⏭ Пропустить гео$"), skip_cargo_geo))
    app.add_handler(CommandHandler("skipgeo", skip_cargo_geo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dispute_reason_text), group=-4)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, review_comment_text), group=-5)
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, deal_document_message), group=-1)
    app.add_handler(MessageHandler(filters.PHOTO, truck_photo_message))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Грузы|📋 Мои грузы|📍 Рядом|🧩 Догрузы|🟢 Выгодные|🗺 Карта|⚙️ Настройки|➕ Груз|🚚 Машина|📨 Отклики|👤 Профиль|💳 Тарифы|🏠 Меню|📝 Подать заявку|🤝 Сделки|📁 Архив сделок|➕ Запросить роль|🛡 Админ)$"), reply_menu_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, tariff_price_text))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, truck_edit_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, dispatcher_commission_text), group=-100)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, rate_text_handler))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("consent", consent_command))
    app.add_handler(CallbackQueryHandler(legal_consent_button, pattern="^legal_consent_"))
    app.add_handler(CallbackQueryHandler(settings_buttons, pattern="^settings_"), group=-10)
    app.add_handler(CommandHandler("cargo", cargo))
    app.add_handler(CommandHandler("find", find_cargo))
    app.add_handler(CommandHandler("routes", routes))
    app.add_handler(CommandHandler("subroutes", subroutes))
    app.add_handler(CommandHandler("findtruck", findtruck))
    app.add_handler(CommandHandler("mytruck", mytruck))
    app.add_handler(CommandHandler("mytrucks", mytrucks_list))
    app.add_handler(CommandHandler("truckinfo", truckinfo))
    app.add_handler(CommandHandler("truckcomment", truckcomment))
    app.add_handler(CommandHandler("findprice", findprice))
    app.add_handler(CommandHandler("mycargo", mycargo))
    app.add_handler(CommandHandler("deletedcargo", deletedcargo))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("topcarriers", topcarriers))
    app.add_handler(CommandHandler("topcargo", topcargo))
    app.add_handler(CommandHandler("toproutes", toproutes))
    app.add_handler(CommandHandler("topdispatchers", topdispatchers))
    app.add_handler(CommandHandler("boostcargo", boostcargo))
    app.add_handler(CommandHandler("vipcargo", vipcargo))
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
    app.add_handler(CommandHandler("audit_test", audit_test))
    app.add_handler(CommandHandler("auditcargo", auditcargo))
    app.add_handler(CommandHandler("auditdeal", auditdeal))
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
    app.add_handler(CommandHandler("deals_archive", deals_archive))
    app.add_handler(CommandHandler("dealtimeline", dealtimeline))
    app.add_handler(CommandHandler("dealreport", dealreport))
    app.add_handler(CommandHandler("dealpdf", dealpdf))
    app.add_handler(CommandHandler("dealact", dealact))
    app.add_handler(CommandHandler("matching", matching))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, deal_chat_text), group=-99)
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("pingtg", pingtg))
    app.add_handler(CommandHandler("dealdebug", dealdebug))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("truck", mytruck))
    app.add_handler(CommandHandler("settruck", settruck_quick))
    app.add_handler(CommandHandler("location", location_cmd))
    app.add_handler(CommandHandler("cargogeo", cargogeo))
    app.add_handler(CommandHandler("nearby", nearby))
    app.add_handler(CommandHandler("nearby_profit", nearby_profit))
    app.add_handler(CommandHandler("check_profit", check_profit))
    app.add_handler(CommandHandler("push_profit", push_profit))
    app.add_handler(CommandHandler("pushmatches", pushmatches))
    app.add_handler(CommandHandler("automatches", automatches))
    app.add_handler(CommandHandler("setrate", setrate))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("sub", subscribe))
    app.add_handler(CommandHandler("mysubs", mysubs))
    app.add_handler(newcargo_handler)

    app.add_handler(CallbackQueryHandler(cargo_promo_request, pattern="^cargo_promo_"))
    app.add_handler(CallbackQueryHandler(cargo_refresh, pattern="^cargo_refresh_"))
    app.add_handler(CallbackQueryHandler(cargo_clone, pattern="^cargo_clone_"))
    app.add_handler(CallbackQueryHandler(cargo_cancel, pattern="^cargo_cancel_"))
    app.add_handler(CallbackQueryHandler(cargo_open, pattern="^cargo_open_"))
    app.add_handler(CallbackQueryHandler(cargo_delete, pattern="^cargo_delete_"))
    app.add_handler(CallbackQueryHandler(report_close, pattern="^report_close_"))
    app.add_handler(CallbackQueryHandler(cargo_report, pattern="^report_"))
    app.add_handler(CallbackQueryHandler(cargo_restore, pattern="^cargo_restore_"))
    app.add_handler(CallbackQueryHandler(cargo_link, pattern="^cargo_link_"))
    app.add_handler(CallbackQueryHandler(cargo_share, pattern="^cargo_share_"))
    app.add_handler(CallbackQueryHandler(respond, pattern="^truckrespond_"))
    app.add_handler(CallbackQueryHandler(respond, pattern="^cargo_"))
    app.add_handler(CallbackQueryHandler(response_action, pattern="^(accept|reject)_"))
    app.add_handler(CallbackQueryHandler(deal_closedispute_button, pattern="^deal_closedispute_"))
    app.add_handler(CallbackQueryHandler(deal_dispute_button, pattern="^deal_dispute_"))
    app.add_handler(CallbackQueryHandler(admin_panel_button, pattern="^admin_panel"))
    app.add_handler(CallbackQueryHandler(admin_tariffs_button, pattern="^admin_tariffs$"))
    app.add_handler(CallbackQueryHandler(noop_button, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(tariff_edit_button, pattern="^tariff_edit_"))
    app.add_handler(CallbackQueryHandler(admin_setplan_button, pattern="^admin_setplan_"))
    app.add_handler(CallbackQueryHandler(admin_dispute_buttons, pattern="^admin_(dealchat|notes|close_dispute)_"))
    app.add_handler(CallbackQueryHandler(deal_reason_help, pattern="^deal_reason_help_"))
    app.add_handler(CallbackQueryHandler(deal_write_help, pattern="^deal_write_help_"))
    app.add_handler(CallbackQueryHandler(deal_history_button, pattern="^deal_history_"))
    app.add_handler(CallbackQueryHandler(deal_timeline_button, pattern="^deal_timeline_"))
    app.add_handler(CallbackQueryHandler(deal_report_button, pattern="^deal_report_"))
    app.add_handler(CallbackQueryHandler(deal_act_button, pattern="^deal_act_"))
    app.add_handler(CallbackQueryHandler(deal_docs_button, pattern="^deal_(docs|adddoc|loadphoto|unloadphoto)_"))
    app.add_handler(CallbackQueryHandler(deal_open_document, pattern="^deal_opendoc_"))
    app.add_handler(CallbackQueryHandler(deal_chat_button, pattern="^deal_chat_"))
    app.add_handler(CallbackQueryHandler(deal_action, pattern="^deal_"))
    app.add_handler(CallbackQueryHandler(user_profile_button, pattern="^user_profile_"))
    app.add_handler(CallbackQueryHandler(profile_reviews, pattern="^profile_reviews_"))
    app.add_handler(CallbackQueryHandler(review_action, pattern="^review_"))
    app.add_handler(CallbackQueryHandler(subroute, pattern="^subroute_"))
    app.add_handler(CallbackQueryHandler(sub_delete, pattern="^sub_delete_"))
    app.add_handler(CallbackQueryHandler(sub_on, pattern="^sub_on_"))
    app.add_handler(CallbackQueryHandler(sub_off, pattern="^sub_off_"))
    app.add_handler(CallbackQueryHandler(driver_profile_button, pattern="^driver_profile_"))
    app.add_handler(CallbackQueryHandler(truck_open_button, pattern="^truck_open_"))
    app.add_handler(CallbackQueryHandler(truck_edit_button, pattern="^truck_edit_"))
    app.add_handler(CallbackQueryHandler(truck_add_button, pattern="^truck_add$"))
    app.add_handler(CallbackQueryHandler(truck_geo_button, pattern="^truck_geo$"))
    app.add_handler(CallbackQueryHandler(truck_refresh, pattern="^truck_refresh_"))
    app.add_handler(CallbackQueryHandler(truck_photo_button, pattern="^truck_photo$"))
    app.add_handler(CallbackQueryHandler(truck_partial_toggle, pattern="^truck_partial_"))
    app.add_handler(CallbackQueryHandler(truck_hide, pattern="^truck_hide_"))
    app.add_handler(CallbackQueryHandler(truck_deal_button, pattern="^truck_deal_"))
    app.add_handler(CallbackQueryHandler(create_deal_button, pattern="^create_deal_"))
    app.add_handler(CallbackQueryHandler(rate_action, pattern="^rate_"))

    app.add_handler(CommandHandler("menu", menu))
    app.add_handler(CallbackQueryHandler(plan_request, pattern="^plan_request_"))
    app.add_handler(CallbackQueryHandler(plan_admin_action, pattern="^plan_(activate|reject)_"))
    app.add_handler(CallbackQueryHandler(menu_button, pattern="^(menu_|buy_plan)"))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("monetization", monetization))
    app.add_handler(CommandHandler("setplan", setplan))
    app.add_handler(CommandHandler("setrole", setrole))
    app.add_handler(CommandHandler("tariffs", tariffs_cmd))
    app.add_handler(CommandHandler("setprice", setprice))

    app.add_handler(CommandHandler("docs", docs))

    app.add_error_handler(error_handler)

    print("🚛 BOT RUNNING")
    app.add_handler(CallbackQueryHandler(access_request_start, pattern="^access_request$"))
    app.add_handler(CallbackQueryHandler(access_request_role, pattern="^access_role_"))
    app.add_handler(CallbackQueryHandler(access_request_admin_action, pattern="^access_(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(dispatcher_clients_button, pattern="^dispatcher_clients$"))
    app.add_handler(CallbackQueryHandler(dispatcher_add_client_button, pattern="^dispatcher_add_client$"))
    app.add_handler(CallbackQueryHandler(dispatcher_commission_button, pattern="^dispatcher_commission$"))
    app.add_handler(CallbackQueryHandler(dispatcher_deals_button, pattern="^dispatcher_deals$"))
    app.add_handler(CallbackQueryHandler(dispatcher_client_card_button, pattern="^dispatcher_client_\\d+$"))
    app.add_handler(CallbackQueryHandler(dispatcher_client_trucks_button, pattern="^dispatcher_client_trucks_\\d+$"))
    app.add_handler(CallbackQueryHandler(dispatcher_client_deals_button, pattern="^dispatcher_client_deals_\\d+$"))
    app.add_handler(CallbackQueryHandler(dispatcher_client_commission_button, pattern="^dispatcher_client_commission_\\d+$"))
    app.add_handler(CommandHandler("addclient", addclient))
    app.add_handler(CommandHandler("setcommission", setcommission))
    app.run_polling()

if __name__ == "__main__":
    main()
