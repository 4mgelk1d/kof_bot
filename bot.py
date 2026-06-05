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

from config import BOT_TOKEN, OWNER_ID, ADMIN_IDS
from messages import BotMessages
from database import db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Временное хранилище для текущей настройки профиля
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
    profiles = db.get_profiles(user_id)
    
    for p in profiles:
        status = "✅" if p['is_active'] else "⏸"
        builder.row(InlineKeyboardButton(text=f"{status} {p['name']}", callback_data=f"profile_{p['id']}"))
    
    builder.row(InlineKeyboardButton(text="➕ Создать профиль", callback_data="create_profile"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def get_profile_actions(profile_id: int, is_active: bool):
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
    builder.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{profile_id}")
    )
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


@dp.channel_post()
async def handle_new_post(message: types.Message):
    """Автоматическое копирование новых постов"""
    source_id = message.chat.id
    profiles = db.get_profiles(OWNER_ID)  # Можно изменить на получение всех пользователей
    
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
                if not db.is_post_copied(profile['id'], source_id, message.message_id):
                    await copy_post(target, message)
                    db.mark_post_copied(profile['id'], source_id, message.message_id)
                    logger.info(f"Скопирован пост для профиля {profile['name']}")


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(BotMessages.START, parse_mode="HTML", reply_markup=get_main_menu())


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(BotMessages.START, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    await callback.message.edit_text(BotMessages.HELP, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "profiles_menu")
async def profiles_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    profiles = db.get_profiles(user_id)
    text = BotMessages.PROFILES_MENU.format(count=len(profiles))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profiles_menu(user_id))
    await callback.answer()


@dp.callback_query(F.data == "create_profile")
async def create_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_for_name)
    await callback.message.edit_text(BotMessages.NEW_PROFILE, parse_mode="HTML")
    await callback.answer()


