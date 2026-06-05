import asyncio
import logging
import re
import json
from datetime import datetime, timedelta
from typing import Dict, List
from collections import defaultdict
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InputMediaPhoto, InputMediaVideo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, OWNER_IDS
from messages import *
from database import db

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

temp: Dict[int, Dict] = {}

# Кэш для альбомов (хранит сообщения, ожидающие группировки)
album_cache: Dict[str, Dict] = {}

class ProfileStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_sources = State()
    waiting_for_targets = State()
    waiting_for_schedule = State()
    waiting_for_confirm = State()


def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="Управление профилями", callback_data="profiles_menu"))
    builder.row(InlineKeyboardButton(text="Помощь", callback_data="help"))
    return builder.as_markup()


def get_profiles_menu(user_id: int):
    builder = InlineKeyboardBuilder()
    profiles = db.get_profiles(user_id)
    
    for p in profiles:
        status = "ON" if p['is_active'] else "OFF"
        builder.row(InlineKeyboardButton(text=f"{status} {p['name']}", callback_data=f"profile_{p['id']}"))
    
    builder.row(InlineKeyboardButton(text="Создать профиль", callback_data="create_profile"))
    builder.row(InlineKeyboardButton(text="Назад", callback_data="back_to_menu"))
    return builder.as_markup()


def get_profile_actions(profile_id: int, is_active: bool):
    builder = InlineKeyboardBuilder()
    
    if is_active:
        builder.row(InlineKeyboardButton(text="Остановить", callback_data=f"stop_{profile_id}"))
    else:
        builder.row(InlineKeyboardButton(text="Запустить", callback_data=f"start_{profile_id}"))
    
    builder.row(
        InlineKeyboardButton(text="Источники", callback_data=f"sources_{profile_id}"),
        InlineKeyboardButton(text="Получатели", callback_data=f"targets_{profile_id}"),
        InlineKeyboardButton(text="Время", callback_data=f"schedule_{profile_id}")
    )
    builder.row(InlineKeyboardButton(text="Удалить", callback_data=f"delete_{profile_id}"))
    builder.row(InlineKeyboardButton(text="К списку", callback_data="profiles_menu"))
    return builder.as_markup()


