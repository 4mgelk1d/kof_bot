import asyncio
import logging
import re
from datetime import datetime
from typing import Dict, List, Any
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
import os

# ===== НАСТРОЙКИ - ОБЯЗАТЕЛЬНО ЗАМЕНИ =====
BOT_TOKEN = "8924285335:AAFdPfErLdSSi9a2soS8_LaazeUWTK1mH00"  # Токен бота
ADMIN_ID = 5584463063  # Твой Telegram ID
# =========================================

# Telethon будет использовать эти значения по умолчанию (работает без регистрации приложения)
# Если не работают - библиотека сама предложит ввести API ID при первом запуске
API_ID = 2040  # Стандартный ID (работает)
API_HASH = "b18441a1ff607e10a989891a5462e627"  # Стандартный Hash (работает)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Telethon клиент (для чтения истории каналов)
telethon_client = TelegramClient('bot_session', API_ID, API_HASH)

# Состояния FSM
class CopyStates(StatesGroup):
    waiting_for_source_channels = State()
    waiting_for_start_date = State()
    waiting_for_target_channels = State()
    waiting_for_confirmation = State()

# Хранилище данных пользователя
user_temp_data: Dict[int, Dict[str, Any]] = {}

def init_user_session(user_id: int):
    """Инициализация сессии пользователя"""
    if user_id not in user_temp_data:
        user_temp_data[user_id] = {
            "source_channels": [],
            "target_channels": [],
            "target_websites": [],
            "start_date": None,
            "task_id": None
        }

def get_main_menu():
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Новое копирование", callback_data="new_copy"),
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )
    return builder.as_markup()

def get_confirmation_keyboard():
    """Клавиатура подтверждения"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_no")
    )
    return builder.as_markup()

async def extract_channel_id(channel_input: str) -> int:
    """Извлекает ID канала из ссылки"""
    channel_input = channel_input.strip()
    
    # Если это уже число
    if channel_input.lstrip('-').isdigit():
        return int(channel_input)
    
    # Если это ссылка
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id
        except Exception as e:
            raise ValueError(f"Не удалось найти канал @{username}")
    
    raise ValueError("Неверный формат ссылки или ID канала")

async def copy_post_to_channel(target_channel_id: int, message):
    """Копирует пост в канал"""
    try:
        if hasattr(message, 'photo') and message.photo:
            # Получаем фото в максимальном качестве
            photo = message.photo[-1]
            file = await telethon_client.download_file(photo, bytes)
            # Отправляем через бота
            await bot.send_photo(
                chat_id=target_channel_id,
                photo=photo.id,
                caption=message.text or ""
            )
        elif hasattr(message, 'video') and message.video:
            await bot.send_video(
                chat_id=target_channel_id,
                video=message.video.id,
                caption=message.text or ""
            )
        elif hasattr(message, 'document') and message.document:
            await bot.send_document(
                chat_id=target_channel_id,
                document=message.document.id,
                caption=message.text or ""
            )
        else:
            # Текстовое сообщение
            await bot.send_message(
                chat_id=target_channel_id,
                text=message.text or "📝 Пост без текста"
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка копирования: {e}")
        return False

async def get_channel_messages(channel_id: int, start_date: datetime, limit: int = 500):
    """Получение сообщений из канала через Telethon"""
    try:
        # Получаем сущность канала
        entity = await telethon_client.get_entity(channel_id)
        
        # Получаем сообщения
        messages = []
        async for message in telethon_client.iter_messages(
            entity, 
            limit=limit,
            offset_date=start_date
        ):
            if message.date.replace(tzinfo=None) >= start_date:
                messages.append(message)
        
        return messages
    except Exception as e:
        logger.error(f"Ошибка получения сообщений из {channel_id}: {e}")
        return []

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    welcome_text = """
🤖 <b>Бот для копирования контента из Telegram каналов</b>

📢 <b>Важное предупреждение:</b>
Бот должен быть <b>администратором ВСЕХ каналов</b>:
• Из которых нужно копировать контент
• В которые нужно отправлять контент

🔧 <b>Функционал:</b>
• Копирование истории постов из каналов
• Отправка в несколько каналов одновременно
• Выбор даты начала копирования

