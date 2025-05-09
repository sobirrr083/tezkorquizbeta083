import os
import logging
import asyncio
import requests
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

# Bot token and API keys from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROUP_ID = os.getenv("GROUP_ID")
MOTIVATION_GROUP_ID = os.getenv("MOTIVATION_GROUP_ID")
WEBSITE_URL = os.getenv("WEBSITE_URL")
NOTIFICATION_TIME = os.getenv("NOTIFICATION_TIME", "08:00")

# Gemini-2.0-Flash API endpoint
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# States
class Form(StatesGroup):
    waiting_for_ai_query = State()
    waiting_for_broadcast = State()
    waiting_for_motivation = State()
    waiting_for_motivation_approval = State()

# Database setup
def setup_database():
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    # Create users table with all columns
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users
        (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP,
            is_subscribed_channel BOOLEAN DEFAULT FALSE,
            is_subscribed_group BOOLEAN DEFAULT FALSE,
            receive_daily_motivation BOOLEAN DEFAULT TRUE,
            is_active BOOLEAN DEFAULT TRUE
        )
    ''')

    # Check and add columns if they don't exist
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if 'last_active' not in columns:
        cursor.execute('''
            ALTER TABLE users
            ADD COLUMN last_active TIMESTAMP
        ''')

    if 'receive_daily_motivation' not in columns:
        cursor.execute('''
            ALTER TABLE users
            ADD COLUMN receive_daily_motivation BOOLEAN DEFAULT TRUE
        ''')

    if 'is_active' not in columns:
        cursor.execute('''
            ALTER TABLE users
            ADD COLUMN is_active BOOLEAN DEFAULT TRUE
        ''')

    # Create motivations table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS motivations
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            submitted_by INTEGER,
            status TEXT DEFAULT 'pending',
            likes INTEGER DEFAULT 0,
            shares INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (submitted_by) REFERENCES users (user_id)
        )
    ''')

    # Create motivation_likes table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS motivation_likes
        (
            user_id INTEGER,
            motivation_id INTEGER,
            PRIMARY KEY (user_id, motivation_id),
            FOREIGN KEY (user_id) REFERENCES users (user_id),
            FOREIGN KEY (motivation_id) REFERENCES motivations (id)
        )
    ''')

    conn.commit()
    conn.close()

# Update last active timestamp for a user
def update_last_active(user_id):
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET last_active = ? WHERE user_id = ?",
                       (datetime.now(pytz.timezone("Asia/Tashkent")).strftime('%Y-%m-%d %H:%M:%S'), user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Error updating last_active for user {user_id}: {e}")

# Main keyboard for private chats
def get_main_keyboard(user_id=None):
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🆘 Yordam"), types.KeyboardButton(text="ℹ️ Biz haqimizda"))
    builder.row(types.KeyboardButton(text="📢 Kanal"), types.KeyboardButton(text="👥 Guruh"))
    builder.row(types.KeyboardButton(text="🌐 Web-sayt"), types.KeyboardButton(text="🤖 AI bilan suhbat"))
    
    if user_id:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute("SELECT receive_daily_motivation FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        conn.close()
        is_subscribed = result[0] if result else False
        button_text = "📅 Obunani bekor qilish" if is_subscribed else "📅 Motivatsiyaga obuna bo'lish"
    else:
        button_text = "📅 Motivatsiyaga obuna bo'lish"
    
    builder.row(types.KeyboardButton(text="✨ Motivatsiya qo'shish"), types.KeyboardButton(text=button_text))
    return builder.as_markup(resize_keyboard=True)

# Group keyboard
def get_group_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🚀 Botga kirish"))
    builder.row(types.KeyboardButton(text="ℹ️ Bot haqida"))
    return builder.as_markup(resize_keyboard=True)

# Back keyboard
def get_back_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="🔙 Ortga qaytish"))
    return builder.as_markup(resize_keyboard=True)

# Check subscription
async def check_subscription(user_id):
    try:
        channel_status = await bot.get_chat_member(CHANNEL_ID, user_id)
        group_status = await bot.get_chat_member(GROUP_ID, user_id)

        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_subscribed_channel = ?, is_subscribed_group = ? WHERE user_id = ?",
                       (channel_status.status not in ['left', 'kicked', 'banned'],
                        group_status.status not in ['left', 'kicked', 'banned'],
                        user_id))
        conn.commit()
        conn.close()

        return (channel_status.status not in ['left', 'kicked', 'banned'] and
                group_status.status not in ['left', 'kicked', 'banned'])
    except Exception as e:
        logging.error(f"Error checking subscription: {e}")
        return False

# Subscription keyboard
def get_subscription_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Kanalga a'zo bo'lish", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
    builder.row(InlineKeyboardButton(text="Guruhga qo'shilish", url=f"https://t.me/{GROUP_ID.replace('@', '')}"))
    builder.row(InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_subscription"))
    return builder.as_markup()

# Function to query Gemini-2.0-Flash API
async def query_gemini_flash(prompt):
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    try:
        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        data = response.json()
        if "candidates" in data and data["candidates"]:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            logging.error("No valid response from Gemini-2.0-Flash API")
            return "Javob olishda xatolik yuz berdi."
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying Gemini-2.0-Flash API: {e}")
        return "Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                   (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

    update_last_active(user_id)

    if message.chat.type in ['group', 'supergroup']:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Guruhda bot bilan ishlash uchun quyidagi tugmalardan foydalaning:",
            reply_markup=get_group_keyboard()
        )
        return

    if user_id in ADMIN_IDS:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Admin sifatida botga xush kelibsiz.\n\n"
            "Bot imkoniyatlari:\n"
            "- Tezkor Quiz AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish beta\n"
            "- Kanallar va guruhlar bilan ishlash\n\n"
            "Quyidagi tugmalar orqali kerakli bo'limlarni tanlang:",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    is_subscribed = await check_subscription(user_id)

    if is_subscribed:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Botimizga xush kelibsiz.\n\n"
            "Bot imkoniyatlari:\n"
            "- Tezkor Quiz AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish beta\n"
            "- Kanallar va guruhlar bilan ishlash\n\n"
            "Quyidagi tugmalar orqali kerakli bo'limlarni tanlang:",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Botimizdan foydalanish uchun quyidagi kanal va guruhga a'zo bo'ling:",
            reply_markup=get_subscription_keyboard()
        )

@dp.message(Command("stop"), lambda message: message.chat.type == 'private')
async def cmd_stop(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)
    await state.clear()
    await message.answer(
        "Bot to'xtatildi. Qayta ishga tushirish uchun /start ni bosing.",
        reply_markup=get_main_keyboard(user_id)
    )

@dp.message(Command("admin"), lambda message: message.chat.type == 'private')
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await message.answer(
            "Bu buyruq faqat adminlar uchun.",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    admin_keyboard = InlineKeyboardBuilder()
    admin_keyboard.row(InlineKeyboardButton(text="📢 Xabar yuborish", callback_data="admin_broadcast"))
    admin_keyboard.row(InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats"))
    admin_keyboard.row(InlineKeyboardButton(text="📋 Barcha motivatsiyalar", callback_data="admin_view_motivations"))

    await message.answer(
        "Assalomu alaykum, admin! Quyidagi imkoniyatlardan foydalaning:",
        reply_markup=admin_keyboard.as_markup()
    )

@dp.message(Command("yordam"), lambda message: message.chat.type == 'private')
async def cmd_help(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id in ADMIN_IDS:
        help_text = (
            "🔍 Bot buyruqlari:\n\n"
            "/start - Botni ishga tushirish\n"
            "/stop - Botni to'xtatish\n"
            "/yordam - Yordam olish\n"
            "/ai - Sun'iy intellekt bilan muloqot\n"
            "/admin - Admin paneli\n\n"
            "👇 Asosiy imkoniyatlar:\n"
            "- Tezkor Quiz AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish\n"
            "- Motivatsiya qo'shish\n"
            "- Kanal va guruh yangiliklari"
        )
        await message.answer(help_text, reply_markup=get_back_keyboard())
        return

    is_subscribed = await check_subscription(user_id)

    if not is_subscribed:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                             reply_markup=get_subscription_keyboard())
        return

    help_text = (
        "🔍 Bot buyruqlari:\n\n"
        "/start - Botni ishga tushirish\n"
        "/stop - Botni to'xtatish\n"
        "/yordam - Yordam olish\n"
        "/ai - Sun'iy intellekt bilan muloqot\n\n"
        "👇 Asosiy imkoniyatlar:\n"
        "- Tezkor Quiz AI bilan suhbatlashish\n"
        "- Kunlik motivatsiya olish\n"
        "- Motivatsiya qo'shish\n"
        "- Kanal va guruh yangiliklari"
    )
    await message.answer(help_text, reply_markup=get_back_keyboard())

@dp.message(Command("ai"), lambda message: message.chat.type == 'private')
async def cmd_ai(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id in ADMIN_IDS:
        await state.set_state(Form.waiting_for_ai_query)
        await message.answer("Tezkor Quiz AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                             reply_markup=get_back_keyboard())
        return

    is_subscribed = await check_subscription(user_id)

    if not is_subscribed:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                             reply_markup=get_subscription_keyboard())
        return

    await state.set_state(Form.waiting_for_ai_query)
    await message.answer("Tezkor Quiz AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                         reply_markup=get_back_keyboard())

# Callback handlers
@dp.callback_query(lambda c: c.data == "check_subscription")
async def process_subscription_check(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    is_subscribed = await check_subscription(user_id)

    if is_subscribed:
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        await bot.send_message(
            user_id,
            f"Tabriklaymiz! Siz muvaffaqiyatli a'zo bo'ldingiz.\n"
            "Endi botimizdan to'liq foydalanishingiz mumkin!",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await callback_query.answer("Siz kanal va guruhga to'liq a'zo bo'lmagansiz!", show_alert=True)

# Keep typing action for long responses
async def keep_typing(chat_id):
    while True:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(5)

# AI query handler
@dp.message(Form.waiting_for_ai_query)
async def process_ai_query(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("AI bilan suhbat tugadi. Asosiy menyu:", reply_markup=get_main_keyboard(user_id))
        return

    typing_task = asyncio.create_task(keep_typing(message.chat.id))

    try:
        response_text = await query_gemini_flash(message.text)
        typing_task.cancel()
        await message.answer(response_text, reply_markup=get_back_keyboard())
    except Exception as e:
        typing_task.cancel()
        logging.error(f"Error in Gemini-2.0-Flash query: {e}")
        await message.answer("Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.", reply_markup=get_back_keyboard())

# Broadcast command
@dp.message(Command("broadcast"), lambda message: message.chat.type == 'private')
async def cmd_broadcast(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat adminlar uchun.", reply_markup=get_main_keyboard(user_id))
        return

    await state.set_state(Form.waiting_for_broadcast)
    await message.answer("Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:", reply_markup=get_back_keyboard())

@dp.message(Form.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await state.clear()
        return

    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Xabar yuborish bekor qilindi.", reply_markup=get_main_keyboard(user_id))
        return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE is_active = 1")
    users = cursor.fetchall()
    conn.close()

    sent_count = 0
    failed_count = 0

    await message.answer(f"Xabar {len(users)} ta foydalanuvchiga yuborilmoqda...")

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(user_id[0], message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await bot.send_video(user_id[0], message.video.file_id, caption=message.caption)
            elif message.document:
                await bot.send_document(user_id[0], message.document.file_id, caption=message.caption)
            else:
                await bot.send_message(user_id[0], message.text)
            sent_count += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logging.error(f"Xabar yuborishda xatolik {user_id[0]}: {e}")
            failed_count += 1

    await message.answer(f"Xabar yuborildi: {sent_count} muvaffaqiyatli, {failed_count} muvaffaqiyatsiz",
                        reply_markup=get_main_keyboard(user_id))
    await state.clear()

# Motivation handlers
@dp.message(lambda message: message.text == "✨ Motivatsiya qo'shish" and message.chat.type == 'private')
async def add_motivation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)
    logging.info(f"User {user_id} clicked 'Motivatsiya qo'shish'")

    if user_id in ADMIN_IDS:
        await state.set_state(Form.waiting_for_motivation)
        await message.answer("Yangi motivatsion fikringizni yozing (bekor qilish uchun /stop):",
                             reply_markup=get_back_keyboard())
        return

    is_subscribed = await check_subscription(user_id)
    if not is_subscribed:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                             reply_markup=get_subscription_keyboard())
        return

    await state.set_state(Form.waiting_for_motivation)
    await message.answer("Yangi motivatsion fikringizni yozing (bekor qilish uchun /stop):",
                         reply_markup=get_back_keyboard())

@dp.message(Form.waiting_for_motivation)
async def process_motivation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_keyboard(user_id))
        return

    motivation_text = message.text
    username = f"@{message.from_user.username}" if message.from_user.username else "Noma'lum"
    logging.info(f"User {user_id} submitted motivation: {motivation_text}")

    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        cursor.execute("INSERT INTO motivations (text, submitted_by, status) VALUES (?, ?, ?)",
                       (motivation_text, user_id, "pending"))
        motivation_id = cursor.lastrowid
        conn.commit()
        conn.close()
        logging.info(f"Motivation #{motivation_id} inserted into database")
    except Exception as e:
        logging.error(f"Database error while inserting motivation: {e}")
        await message.answer("Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.", reply_markup=get_main_keyboard(user_id))
        await state.clear()
        return

    await message.answer("Rahmat! Motivatsiyangiz ko'rib chiqish uchun yuborildi.", reply_markup=get_main_keyboard(user_id))

    approval_keyboard = InlineKeyboardBuilder()
    approval_keyboard.row(
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_motivation_{motivation_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_motivation_{motivation_id}")
    )

    notification_text = (
        f"Yangi motivatsiya taklifi #{motivation_id}:\n\n"
        f"{motivation_text}\n\n"
        f"Foydalanuvchi: {message.from_user.full_name}\n"
        f"ID: {user_id}\n"
        f"Username: {username}"
    )

    if MOTIVATION_GROUP_ID:
        try:
            await bot.send_message(
                MOTIVATION_GROUP_ID,
                notification_text,
                reply_markup=approval_keyboard.as_markup()
            )
            logging.info(f"Sent motivation #{motivation_id} to motivation group {MOTIVATION_GROUP_ID}")
        except Exception as e:
            logging.error(f"Failed to send to motivation group {MOTIVATION_GROUP_ID}: {e}")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        notification_text,
                        reply_markup=approval_keyboard.as_markup()
                    )
                    logging.info(f"Sent motivation #{motivation_id} to admin {admin_id}")
                except Exception as admin_e:
                    logging.error(f"Failed to notify admin {admin_id}: {admin_e}")
    else:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    notification_text,
                    reply_markup=approval_keyboard.as_markup()
                )
                logging.info(f"Sent motivation #{motivation_id} to admin {admin_id}")
            except Exception as e:
                logging.error(f"Failed to notify admin {admin_id}: {e}")

    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("approve_motivation_"))
async def approve_motivation(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return

    motivation_id = int(callback_query.data.split("_")[2])

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE motivations SET status = 'approved' WHERE id = ?", (motivation_id,))
    cursor.execute("SELECT text, submitted_by FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    conn.commit()
    conn.close()

    if motivation:
        motivation_text, submitted_by = motivation
        try:
            await bot.send_message(
                submitted_by,
                f"Tabriklaymiz! Sizning motivatsiyangiz tasdiqlandi:\n\n{motivation_text}"
            )
        except Exception as e:
            logging.error(f"Failed to notify user {submitted_by}: {e}")

        if MOTIVATION_GROUP_ID:
            try:
                keyboard = InlineKeyboardBuilder()
                keyboard.row(
                    InlineKeyboardButton(text="👍", callback_data=f"like_motivation_{motivation_id}"),
                    InlineKeyboardButton(text="🔄 Ulashish", callback_data=f"share_motivation_{motivation_id}")
                )
                await bot.send_message(
                    MOTIVATION_GROUP_ID,
                    f"✅ Tasdiqlangan motivatsiya #{motivation_id}:\n\n{motivation_text}",
                    reply_markup=keyboard.as_markup()
                )
                logging.info(f"Approved motivation #{motivation_id} sent to motivation group {MOTIVATION_GROUP_ID}")
            except Exception as e:
                logging.error(f"Failed to send approved motivation to group {MOTIVATION_GROUP_ID}: {e}")

        edit_keyboard = InlineKeyboardBuilder()
        edit_keyboard.row(
            InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"edit_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f"delete_motivation_{motivation_id}")
        )

        await bot.edit_message_text(
            f"✅ TASDIQLANGAN: Motivatsiya #{motivation_id}:\n\n"
            f"{motivation_text}\n\n"
            f"Admin: {callback_query.from_user.full_name}",
            callback_query.message.chat.id,
            callback_query.message.message_id,
            reply_markup=edit_keyboard.as_markup()
        )

@dp.callback_query(lambda c: c.data.startswith("reject_motivation_"))
async def reject_motivation(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return

    motivation_id = int(callback_query.data.split("_")[2])

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE motivations SET status = 'rejected' WHERE id = ?", (motivation_id,))
    cursor.execute("SELECT text, submitted_by FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    conn.commit()
    conn.close()

    if motivation:
        try:
            await bot.send_message(
                motivation[1],
                f"Afsuski, sizning motivatsiyangiz rad etildi:\n\n{motivation[0]}"
            )
        except Exception as e:
            logging.error(f"Failed to notify user {motivation[1]}: {e}")

        await bot.edit_message_text(
            f"❌ RAD ETILGAN: Motivatsiya #{motivation_id}:\n\n"
            f"{motivation[0]}\n\n"
            f"Admin: {callback_query.from_user.full_name}",
            callback_query.message.chat.id,
            callback_query.message.message_id,
            reply_markup=None
        )

@dp.callback_query(lambda c: c.data.startswith("edit_motivation_"))
async def edit_motivation_command(callback_query: types.CallbackQuery, state: FSMContext):
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return

    motivation_id = int(callback_query.data.split("_")[2])
    await state.update_data(editing_motivation_id=motivation_id)
    await state.set_state(Form.waiting_for_motivation_approval)

    await callback_query.answer()
    await bot.send_message(
        callback_query.from_user.id,
        f"Motivatsiya #{motivation_id} uchun yangi matnni yuboring (bekor qilish uchun /stop):",
        reply_markup=get_back_keyboard()
    )

@dp.message(Form.waiting_for_motivation_approval)
async def process_motivation_edit(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Tahrirlash bekor qilindi.", reply_markup=get_main_keyboard(user_id))
        return

    data = await state.get_data()
    motivation_id = data.get("editing_motivation_id")

    if not motivation_id:
        await state.clear()
        await message.answer("Xatolik yuz berdi.", reply_markup=get_main_keyboard(user_id))
        return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE motivations SET text = ? WHERE id = ?", (message.text, motivation_id))
    conn.commit()
    conn.close()

    await message.answer(f"Motivatsiya #{motivation_id} muvaffaqiyatli tahrirlandi.", reply_markup=get_main_keyboard(user_id))
    await state.clear()

@dp.callback_query(lambda c: c.data.startswith("delete_motivation_"))
async def delete_motivation(callback_query: types.CallbackQuery):
    if callback_query.from_user.id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return

    motivation_id = int(callback_query.data.split("_")[2])

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM motivations WHERE id = ?", (motivation_id,))
    conn.commit()
    conn.close()

    await bot.edit_message_text(
        f"🗑️ O'CHIRILDI: Motivatsiya #{motivation_id}\n\n"
        f"Admin: {callback_query.from_user.full_name}",
        callback_query.message.chat.id,
        callback_query.message.message_id,
        reply_markup=None
    )

@dp.callback_query(lambda c: c.data.startswith("like_motivation_"))
async def like_motivation(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    motivation_id = int(callback_query.data.split("_")[2])

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM motivation_likes WHERE user_id = ? AND motivation_id = ?",
                   (user_id, motivation_id))
    existing_like = cursor.fetchone()

    if existing_like:
        cursor.execute("DELETE FROM motivation_likes WHERE user_id = ? AND motivation_id = ?",
                       (user_id, motivation_id))
        cursor.execute("UPDATE motivations SET likes = likes - 1 WHERE id = ?", (motivation_id,))
        like_action = "bekor qilindi"
    else:
        cursor.execute("INSERT INTO motivation_likes (user_id, motivation_id) VALUES (?, ?)",
                       (user_id, motivation_id))
        cursor.execute("UPDATE motivations SET likes = likes + 1 WHERE id = ?", (motivation_id,))
        like_action = "qo'shildi"

    cursor.execute("SELECT text, likes, shares FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    conn.commit()
    conn.close()

    if motivation:
        motivation_text, likes_count, shares_count = motivation

        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text=f"👍 ({likes_count})", callback_data=f"like_motivation_{motivation_id}"),
            InlineKeyboardButton(text=f"🔄 Ulashish ({shares_count})", callback_data=f"share_motivation_{motivation_id}")
        )

        try:
            await bot.edit_message_reply_markup(
                callback_query.message.chat.id,
                callback_query.message.message_id,
                reply_markup=keyboard.as_markup()
            )
            await callback_query.answer(f"Like {like_action}")
        except Exception as e:
            logging.error(f"Failed to update likes: {e}")
            await callback_query.answer("Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data.startswith("share_motivation_"))
async def share_motivation(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    motivation_id = int(callback_query.data.split("_")[2])
    
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE motivations SET shares = shares + 1 WHERE id = ?", (motivation_id,))
    cursor.execute("SELECT text, likes, shares FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    conn.commit()
    conn.close()
    
    if motivation:
        motivation_text, likes_count, shares_count = motivation
        
        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text=f"👍 ({likes_count})", callback_data=f"like_motivation_{motivation_id}"),
            InlineKeyboardButton(text=f"🔄 Ulashish ({shares_count})", callback_data=f"share_motivation_{motivation_id}")
        )
        
        try:
            await bot.edit_message_reply_markup(
                callback_query.message.chat.id,
                callback_query.message.message_id,
                reply_markup=keyboard.as_markup()
            )
            
            share_text = f"📢 Motivatsiya:\n\n{motivation_text}\n\n👉 @{(await bot.me()).username}"
            share_url = f"https://t.me/share/url?url={WEBSITE_URL}&text={share_text}"
            
            await callback_query.answer("Ulashish uchun havola nusxalandi", show_alert=True)
        except Exception as e:
            logging.error(f"Failed to share motivation: {e}")
            await callback_query.answer("Xatolik yuz berdi")

@dp.callback_query(lambda c: c.data == "admin_broadcast")
async def admin_broadcast_command(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return
    
    await state.set_state(Form.waiting_for_broadcast)
    await callback_query.answer()
    await bot.send_message(
        user_id,
        "Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:",
        reply_markup=get_back_keyboard()
    )

@dp.callback_query(lambda c: c.data == "admin_stats")
async def admin_stats(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return
    
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_subscribed_channel = 1 AND is_subscribed_group = 1")
    subscribed_users = cursor.fetchone()[0]
    
    now = datetime.now(pytz.timezone("Asia/Tashkent"))
    one_day_ago = (now - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
    one_week_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
    one_month_ago = (now - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ? AND is_active = 1", (one_day_ago,))
    daily_active = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ? AND is_active = 1", (one_week_ago,))
    weekly_active = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ? AND is_active = 1", (one_month_ago,))
    monthly_active = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 0")
    inactive_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM motivations WHERE status = 'approved'")
    approved_motivations = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM motivations WHERE status = 'pending'")
    pending_motivations = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM motivations")
    total_motivations = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(likes) FROM motivations")
    total_likes = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT SUM(shares) FROM motivations")
    total_shares = cursor.fetchone()[0] or 0
    
    conn.close()
    
    stats_text = (
        "📊 Bot statistikasi:\n\n"
        f"👤 Jami foydalanuvchilar: {total_users}\n"
        f"✅ A'zo bo'lganlar: {subscribed_users}\n"
        f"📢 A'zo bo'lmaganlar: {total_users - subscribed_users}\n"
        f"🕒 Kunlik faol foydalanuvchilar: {daily_active}\n"
        f"🕔 Haftalik faol foydalanuvchilar: {weekly_active}\n"
        f"🕕 Oylik faol foydalanuvchilar: {monthly_active}\n"
        f"🚫 Botdan chiqib ketganlar: {inactive_users}\n\n"
        f"✨ Jami motivatsiyalar: {total_motivations}\n"
        f"✅ Tasdiqlangan: {approved_motivations}\n"
        f"⏳ Kutilmoqda: {pending_motivations}\n"
        f"❌ Rad etilgan: {total_motivations - approved_motivations - pending_motivations}\n\n"
        f"👍 Jami like'lar: {total_likes}\n"
        f"🔄 Jami ulashishlar: {total_shares}"
    )
    
    await callback_query.answer()
    await bot.send_message(
        user_id,
        stats_text
    )

@dp.callback_query(lambda c: c.data == "admin_view_motivations")
async def admin_view_motivations(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        await callback_query.answer("Bu harakat faqat adminlar uchun", show_alert=True)
        return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.id, m.text, m.status, m.likes, m.shares, m.submitted_by, 
               u.first_name, u.last_name, u.username 
        FROM motivations m
        LEFT JOIN users u ON m.submitted_by = u.user_id
        ORDER BY m.created_at DESC
    ''')
    motivations = cursor.fetchall()
    conn.close()

    if not motivations:
        await bot.send_message(
            user_id,
            "Hozircha motivatsiyalar mavjud emas."
        )
        await callback_query.answer()
        return

    for motivation in motivations:
        motivation_id, text, status, likes, shares, submitted_by, first_name, last_name, username = motivation
        full_name = f"{first_name} {last_name}".strip() or "Noma'lum"
        username = f"@{username}" if username else "Noma'lum"

        status_text = {
            "pending": "⏳ Kutilmoqda",
            "approved": "✅ Tasdiqlangan",
            "rejected": "❌ Rad etilgan"
        }.get(status, "Noma'lum")

        motivation_text = (
            f"Motivatsiya #{motivation_id}:\n\n"
            f"📝 Matn: {text}\n"
            f"📊 Status: {status_text}\n"
            f"👍 Like'lar: {likes}\n"
            f"🔄 Ulashishlar: {shares}\n"
            f"👤 Yuborgan: {full_name}\n"
            f"🆔 ID: {submitted_by}\n"
            f"🌐 Username: {username}"
        )

        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"edit_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f"delete_motivation_{motivation_id}")
        )

        try:
            await bot.send_message(
                user_id,
                motivation_text,
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logging.error(f"Failed to send motivation #{motivation_id} to admin {user_id}: {e}")

    await callback_query.answer()

@dp.message(lambda message: message.text in ["📅 Motivatsiyaga obuna bo'lish", "📅 Obunani bekor qilish"] and message.chat.type == 'private')
async def toggle_daily_motivation(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    if user_id not in ADMIN_IDS:
        is_subscribed = await check_subscription(user_id)
        if not is_subscribed:
            await message.answer("Kunlik motivatsiyalarga obuna bo'lish uchun kanal va guruhga a'zo bo'ling:",
                                 reply_markup=get_subscription_keyboard())
            return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT receive_daily_motivation FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()

    if result is None:
        await message.answer("Xatolik yuz berdi. Iltimos, /start buyrug'ini qayta ishlatib ko'ring.")
        conn.close()
        return

    current_status = result[0]
    new_status = not current_status

    cursor.execute("UPDATE users SET receive_daily_motivation = ? WHERE user_id = ?", (new_status, user_id))
    conn.commit()
    conn.close()

    status_text = "obuna bo'ldingiz" if new_status else "obunadan chiqdingiz"
    await message.answer(f"Kunlik motivatsiyalarga {status_text}.", reply_markup=get_main_keyboard(user_id))

@dp.message(lambda message: message.text == "🆘 Yordam" and message.chat.type == 'private')
async def help_button(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)
    await cmd_help(message)

@dp.message(lambda message: message.text == "ℹ️ Biz haqimizda" and message.chat.type == 'private')
async def about_button(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)
    
    if user_id in ADMIN_IDS:
        about_text = (
            "ℹ️ Bot haqida ma'lumot:\n\n"
            "Bu bot Tezkor Quiz AI orqali sun'iy intellekt imkoniyatlarini taqdim etadi.\n"
            "Shuningdek, motivatsion fikrlar almashinuvini qo'llab-quvvatlaydi.\n\n"
            "Asosiy imkoniyatlar:\n"
            "- Tezkor Quiz AI bilan suhbatlashish\n"
            "- Motivatsion fikrlar qo'shish va ulashish\n"
            "- Kunlik motivatsiyalar olish\n\n"
            "Bizning kanalimizga a'zo bo'ling va guruhimizga qo'shiling!"
        )
        await message.answer(about_text, reply_markup=get_main_keyboard(user_id))
        return
    
    is_subscribed = await check_subscription(user_id)
    
    if not is_subscribed:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                          reply_markup=get_subscription_keyboard())
        return
    
    about_text = (
        "ℹ️ Bot haqida ma'lumot:\n\n"
        "Bu bot Tezkor Quiz AI orqali sun'iy intellekt imkoniyatlarini taqdim etadi.\n"
        "Shuningdek, motivatsion fikrlar almashinuvini qo'llab-quvvatlaydi.\n\n"
        "Asosiy imkoniyatlar:\n"
        "- Tezkor Quiz AI bilan suhbatlashish\n"
        "- Motivatsion fikrlar qo'shish va ulashish\n"
        "- Kunlik motivatsiyalar olish\n\n"
        "Bizning kanalimizga a'zo bo'ling va guruhimizga qo'shiling!"
    )
    await message.answer(about_text, reply_markup=get_main_keyboard(user_id))

@dp.message(lambda message: message.text == "📢 Kanal" and message.chat.type == 'private')
async def channel_button(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    channel_link = f"https://t.me/{CHANNEL_ID.replace('@', '')}"
    
    channel_keyboard = InlineKeyboardBuilder()
    channel_keyboard.row(InlineKeyboardButton(text="Kanalga o'tish", url=channel_link))
    
    await message.answer(
        "Bizning rasmiy kanalimizga a'zo bo'ling va yangiliklar bilan tanishing!",
        reply_markup=channel_keyboard.as_markup()
    )

@dp.message(lambda message: message.text == "👥 Guruh" and message.chat.type == 'private')
async def group_button(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    group_link = f"https://t.me/{GROUP_ID.replace('@', '')}"
    
    group_keyboard = InlineKeyboardBuilder()
    group_keyboard.row(InlineKeyboardButton(text="Guruhga o'tish", url=group_link))
    
    await message.answer(
        "Bizning rasmiy guruhimizga qo'shiling va muhokamada qatnashing!",
        reply_markup=group_keyboard.as_markup()
    )

@dp.message(lambda message: message.text == "🌐 Web-sayt" and message.chat.type == 'private')
async def website_button(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    website_keyboard = InlineKeyboardBuilder()
    website_keyboard.row(InlineKeyboardButton(text="Web-saytga o'tish", url=WEBSITE_URL))
    
    await message.answer(
        "Bizning rasmiy web-saytimizga tashrif buyuring!",
        reply_markup=website_keyboard.as_markup()
    )

@dp.message(lambda message: message.text == "🤖 AI bilan suhbat" and message.chat.type == 'private')
async def ai_chat_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)
    await cmd_ai(message, state)

@dp.message(lambda message: message.text == "🔙 Ortga qaytish" and message.chat.type == 'private')
async def back_button(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    update_last_active(user_id)

    current_state = await state.get_state()
    if current_state:
        await state.clear()
    
    await message.answer("Asosiy menyu:", reply_markup=get_main_keyboard(user_id))

@dp.message(lambda message: message.text == "🚀 Botga kirish" and message.chat.type in ['group', 'supergroup'])
async def start_from_group(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    bot_username = (await bot.me()).username
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="Botga o'tish", url=f"https://t.me/{bot_username}"))
    
    await message.answer(
        "Botdan foydalanish uchun quyidagi havolaga o'ting:",
        reply_markup=keyboard.as_markup()
    )

@dp.message(lambda message: message.text == "ℹ️ Bot haqida" and message.chat.type in ['group', 'supergroup'])
async def about_from_group(message: types.Message):
    user_id = message.from_user.id
    update_last_active(user_id)

    bot_username = (await bot.me()).username
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="Botga o'tish", url=f"https://t.me/{bot_username}"))
    
    about_text = (
        "ℹ️ Bot haqida ma'lumot:\n\n"
        "Bu bot Tezkor Quiz AI orqali sun'iy intellekt imkoniyatlarini taqdim etadi.\n"
        "Shuningdek, motivatsion fikrlar almashinuvini qo'llab-quvvatlaydi.\n\n"
        "Asosiy imkoniyatlar:\n"
        "- Tezkor Quiz AI bilan suhbatlashish\n"
        "- Motivatsion fikrlar qo'shish va ulashish\n"
        "- Kunlik motivatsiyalar olish\n\n"
        "Botdan foydalanish uchun quyidagi havolaga o'ting:"
    )
    
    await message.answer(about_text, reply_markup=keyboard.as_markup())

# Daily motivation function
async def send_daily_motivation():
    try:
        logging.info("Starting daily motivation task...")
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()
        
        cursor.execute("SELECT id, text FROM motivations WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
        motivation = cursor.fetchone()
        
        if not motivation:
            logging.warning("No approved motivations found for daily sending")
            conn.close()
            return
        
        motivation_id, motivation_text = motivation
        
        cursor.execute("SELECT user_id FROM users WHERE is_subscribed_channel = 1 AND is_subscribed_group = 1 AND receive_daily_motivation = 1 AND is_active = 1")
        subscribed_users = cursor.fetchall()
        
        conn.close()
        
        if not subscribed_users:
            logging.warning("No subscribed users found for daily motivation")
            return
        
        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text="👍", callback_data=f"like_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🔄 Ulashish", callback_data=f"share_motivation_{motivation_id}")
        )
        
        daily_text = (
            "🌞 Bugungi kunning motivatsiyasi:\n\n"
            f"{motivation_text}"
        )
        
        sent_count = 0
        failed_count = 0
        
        for user in subscribed_users:
            try:
                await bot.send_message(user[0], daily_text, reply_markup=keyboard.as_markup())
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logging.error(f"Failed to send daily motivation to {user[0]}: {e}")
                failed_count += 1
        
        logging.info(f"Daily motivation sent: {sent_count} successful, {failed_count} failed")
        
    except Exception as e:
        logging.error(f"Error in daily motivation function: {e}")

# Setup scheduler with timezone
async def setup_scheduler():
    scheduler = AsyncIOScheduler(timezone="Asia/Tashkent")
    
    hour, minute = map(int, NOTIFICATION_TIME.split(':'))
    
    scheduler.add_job(
        send_daily_motivation,
        CronTrigger(hour=hour, minute=minute, timezone="Asia/Tashkent")
    )
    scheduler.start()
    logging.info(f"Scheduler set up for daily motivation at {NOTIFICATION_TIME} Asia/Tashkent")

# Detect blocked users
@dp.errors()
async def handle_errors(update: types.Update, exception: Exception):
    if isinstance(exception, types.errors.TelegramAPIError) and "blocked by user" in str(exception).lower():
        user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
        try:
            conn = sqlite3.connect('bot_database.db')
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET is_active = 0 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            logging.info(f"User {user_id} marked as inactive (blocked bot)")
        except Exception as e:
            logging.error(f"Error marking user {user_id} as inactive: {e}")
    else:
        logging.error(f"Unhandled error: {exception}")
    return True

# Run the bot
async def main():
    setup_database()
    await setup_scheduler()
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.info("Bot ishga tushdi...")
    asyncio.run(main())
