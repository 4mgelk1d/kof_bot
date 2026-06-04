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

# ===== НАСТРОЙКИ - ОБЯЗАТЕЛЬНО ЗАМЕНИ =====
BOT_TOKEN = "8924285335:AAFdPfErLdSSi9a2soS8_LaazeUWTK1mH00"
ADMIN_ID = 5584463063
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище настроек пользователя
user_settings: Dict[int, Dict[str, any]] = {}

# Состояния FSM
class SetupStates(StatesGroup):
    waiting_for_source_channels = State()
    waiting_for_target_channels = State()
    waiting_for_schedule = State()
    waiting_for_confirm = State()


def get_main_menu():
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Новое копирование", callback_data="setup"),
        InlineKeyboardButton(text="📊 Мои настройки", callback_data="my_settings"),
        InlineKeyboardButton(text="🗑 Очистить настройки", callback_data="clear_settings")
    )
    return builder.as_markup()


def get_schedule_keyboard():
    """Клавиатура выбора времени отправки"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 Отправить сейчас", callback_data="schedule_now"),
        InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule_later")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_targets")
    )
    return builder.as_markup()


def get_confirmation_keyboard():
    """Клавиатура подтверждения"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить и запустить", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_no"),
        InlineKeyboardButton(text="⏰ Изменить время", callback_data="change_schedule")
    )
    return builder.as_markup()


async def extract_channel_info(channel_input: str) -> tuple:
    """Извлекает ID и username канала из ссылки"""
    channel_input = channel_input.strip()
    
    # Если уже ID
    if channel_input.lstrip('-').isdigit():
        channel_id = int(channel_input)
        try:
            chat = await bot.get_chat(channel_id)
            username = chat.username
            return channel_id, username
        except:
            return channel_id, None
    
    # Если ссылка
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id, username
        except Exception as e:
            raise ValueError(f"Не удалось найти канал @{username}")
    
    raise ValueError("Неверный формат")


async def copy_album_to_channel(target_channel_id: int, messages: List[types.Message], caption: str = ""):
    """Копирует альбом (несколько фото/видео) в канал, сохраняя группировку"""
    try:
        media_group = []
        for msg in messages:
            if msg.photo:
                media_group.append(InputMediaPhoto(
                    media=msg.photo[-1].file_id,
                    caption=caption if msg == messages[0] else ""
                ))
            elif msg.video:
                media_group.append(InputMediaVideo(
                    media=msg.video.file_id,
                    caption=caption if msg == messages[0] else ""
                ))
        
        if media_group:
            await bot.send_media_group(chat_id=target_channel_id, media=media_group)
            return True
    except Exception as e:
        logger.error(f"Ошибка копирования альбома: {e}")
        return False


