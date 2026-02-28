import os
import asyncio
import logging
import aiosqlite
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))  # "1969719151,1145747390"
DB_NAME = "enterprise_vip_salon.db"

WEBHOOK_HOST = os.getenv("RAILWAY_STATIC_URL")  # https://tg-bot.railway.internal
WEBAPP_HOST = "0.0.0.0"
WEBAPP_PORT = int(os.getenv("PORT", 3000))

if not BOT_TOKEN or not ADMIN_IDS or not WEBHOOK_HOST:
    raise ValueError("❌ Необходимо задать BOT_TOKEN, ADMIN_IDS и RAILWAY_STATIC_URL")

WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ================= STATES =================
class AdminStates(StatesGroup):
    waiting_portfolio = State()
    waiting_review = State()
    waiting_slot = State()

# ================= DATABASE =================
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS portfolio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT,
            text TEXT
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            time TEXT,
            booked INTEGER DEFAULT 0
        )""")
        await db.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            slot_id INTEGER,
            reminder_24 INTEGER DEFAULT 0,
            reminder_1 INTEGER DEFAULT 0
        )""")
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
        [InlineKeyboardButton("📋 Все записи", callback_data="all_bookings")],
        [InlineKeyboardButton("⬅ Назад", callback_data="menu")]
    ])

# ================= HANDLERS =================
# ================= USER HANDLERS =================
async def start(message: Message):
    await message.answer("✨ <b>VIP Студия красоты</b>\nВыберите раздел:", reply_markup=main_menu())

async def menu(callback: CallbackQuery):
    await callback.message.edit_text("Главное меню 👇", reply_markup=main_menu())

async def price(callback: CallbackQuery):
    await callback.message.edit_text(
        "<b>💎 VIP ПРАЙС</b>\nМаникюр — 450₽\nМаникюр+покрытие — 850₽\nНаращивание — 1150₽\nДизайн — от 20₽",
        reply_markup=main_menu()
    )

