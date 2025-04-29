import os
import logging
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, ChatTypeFilter
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import google.generativeai as genai
import sqlite3
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Configure logging
logging.basicConfig(level=logging.INFO)

# Load environment variables
load_dotenv()

# Bot token from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
CHANNEL_ID = os.getenv("CHANNEL_ID")
GROUP_ID = os.getenv("GROUP_ID")
MOTIVATION_GROUP_ID = os.getenv("MOTIVATION_GROUP_ID")
WEBSITE_URL = os.getenv("WEBSITE_URL")
NOTIFICATION_TIME = os.getenv("NOTIFICATION_TIME", "08:00")

# Configure Gemini AI
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-pro')

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

    cursor.execute('''
                   CREATE TABLE IF NOT EXISTS users
                   (
                       user_id INTEGER PRIMARY KEY,
                       username TEXT,
                       first_name TEXT,
                       last_name TEXT,
                       joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                       is_subscribed_channel BOOLEAN DEFAULT FALSE,
                       is_subscribed_group BOOLEAN DEFAULT FALSE
                   )
                   ''')

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

# Main keyboard for private chats
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="Yordam 🆘"), types.KeyboardButton(text="Biz haqimizda ℹ️"))
    builder.row(types.KeyboardButton(text="Kanal 📢"), types.KeyboardButton(text="Guruh 👥"))
    builder.row(types.KeyboardButton(text="Web-sayt 🌐"), types.KeyboardButton(text="AI bilan suhbat 🤖"))
    builder.row(types.KeyboardButton(text="Motivatsiya qo'shish ✨"))
    return builder.as_markup(resize_keyboard=True)

# Group keyboard
def get_group_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(types.KeyboardButton(text="Botga kirish 🚀"))
    builder.row(types.KeyboardButton(text="Bot haqida ℹ️"))
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
    builder.row(InlineKeyboardButton(text="Tekshirish ✅", callback_data="check_subscription"))
    return builder.as_markup()

# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name

    # Add user to database
    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                   (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

    # Group chat
    if message.chat.type in ['group', 'supergroup']:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Guruhda bot bilan ishlash uchun quyidagi tugmalardan foydalaning:",
            reply_markup=get_group_keyboard()
        )
        return

    # Private chat
    if user_id in ADMIN_IDS:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Admin sifatida botga xush kelibsiz.\n\n"
            "Bot imkoniyatlari:\n"
            "- Gemini AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish\n"
            "- Kanallar va guruhlar bilan ishlash\n\n"
            "Quyidagi tugmalar orqali kerakli bo'limlarni tanlang:",
            reply_markup=get_main_keyboard()
        )
        return

    is_subscribed = await check_subscription(user_id)

    if is_subscribed:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Botimizga xush kelibsiz.\n\n"
            "Bot imkoniyatlari:\n"
            "- Gemini AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish\n"
            "- Kanallar va guruhlar bilan ishlash\n\n"
            "Quyidagi tugmalar orqali kerakli bo'limlarni tanlang:",
            reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            f"Assalomu alaykum, {first_name}! Botimizdan foydalanish uchun quyidagi kanal va guruhga a'zo bo'ling:",
            reply_markup=get_subscription_keyboard()
        )

