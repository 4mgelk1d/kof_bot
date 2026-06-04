import asyncio
import logging
import re
from typing import Dict, List, Set
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== НАСТРОЙКИ - ОБЯЗАТЕЛЬНО ЗАМЕНИ =====
BOT_TOKEN = "8924285335:AAFdPfErLdSSi9a2soS8_LaazeUWTK1mH00"  # Токен бота
ADMIN_ID = 5584463063  # Твой Telegram ID
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Хранилище настроек пользователя
user_settings: Dict[int, Dict[str, List[int]]] = {}

# Состояния FSM для настройки
class SetupStates(StatesGroup):
    waiting_for_source_channels = State()
    waiting_for_target_channels = State()
    waiting_for_confirm = State()


def get_main_menu():
    """Главное меню"""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Настроить копирование", callback_data="setup"),
        InlineKeyboardButton(text="📊 Мои настройки", callback_data="my_settings"),
        InlineKeyboardButton(text="🗑 Очистить настройки", callback_data="clear_settings"),
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
    
    if channel_input.lstrip('-').isdigit():
        return int(channel_input)
    
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            return chat.id
        except Exception as e:
            raise ValueError(f"Не удалось найти канал @{username}")
    
    raise ValueError("Неверный формат ссылки или ID канала")


async def copy_post_to_channel(target_channel_id: int, message: types.Message):
    """Копирует пост в канал без информации об отправителе"""
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
        elif message.animation:
            await bot.send_animation(
                chat_id=target_channel_id,
                animation=message.animation.file_id,
                caption=message.caption or ""
            )
        elif message.text:
            await bot.send_message(
                chat_id=target_channel_id,
                text=message.text
            )
        elif message.sticker:
            await bot.send_sticker(
                chat_id=target_channel_id,
                sticker=message.sticker.file_id
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка копирования в канал {target_channel_id}: {e}")
        return False


# ============ ОБРАБОТЧИК НОВЫХ ПОСТОВ ============
@dp.channel_post()
async def handle_new_post(message: types.Message):
    """Автоматически копирует новые посты из отслеживаемых каналов"""
    source_channel_id = message.chat.id
    
    # Ищем пользователя, у которого этот канал в источниках
    for user_id, settings in user_settings.items():
        if source_channel_id in settings.get("source_channels", []):
            # Копируем во все целевые каналы этого пользователя
            for target_channel in settings.get("target_channels", []):
                await copy_post_to_channel(target_channel, message)
                logger.info(f"Скопирован пост из {source_channel_id} в {target_channel}")
            break


# ============ КОМАНДЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Приветственное сообщение"""
    welcome_text = """
🤖 <b>Бот для автоматического копирования контента из Telegram каналов</b>

📢 <b>Как это работает:</b>
1️⃣ Вы настраиваете каналы-источники (откуда копировать)
2️⃣ Вы настраиваете каналы-получатели (куда копировать)
3️⃣ Бот автоматически копирует ВСЕ НОВЫЕ посты в реальном времени

📢 <b>Важное предупреждение:</b>
Бот должен быть <b>администратором ВСЕХ каналов</b>:
• Из которых нужно копировать контент
• В которые нужно отправлять контент

⚠️ <b>Внимание:</b>
• Копируются ТОЛЬКО новые посты (после настройки)
• Старые посты (до настройки) скопировать НЕВОЗМОЖНО
• Это ограничение Telegram, а не бота

📋 <b>Как использовать:</b>
Нажмите кнопку "Настроить копирование" ниже
"""
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())