# ================= PAGINATION =================
async def show_portfolio(callback: CallbackQuery):
    page = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall("SELECT id,file_id FROM portfolio")
    if not rows:
        await callback.message.answer("Портфолио пустое 😔")
        return
    total = len(rows)
    photo_id = rows[page][1]
    nav = []
    if page>0: nav.append(InlineKeyboardButton("⬅", callback_data=f"portfolio_{page-1}"))
    if page<total-1: nav.append(InlineKeyboardButton("➡", callback_data=f"portfolio_{page+1}"))
    keyboard = [nav, [InlineKeyboardButton("⬅ В меню", callback_data="menu")]]
    await callback.message.answer_photo(photo_id, caption=f"✨ Работа {page+1}/{total}", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_reviews(callback: CallbackQuery):
    page = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall("SELECT id,file_id,text FROM reviews")
    if not rows:
        await callback.message.answer("Отзывов пока нет 😔")
        return
    total = len(rows)
    r = rows[page]
    nav=[]
    if page>0: nav.append(InlineKeyboardButton("⬅", callback_data=f"reviews_{page-1}"))
    if page<total-1: nav.append(InlineKeyboardButton("➡", callback_data=f"reviews_{page+1}"))
    keyboard = [nav,[InlineKeyboardButton("⬅ В меню", callback_data="menu")]]
    await callback.message.answer_photo(r[1], caption=f"<b>Отзыв {page+1}/{total}</b>\n\n{r[2]}", reply_markup=InlineKeyboardMarkup(keyboard))

# ================= BOOKING =================
async def booking(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        rows = await db.execute_fetchall("SELECT id,date,time FROM slots WHERE booked=0 ORDER BY date,time")
    if not rows:
        await callback.message.answer("Свободных окон нет 💔")
        return
    keyboard = [[InlineKeyboardButton(f"{d} {t}", callback_data=f"book_{id_}")] for id_, d, t in rows]
    await callback.message.answer("Выберите время ✨", reply_markup=InlineKeyboardMarkup(keyboard))

async def book_slot(callback: CallbackQuery):
    slot_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        slot = await db.execute_fetchone("SELECT booked,date,time FROM slots WHERE id=?", (slot_id,))
        if not slot or slot[0]==1:
            await callback.message.answer("❌ Время занято")
            return
        await db.execute("UPDATE slots SET booked=1 WHERE id=?", (slot_id,))
        await db.execute("INSERT INTO bookings(user_id,username,slot_id) VALUES(?,?,?)",
                         (callback.from_user.id, callback.from_user.username, slot_id))
        await db.commit()
    await callback.message.answer("✅ Вы записаны 💖")
    for admin in ADMIN_IDS:
        await callback.bot.send_message(admin, f"Новая запись: @{callback.from_user.username} {slot[1]} {slot[2]}")

async def cancel_my(callback: CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        booking = await db.execute_fetchone("SELECT slot_id FROM bookings WHERE user_id=?", (callback.from_user.id,))
        if not booking:
            await callback.message.answer("У вас нет активных записей")
            return
        slot_id = booking[0]
        await db.execute("DELETE FROM bookings WHERE user_id=?", (callback.from_user.id,))
        await db.execute("UPDATE slots SET booked=0 WHERE id=?", (slot_id,))
        await db.commit()
    await callback.message.answer("Запись отменена ❌")

# ================= REMINDERS =================
async def reminder_loop(bot: Bot):
    while True:
        now = datetime.now()
        async with aiosqlite.connect(DB_NAME) as db:
            rows = await db.execute_fetchall("""SELECT b.id,b.user_id,s.date,s.time,b.reminder_24,b.reminder_1
                                                FROM bookings b JOIN slots s ON b.slot_id=s.id""")
            for bid, uid, d, t, r24, r1 in rows:
                dt = datetime.strptime(f"{d} {t}","%d.%m.%Y %H:%M")
                diff=(dt-now).total_seconds()
                if 0<diff<=86400 and r24==0:
                    await bot.send_message(uid,f"💌 Напоминание: завтра в {t} ваша запись!")
                    await db.execute("UPDATE bookings SET reminder_24=1 WHERE id=?",(bid,))
                if 0<diff<=3600 and r1==0:
                    await bot.send_message(uid,f"💖 Через час встречаемся! {d} {t}")
                    await db.execute("UPDATE bookings SET reminder_1=1 WHERE id=?",(bid,))
            await db.commit()
        await asyncio.sleep(60)

# ================= ADMIN HANDLERS =================
async def admin(callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("Нет доступа", show_alert=True)
        return
    await callback.message.edit_text("👑 VIP Админка", reply_markup=admin_menu())

async def add_portfolio(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_portfolio)
    await callback.message.answer("Отправьте фото работы")

async def save_portfolio(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO portfolio(file_id) VALUES(?)",(message.photo[-1].file_id,))
        await db.commit()
    await message.answer("Работа добавлена 💎")
    await state.clear()

async def add_review(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_review)
    await callback.message.answer("Отправьте фото + подпись")

async def save_review(message: Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO reviews(file_id,text) VALUES(?,?)",(message.photo[-1].file_id,message.caption or ""))
        await db.commit()
    await message.answer("Отзыв добавлен 💎")
    await state.clear()

async def add_slot(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_slot)
    await callback.message.answer("Введите дату и время (пример: 25.12.2026 14:00)")

async def save_slot(message: Message, state: FSMContext):
    try:
        date,time = message.text.split()
        datetime.strptime(f"{date} {time}","%d.%m.%Y %H:%M")
    except:
        await message.answer("❌ Неверный формат")
        return
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO slots(date,time) VALUES(?,?)",(date,time))
        await db.commit()
    await message.answer("Окошко создано 💎")
    await state.clear()


# ================= MAIN =================
async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # регистрация хендлеров
    dp.message.register(start, Command("start"))
    dp.callback_query.register(menu, F.data == "menu")
    dp.callback_query.register(price, F.data == "price")
    dp.callback_query.register(show_portfolio, F.data.startswith("portfolio_"))
    dp.callback_query.register(show_reviews, F.data.startswith("reviews_"))
    dp.callback_query.register(booking, F.data == "booking")
    dp.callback_query.register(book_slot, F.data.startswith("book_"))
    dp.callback_query.register(cancel_my, F.data == "cancel_my")
    dp.callback_query.register(admin, F.data == "admin")
    dp.callback_query.register(add_portfolio, F.data == "add_portfolio")
    dp.message.register(save_portfolio, AdminStates.waiting_portfolio)
    dp.callback_query.register(add_review, F.data == "add_review")
    dp.message.register(save_review, AdminStates.waiting_review)
    dp.callback_query.register(add_slot, F.data == "add_slot")
    dp.message.register(save_slot, AdminStates.waiting_slot)

    # Webhook
    await bot.delete_webhook(drop_pending_updates=True)
    app = web.Application()
    SimpleRequestHandler(dp, bot).register(app, path=WEBHOOK_PATH)

    # reminders
    asyncio.create_task(reminder_loop(bot))

    # запуск сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    logging.info(f"Webhook URL: {WEBHOOK_URL}")

    await bot.set_webhook(WEBHOOK_URL)
    logging.info("Webhook установлен. Бот работает 24/7")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