📋 <b>Как использовать:</b>
Нажмите кнопку "Новое копирование" или введите /send

⚠️ <b>Важно:</b> При первом запуске бот попросит ввести номер телефона и код из Telegram для авторизации Telethon. Это нужно для чтения истории каналов.
"""
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@dp.message(Command("send"))
async def cmd_send(message: types.Message, state: FSMContext):
    """Начало процесса копирования"""
    user_id = message.from_user.id
    init_user_session(user_id)
    
    await state.set_state(CopyStates.waiting_for_source_channels)
    await message.answer(
        "📢 <b>Шаг 1/3: Откуда копировать?</b>\n\n"
        "Отправьте мне ссылки или ID каналов, с которых нужно скопировать контент.\n"
        "Каждый канал с новой строки.\n\n"
        "<b>Пример:</b>\n"
        "- https://t.me/channel_name\n"
        "- -1001234567890\n\n"
        "Когда закончите, отправьте слово <code>готово</code>",
        parse_mode="HTML"
    )

@dp.message(CopyStates.waiting_for_source_channels)
async def process_source_channels(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    init_user_session(user_id)
    
    if message.text.lower() == "готово":
        if not user_temp_data[user_id].get("source_channels"):
            await message.answer("❌ Вы не добавили ни одного канала. Пожалуйста, добавьте хотя бы один канал.")
            return
        
        await state.set_state(CopyStates.waiting_for_start_date)
        await message.answer(
            "📅 <b>Шаг 2/3: С какой даты брать контент?</b>\n\n"
            "Укажите дату в формате <code>ДД.ММ.ГГГГ</code>\n"
            "Например: <code>01.01.2024</code>\n\n"
            "Все посты после указанной даты будут скопированы.",
            parse_mode="HTML"
        )
    else:
        channels = message.text.strip().split('\n')
        for ch in channels:
            if ch.strip():
                try:
                    channel_id = await extract_channel_id(ch)
                    user_temp_data[user_id]["source_channels"].append(channel_id)
                    await message.answer(f"✅ Добавлен канал: {ch}")
                except ValueError as e:
                    await message.answer(f"❌ Ошибка: {e}\nПропускаю: {ch}")

@dp.message(CopyStates.waiting_for_start_date)
async def process_start_date(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    init_user_session(user_id)
    
    try:
        if message.text.lower() == "сегодня":
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = datetime.strptime(message.text.strip(), "%d.%m.%Y")
        
        user_temp_data[user_id]["start_date"] = start_date
        
        await state.set_state(CopyStates.waiting_for_target_channels)
        await message.answer(
            "📢 <b>Шаг 3/3: Куда копировать?</b>\n\n"
            "Отправьте мне ссылки или ID каналов, куда нужно копировать контент.\n"
            "Каждый канал с новой строки.\n\n"
            "Когда закончите, отправьте слово <code>готово</code>",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ или 'сегодня'")

@dp.message(CopyStates.waiting_for_target_channels)
async def process_target_channels(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    init_user_session(user_id)
    
    if message.text.lower() == "готово":
        if not user_temp_data[user_id].get("target_channels"):
            await message.answer("❌ Вы не добавили ни одного канала-получателя. Пожалуйста, добавьте хотя бы один канал.")
            return
        
        # Показываем сводку
        source_list = "\n".join([str(ch) for ch in user_temp_data[user_id]["source_channels"]])
        target_list = "\n".join([str(ch) for ch in user_temp_data[user_id]["target_channels"]])
        start_date_str = user_temp_data[user_id]["start_date"].strftime("%d.%m.%Y")
        
        summary = f"""
📋 <b>Сводка для копирования</b>

📍 <b>Источники ({len(user_temp_data[user_id]['source_channels'])}):</b>
<code>{source_list}</code>

🎯 <b>Каналы-получатели ({len(user_temp_data[user_id]['target_channels'])}):</b>
<code>{target_list}</code>

📅 <b>Дата начала:</b> {start_date_str}

