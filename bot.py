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
OWNER_ID = 5584463063
ADMIN_IDS = [5584463063]
# =========================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_settings: Dict[int, Dict[str, any]] = {}
user_messages: Dict[int, Dict[str, any]] = {}

class SetupStates(StatesGroup):
    waiting_for_source_channels = State()
    waiting_for_target_channels = State()
    waiting_for_schedule = State()
    waiting_for_confirm = State()

class SupportStates(StatesGroup):
    waiting_for_admin_message = State()
    waiting_for_owner_reply = State()


def get_main_menu(user_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Новое копирование", callback_data="setup"),
        InlineKeyboardButton(text="📊 Мои настройки", callback_data="my_settings"),
        InlineKeyboardButton(text="🗑 Очистить настройки", callback_data="clear_settings")
    )
    if user_id in ADMIN_IDS or user_id == OWNER_ID:
        builder.row(
            InlineKeyboardButton(text="📞 Связь с владельцем", callback_data="contact_owner")
        )
    return builder.as_markup()


def get_schedule_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📤 Отправить сейчас", callback_data="schedule_now"),
        InlineKeyboardButton(text="⏰ Отложить", callback_data="schedule_later")
    )
    builder.row(
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_targets")
    )
    return builder.as_markup()


def get_schedule_back_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🔙 Назад к выбору времени", callback_data="back_to_schedule_menu")
    )
    return builder.as_markup()


def get_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить и запустить", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_no"),
        InlineKeyboardButton(text="⏰ Изменить время", callback_data="change_schedule")
    )
    return builder.as_markup()


def get_owner_reply_keyboard(admin_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✉️ Ответить", callback_data=f"reply_to_admin_{admin_id}")
    )
    return builder.as_markup()


async def extract_channel_info(channel_input: str) -> tuple:
    """Извлекает ID, username и красивую ссылку канала"""
    channel_input = channel_input.strip()
    
    # Если уже ID
    if channel_input.lstrip('-').isdigit():
        channel_id = int(channel_input)
        try:
            chat = await bot.get_chat(channel_id)
            username = chat.username
            if username:
                display_link = f"https://t.me/{username}"
            else:
                display_link = f"канал {channel_id}"
            return channel_id, username, display_link
        except:
            return channel_id, None, f"канал {channel_id}"
    
    # Если ссылка
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            display_link = f"https://t.me/{username}"
            return chat.id, username, display_link
        except Exception as e:
            raise ValueError(f"Не удалось найти канал @{username}")
    
    raise ValueError("Неверный формат")


async def copy_album_to_channel(target_channel_id: int, messages: List[types.Message], caption: str = ""):
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
    if message.media_group_id:
        return await copy_album_to_channel(target_channel_id, [message], message.caption or "")
    else:
        return await copy_single_post_to_channel(target_channel_id, message)