@dp.message(ProfileStates.waiting_for_name)
async def process_profile_name(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text.strip()
    
    existing = db.get_profiles(user_id)
    if len(existing) >= 10:
        await message.answer("❌ Достигнут лимит профилей (максимум 10)")
        await state.clear()
        return
    
    profile_id = db.create_profile(user_id, name)
    temp_settings[user_id] = {"profile_id": profile_id, "name": name}
    
    await message.answer(BotMessages.PROFILE_CREATED.format(name=name), parse_mode="HTML")
    await state.set_state(ProfileStates.waiting_for_sources)
    await message.answer(BotMessages.ASK_SOURCES.format(name=name), parse_mode="HTML")


@dp.callback_query(F.data.startswith("profile_"))
async def open_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if not profile:
        await callback.answer("Профиль не найден")
        return
    
    sources_count = len(profile['source_channels'])
    targets_count = len(profile['target_channels'])
    schedule = profile.get('schedule_date') or "сегодня"
    if schedule != "сегодня":
        try:
            schedule = datetime.strptime(schedule, "%Y-%m-%d").strftime("%d.%m.%Y")
        except:
            pass
    
    text = BotMessages.PROFILE_SETTINGS.format(
        name=profile['name'],
        sources_count=sources_count,
        targets_count=targets_count,
        schedule=schedule
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profile_actions(profile_id, profile['is_active']))
    await callback.answer()


@dp.callback_query(F.data.startswith("sources_"))
async def edit_sources(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_sources)
    await callback.message.answer(BotMessages.ASK_SOURCES.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("targets_"))
async def edit_targets(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_targets)
    await callback.message.answer(BotMessages.ASK_TARGETS.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("schedule_"))
async def edit_schedule(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp_settings[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_schedule)
    await callback.message.answer(BotMessages.ASK_SCHEDULE.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.message(ProfileStates.waiting_for_sources)
async def process_sources(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = temp_settings.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        if profile_id:
            profile = db.get_profile(profile_id)
            if profile and profile['source_channels']:
                await state.set_state(ProfileStates.waiting_for_targets)
                await message.answer(BotMessages.ASK_TARGETS.format(name=name), parse_mode="HTML")
            else:
                await message.answer(BotMessages.EMPTY_SOURCES, parse_mode="HTML")
        else:
            await message.answer("❌ Ошибка: профиль не найден")
            await state.clear()
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer(f"✅ Добавлен канал-источник")
            except Exception as e:
                await message.answer(f"❌ Ошибка: {e}")
    
    if channels and profile_id:
        db.update_profile_sources(profile_id, channels)
        temp_settings[user_id]["sources"] = channels


@dp.message(ProfileStates.waiting_for_targets)
async def process_targets(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = temp_settings.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        if profile_id:
            profile = db.get_profile(profile_id)
            if profile and profile['target_channels']:
                await state.set_state(ProfileStates.waiting_for_schedule)
                await message.answer(BotMessages.ASK_SCHEDULE.format(name=name), parse_mode="HTML")
            else:
                await message.answer(BotMessages.EMPTY_TARGETS, parse_mode="HTML")
        else:
            await message.answer("❌ Ошибка: профиль не найден")
            await state.clear()
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer(f"✅ Добавлен канал-получатель")
            except Exception as e:
                await message.answer(f"❌ Ошибка: {e}")
    
    if channels and profile_id:
        db.update_profile_targets(profile_id, channels)
        temp_settings[user_id]["targets"] = channels


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
            display_schedule = "сегодня"
        elif schedule_text == "завтра":
            schedule_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            display_schedule = "завтра"
        else:
            schedule_date = datetime.strptime(schedule_text, "%d.%m.%Y").strftime("%Y-%m-%d")
            display_schedule = schedule_text
        
        if profile_id:
            db.update_profile_schedule(profile_id, schedule_date)
            profile = db.get_profile(profile_id)
            
            text = BotMessages.CONFIRM_SETTINGS.format(
                name=name,
                sources=len(profile['source_channels']),
                targets=len(profile['target_channels']),
                schedule=display_schedule
            )
            
            await state.set_state(ProfileStates.waiting_for_confirm)
            await message.answer(text, parse_mode="HTML", reply_markup=get_confirmation_keyboard(profile_id))
        
    except ValueError:
        await message.answer(BotMessages.INVALID_DATE, parse_mode="HTML")


@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_settings(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if profile:
        db.update_profile_active(profile_id, 1)
        schedule = profile.get('schedule_date') or "сегодня"
        if schedule != "сегодня":
            try:
                schedule = datetime.strptime(schedule, "%Y-%m-%d").strftime("%d.%m.%Y")
            except:
                pass
        
        text = BotMessages.SETTINGS_SAVED.format(
            name=profile['name'],
            sources=len(profile['source_channels']),
            targets=len(profile['target_channels']),
            schedule=schedule
        )
        await callback.message.edit_text(text, parse_mode="HTML")
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
    await callback.message.edit_text(BotMessages.ACTION_CANCELLED, parse_mode="HTML")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("start_"))
async def start_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    db.update_profile_active(profile_id, 1)
    await callback.answer("✅ Профиль запущен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    db.update_profile_active(profile_id, 0)
    await callback.answer("⏸ Профиль остановлен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("delete_"))
async def delete_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if profile:
        name = profile['name']
        db.delete_profile(profile_id)
        await callback.message.edit_text(BotMessages.PROFILE_DELETED.format(name=name), parse_mode="HTML")
        await callback.message.answer("Меню:", reply_markup=get_main_menu())
    
    await callback.answer()


@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    if await state.get_state():
        await state.clear()
        await message.answer(BotMessages.ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
    else:
        await message.answer("❌ Нет активных действий")


async def main():
    me = await bot.get_me()
    print("=" * 50)
    print(f"🚀 Бот запущен: @{me.username}")
    print(f"👑 Владелец: {OWNER_ID}")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