@dp.message(Command("stop"), ChatTypeFilter(chat_types=['private']))
async def cmd_stop(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Bot to'xtatildi. Qayta ishga tushirish uchun /start ni bosing.",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("admin"), ChatTypeFilter(chat_types=['private']))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id

    if user_id not in ADMIN_IDS:
        await message.answer(
            "Bu buyruq faqat adminlar uchun.",
            reply_markup=get_main_keyboard()
        )
        return

    admin_keyboard = InlineKeyboardBuilder()
    admin_keyboard.row(InlineKeyboardButton(text="Xabar yuborish 📢", callback_data="admin_broadcast"))
    admin_keyboard.row(InlineKeyboardButton(text="Statistika 📊", callback_data="admin_stats"))

    await message.answer(
        "Assalomu alaykum, admin! Quyidagi imkoniyatlardan foydalaning:",
        reply_markup=admin_keyboard.as_markup()
    )

@dp.message(Command("yordam"), ChatTypeFilter(chat_types=['private']))
async def cmd_help(message: types.Message):
    user_id = message.from_user.id

    if user_id in ADMIN_IDS:
        help_text = (
            "🔍 Bot buyruqlari:\n\n"
            "/start - Botni ishga tushirish\n"
            "/stop - Botni to'xtatish\n"
            "/yordam - Yordam olish\n"
            "/ai - Sun'iy intellekt bilan muloqot\n"
            "/admin - Admin paneli\n\n"
            "👇 Asosiy imkoniyatlar:\n"
            "- Gemini AI bilan suhbatlashish\n"
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
        "- Gemini AI bilan suhbatlashish\n"
        "- Kunlik motivatsiya olish\n"
        "- Motivatsiya qo'shish\n"
        "- Kanal va guruh yangiliklari"
    )
    await message.answer(help_text, reply_markup=get_back_keyboard())

@dp.message(Command("ai"), ChatTypeFilter(chat_types=['private']))
async def cmd_ai(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    if user_id in ADMIN_IDS:
        await state.set_state(Form.waiting_for_ai_query)
        await message.answer("Gemini AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                             reply_markup=get_back_keyboard())
        return

    is_subscribed = await check_subscription(user_id)

    if not is_subscribed:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                             reply_markup=get_subscription_keyboard())
        return

    await state.set_state(Form.waiting_for_ai_query)
    await message.answer("Gemini AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                         reply_markup=get_back_keyboard())

# Callback handlers
@dp.callback_query(lambda c: c.data == "check_subscription")
async def process_subscription_check(callback_query: types.CallbackQuery):
    is_subscribed = await check_subscription(callback_query.from_user.id)

    if is_subscribed:
        await bot.delete_message(callback_query.message.chat.id, callback_query.message.message_id)
        await bot.send_message(
            callback_query.from_user.id,
            f"Tabriklaymiz! Siz muvaffaqiyatli a'zo bo'ldingiz.\n"
            "Endi botimizdan to'liq foydalanishingiz mumkin!",
            reply_markup=get_main_keyboard()
        )
    else:
        await callback_query.answer("Siz kanal va guruhga to'liq a'zo bo'lmagansiz!", show_alert=True)

# Keep typing action for long responses
async def keep_typing(chat_id):
    while True:
        await bot.send_chat_action(chat_id, "typing")
        await asyncio.sleep(5)

# Message handlers for private chats
@dp.message(Form.waiting_for_ai_query)
async def process_ai_query(message: types.Message, state: FSMContext):
    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("AI bilan suhbat tugadi. Asosiy menyu:", reply_markup=get_main_keyboard())
        return

    typing_task = asyncio.create_task(keep_typing(message.chat.id))

    try:
        response = model.generate_content(message.text)
        typing_task.cancel()
        await message.answer(response.text, reply_markup=get_back_keyboard())
    except Exception as e:
        typing_task.cancel()
        logging.error(f"Gemini AI error: {e}")
        await message.answer("Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.", reply_markup=get_back_keyboard())

@dp.message(Command("broadcast"), ChatTypeFilter(chat_types=['private']))
async def cmd_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("Bu buyruq faqat adminlar uchun.", reply_markup=get_main_keyboard())
        return

    await state.set_state(Form.waiting_for_broadcast)
    await message.answer("Barcha foydalanuvchilarga yuboriladigan xabarni kiriting:", reply_markup=get_back_keyboard())

@dp.message(Form.waiting_for_broadcast)
async def process_broadcast(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Xabar yuborish bekor qilindi.", reply_markup=get_main_keyboard())
        return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
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
            logging.error(f"Failed to send message to {user_id[0]}: {e}")
            failed_count += 1

    await message.answer(f"Xabar yuborildi: {sent_count} muvaffaqiyatli, {failed_count} muvaffaqiyatsiz",
                        reply_markup=get_main_keyboard())
    await state.clear()

@dp.message(lambda message: message.text == "Motivatsiya qo'shish ✨", ChatTypeFilter(chat_types=['private']))
async def add_motivation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

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
    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Bekor qilindi.", reply_markup=get_main_keyboard())
        return

    user_id = message.from_user.id
    motivation_text = message.text

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO motivations (text, submitted_by, status) VALUES (?, ?, ?)",
                   (motivation_text, user_id, "pending"))
    motivation_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await message.answer("Rahmat! Motivatsiyangiz ko'rib chiqish uchun yuborildi.", reply_markup=get_main_keyboard())
    await state.clear()

    approval_keyboard = InlineKeyboardBuilder()
    approval_keyboard.row(
        InlineKeyboardButton(text="✅ Tasdiqlash", callback_data=f"approve_motivation_{motivation_id}"),
        InlineKeyboardButton(text="❌ Rad etish", callback_data=f"reject_motivation_{motivation_id}")
    )

    if MOTIVATION_GROUP_ID:
        try:
            await bot.send_message(
                MOTIVATION_GROUP_ID,
                f"Yangi motivatsiya taklifi #{motivation_id}:\n\n"
                f"{motivation_text}\n\n"
                f"Foydalanuvchi: {message.from_user.full_name} (ID: {user_id})",
                reply_markup=approval_keyboard.as_markup()
            )
        except Exception as e:
            logging.error(f"Failed to send to motivation group: {e}")
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"Yangi motivatsiya taklifi #{motivation_id}:\n\n"
                        f"{motivation_text}\n\n"
                        f"Foydalanuvchi: {message.from_user.full_name} (ID: {user_id})",
                        reply_markup=approval_keyboard.as_markup()
                    )
                except Exception as admin_e:
                    logging.error(f"Failed to notify admin {admin_id}: {admin_e}")
    else:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"Yangi motivatsiya taklifi #{motivation_id}:\n\n"
                    f"{motivation_text}\n\n"
                    f"Foydalanuvchi: {message.from_user.full_name} (ID: {user_id})",
                    reply_markup=approval_keyboard.as_markup()
                )
            except Exception as e:
                logging.error(f"Failed to notify admin {admin_id}: {e}")

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
        try:
            await bot.send_message(
                motivation[1],
                f"Tabriklaymiz! Sizning motivatsiyangiz tasdiqlandi:\n\n{motivation[0]}"
            )
        except Exception as e:
            logging.error(f"Failed to notify user {motivation[1]}: {e}")

        edit_keyboard = InlineKeyboardBuilder()
        edit_keyboard.row(
            InlineKeyboardButton(text="✏️ Tahrirlash", callback_data=f"edit_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🗑️ O'chirish", callback_data=f"delete_motivation_{motivation_id}")
        )

        await bot.edit_message_text(
            f"✅ TASDIQLANGAN: Motivatsiya #{motivation_id}:\n\n"
            f"{motivation[0]}\n\n"
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
    if message.text == "/stop" or message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Tahrirlash bekor qilindi.", reply_markup=get_main_keyboard())
        return

    data = await state.get_data()
    motivation_id = data.get("editing_motivation_id")

    if not motivation_id:
        await state.clear()
        await message.answer("Xatolik yuz berdi.", reply_markup=get_main_keyboard())
        return

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE motivations SET text = ? WHERE id = ?", (message.text, motivation_id))
    conn.commit()
    conn.close()

    await message.answer(f"Motivatsiya #{motivation_id} muvaffaqiyatli tahrirlandi.", reply_markup=get_main_keyboard())
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
    motivation_id = int(callback_query.data.split("_")[2])
    user_id = callback_query.from_user.id

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

    cursor.execute("SELECT text, likes FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    conn.commit()
    conn.close()

    if motivation:
        motivation_text, likes_count = motivation

        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text=f"👍 ({likes_count})", callback_data=f"like_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🔄 Ulashish", callback_data=f"share_motivation_{motivation_id}")
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
    motivation_id = int(callback_query.data.split("_")[2])

    conn = sqlite3.connect('bot_database.db')
    cursor = conn.cursor()
    cursor.execute("SELECT text FROM motivations WHERE id = ?", (motivation_id,))
    motivation = cursor.fetchone()
    cursor.execute("UPDATE motivations SET shares = shares + 1 WHERE id = ?", (motivation_id,))
    conn.commit()
    conn.close()

    if motivation:
        share_text = f"📣 Motivatsiya:\n\n{motivation[0]}\n\n📲 @tezkorquiz_bot orqali"
        await callback_query.answer("Ulashish uchun tayyor")

        share_button = InlineKeyboardBuilder()
        share_button.row(InlineKeyboardButton(text="Botga o'tish", url=f"https://t.me/{(await bot.me()).username}"))

        await bot.send_message(
            callback_query.message.chat.id,
            share_text,
            reply_markup=share_button.as_markup()
        )

# Daily motivation sender
async def send_daily_motivation():
    try:
        conn = sqlite3.connect('bot_database.db')
        cursor = conn.cursor()

        cursor.execute("SELECT id, text FROM motivations WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
        motivation = cursor.fetchone()

        if not motivation:
            logging.warning("No approved motivations found for daily sending")
            conn.close()
            return

        motivation_id, motivation_text = motivation

        cursor.execute("SELECT user_id FROM users")
        users = cursor.fetchall()
        conn.close()

        keyboard = InlineKeyboardBuilder()
        keyboard.row(
            InlineKeyboardButton(text="👍 (0)", callback_data=f"like_motivation_{motivation_id}"),
            InlineKeyboardButton(text="🔄 Ulashish", callback_data=f"share_motivation_{motivation_id}")
        )

        for user in users:
            try:
                await bot.send_message(
                    user[0],
                    f"🌟 Bugungi motivatsiya:\n\n{motivation_text}",
                    reply_markup=keyboard.as_markup()
                )
                await asyncio.sleep(0.05)
            except Exception as e:
                logging.error(f"Failed to send daily motivation to {user[0]}: {e}")

    except Exception as e:
        logging.error(f"Error in daily motivation sender: {e}")

# Text message handler for group and private chats
@dp.message()
async def process_text_messages(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    # Group chat handling
    if message.chat.type in ['group', 'supergroup']:
        if message.text == "Botga kirish 🚀":
            bot_username = (await bot.me()).username
            await message.answer(
                f"Bot bilan shaxsiy suhbatda ishlash uchun quyidagi link orqali o'ting:\n"
                f"https://t.me/{bot_username}",
                reply_markup=get_group_keyboard()
            )
        elif message.text == "Bot haqida ℹ️":
            about_text = (
                "📱 Bot haqida ma'lumot:\n\n"
                "Bu bot Gemini sun'iy intellekt bilan integratsiyalashgan multifunksional Telegram bot. "
                "Bot orqali siz sun'iy intellekt bilan suhbatlashish, kunlik motivatsiyalar olish, "
                "o'z motivatsiyalaringizni qo'shish va ko'plab boshqa imkoniyatlardan foydalanishingiz mumkin.\n\n"
                "🌟 Asosiy funksiyalar:\n"
                "- Gemini AI bilan sun'iy intellekt suhbati\n"
                "- Kunlik motivatsiyalar\n"
                "- Kanal va guruh yangiliklari\n"
                "- O'z motivatsiyalaringizni qo'shish\n\n"
                "📞 Bot bilan shaxsiy suhbatda ishlash uchun 'Botga kirish' tugmasini bosing."
            )
            await message.answer(about_text, reply_markup=get_group_keyboard())
        else:
            await message.answer(
                "Guruhda faqat quyidagi funksiyalar mavjud:",
                reply_markup=get_group_keyboard()
            )
        return

    # Private chat handling
    if message.text == "🔙 Ortga qaytish":
        await state.clear()
        await message.answer("Asosiy menyuga qaytildi:", reply_markup=get_main_keyboard())
        return

    if user_id in ADMIN_IDS:
        if message.text == "Yordam 🆘":
            help_text = (
                "🔍 Bot buyruqlari:\n\n"
                "/start - Botni ishga tushirish\n"
                "/stop - Botni to'xtatish\n"
                "/yordam - Yordam olish\n"
                "/ai - Sun'iy intellekt bilan muloqot\n"
                "/admin - Admin paneli\n\n"
                "👇 Asosiy imkoniyatlar:\n"
                "- Gemini AI bilan suhbatlashish\n"
                "- Kunlik motivatsiya olish\n"
                "- Motivatsiya qo'shish\n"
                "- Kanal va guruh yangiliklari"
            )
            await message.answer(help_text, reply_markup=get_back_keyboard())
        elif message.text == "Biz haqimizda ℹ️":
            about_text = (
                "📱 Bot haqida ma'lumot:\n\n"
                "Bu bot Gemini sun'iy intellekt bilan integratsiyalashgan multifunksional Telegram bot. "
                "Bot orqali siz sun'iy intellekt bilan suhbatlashish, kunlik motivatsiyalar olish, "
                "o'z motivatsiyalaringizni qo'shish va ko'plab boshqa imkoniyatlardan foydalanishingiz mumkin.\n\n"
                "🌟 Asosiy funksiyalar:\n"
                "- Gemini AI bilan sun'iy intellekt suhbati\n"
                "- Kunlik motivatsiyalar\n"
                "- Kanal va guruh yangiliklari\n"
                "- O'z motivatsiyalaringizni qo'shish\n\n"
                "📞 Murojaat uchun: @sobirrr083"
            )
            await message.answer(about_text, reply_markup=get_back_keyboard())
        elif message.text == "Kanal 📢":
            channel_keyboard = InlineKeyboardBuilder()
            channel_keyboard.row(
                InlineKeyboardButton(text="Kanalga o'tish", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
            await message.answer("Bizning rasmiy kanalimizga o'ting va yangiliklardan xabardor bo'ling:",
                                 reply_markup=channel_keyboard.as_markup())
            await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
        elif message.text == "Guruh 👥":
            group_keyboard = InlineKeyboardBuilder()
            group_keyboard.row(
                InlineKeyboardButton(text="Guruhga o'tish", url=f"https://t.me/{GROUP_ID.replace('@', '')}"))
            await message.answer("Bizning guruhimizga qo'shiling va muhokamada ishtirok eting:",
                                 reply_markup=group_keyboard.as_markup())
            await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
        elif message.text == "Web-sayt 🌐":
            website_keyboard = InlineKeyboardBuilder()
            website_keyboard.row(InlineKeyboardButton(text="Saytga o'tish", url=WEBSITE_URL))
            await message.answer("Rasmiy web-saytimizga tashrif buyuring:",
                                 reply_markup=website_keyboard.as_markup())
            await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
        elif message.text == "AI bilan suhbat 🤖":
            await state.set_state(Form.waiting_for_ai_query)
            await message.answer("Gemini AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                                 reply_markup=get_back_keyboard())
        else:
            await message.answer("Noma'lum buyruq. Iltimos, quyidagi tugmalardan foydalaning:",
                                 reply_markup=get_main_keyboard())
        return

    is_subscribed = await check_subscription(user_id)

    if not is_subscribed and message.text not in ["Kanal 📢", "Guruh 👥"]:
        await message.answer("Botdan foydalanish uchun kanal va guruhga a'zo bo'ling:",
                             reply_markup=get_subscription_keyboard())
        return

    if message.text == "Yordam 🆘":
        help_text = (
            "🔍 Bot buyruqlari:\n\n"
            "/start - Botni ishga tushirish\n"
            "/stop - Botni to'xtatish\n"
            "/yordam - Yordam olish\n"
            "/ai - Sun'iy intellekt bilan muloqot\n\n"
            "👇 Asosiy imkoniyatlar:\n"
            "- Gemini AI bilan suhbatlashish\n"
            "- Kunlik motivatsiya olish\n"
            "- Motivatsiya qo'shish\n"
            "- Kanal va guruh yangiliklari"
        )
        await message.answer(help_text, reply_markup=get_back_keyboard())
    elif message.text == "Biz haqimizda ℹ️":
        about_text = (
            "📱 Bot haqida ma'lumot:\n\n"
            "Bu bot Gemini sun'iy intellekt bilan integratsiyalashgan multifunksional Telegram bot. "
            "Bot orqali siz sun'iy intellekt bilan suhbatlashish, kunlik motivatsiyalar olish, "
            "o'z motivatsiyalaringizni qo'shish va ko'plab boshqa imkoniyatlardan foydalanishingiz mumkin.\n\n"
            "🌟 Asosiy funksiyalar:\n"
            "- Gemini AI bilan sun'iy intellekt suhbati\n"
            "- Kunlik motivatsiyalar\n"
            "- Kanal va guruh yangiliklari\n"
            "- O'z motivatsiyalaringizni qo'shish\n\n"
            "📞 Murojaat uchun: @admin_username"
        )
        await message.answer(about_text, reply_markup=get_back_keyboard())
    elif message.text == "Kanal 📢":
        channel_keyboard = InlineKeyboardBuilder()
        channel_keyboard.row(
            InlineKeyboardButton(text="Kanalga o'tish", url=f"https://t.me/{CHANNEL_ID.replace('@', '')}"))
        await message.answer("Bizning rasmiy kanalimizga o'ting va yangiliklardan xabardor bo'ling:",
                             reply_markup=channel_keyboard.as_markup())
        await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
    elif message.text == "Guruh 👥":
        group_keyboard = InlineKeyboardBuilder()
        group_keyboard.row(
            InlineKeyboardButton(text="Guruhga o'tish", url=f"https://t.me/{GROUP_ID.replace('@', '')}"))
        await message.answer("Bizning guruhimizga qo'shiling va muhokamada ishtirok eting:",
                             reply_markup=group_keyboard.as_markup())
        await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
    elif message.text == "Web-sayt 🌐":
        website_keyboard = InlineKeyboardBuilder()
        website_keyboard.row(InlineKeyboardButton(text="Saytga o'tish", url=WEBSITE_URL))
        await message.answer("Rasmiy web-saytimizga tashrif buyuring:",
                             reply_markup=website_keyboard.as_markup())
        await message.answer("Qaytish uchun:", reply_markup=get_back_keyboard())
    elif message.text == "AI bilan suhbat 🤖":
        await state.set_state(Form.waiting_for_ai_query)
        await message.answer("Gemini AI bilan suhbatni boshladingiz. Savolingizni yozing (chiqish uchun /stop):",
                             reply_markup=get_back_keyboard())
    else:
        await message.answer("Noma'lum buyruq. Iltimos, quyidagi tugmalardan foydalaning:",
                             reply_markup=get_main_keyboard())

# Setup scheduler for daily motivation
async def setup_scheduler():
    scheduler = AsyncIOScheduler()
    hour, minute = map(int, NOTIFICATION_TIME.split(':'))
    scheduler.add_job(send_daily_motivation, 'cron', hour=hour, minute=minute)
    scheduler.start()
    logging.info(f"Scheduler started for daily motivation at {NOTIFICATION_TIME}")

# Main function
async def main():
    setup_database()
    await setup_scheduler()

    try:
        await dp.start_polling(bot)
    except KeyboardInterrupt:
        logging.info("Bot polling stopped by user")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