@dp.callback_query(F.data == "setup")
async def setup_copy(callback: types.CallbackQuery, state: FSMContext):
    """Настройка копирования"""
    user_id = callback.from_user.id
    
    if user_id not in user_settings:
        user_settings[user_id] = {"source_channels": [], "target_channels": []}
    
    await state.set_state(SetupStates.waiting_for_source_channels)
    await callback.message.answer(
        "📢 <b>Шаг 1/2: Откуда копировать?</b>\n\n"
        "Отправьте мне ссылки или ID каналов, с которых нужно копировать контент.\n"
        "Каждый канал с новой строки.\n\n"
        "<b>Пример:</b>\n"
        "- https://t.me/channel_name\n"
        "- -1001234567890\n\n"
        "Когда закончите, отправьте слово <code>готово</code>\n\n"
        "⚠️ <b>Важно:</b> Бот должен быть администратором этих каналов!",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(SetupStates.waiting_for_source_channels)
async def process_source_channels(message: types.Message, state: FSMContext):
    """Обработка каналов-источников"""
    user_id = message.from_user.id
    
    if user_id not in user_settings:
        user_settings[user_id] = {"source_channels": [], "target_channels": []}
    
    if message.text.lower() == "готово":
        if not user_settings[user_id].get("source_channels"):
            await message.answer("❌ Вы не добавили ни одного канала. Добавьте хотя бы один канал.")
            return
        
        await state.set_state(SetupStates.waiting_for_target_channels)
        await message.answer(
            "📢 <b>Шаг 2/2: Куда копировать?</b>\n\n"
            "Отправьте мне ссылки или ID каналов, куда нужно копировать контент.\n"
            "Каждый канал с новой строки.\n\n"
            "Когда закончите, отправьте слово <code>готово</code>\n\n"
            "⚠️ <b>Важно:</b> Бот должен быть администратором этих каналов!",
            parse_mode="HTML"
        )
    else:
        channels = message.text.strip().split('\n')
        for ch in channels:
            if ch.strip():
                try:
                    channel_id = await extract_channel_id(ch)
                    if channel_id not in user_settings[user_id]["source_channels"]:
                        user_settings[user_id]["source_channels"].append(channel_id)
                        await message.answer(f"✅ Добавлен канал-источник: {ch}")
                    else:
                        await message.answer(f"ℹ️ Канал уже добавлен: {ch}")
                except ValueError as e:
                    await message.answer(f"❌ Ошибка: {e}\nПропускаю: {ch}")


@dp.message(SetupStates.waiting_for_target_channels)
async def process_target_channels(message: types.Message, state: FSMContext):
    """Обработка каналов-получателей"""
    user_id = message.from_user.id
    
    if message.text.lower() == "готово":
        if not user_settings[user_id].get("target_channels"):
            await message.answer("❌ Вы не добавили ни одного канала-получателя.")
            return
        
        # Показываем сводку
        source_list = "\n".join([str(ch) for ch in user_settings[user_id]["source_channels"]])
        target_list = "\n".join([str(ch) for ch in user_settings[user_id]["target_channels"]])
        
        summary = f"""
📋 <b>Сводка настроек</b>

📍 <b>Каналы-источники ({len(user_settings[user_id]['source_channels'])}):</b>
<code>{source_list}</code>

🎯 <b>Каналы-получатели ({len(user_settings[user_id]['target_channels'])}):</b>
<code>{target_list}</code>

⚠️ <b>Важно:</b>
• Бот будет копировать ТОЛЬКО новые посты
• Бот должен быть администратором всех каналов
• Посты копируются без отметки "переслано"

✅ <b>Подтвердите настройки</b>
        """
        
        await state.set_state(SetupStates.waiting_for_confirm)
        await message.answer(summary, parse_mode="HTML", reply_markup=get_confirmation_keyboard())
    else:
        targets = message.text.strip().split('\n')
        for target in targets:
            if target.strip():
                try:
                    channel_id = await extract_channel_id(target)
                    if channel_id not in user_settings[user_id]["target_channels"]:
                        user_settings[user_id]["target_channels"].append(channel_id)
                        await message.answer(f"✅ Добавлен канал-получатель: {target}")
                    else:
                        await message.answer(f"ℹ️ Канал уже добавлен: {target}")
                except ValueError as e:
                    await message.answer(f"❌ Ошибка: {e}\nПропускаю: {target}")


@dp.callback_query(F.data == "confirm_yes")
async def confirm_settings(callback: types.CallbackQuery, state: FSMContext):
    """Подтверждение настроек"""
    user_id = callback.from_user.id
    
    result_text = f"""
✅ <b>Настройки сохранены!</b>

📊 <b>Настроено копирование:</b>
• Из {len(user_settings[user_id]['source_channels'])} каналов-источников
• В {len(user_settings[user_id]['target_channels'])} каналов-получателей

🔄 <b>Бот работает в автоматическом режиме</b>
• Все новые посты из каналов-источников будут скопированы
• Копирование происходит в реальном времени
"""
    
    await callback.message.edit_text(result_text, parse_mode="HTML")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "confirm_no")
async def cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    """Отмена настроек"""
    await callback.message.edit_text("❌ <b>Настройка отменена</b>", parse_mode="HTML")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "my_settings")
async def show_settings(callback: types.CallbackQuery):
    """Показать текущие настройки"""
    user_id = callback.from_user.id
    
    if user_id not in user_settings or not user_settings[user_id].get("source_channels"):
        await callback.message.edit_text(
            "📋 <b>У вас нет активных настроек</b>\n\n"
            "Нажмите 'Настроить копирование' для начала работы",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
        await callback.answer()
        return
    
    source_list = "\n".join([f"• {ch}" for ch in user_settings[user_id]["source_channels"]])
    target_list = "\n".join([f"• {ch}" for ch in user_settings[user_id]["target_channels"]])
    
    settings_text = f"""
📋 <b>Ваши текущие настройки</b>

📍 <b>Каналы-источники ({len(user_settings[user_id]['source_channels'])}):</b>
{source_list}

🎯 <b>Каналы-получатели ({len(user_settings[user_id]['target_channels'])}):</b>
{target_list}

🔄 <b>Статус:</b> Активен
    """
    
    await callback.message.edit_text(settings_text, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "clear_settings")
async def clear_settings(callback: types.CallbackQuery):
    """Очистить настройки"""
    user_id = callback.from_user.id
    
    if user_id in user_settings:
        del user_settings[user_id]
        await callback.message.edit_text(
            "🗑 <b>Все настройки удалены</b>\n\n"
            "Копирование остановлено",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
    else:
        await callback.message.edit_text(
            "📋 <b>У вас нет активных настроек</b>",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
    await callback.answer()


@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()


# ============ ЗАПУСК ============
async def main():
    """Запуск бота"""
    me = await bot.get_me()
    print("=" * 50)
    print(f"🚀 Бот запущен: @{me.username}")
    print(f"👤 Админ ID: {ADMIN_ID}")
    print("📢 Режим: Копирование НОВЫХ постов в реальном времени")
    print("=" * 50)
    
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