def get_confirm_keyboard(profile_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Подтвердить", callback_data=f"confirm_{profile_id}"),
        InlineKeyboardButton(text="Отменить", callback_data="cancel_settings")
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


async def send_album(target_channel: int, messages: List[types.Message], caption: str = ""):
    """Отправляет альбом из нескольких фото/видео"""
    try:
        media_group = []
        for i, msg in enumerate(messages):
            if msg.photo:
                media_group.append(InputMediaPhoto(
                    media=msg.photo[-1].file_id,
                    caption=caption if i == 0 else ""
                ))
            elif msg.video:
                media_group.append(InputMediaVideo(
                    media=msg.video.file_id,
                    caption=caption if i == 0 else ""
                ))
        
        if media_group:
            await bot.send_media_group(chat_id=target_channel, media=media_group)
            return True
        return False
    except Exception as e:
        logger.error(f"Ошибка отправки альбома: {e}")
        return False


async def send_single_post(target_channel: int, message: types.Message):
    """Отправляет одиночный пост"""
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
        logger.error(f"Ошибка отправки: {e}")
        return False


async def process_post_with_album_check(target_channel: int, message: types.Message, profile_id: int):
    """Обрабатывает пост с проверкой на альбом"""
    media_group_id = message.media_group_id
    
    if media_group_id:
        # Это часть альбома
        cache_key = f"{media_group_id}_{message.chat.id}"
        
        if cache_key not in album_cache:
            album_cache[cache_key] = {
                "messages": [],
                "targets": [],
                "profile_id": profile_id,
                "timer": None
            }
        
        album_cache[cache_key]["messages"].append(message)
        
        if target_channel not in album_cache[cache_key]["targets"]:
            album_cache[cache_key]["targets"].append(target_channel)
        
        # Если таймер уже есть, не создаем новый
        if album_cache[cache_key]["timer"] is None:
            # Ждем 1 секунду для сбора всех сообщений альбома
            async def send_album_cached():
                await asyncio.sleep(1)
                if cache_key in album_cache:
                    cache_data = album_cache[cache_key]
                    for tgt in cache_data["targets"]:
                        # Проверяем, не скопированы ли уже эти сообщения
                        all_new = True
                        for msg in cache_data["messages"]:
                            if db.is_post_copied(profile_id, message.chat.id, msg.message_id):
                                all_new = False
                                break
                        
                        if all_new:
                            await send_album(tgt, cache_data["messages"])
                            for msg in cache_data["messages"]:
                                db.mark_post_copied(profile_id, message.chat.id, msg.message_id)
                            logger.info(f"Альбом из {len(cache_data['messages'])} отправлен в {tgt}")
                    
                    del album_cache[cache_key]
            
            album_cache[cache_key]["timer"] = asyncio.create_task(send_album_cached())
    else:
        # Не альбом - отправляем сразу
        if not db.is_post_copied(profile_id, message.chat.id, message.message_id):
            await send_single_post(target_channel, message)
            db.mark_post_copied(profile_id, message.chat.id, message.message_id)


@dp.channel_post()
async def handle_new_post(message: types.Message):
    source_id = message.chat.id
    logger.info(f"Новый пост в канале {source_id}")
    
    # Получаем профили ВСЕХ владельцев (всех пользователей)
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM profiles WHERE is_active = 1").fetchall()
        
        for row in rows:
            profile = dict(row)
            profile['source_channels'] = json.loads(profile['source_channels'] or '[]')
            profile['target_channels'] = json.loads(profile['target_channels'] or '[]')
            
            schedule = profile.get('schedule_date')
            if schedule:
                try:
                    start = datetime.strptime(schedule, "%Y-%m-%d")
                    if datetime.now() < start:
                        continue
                except:
                    pass
            
            if source_id in profile['source_channels']:
                for target in profile['target_channels']:
                    await process_post_with_album_check(target, message, profile['id'])


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    # Проверяем, есть ли пользователь в списке владельцев
    if user_id not in OWNER_IDS:
        await message.answer("Вас нет в списке админов этого бота, попробуйте позже...")
        return
    
    await message.answer(START, parse_mode="HTML", reply_markup=get_main_menu())


@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await callback.message.edit_text(START, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    await callback.message.edit_text(HELP, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data == "profiles_menu")
async def profiles_menu(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    profiles = db.get_profiles(user_id)
    text = PROFILES_MENU.format(count=len(profiles))
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profiles_menu(user_id))
    await callback.answer()


@dp.callback_query(F.data == "create_profile")
async def create_profile(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ProfileStates.waiting_for_name)
    await callback.message.edit_text(NEW_PROFILE, parse_mode="HTML")
    await callback.answer()


@dp.message(ProfileStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    if message.text.lower() == "/cancel":
        await state.clear()
        await message.answer(ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
        return
    
    user_id = message.from_user.id
    name = message.text.strip()
    
    profiles = db.get_profiles(user_id)
    if len(profiles) >= 10:
        await message.answer("Достигнут лимит профилей (максимум 10)")
        await state.clear()
        return
    
    profile_id = db.create_profile(user_id, name)
    temp[user_id] = {"profile_id": profile_id, "name": name}
    
    await message.answer(PROFILE_CREATED.format(name=name), parse_mode="HTML")
    await state.set_state(ProfileStates.waiting_for_sources)
    await message.answer(ASK_SOURCES.format(name=name), parse_mode="HTML")


@dp.callback_query(F.data.startswith("profile_"))
async def open_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if not profile:
        await callback.answer("Профиль не найден")
        return
    
    schedule = profile.get('schedule_date') or "сегодня"
    if schedule != "сегодня":
        try:
            schedule = datetime.strptime(schedule, "%Y-%m-%d").strftime("%d.%m.%Y")
        except:
            pass
    
    text = PROFILE_SETTINGS.format(
        name=profile['name'],
        sources=len(profile['source_channels']),
        targets=len(profile['target_channels']),
        schedule=schedule
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_profile_actions(profile_id, profile['is_active']))
    await callback.answer()


@dp.callback_query(F.data.startswith("sources_"))
async def edit_sources(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_sources)
    await callback.message.answer(ASK_SOURCES.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("targets_"))
async def edit_targets(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_targets)
    await callback.message.answer(ASK_TARGETS.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data.startswith("schedule_"))
async def edit_schedule(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    temp[callback.from_user.id] = {"profile_id": profile_id, "name": profile['name']}
    await state.set_state(ProfileStates.waiting_for_schedule)
    await callback.message.answer(ASK_SCHEDULE.format(name=profile['name']), parse_mode="HTML")
    await callback.answer()


@dp.message(ProfileStates.waiting_for_sources)
async def process_sources(message: types.Message, state: FSMContext):
    if message.text.lower() == "/cancel":
        await state.clear()
        if message.from_user.id in temp:
            del temp[message.from_user.id]
        await message.answer(ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
        return
    
    user_id = message.from_user.id
    data = temp.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        profile = db.get_profile(profile_id)
        if profile and profile['source_channels']:
            await state.set_state(ProfileStates.waiting_for_targets)
            await message.answer(ASK_TARGETS.format(name=name), parse_mode="HTML")
        else:
            await message.answer(EMPTY_SOURCES, parse_mode="HTML")
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer("Добавлен канал-источник")
            except Exception as e:
                await message.answer(f"Ошибка: {e}")
    
    if channels and profile_id:
        existing = db.get_profile(profile_id)['source_channels']
        all_sources = list(set(existing + channels))
        db.update_sources(profile_id, all_sources)


@dp.message(ProfileStates.waiting_for_targets)
async def process_targets(message: types.Message, state: FSMContext):
    if message.text.lower() == "/cancel":
        await state.clear()
        if message.from_user.id in temp:
            del temp[message.from_user.id]
        await message.answer(ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
        return
    
    user_id = message.from_user.id
    data = temp.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    
    if message.text.lower() == "готово":
        profile = db.get_profile(profile_id)
        if profile and profile['target_channels']:
            await state.set_state(ProfileStates.waiting_for_schedule)
            await message.answer(ASK_SCHEDULE.format(name=name), parse_mode="HTML")
        else:
            await message.answer(EMPTY_TARGETS, parse_mode="HTML")
        return
    
    channels = []
    for ch in message.text.strip().split('\n'):
        if ch.strip():
            try:
                cid = await extract_channel_id(ch)
                channels.append(cid)
                await message.answer("Добавлен канал-получатель")
            except Exception as e:
                await message.answer(f"Ошибка: {e}")
    
    if channels and profile_id:
        existing = db.get_profile(profile_id)['target_channels']
        all_targets = list(set(existing + channels))
        db.update_targets(profile_id, all_targets)


@dp.message(ProfileStates.waiting_for_schedule)
async def process_schedule(message: types.Message, state: FSMContext):
    if message.text.lower() == "/cancel":
        await state.clear()
        if message.from_user.id in temp:
            del temp[message.from_user.id]
        await message.answer(ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
        return
    
    user_id = message.from_user.id
    data = temp.get(user_id, {})
    profile_id = data.get("profile_id")
    name = data.get("name", "без названия")
    text = message.text.strip().lower()
    
    try:
        if text == "сегодня":
            schedule_date = datetime.now().strftime("%Y-%m-%d")
            display = "сегодня"
        elif text == "завтра":
            schedule_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            display = "завтра"
        else:
            schedule_date = datetime.strptime(text, "%d.%m.%Y").strftime("%Y-%m-%d")
            display = text
        
        if profile_id:
            db.update_schedule(profile_id, schedule_date)
            profile = db.get_profile(profile_id)
            
            sources_list = "\n".join([f"• {ch}" for ch in profile['source_channels']]) if profile['source_channels'] else "нет"
            targets_list = "\n".join([f"• {ch}" for ch in profile['target_channels']]) if profile['target_channels'] else "нет"
            
            confirm_text = CONFIRM_SETTINGS.format(
                name=name,
                sources_list=sources_list,
                targets_list=targets_list,
                schedule=display
            )
            
            await state.set_state(ProfileStates.waiting_for_confirm)
            await message.answer(confirm_text, parse_mode="HTML", reply_markup=get_confirm_keyboard(profile_id))
            return
        
    except ValueError:
        await message.answer(INVALID_DATE, parse_mode="HTML")
        return


@dp.callback_query(F.data.startswith("confirm_"))
async def confirm_settings(callback: types.CallbackQuery, state: FSMContext):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if profile:
        db.update_active(profile_id, 1)
        
        schedule = profile.get('schedule_date') or "сегодня"
        if schedule != "сегодня":
            try:
                schedule = datetime.strptime(schedule, "%Y-%m-%d").strftime("%d.%m.%Y")
            except:
                pass
        
        sources_count = len(profile['source_channels'])
        targets_count = len(profile['target_channels'])
        
        text = SETTINGS_SAVED.format(
            name=profile['name'],
            sources=sources_count,
            targets=targets_count,
            schedule=schedule
        )
        
        await callback.message.edit_text(text, parse_mode="HTML")
        await callback.message.answer("Меню:", reply_markup=get_main_menu())
    
    await state.clear()
    if callback.from_user.id in temp:
        del temp[callback.from_user.id]
    await callback.answer()


@dp.callback_query(F.data == "cancel_settings")
async def cancel_settings(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if callback.from_user.id in temp:
        del temp[callback.from_user.id]
    await callback.message.edit_text(ACTION_CANCELLED, parse_mode="HTML")
    await callback.message.answer("Меню:", reply_markup=get_main_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("start_"))
async def start_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    db.update_active(profile_id, 1)
    await callback.answer("Профиль запущен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("stop_"))
async def stop_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    db.update_active(profile_id, 0)
    await callback.answer("Профиль остановлен")
    await open_profile(callback)


@dp.callback_query(F.data.startswith("delete_"))
async def delete_profile(callback: types.CallbackQuery):
    profile_id = int(callback.data.split("_")[1])
    profile = db.get_profile(profile_id)
    
    if profile:
        db.delete_profile(profile_id)
        await callback.message.edit_text(PROFILE_DELETED.format(name=profile['name']), parse_mode="HTML")
        await callback.message.answer("Меню:", reply_markup=get_main_menu())
    
    await callback.answer()


@dp.message(Command("cancel"))
async def cancel_cmd(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        if message.from_user.id in temp:
            del temp[message.from_user.id]
        await message.answer(ACTION_CANCELLED, parse_mode="HTML", reply_markup=get_main_menu())
    else:
        await message.answer("Нет активных действий для отмены", reply_markup=get_main_menu())


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    me = await bot.get_me()
    print("=" * 50)
    print(f"Бот запущен: @{me.username}")
    print("=" * 50)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