@dp.channel_post()
async def handle_new_post(message: types.Message):
    source_channel_id = message.chat.id
    
    for user_id, settings in user_settings.items():
        if source_channel_id in settings.get("source_channels", []):
            schedule_time = settings.get("schedule_time")
            if schedule_time and datetime.now() < schedule_time:
                continue
            
            for target_channel in settings.get("target_channels", []):
                await copy_post_with_album_check(target_channel, message)
                logger.info(f"Скопирован пост из {source_channel_id} в {target_channel}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    welcome_text = """
🤖 <b>Бот для автоматического копирования контента</b>

📢 <b>Как работает:</b>
1. Нажмите кнопку "Новое копирование"
2. Укажите каналы-источники (откуда копировать)
3. Укажите каналы-получатели (куда копировать)
4. Выберите время отправки (сейчас или отложить)
5. Бот автоматически копирует ВСЕ НОВЫЕ посты

✅ <b>Возможности:</b>
• Копирование альбомов
• Работа с приватными каналами
• Отложенная публикация
• Копирование без отметки "переслано"

⚠️ <b>Важно:</b>
• Бот должен быть АДМИНИСТРАТОМ всех каналов
• Копируются ТОЛЬКО новые посты
"""
    
    if user_id in ADMIN_IDS or user_id == OWNER_ID:
        welcome_text += "\n\n📞 <b>Администраторам:</b> Кнопка 'Связь с владельцем' доступна для связи."
    
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu(user_id))


@dp.callback_query(F.data == "setup")
async def setup_callback(callback: types.CallbackQuery, state: FSMContext):
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
        "https://t.me/channel1\n"
        "https://t.me/channel2\n"
        "-1001234567890\n\n"
        "Когда закончите, отправьте слово <code>готово</code>\n\n"
        "❌ Для отмены отправьте /cancel",
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
            "Когда закончите, отправьте слово <code>готово</code>\n\n"
            "❌ Для отмены отправьте /cancel",
            parse_mode="HTML"
        )
    else:
        channels = message.text.strip().split('\n')
        for ch in channels:
            if ch.strip():
                try:
                    channel_id, username, display_link = await extract_channel_info(ch)
                    if channel_id not in user_settings[user_id]["source_channels"]:
                        user_settings[user_id]["source_channels"].append(channel_id)
                        user_settings[user_id]["source_channel_info"].append({
                            "id": channel_id,
                            "username": username,
                            "link": display_link  # Сохраняем красивую ссылку
                        })
                        await message.answer(f"✅ Источник добавлен: {display_link}")
                    else:
                        await message.answer(f"ℹ️ Канал уже добавлен")
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
                    channel_id, username, display_link = await extract_channel_info(target)
                    if channel_id not in user_settings[user_id]["target_channels"]:
                        user_settings[user_id]["target_channels"].append(channel_id)
                        user_settings[user_id]["target_channel_info"].append({
                            "id": channel_id,
                            "username": username,
                            "link": display_link  # Сохраняем красивую ссылку
                        })
                        await message.answer(f"✅ Получатель добавлен: {display_link}")
                    else:
                        await message.answer(f"ℹ️ Канал уже добавлен")
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
        "Или: <code>+30</code> (через 30 минут)\n\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="HTML",
        reply_markup=get_schedule_back_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "back_to_schedule_menu")
async def back_to_schedule_menu(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "⏰ <b>Шаг 3/3: Время отправки</b>\n\n"
        "Выберите, когда отправлять посты:",
        parse_mode="HTML",
        reply_markup=get_schedule_keyboard()
    )
    await callback.answer()


@dp.message(SetupStates.waiting_for_schedule)
async def process_schedule(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    schedule_text = message.text.strip()
    
    try:
        if schedule_text.startswith('+'):
            hours = int(schedule_text[1:])
            schedule_time = datetime.now() + timedelta(hours=hours)
        elif ':' in schedule_text:
            schedule_time = datetime.strptime(schedule_text, "%d.%m.%Y %H:%M")
        else:
            raise ValueError("Неверный формат")
        
        if schedule_time <= datetime.now():
            await message.answer("❌ Время должно быть в будущем!")
            return
        
        user_settings[user_id]["schedule_time"] = schedule_time
        await show_confirmation(message, user_id, state)
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ или +часы\n\n❌ Для отмены отправьте /cancel")


async def show_confirmation(message: types.Message, user_id: int, state: FSMContext):
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
    await callback.message.edit_text(
        "📢 <b>Шаг 2/3: Куда копировать?</b>\n\n"
        "Отправьте ссылки на каналы-получатели\n\n"
        "Когда закончите, отправьте слово <code>готово</code>\n\n"
        "❌ Для отмены отправьте /cancel",
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
    await callback.message.answer("Меню:", reply_markup=get_main_menu(user_id))
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data == "confirm_no")
async def cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await callback.message.edit_text("❌ Настройки отменены")
    await callback.message.answer("Меню:", reply_markup=get_main_menu(user_id))
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
        
        source_links = []
        for info in settings.get("source_channel_info", []):
            if info.get("username"):
                source_links.append(f"• <a href='https://t.me/{info['username']}'>{info['link']}</a>")
            else:
                source_links.append(f"• {info['link']}")
        
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
    
    await callback.message.answer("Меню:", reply_markup=get_main_menu(user_id))
    await callback.answer()


@dp.callback_query(F.data == "clear_settings")
async def clear_settings(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id in user_settings:
        del user_settings[user_id]
    await callback.message.edit_text("🗑 Настройки удалены")
    await callback.message.answer("Меню:", reply_markup=get_main_menu(user_id))
    await callback.answer()


@dp.callback_query(F.data == "contact_owner")
async def contact_owner(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id not in ADMIN_IDS and user_id != OWNER_ID:
        await callback.answer("У вас нет доступа к этой функции", show_alert=True)
        return
    
    await state.set_state(SupportStates.waiting_for_admin_message)
    await callback.message.answer(
        "📞 <b>Связь с владельцем бота</b>\n\n"
        "Напишите ваше сообщение. Владелец получит его и сможет ответить.\n\n"
        "❌ Для отмены отправьте /cancel",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(SupportStates.waiting_for_admin_message)
async def process_admin_message(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    admin_name = message.from_user.full_name
    admin_username = f"@{message.from_user.username}" if message.from_user.username else "без username"
    
    user_messages[admin_id] = {
        "text": message.text,
        "admin_name": admin_name,
        "admin_username": admin_username,
        "admin_id": admin_id
    }
    
    owner_text = f"""
📨 <b>Новое сообщение от администратора</b>

👤 <b>От:</b> {admin_name} ({admin_username})
🆔 <b>ID:</b> <code>{admin_id}</code>

📝 <b>Сообщение:</b>
{message.text}
    """
    
    await bot.send_message(
        chat_id=OWNER_ID,
        text=owner_text,
        parse_mode="HTML",
        reply_markup=get_owner_reply_keyboard(admin_id)
    )
    
    await message.answer(
        "✅ Ваше сообщение отправлено владельцу. Ожидайте ответа.",
        parse_mode="HTML"
    )
    await state.clear()


@dp.callback_query(F.data.startswith("reply_to_admin_"))
async def owner_reply(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Только владелец бота может отвечать", show_alert=True)
        return
    
    admin_id = int(callback.data.split("_")[3])
    
    await state.update_data(reply_to_admin=admin_id)
    await state.set_state(SupportStates.waiting_for_owner_reply)
    
    await callback.message.answer(
        f"✉️ <b>Ответ администратору (ID: {admin_id})</b>\n\n"
        "Напишите ваш ответ.\n\n❌ Для отмены /cancel",
        parse_mode="HTML"
    )
    await callback.answer()


@dp.message(SupportStates.waiting_for_owner_reply)
async def process_owner_reply(message: types.Message, state: FSMContext):
    data = await state.get_data()
    admin_id = data.get("reply_to_admin")
    
    if not admin_id:
        await message.answer("❌ Ошибка: не найден получатель")
        return
    
    reply_text = f"""
📨 <b>Ответ от владельца бота</b>

📝 <b>Сообщение:</b>
{message.text}
    """
    
    try:
        await bot.send_message(chat_id=admin_id, text=reply_text, parse_mode="HTML")
        await message.answer(f"✅ Ответ отправлен администратору")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    
    await state.clear()


@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    
    if current_state is None:
        await message.answer("❌ Нет активных действий для отмены")
        return
    
    await state.clear()
    await message.answer(
        "❌ Действие отменено",
        reply_markup=get_main_menu(message.from_user.id)
    )


async def main():
    me = await bot.get_me()
    print("=" * 50)
    print(f"🚀 Бот запущен: @{me.username}")
    print(f"👑 Владелец ID: {OWNER_ID}")
    print(f"👥 Администраторы: {ADMIN_IDS}")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
