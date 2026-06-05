import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "7958403209:AAGQPN_ZvRButSZYWibDx8ovH4_ofaTSXIA"
OWNER_ID = 5584463063
ADMIN_IDS = [5584463063]
# ====================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# База данных (простая, без отдельного файла для простоты)
import sqlite3
import json
from contextlib import contextmanager

DB_PATH = "copy_bot.db"

@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                source_channels TEXT,
                target_channels TEXT,
                schedule_date TEXT,
                is_active INTEGER DEFAULT 1
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS copied_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                source_channel INTEGER,
                source_message_id INTEGER
            )
        ''')

init_db()

def get_profiles(user_id):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchall()
        profiles = []
        for row in rows:
            p = dict(row)
            p['source_channels'] = json.loads(p['source_channels'] or '[]')
            p['target_channels'] = json.loads(p['target_channels'] or '[]')
            profiles.append(p)
        return profiles

def get_profile(profile_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row:
            p = dict(row)
            p['source_channels'] = json.loads(p['source_channels'] or '[]')
            p['target_channels'] = json.loads(p['target_channels'] or '[]')
            return p
        return None

def create_profile(user_id, name):
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO profiles (user_id, name, source_channels, target_channels) VALUES (?, ?, ?, ?)",
            (user_id, name, '[]', '[]')
        )
        return cursor.lastrowid

def update_profile_sources(profile_id, sources):
    with get_db() as conn:
        conn.execute("UPDATE profiles SET source_channels = ? WHERE id = ?", (json.dumps(sources), profile_id))

def update_profile_targets(profile_id, targets):
    with get_db() as conn:
        conn.execute("UPDATE profiles SET target_channels = ? WHERE id = ?", (json.dumps(targets), profile_id))

def update_profile_schedule(profile_id, schedule_date):
    with get_db() as conn:
        conn.execute("UPDATE profiles SET schedule_date = ? WHERE id = ?", (schedule_date, profile_id))

def update_profile_active(profile_id, is_active):
    with get_db() as conn:
        conn.execute("UPDATE profiles SET is_active = ? WHERE id = ?", (is_active, profile_id))

def delete_profile(profile_id):
    with get_db() as conn:
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

def is_post_copied(profile_id, source_channel, message_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM copied_posts WHERE profile_id = ? AND source_channel = ? AND source_message_id = ?",
            (profile_id, source_channel, message_id)
        ).fetchone()
        return row is not None

def mark_post_copied(profile_id, source_channel, message_id):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO copied_posts (profile_id, source_channel, source_message_id) VALUES (?, ?, ?)",
            (profile_id, source_channel, message_id)
        )


# Временные данные
temp_settings: Dict[int, Dict] = {}

class ProfileStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_sources = State()
    waiting_for_targets = State()
    waiting_for_schedule = State()
    waiting_for_confirm = State()


def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📁 Управление профилями", callback_data="profiles_menu"))
    builder.row(InlineKeyboardButton(text="❓ Помощь", callback_data="help"))
    return builder.as_markup()


def get_profiles_menu(user_id: int):
    builder = InlineKeyboardBuilder()
    profiles = get_profiles(user_id)
    
    for p in profiles:
        status = "✅" if p['is_active'] else "⏸"
        builder.row(InlineKeyboardButton(text=f"{status} {p['name']}", callback_data=f"profile_{p['id']}"))
    
    builder.row(InlineKeyboardButton(text="➕ Создать профиль", callback_data="create_profile"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def get_profile_actions(profile_id: int, is_active: bool, name: str):
    builder = InlineKeyboardBuilder()
    
    if is_active:
        builder.row(InlineKeyboardButton(text="⏸ Остановить", callback_data=f"stop_{profile_id}"))
    else:
        builder.row(InlineKeyboardButton(text="▶️ Запустить", callback_data=f"start_{profile_id}"))
    
    builder.row(
        InlineKeyboardButton(text="📥 Источники", callback_data=f"sources_{profile_id}"),
        InlineKeyboardButton(text="📤 Получатели", callback_data=f"targets_{profile_id}"),
        InlineKeyboardButton(text="⏰ Время", callback_data=f"schedule_{profile_id}")
    )
    builder.row(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{profile_id}"))
    builder.row(InlineKeyboardButton(text="🔙 К списку", callback_data="profiles_menu"))
    return builder.as_markup()


def get_confirmation_keyboard(profile_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm_{profile_id}"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_settings")
    )
    return builder.as_markup()


async def extract_channel_id(channel_input: str) -> int:
    channel_input = channel_input.strip()
    if channel_input.lstrip('-').isdigit():
        return int(channel_input)
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        chat = await bot.get_chat(f"@{username}")
        return chat.id
    raise ValueError("Неверный формат")


async def copy_post(target_channel: int, message: types.Message):
    try:
        if message.photo:
            await bot.send_photo(chat_id=target_channel, photo=message.photo[-1].file_id, caption=message.caption or "")
        elif message.video:
            await bot.send_video(chat_id=target_channel, video=message.video.file_id, caption=message.caption or "")
        elif message.document:
            await bot.send_document(chat_id=target_channel, document=message.document.file_id, caption=message.caption or "")
        elif message.text:
            await bot.send_message(chat_id=target_channel, text=message.text)
        return True
    except Exception as e:
        logger.error(f"Ошибка копирования: {e}")
        return False


# ============ ОБРАБОТЧИК НОВЫХ ПОСТОВ В КАНАЛАХ ============
@dp.channel_post()
async def handle_new_post(message: types.Message):
    """Автоматическое копирование новых постов (НЕ влияет на команды /start)"""
    source_id = message.chat.id
    logger.info(f"Новый пост в канале {source_id}")
    
    profiles = get_profiles(OWNER_ID)
    
    for profile in profiles:
        if not profile['is_active']:
            continue
        
        schedule_date = profile.get('schedule_date')
        if schedule_date:
            try:
                start_date = datetime.strptime(schedule_date, "%Y-%m-%d")
                if datetime.now() < start_date:
                    continue
            except:
                pass
        
        if source_id in profile['source_channels']:
            for target in profile['target_channels']:
                if not is_post_copied(profile['id'], source_id, message.message_id):
                    await copy_post(target, message)
                    mark_post_copied(profile['id'], source_id, message.message_id)
                    logger.info(f"Скопирован пост для профиля {profile['name']}")


# ============ КОМАНДЫ И КОЛБЭКИ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    logger.info(f"Команда /start от {message.from_user.id}")
    await message.answer(
        "🤖 <b>Бот для автоматического копирования контента</b>\n\n"
        "📌 Нажмите 'Управление профилями' для настройки\n"
        "⚠️ Бот должен быть администратором всех каналов",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "🤖 <b>Бот для автоматического копирования контента</b>\n\n"
        "📌 Нажмите 'Управление профилями' для настройки",
        parse_mode="HTML",
        reply_markup=get_main_menu()
    )
    await callback.answer()


@dp.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    help_text = """
📖 <b>Помощь</b>

1️⃣ <b>Создание профиля</b>
   - Нажмите "Управление профилями"
   - Выберите "Создать профиль"

2️⃣ <b>Настройка</b>
   - Источники (откуда копировать)
   - Получатели (куда копировать)
   - Дата начала (сегодня/завтра/ДД.ММ.ГГГГ)

3️⃣ <b>Управление</b>
   - ▶️ Запустить профиль
   - ⏸ Остановить профиль
   - 🗑 Удалить профиль
"""
    await callback.message.edit_text(help_text, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "profiles_menu")
async def profiles_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    profiles = get_profiles(user_id)
    text = f"📁 <b>Управление профилями</b>\n\nУ вас {len(profiles)} профиль(ей)"
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profiles_menu(user_id))
    await callback.answer()


@dp.callback_query(F.data == "create_profile")
async def create_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_for_name)
    await callback.message.edit_text(
        "🆕 <b>Создание профиля</b>\n\nВведите название профиля:\n\n❌ /cancel - отмена",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(ProfileStates.waiting_for_name)
async def process_profile_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text.strip()
    
    existing = get_profiles(user_id)
    if len(existing) >= 10:
        await message.answer("❌ Достигнут лимит профилей (максимум 10)")
        await state.clear()
        return
    
    profile_id = create_profile(user_id, name)
    temp_settings[user_id] = {"profile_id": profile_id, "name": name}
    
    await message.answer(f"✅ Профиль \"{name}\" создан!\n\nТеперь настройте источники (откуда копировать):")
    await state.set_state(ProfileStates.waiting_for_sources)
    await message.answer(
        "📢 <b>Настройка источников</b>\n\n"
        "Отправьте ссылки на каналы-источники (каждый с новой строки)\n"
        "Когда закончите - напишите <code>готово</code>\n\n"
        "❌ /cancel - отмена",
        parse_mode="HTML"
    )


@dp.callback_query(F.data.startswith("profile_"))
async def open_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    if not profile:
        await callback.answer("Профиль не найден")
        return
    
    sources_count = len(profile['source_channels'])
    targets_count = len(profile['target_channels'])
    schedule = profile.get('schedule_date') or "сегодня"
    
    text = f"""
⚙️ <b>Настройки профиля "{profile['name']}"</b>

📥 Источников: {sources_count}
📤 Получателей: {targets_count}
⏰ Начало: {schedule}
"""
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profile_actions(profile_id, profile['is_active'], profile['name']))
    await callback.answer()


@dp.callback_query(F.data.startswith("sources_"))
async def edit_sources(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_sources)
    await callback.message.answer(
        f"📢 <b>Настройка источников - профиль \"{profile['name']}\"</b>\n\n"
        "Отправьте ссылки на каналы-источники (каждый с новой строки)\n"
        "Когда закончите - напишите <code>готово</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("targets_"))
async def edit_targets(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_targets)
    await callback.message.answer(
        f"📢 <b>Настройка получателей - профиль \"{profile['name']}\"</b>\n\n"
        "Отправьте ссылки на каналы-получатели (каждый с новой строки)\n"
        "Когда закончите - напишите <code>готово</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("schedule_"))
async def edit_schedule(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_schedule)
    await callback.message.answer(
        f"⏰ <b>Настройка времени - профиль \"{profile['name']}\"</b>\n\n"
        "Отправьте дату в одном из форматов:\n"
        "• <code>сегодня</code> - начать сразу\n"
        "• <code>завтра</code> - начать завтра\n"
        "• <code>25.12.2024</code> - конкретная дата",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(ProfileStates.waiting_for_sources)
async def process_sources(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = temp_settings.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        if profile_id:
            profile = get_profile(profile_id)
            if profile and profile['source_channels']:
                await state.set_state(ProfileStates.waiting_for_targets)
                await message.answer(
                    f"📢 <b>Настройка получателей - профиль \"{name}\"</b>\n\n"
                    "Отправьте ссылки на каналы-получатели\n"
                    "Когда закончите - напишите <code>готово</code>",
                    parse_mode="HTML"
                )
            else:
                await message.answer("❌ Добавьте хотя бы один канал-источник")
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer(f"✅ Добавлен источник")
            except Exception as e:
                await message.answer(f"❌ Ошибка: {e}")
    
    if channels and profile_id:
        update_profile_sources(profile_id, channels)


@dp.message(ProfileStates.waiting_for_targets)
async def process_targets(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = temp_settings.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        if profile_id:
            profile = get_profile(profile_id)
            if profile and profile['target_channels']:
                await state.set_state(ProfileStates.waiting_for_schedule)
                await message.answer(
                    f"⏰ <b>Настройка времени - профиль \"{name}\"</b>\n\n"
                    "Отправьте дату:\n• <code>сегодня</code>\n• <code>завтра</code>\n• <code>25.12.2024</code>",
                    parse_mode="HTML"
                )
            else:
                await message.answer("❌ Добавьте хотя бы один канал-получатель")
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer(f"✅ Добавлен получатель")
            except Exception as e:
                await message.answer(f"❌ Ошибка: {e}")
    
    if channels and profile_id:
        update_profile_targets(profile_id, channels)


@dp.message(ProfileStates.waiting_for_schedule)
async def process_schedule(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = temp_settings.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    schedule_text = message.text.strip().lower()
    
    try:
        if schedule_text == "сегодня":
            schedule_date = datetime.now().strftime("%Y-%m-%d")
            display = "сегодня"
        elif schedule_text == "завтра":
            schedule_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            display = "завтра"
        else:
            schedule_date = datetime.strptime(schedule_text, "%d.%m.%Y").strftime("%Y-%m-%d")
            display = schedule_text
        
        if profile_id:
            update_profile_schedule(profile_id, schedule_date)
            profile = get_profile(profile_id)
            
            text = f"""
📋 <b>Подтвердите настройки профиля "{name}"</b>

📥 Источников: {len(profile['source_channels'])}
📤 Получателей: {len(profile['target_channels'])}
⏰ Начало: {display}

✅ Всё верно?
"""
            await state.set_state(ProfileStates.waiting_for_confirm)
            await message.answer(text, parse_mode="HTML", reply_markup=get_confirmation_keyboard(profile_id))
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: сегодня, завтра или ДД.ММ.ГГГГ")


@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_settings(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    if profile:
        update_profile_active(profile_id, 1)
        await callback.message.edit_text(
            f"✅ <b>Настройки профиля \"{profile['name']}\" сохранены!</b>\n\n"
            f"📥 {len(profile['source_channels'])} источников → 📤 {len(profile['target_channels'])} получателей\n\n"
            f"🔄 Бот копирует новые посты",
            parse_mode="HTML"
        )
        await callback.message.answer("Меню:", reply_markup=get_main_menu())
    
    await state.clear()
    if callback.from_user.id in temp_settings:
        del temp_settings[callback.from_user.id]
    await callback.answer()


@dp.callback_query(F.data == "cancel_settings")
async def cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in temp_settings:
        del temp_settings[callback.from_user.id]
    await callback.message.edit_text("❌ Действие отменено", parse_mode="HTML")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("start_"))
async def start_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    update_profile_active(profile_id, 1)
    await callback.answer("✅ Профиль запущен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    update_profile_active(profile_id, 0)
    await callback.answer("⏸ Профиль остановлен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("delete_"))
async def delete_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = get_profile(profile_id)
    
    if profile:
        name = profile['name']
        delete_profile(profile_id)
        await callback.message.edit_text(f"🗑 Профиль \"{name}\" удален", parse_mode="HTML")
        await callback.message.answer("Меню:", reply_markup=get_main_menu())
    
    await callback.answer()


@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await message.answer("❌ Действие отменено", reply_markup=get_main_menu())
    else:
        await message.answer("❌ Нет активных действий")


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print("=" * 50)
    print(f"🚀 Бот запущен: @{me.username}")
    print(f"👑 Владелец: {OWNER_ID}")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