⚠️ <b>Важно:</b> Бот скопирует ВСЕ посты с указанной даты
        """
        
        await state.set_state(CopyStates.waiting_for_confirmation)
        await message.answer(summary, parse_mode="HTML", reply_markup=get_confirmation_keyboard())
    else:
        targets = message.text.strip().split('\n')
        for target in targets:
            if target.strip():
                try:
                    channel_id = await extract_channel_id(target)
                    user_temp_data[user_id]["target_channels"].append(channel_id)
                    await message.answer(f"✅ Добавлен канал-получатель: {target}")
                except ValueError as e:
                    await message.answer(f"❌ Ошибка: {e}\nПропускаю: {target}")

@dp.callback_query(F.data == "confirm_yes")
async def confirm_yes(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = user_temp_data.get(user_id)
    
    if not data:
        await callback.message.answer("❌ Сессия не найдена. Начните заново командой /send")
        return
    
    await callback.message.edit_text("🔄 <b>Начинаю получение истории каналов...</b>", parse_mode="HTML")
    
    # Проверяем авторизацию Telethon
    if not telethon_client.is_connected():
        await callback.message.answer("⚠️ Подключаюсь к Telethon...")
        await telethon_client.connect()
    
    if not await telethon_client.is_user_authorized():
        await callback.message.answer(
            "🔐 <b>Требуется авторизация Telethon</b>\n\n"
            "Это нужно для чтения истории каналов.\n"
            "Пожалуйста, отправьте код, который придет в Telegram.\n\n"
            "Введите номер телефона в формате: +71234567890",
            parse_mode="HTML"
        )
        # Ждем ввода номера телефона
        # Для простоты рекомендую сначала авторизоваться отдельно
        await callback.message.answer("Пожалуйста, выполните авторизацию отдельно. Напишите /auth")
        return
    
    total_posts = 0
    success_count = 0
    
    await callback.message.answer("📥 <b>Начинаю копирование постов...</b>", parse_mode="HTML")
    
    for source_channel in data["source_channels"]:
        await callback.message.answer(f"📖 Читаю канал: {source_channel}")
        
        messages = await get_channel_messages(source_channel, data["start_date"])
        
        await callback.message.answer(f"📊 Найдено {len(messages)} постов в канале")
        
        for msg in messages:
            for target_channel in data["target_channels"]:
                if await copy_post_to_channel(target_channel, msg):
                    success_count += 1
            total_posts += 1
            
            # Показываем прогресс каждые 10 постов
            if total_posts % 10 == 0:
                await callback.message.answer(f"⏳ Прогресс: {total_posts} постов обработано")
    
    result_text = f"""
✅ <b>Копирование завершено!</b>

📊 <b>Статистика:</b>
• Постов обработано: {total_posts}
• Успешных отправок: {success_count}
• Каналов-источников: {len(data['source_channels'])}
• Каналов-получателей: {len(data['target_channels'])}
    """
    
    await callback.message.answer(result_text, parse_mode="HTML", reply_markup=get_main_menu())
    del user_temp_data[user_id]
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "confirm_no")
async def confirm_no(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in user_temp_data:
        del user_temp_data[user_id]
    
    await callback.message.edit_text("❌ <b>Операция отменена</b>", parse_mode="HTML")
    await callback.message.answer("Можете начать заново командой /send", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "new_copy")
async def new_copy(callback: types.CallbackQuery, state: FSMContext):
    await cmd_send(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

@dp.message(Command("auth"))
async def auth_telethon(message: types.Message):
    """Команда для авторизации Telethon"""
    await message.answer(
        "🔐 <b>Авторизация Telethon</b>\n\n"
        "Это нужно для чтения истории каналов.\n\n"
        "Отправьте ваш номер телефона в формате:\n"
        "<code>+71234567890</code>\n\n"
        "Или нажмите /skip если хотите пропустить (копирование истории не будет работать)",
        parse_mode="HTML"
    )
    # Здесь нужно реализовать авторизацию

async def main():
    """Запуск бота"""
    print("🚀 Запуск бота...")
    print(f"🤖 Бот: @{ (await bot.get_me()).username }")
    print(f"👤 Админ ID: {ADMIN_ID}")
    print("⚠️ Telethon будет запрашивать авторизацию при первом использовании")
    
    # Подключаем Telethon
    await telethon_client.connect()
    
    if not await telethon_client.is_user_authorized():
        print("⚠️ Telethon не авторизован! Используйте команду /auth в боте")
    else:
        print("✅ Telethon авторизован")
    
    # Запускаем бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
