import asyncio
import asyncpg
import aiohttp
from math import radians, sin, cos, sqrt, atan2

from config import TOKEN, DB_DSN

BOT_TOKEN = TOKEN


def distance_km(lat1, lon1, lat2, lon2):
    r = 6371

    dlat = radians(float(lat2) - float(lat1))
    dlon = radians(float(lon2) - float(lon1))

    a = (
        sin(dlat / 2) ** 2
        + cos(radians(float(lat1)))
        * cos(radians(float(lat2)))
        * sin(dlon / 2) ** 2
    )

    return round(r * 2 * atan2(sqrt(a), sqrt(1 - a)), 1)


async def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    async with aiohttp.ClientSession() as session:
        await session.post(url, json={
            "chat_id": chat_id,
            "text": text
        })


async def main():
    db = await asyncpg.connect(DB_DSN)

    while True:
        cargos = await db.fetch("""
            SELECT *
            FROM cargo
            WHERE status='open'
              AND load_latitude IS NOT NULL
              AND load_longitude IS NOT NULL
            ORDER BY id DESC
            LIMIT 30
        """)

        trucks = await db.fetch("""
            SELECT
                t.*,
                u.telegram_id
            FROM trucks t
            JOIN users u ON u.id=t.driver_id
            WHERE t.status='active'
              AND t.notifications_enabled=true
              AND t.latitude IS NOT NULL
              AND t.longitude IS NOT NULL
        """)

        for cargo in cargos:
            for truck in trucks:

                dist = distance_km(
                    truck["latitude"],
                    truck["longitude"],
                    cargo["load_latitude"],
                    cargo["load_longitude"]
                )

                radius = float(truck["search_radius_km"] or 50)

                if dist > radius:
                    continue

                rate = cargo["rate_per_km"]
                min_rate = truck["min_rate_per_km"]

                profitable_only = truck["notify_profitable_only"]

                if profitable_only:
                    if rate and min_rate and float(rate) < float(min_rate):
                        continue

                exists = await db.fetchrow("""
                    SELECT id
                    FROM cargo_notifications
                    WHERE cargo_id=$1
                      AND truck_id=$2
                """, cargo["id"], truck["id"])

                if exists:
                    continue

                await db.execute("""
                    INSERT INTO cargo_notifications(cargo_id, truck_id)
                    VALUES($1,$2)
                """, cargo["id"], truck["id"])

                text = (
                    f"🟢 Новый выгодный груз\n\n"
                    f"🚩 {cargo['from_city']} → {cargo['to_city']}\n"
                    f"💰 {cargo['price_amount']} RUB\n"
                    f"📍 {dist} км до загрузки\n"
                    f"💵 {cargo['rate_per_km']} ₽/км"
                )

                await send_message(truck["telegram_id"], text)

                print("NOTIFIED", cargo["id"], truck["id"])

        await asyncio.sleep(30)


asyncio.run(main())