async def copy_single_post_to_channel(target_channel_id: int, message: types.Message):
    """Копирует одиночный пост"""
    try:
        if message.photo:
            await bot.send_photo(
                chat_id=target_channel_id,
                photo=message.photo[-1].file_id,
                caption=message.caption or ""
            )
        elif message.video:
            await bot.send_video(
                chat_id=target_channel_id,
                video=message.video.file_id,
                caption=message.caption or ""
            )
        elif message.document:
            await bot.send_document(
                chat_id=target_channel_id,
                document=message.document.file_id,
                caption=message.caption or ""
            )
        elif message.audio:
            await bot.send_audio(
                chat_id=target_channel_id,
                audio=message.audio.file_id,
                caption=message.caption or ""
            )
        elif message.text:
            await bot.send_message(
                chat_id=target_channel_id,
                text=message.text
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка копирования: {e}")
        return False


async def copy_post_with_album_check(target_channel_id: int, message: types.Message):
    """Копирует пост с проверкой на альбом"""
    if message.media_group_id:
        return await copy_album_to_channel(target_channel_id, [message], message.caption or "")
    else:
        return await copy_single_post_to_channel(target_channel_id, message)


@dp.channel_post()
async def handle_new_post(message: types.Message):
    """Автоматически копирует новые посты из отслеживаемых каналов"""
    source_channel_id = message.chat.id
    
    for user_id, settings in user_settings.items():
        if source_channel_id in settings.get("source_channels", []):
            # Проверяем расписание
            schedule_time = settings.get("schedule_time")
            if schedule_time and datetime.now() < schedule_time:
                continue
            
            for target_channel in settings.get("target_channels", []):
                await copy_post_with_album_check(target_channel, message)
                logger.info(f"Скопирован пост из {source_channel_id} в {target_channel}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = """
🤖 <b>Бот для автоматического копирования контента</b>

📢 <b>Как работает:</b>
1. Нажмите кнопку "Новое копирование"
2. Укажите канал источник (откуда копировать)
3. Укажите каналы получатели (куда копировать)
4. Выберите время отправки (сейчас или отложить)

✅ <b>Возможности:</b>
• Работа с приватными каналами
• Отложенная публикация
• Копирование без отметки "переслано"

⚠️ <b>Важно:</b>
• Бот должен быть АДМИНИСТРАТОМ всех каналов
"""
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())


@dp.callback_query(F.data == "setup")
async def setup_callback(callback: types.CallbackQuery, state: FSMContext):
    """Начало настройки копирования"""
    user_id = callback.from_user.id
    
    if user_id not in user_settings:
        user_settings[user_id] = {
            "source_channels": [],
            "source_channel_info": [],
            "target_channels": [],
            "target_channel_info": [],
            "schedule_time": None
        }
    
    await state.set_state(SetupStates.waiting_for_source_channels)
    await callback.message.answer(
        "📢 <b>Шаг 1/3: Откуда копировать?</b>\n\n"
        "Отправьте ссылки на каналы-источники (каждый с новой строки)\n\n"
        "<b>Пример:</b>\n"
        "https://t.me/test1\n"
        "https://t.me/test2\n"
        "-1001234567890\n\n"
        "Когда закончите, отправьте слово <code>готово</code>\n\n"
        "🔓 <b>Для приватных каналов:</b> просто добавьте бота в канал админом и укажите ID канала",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(SetupStates.waiting_for_source_channels)
async def process_source_channels(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.text.lower() == "готово":
        if not user_settings[user_id].get("source_channels"):
            await message.answer("❌ Добавьте хотя бы один канал-источник")
            return
        
        await state.set_state(SetupStates.waiting_for_target_channels)
        await message.answer(
            "📢 <b>Шаг 2/3: Куда копировать?</b>\n\n"
            "Отправьте ссылки на каналы-получатели (каждый с новой строки)\n\n"
            "Когда закончите, отправьте слово <code>готово</code>",
            parse_mode="HTML"
        )
    else:
        channels = message.text.strip().split('\n')
        for ch in channels:
            if ch.strip():
                try:
                    channel_id, username = await extract_channel_info(ch)
                    if channel_id not in user_settings[user_id]["source_channels"]:
                        user_settings[user_id]["source_channels"].append(channel_id)
                        user_settings[user_id]["source_channel_info"].append({
                            "id": channel_id,
                            "username": username,
                            "link": ch
                        })
                        await message.answer(f"✅ Источник добавлен: {ch}")
                    else:
                        await message.answer(f"ℹ️ Канал уже добавлен: {ch}")
                except ValueError as e:
                    await message.answer(f"❌ {e}")


@dp.message(SetupStates.waiting_for_target_channels)
async def process_target_channels(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.text.lower() == "готово":
        if not user_settings[user_id].get("target_channels"):
            await message.answer("❌ Добавьте хотя бы один канал-получатель")
            return
        
        await state.set_state(SetupStates.waiting_for_schedule)
        await message.answer(
            "⏰ <b>Шаг 3/3: Время отправки</b>\n\n"
            "Выберите, когда отправлять посты:",
            parse_mode="HTML",
            reply_markup=get_schedule_keyboard()
        )
    else:
        targets = message.text.strip().split('\n')
        for target in targets:
            if target.strip():
                try:
                    channel_id, username = await extract_channel_info(target)
                    if channel_id not in user_settings[user_id]["target_channels"]:
                        user_settings[user_id]["target_channels"].append(channel_id)
                        user_settings[user_id]["target_channel_info"].append({
                            "id": channel_id,
                            "username": username,
                            "link": target
                        })
                        await message.answer(f"✅ Получатель добавлен: {target}")
                except ValueError as e:
                    await message.answer(f"❌ {e}")


@dp.callback_query(F.data == "schedule_now")
async def schedule_now(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    user_settings[user_id]["schedule_time"] = None
    await show_confirmation(callback.message, user_id, state)
    await callback.answer()


@dp.callback_query(F.data == "schedule_later")
async def schedule_later(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "⏰ <b>Укажите время отправки</b>\n\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Например: <code>25.12.2024 14:30</code>\n\n"
        "Или через сколько часов: <code>+2</code> (через 2 часа)\n"
        "Или: <code>+30</code> (через 30 минут)",
        parse_mode="HTML"
    )
    await state.set_state(SetupStates.waiting_for_schedule)
    await callback.answer()


@dp.message(SetupStates.waiting_for_schedule)
async def process_schedule(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    schedule_text = message.text.strip()
    
    try:
        if schedule_text.startswith('+'):
            # Относительное время
            hours = int(schedule_text[1:])
            schedule_time = datetime.now() + timedelta(hours=hours)
        elif ':' in schedule_text:
            # Абсолютное время
            schedule_time = datetime.strptime(schedule_text, "%d.%m.%Y %H:%M")
        else:
            raise ValueError("Неверный формат")
        
        if schedule_time <= datetime.now():
            await message.answer("❌ Время должно быть в будущем!")
            return
        
        user_settings[user_id]["schedule_time"] = schedule_time
        await show_confirmation(message, user_id, state)
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ или +часы")


async def show_confirmation(message: types.Message, user_id: int, state: FSMContext):
    """Показать подтверждение настроек"""
    settings = user_settings[user_id]
    
    source_list = "\n".join([f"• {info['link']}" for info in settings["source_channel_info"]])
    target_list = "\n".join([f"• {info['link']}" for info in settings["target_channel_info"]])
    
    schedule_text = "Сразу (сейчас)" if not settings["schedule_time"] else settings["schedule_time"].strftime("%d.%m.%Y %H:%M")
    
    summary = f"""
📋 <b>Подтвердите настройки</b>

📍 <b>Источники ({len(settings['source_channels'])}):</b>
{source_list}

🎯 <b>Получатели ({len(settings['target_channels'])}):</b>
{target_list}

⏰ <b>Время отправки:</b> {schedule_text}

✅ <b>Всё верно?</b>
    """
    
    await state.set_state(SetupStates.waiting_for_confirm)
    await message.answer(summary, parse_mode="HTML", reply_markup=get_confirmation_keyboard())


@dp.callback_query(F.data == "change_schedule")
async def change_schedule(callback: types.CallbackQuery, state: FSMContext):
    await schedule_later(callback, state)


@dp.callback_query(F.data == "back_to_targets")
async def back_to_targets(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(SetupStates.waiting_for_target_channels)
    await callback.message.answer(
        "📢 <b>Шаг 2/3: Куда копировать?</b>\n\n"
        "Отправьте ссылки на каналы-получатели\n\n"
        "Когда закончите, отправьте слово <code>готово</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data == "confirm_yes")
async def confirm_settings(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    settings = user_settings[user_id]
    
    schedule_text = "сразу" if not settings["schedule_time"] else settings["schedule_time"].strftime("%d.%m.%Y %H:%M")
    
    await callback.message.edit_text(
        f"✅ <b>Настройки сохранены!</b>\n\n"
        f"📊 {len(settings['source_channels'])} источников → {len(settings['target_channels'])} получателей\n"
        f"⏰ Время отправки: {schedule_text}\n\n"
        f"🔄 Бот копирует новые посты в реальном времени",
        parse_mode="HTML"
    )
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "confirm_no")
async def cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text("❌ Настройки отменены")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "my_settings")
async def show_settings(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in user_settings or not user_settings[user_id].get("source_channels"):
        await callback.message.edit_text(
            "📋 <b>Нет активных настроек</b>\n\n"
            "Нажмите 'Новое копирование' чтобы настроить бота",
            parse_mode="HTML"
        )
    else:
        settings = user_settings[user_id]
        
        # Формируем список источников со ссылками
        source_links = []
        for info in settings.get("source_channel_info", []):
            if info.get("username"):
                source_links.append(f"• <a href='https://t.me/{info['username']}'>{info['link']}</a>")
            else:
                source_links.append(f"• {info['link']}")
        
        # Формируем список получателей со ссылками
        target_links = []
        for info in settings.get("target_channel_info", []):
            if info.get("username"):
                target_links.append(f"• <a href='https://t.me/{info['username']}'>{info['link']}</a>")
            else:
                target_links.append(f"• {info['link']}")
        
        source_text = "\n".join(source_links) if source_links else "Нет"
        target_text = "\n".join(target_links) if target_links else "Нет"
        schedule_text = "Сразу" if not settings.get("schedule_time") else settings["schedule_time"].strftime("%d.%m.%Y %H:%M")
        
        text = f"""
📊 <b>Ваши настройки</b>

📍 <b>Источники ({len(settings['source_channels'])}):</b>
{source_text}

🎯 <b>Получатели ({len(settings['target_channels'])}):</b>
{target_text}

⏰ <b>Время отправки:</b> {schedule_text}
        """
        
        await callback.message.edit_text(text, parse_mode="HTML")
    
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "clear_settings")
async def clear_settings(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_settings:
        del user_settings[user_id]
    await callback.message.edit_text("🗑 Настройки удалены")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await callback.answer()


async def main():
    me = await bot.get_me()
    print("=" * 50)
    print(f"🚀 Бот запущен: @{me.username}")
    print("✅ Режим: копирование новых постов + альбомы + отложенная отправка")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
