import asyncio
import logging
import aiosqlite
import os
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp import web

# ================= LOAD ENV =================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS").split(",")))
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN")  # example: https://yourdomain.ru

WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"

DB_NAME = "enterprise_vip_salon.db"

WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 8000))

logging.basicConfig(level=logging.INFO)

# ================= STATES =================

class AdminStates(StatesGroup):
    waiting_portfolio = State()
    waiting_review = State()
    waiting_slot = State()

# ================= DATABASE =================

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            text TEXT
        );

        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            booked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            slot_id INTEGER,
            reminder_24 INTEGER DEFAULT 0,
            reminder_1 INTEGER DEFAULT 0
        );
        """)
        await db.commit()

# ================= KEYBOARDS =================

def main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("💎 Прайс", callback_data="price")],
        [InlineKeyboardButton("📸 Портфолио", callback_data="portfolio_0")],
        [InlineKeyboardButton("📝 Отзывы", callback_data="reviews_0")],
        [InlineKeyboardButton("📅 Онлайн-запись", callback_data="booking")],
        [InlineKeyboardButton("❌ Отменить запись", callback_data="cancel_my")],
        [InlineKeyboardButton("👑 Админка", callback_data="admin")]
    ])

def back_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("⬅ В меню", callback_data="menu")]
    ])

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("➕ Добавить работу", callback_data="add_portfolio")],
        [InlineKeyboardButton("➕ Добавить отзыв", callback_data="add_review")],
        [InlineKeyboardButton("➕ Создать слот", callback_data="add_slot")],
        [InlineKeyboardButton("⬅ Назад", callback_data="menu")]
    ])

# ================= USER =================

async def start(message: Message):
    await message.answer("✨ <b>VIP Студия красоты</b> 💅", reply_markup=main_menu())

async def menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню 👇", reply_markup=main_menu())

async def price(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>💎 ПРАЙС</b>\n\nМаникюр — 1500₽\nПокрытие — 2000₽\nНаращивание — 3000₽",
        reply_markup=back_menu()
    )

# ================= BOOKING =================

async def booking(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall(
            "SELECT id, date, time FROM slots WHERE booked=0"
        )

    if not rows:
        await callback.message.answer("Свободных окон нет 💔")
        return

    keyboard = [[InlineKeyboardButton(
        f"{r[1]} {r[2]}",
        callback_data=f"book_{r[0]}"
    )] for r in rows]

    await callback.message.answer(
        "Выберите удобное время ✨",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
    )

async def book_slot(callback: CallbackQuery):
    slot_id = int(callback.data.split("_")[1])

    async with aiosqlite.connect(DB_NAME) as db:
        slot = await db.execute_fetchone(
            "SELECT booked, date, time FROM slots WHERE id=?",
            (slot_id,)
        )

        if not slot or slot[0] == 1:
            await callback.message.answer("Время уже занято ❌")
            return

        await db.execute("UPDATE slots SET booked=1 WHERE id=?", (slot_id,))
        await db.execute(
            "INSERT INTO bookings (user_id, username, slot_id) VALUES (?, ?, ?)",
            (callback.from_user.id, callback.from_user.username, slot_id)
        )
        await db.commit()

    await callback.message.answer("Вы записаны 💖")

# ================= REMINDER LOOP =================

async def reminder_loop(bot: Bot):
    while True:
        now = datetime.now()
        async with aiosqlite.connect(DB_NAME) as db:
            rows = await db.execute_fetchall("""
            SELECT b.id, b.user_id, s.date, s.time, b.reminder_24, b.reminder_1
            FROM bookings b
            JOIN slots s ON b.slot_id = s.id
            """)

            for booking_id, user_id, date, time, r24, r1 in rows:
                dt = datetime.strptime(f"{date} {time}", "%d.%m.%Y %H:%M")
                diff = (dt - now).total_seconds()

                if 0 < diff <= 86400 and r24 == 0:
                    await bot.send_message(user_id, f"💖 Завтра в {time} у вас запись!")
                    await db.execute("UPDATE bookings SET reminder_24=1 WHERE id=?", (booking_id,))

                if 0 < diff <= 3600 and r1 == 0:
                    await bot.send_message(user_id, f"💌 Через час встречаемся! {date} {time}")
                    await db.execute("UPDATE bookings SET reminder_1=1 WHERE id=?", (booking_id,))

            await db.commit()
        await asyncio.sleep(60)

# ================= MAIN =================

async def main():
    await init_db()

    bot = Bot(token=BOT_TOKEN,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # handlers
    dp.message.register(start, Command("start"))
    dp.callback_query.register(menu, F.data == "menu")
    dp.callback_query.register(price, F.data == "price")
    dp.callback_query.register(booking, F.data == "booking")
    dp.callback_query.register(book_slot, F.data.startswith("book_"))

    await bot.delete_webhook(drop_pending_updates=True)

    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()

    await bot.set_webhook(WEBHOOK_URL)

    asyncio.create_task(reminder_loop(bot))

    logging.info("🚀 Бот работает через Webhook на VPS")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
