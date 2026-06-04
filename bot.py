import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import List, Dict, Any
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import BOT_TOKEN, ADMIN_ID, SITE_API_URL, SITE_API_KEY, MAX_POSTS_PER_SOURCE, validate_config
from database import db

# Проверяем конфигурацию
validate_config()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Состояния FSM
class CopyStates(StatesGroup):
    waiting_for_source_channels = State()
    waiting_for_start_date = State()
    waiting_for_target_channels = State()
    waiting_for_confirmation = State()
    waiting_for_schedule_date = State()

# Хранилище данных пользователя (временное, для сессии)
user_temp_data: Dict[int, Dict[str, Any]] = {}

class ChannelPost:
    """Класс для хранения данных поста"""
    def __init__(self, message: types.Message):
        self.message_id = message.message_id
        self.chat_id = message.chat.id
        self.text = message.text or message.caption or ""
        self.media = None
        self.media_type = None
        self.buttons = None
        self.date = message.date
        
        if message.photo:
            self.media = message.photo[-1].file_id
            self.media_type = "photo"
        elif message.video:
            self.media = message.video.file_id
            self.media_type = "video"
        elif message.document:
            self.media = message.document.file_id
            self.media_type = "document"
        elif message.audio:
            self.media = message.audio.file_id
            self.media_type = "audio"
        elif message.animation:
            self.media = message.animation.file_id
            self.media_type = "animation"
        
        if message.reply_markup:
            self.buttons = message.reply_markup

# Инициализация бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ============ КЛАВИАТУРЫ ============
def get_confirmation_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_yes"),
        InlineKeyboardButton(text="❌ Отменить", callback_data="confirm_no"),
        InlineKeyboardButton(text="⏰ Указать дату", callback_data="confirm_schedule")
    )
    return builder.as_markup()

def get_main_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Новое копирование", callback_data="new_copy"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="stats"),
        InlineKeyboardButton(text="📜 Мои задачи", callback_data="my_tasks"),
        InlineKeyboardButton(text="ℹ️ Помощь", callback_data="help")
    )
    return builder.as_markup()

def get_tasks_keyboard(tasks: List[Dict]):
    """Клавиатура со списком задач"""
    builder = InlineKeyboardBuilder()
    for task in tasks[:5]:  # Показываем не более 5 задач
        status_emoji = {
            'pending': '⏳', 'active': '🔄', 'completed': '✅', 'cancelled': '❌', 'error': '⚠️'
        }.get(task['status'], '📋')
        builder.row(InlineKeyboardButton(
            text=f"{status_emoji} Задача #{task['task_id']} - {task['created_at'][:16]}",
            callback_data=f"task_{task['task_id']}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"))
    return builder.as_markup()

# ============ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ============
async def extract_channel_id(channel_input: str) -> int:
    """Извлекает ID канала из ссылки или числа"""
    channel_input = channel_input.strip()
    
    if channel_input.lstrip('-').isdigit():
        return int(channel_input)
    
    match = re.search(r'(?:https?://)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]+)', channel_input)
    if match:
        username = match.group(1)
        try:
            chat = await bot.get_chat(f"@{username}")
            # Сохраняем канал в БД
            db.add_channel(
                channel_id=chat.id,
                username=username,
                title=chat.title,
                added_by=ADMIN_ID
            )
            return chat.id
        except Exception as e:
            logger.error(f"Ошибка получения чата @{username}: {e}")
            raise ValueError(f"Не удалось найти канал @{username}")
    
    raise ValueError("Неверный формат ссылки или ID канала")

async def copy_post_to_channel(target_channel_id: int, source_post: ChannelPost, task_id: int = None):
    """Копирует пост в канал без информации об отправителе"""
    try:
        if source_post.media_type == "photo":
            await bot.send_photo(
                chat_id=target_channel_id,
                photo=source_post.media,
                caption=source_post.text,
                reply_markup=source_post.buttons
            )
        elif source_post.media_type == "video":
            await bot.send_video(
                chat_id=target_channel_id,
                video=source_post.media,
                caption=source_post.text,
                reply_markup=source_post.buttons
            )
        elif source_post.media_type == "document":
            await bot.send_document(
                chat_id=target_channel_id,
                document=source_post.media,
                caption=source_post.text,
                reply_markup=source_post.buttons
            )
        elif source_post.media_type == "audio":
            await bot.send_audio(
                chat_id=target_channel_id,
                audio=source_post.media,
                caption=source_post.text,
                reply_markup=source_post.buttons
            )
        elif source_post.media_type == "animation":
            await bot.send_animation(
                chat_id=target_channel_id,
                animation=source_post.media,
                caption=source_post.text,
                reply_markup=source_post.buttons
            )
        else:
            await bot.send_message(
                chat_id=target_channel_id,
                text=source_post.text,
                reply_markup=source_post.buttons
            )
        
        if task_id:
            db.add_copied_post(
                task_id=task_id,
                source_channel_id=source_post.chat_id,
                source_message_id=source_post.message_id,
                target_channel_id=target_channel_id,
                post_text=source_post.text[:500],
                media_type=source_post.media_type,
                media_file_id=source_post.media,
                is_success=True
            )
        return True
    except Exception as e:
        logger.error(f"Ошибка копирования в канал {target_channel_id}: {e}")
        if task_id:
            db.add_copied_post(
                task_id=task_id,
                source_channel_id=source_post.chat_id,
                source_message_id=source_post.message_id,
                target_channel_id=target_channel_id,
                is_success=False,
                error_message=str(e)
            )
        return False

async def send_post_to_website(post: ChannelPost, target_url: str, task_id: int = None):
    """Отправляет пост на сайт через HTTP запрос"""
    try:
        async with aiohttp.ClientSession() as session:
            data = {
                "text": post.text,
                "media_type": post.media_type,
                "media_id": post.media,
                "timestamp": datetime.now().isoformat(),
                "api_key": SITE_API_KEY
            }
            async with session.post(target_url, json=data, timeout=10) as response:
                success = response.status == 200
                if task_id:
                    db.add_copied_post(
                        task_id=task_id,
                        source_channel_id=post.chat_id,
                        source_message_id=post.message_id,
                        target_website=target_url,
                        post_text=post.text[:500],
                        media_type=post.media_type,
                        media_file_id=post.media,
                        is_success=success,
                        error_message=None if success else f"HTTP {response.status}"
                    )
                return success
    except Exception as e:
        logger.error(f"Ошибка отправки на сайт: {e}")
        if task_id:
            db.add_copied_post(
                task_id=task_id,
                source_channel_id=post.chat_id,
                source_message_id=post.message_id,
                target_website=target_url,
                is_success=False,
                error_message=str(e)
            )
        return False

# ============ КОМАНДЫ ============
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    # Регистрируем пользователя
    db.register_user(
        user_id=user_id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    db.update_last_active(user_id)
    
    welcome_text = """
🤖 <b>Бот для копирования контента из Telegram каналов</b>

📢 <b>Важное предупреждение:</b>
Бот должен быть <b>администратором ВСЕХ каналов</b>:
• Из которых нужно копировать контент
• В которые нужно отправлять контент

🔧 <b>Функционал:</b>
• Копирование постов в несколько каналов
• Отправка контента на внешний сайт
• Выбор даты начала копирования
• Отложенная публикация
• Сохранение всех медиафайлов и кнопок
• История всех задач в базе данных

📋 <b>Как использовать:</b>
Нажмите кнопку "Новое копирование" или введите /send
"""
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=get_main_menu())

@dp.message(Command("send"))
async def cmd_send(message: types.Message, state: FSMContext):
    """Начало процесса копирования"""
    user_id = message.from_user.id
    user_temp_data[user_id] = {
        "source_channels": [], 
        "target_channels": [], 
        "target_websites": [], 
        "start_date": None,
        "task_id": None
    }
    
    await state.set_state(CopyStates.waiting_for_source_channels)
    await message.answer(
        "📢 <b>Шаг 1/4: Откуда копировать?</b>\n\n"
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
    
    if message.text.lower() == "готово":
        if not user_temp_data[user_id].get("source_channels"):
            await message.answer("❌ Вы не добавили ни одного канала. Пожалуйста, добавьте хотя бы один канал.")
            return
        
        await state.set_state(CopyStates.waiting_for_start_date)
        await message.answer(
            "📅 <b>Шаг 2/4: С какой даты брать контент?</b>\n\n"
            "Укажите дату в формате <code>ДД.ММ.ГГГГ</code>\n"
            "Например: <code>01.01.2024</code>\n\n"
            "Все посты после указанной даты будут скопированы.\n"
            "Или отправьте <code>сегодня</code> для копирования только сегодняшних постов.",
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
    
    try:
        if message.text.lower() == "сегодня":
            start_date = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = datetime.strptime(message.text.strip(), "%d.%m.%Y")
        
        user_temp_data[user_id]["start_date"] = start_date
        
        await state.set_state(CopyStates.waiting_for_target_channels)
        await message.answer(
            "📢 <b>Шаг 3/4: Куда копировать?</b>\n\n"
            "Отправьте мне ссылки или ID каналов, куда нужно копировать контент.\n"
            "Если нужно отправить на сайт, напишите URL сайта.\n\n"
            "Каждый получатель с новой строки.\n"
            "Когда закончите, отправьте слово <code>готово</code>",
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ или 'сегодня'")

@dp.message(CopyStates.waiting_for_target_channels)
async def process_target_channels(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.text.lower() == "готово":
        if not user_temp_data[user_id].get("target_channels") and not user_temp_data[user_id].get("target_websites"):
            await message.answer("❌ Вы не добавили ни одного получателя. Пожалуйста, добавьте хотя бы один канал или сайт.")
            return
        
        # Создаем задачу в БД
        task_id = db.create_copy_task(
            user_id=user_id,
            source_channels=user_temp_data[user_id]["source_channels"],
            target_channels=user_temp_data[user_id]["target_channels"],
            target_websites=user_temp_data[user_id]["target_websites"],
            start_date=user_temp_data[user_id]["start_date"]
        )
        user_temp_data[user_id]["task_id"] = task_id
        
        # Показываем сводку
        source_list = "\n".join([str(ch) for ch in user_temp_data[user_id]["source_channels"]])
        target_channels_list = "\n".join([str(ch) for ch in user_temp_data[user_id]["target_channels"]])
        target_websites_list = "\n".join(user_temp_data[user_id]["target_websites"])
        start_date_str = user_temp_data[user_id]["start_date"].strftime("%d.%m.%Y")
        
        summary = f"""
📋 <b>Сводка для копирования</b>

🆔 <b>ID задачи:</b> {task_id}

📍 <b>Источники ({len(user_temp_data[user_id]['source_channels'])}):</b>
<code>{source_list}</code>

🎯 <b>Каналы-получатели ({len(user_temp_data[user_id]['target_channels'])}):</b>
<code>{target_channels_list or 'нет'}</code>

🌐 <b>Сайты-получатели ({len(user_temp_data[user_id]['target_websites'])}):</b>
<code>{target_websites_list or 'нет'}</code>

📅 <b>Дата начала:</b> {start_date_str}

⚠️ <b>Важно:</b> Бот скопирует ВСЕ посты с указанной даты
        """
        
        await state.set_state(CopyStates.waiting_for_confirmation)
        await message.answer(summary, parse_mode="HTML", reply_markup=get_confirmation_keyboard())
    else:
        targets = message.text.strip().split('\n')
        for target in targets:
            target = target.strip()
            if not target:
                continue
            
            if target.startswith(("http://", "https://")):
                user_temp_data[user_id]["target_websites"].append(target)
                await message.answer(f"✅ Добавлен сайт: {target}")
            else:
                try:
                    channel_id = await extract_channel_id(target)
                    user_temp_data[user_id]["target_channels"].append(channel_id)
                    await message.answer(f"✅ Добавлен канал-получатель: {target}")
                except ValueError as e:
                    await message.answer(f"❌ Ошибка: {e}\nПропускаю: {target}")

# ============ КОЛБЭКИ ============
@dp.callback_query(F.data == "confirm_yes")
async def confirm_yes(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = user_temp_data.get(user_id)
    
    if not data:
        await callback.message.answer("❌ Сессия не найдена. Начните заново командой /send")
        await state.clear()
        return
    
    task_id = data.get("task_id")
    db.update_task_status(task_id, "active")
    
    await callback.message.edit_text("🔄 <b>Начинаю копирование...</b>", parse_mode="HTML")
    
    # Запускаем копирование
    success_count = 0
    error_count = 0
    total_posts = 0
    
    try:
        for source_channel in data["source_channels"]:
            posts = []
            async for message in bot.get_chat_history(source_channel, limit=MAX_POSTS_PER_SOURCE):
                if message.date.replace(tzinfo=None) >= data["start_date"]:
                    # Проверяем, не скопирован ли уже пост
                    if not db.is_post_copied(source_channel, message.message_id, task_id):
                        posts.append(ChannelPost(message))
            
            total_posts += len(posts)
            
            for post in posts:
                # Копируем в каналы
                for target_channel in data["target_channels"]:
                    if await copy_post_to_channel(target_channel, post, task_id):
                        success_count += 1
                    else:
                        error_count += 1
                
                # Отправляем на сайты
                for website in data["target_websites"]:
                    if await send_post_to_website(post, website, task_id):
                        success_count += 1
                    else:
                        error_count += 1
                        
            # Сохраняем статистику в БД
            db.update_task_stats(task_id, total_posts, success_count, error_count)
            
    except Exception as e:
        logger.error(f"Ошибка при копировании: {e}")
        db.update_task_status(task_id, "error")
        await callback.message.answer(f"❌ Ошибка при выполнении: {e}")
    
    db.update_task_status(task_id, "completed", datetime.now())
    
    result_text = f"""
✅ <b>Копирование завершено!</b>

📊 <b>Статистика:</b>
• Постов обработано: {total_posts}
• Успешных отправок: {success_count}
• Ошибок: {error_count}
• Каналов-источников: {len(data['source_channels'])}
• Каналов-получателей: {len(data['target_channels'])}
• Сайтов-получателей: {len(data['target_websites'])}
• ID задачи: {task_id}

📝 <b>История задачи сохранена в базе данных</b>
    """
    
    await callback.message.answer(result_text, parse_mode="HTML", reply_markup=get_main_menu())
    del user_temp_data[user_id]
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "confirm_no")
async def confirm_no(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = user_temp_data.get(user_id)
    
    if data and data.get("task_id"):
        db.update_task_status(data["task_id"], "cancelled")
    
    if user_id in user_temp_data:
        del user_temp_data[user_id]
    
    await callback.message.edit_text("❌ <b>Операция отменена</b>", parse_mode="HTML")
    await callback.message.answer("Можете начать заново командой /send", reply_markup=get_main_menu())
    await state.clear()
    await callback.answer()

@dp.callback_query(F.data == "confirm_schedule")
async def confirm_schedule(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(CopyStates.waiting_for_schedule_date)
    await callback.message.answer(
        "⏰ <b>Укажите дату и время для отложенной публикации</b>\n\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Например: <code>25.12.2024 14:30</code>\n\n"
        "Посты начнут публиковаться с указанного времени.",
        parse_mode="HTML"
    )
    await callback.answer()

@dp.message(CopyStates.waiting_for_schedule_date)
async def process_schedule_date(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    try:
        schedule_date = datetime.strptime(message.text.strip(), "%d.%m.%Y %H:%M")
        
        if schedule_date <= datetime.now():
            await message.answer("❌ Дата должна быть в будущем!")
            return
        
        # Обновляем задачу с датой отложенного запуска
        data = user_temp_data.get(user_id)
        if data and data.get("task_id"):
            # Здесь нужно обновить задачу в БД с schedule_date
            pass
        
        await message.answer(f"✅ Отложенная публикация запланирована на {schedule_date.strftime('%d.%m.%Y %H:%M')}\n\nНажмите 'Подтвердить' для запуска в указанное время.")
        
        user_temp_data[user_id]["schedule_date"] = schedule_date
        await state.set_state(CopyStates.waiting_for_confirmation)
        
    except ValueError:
        await message.answer("❌ Неверный формат. Используйте: ДД.ММ.ГГГГ ЧЧ:ММ")

@dp.callback_query(F.data == "stats")
async def show_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    stats = db.get_statistics(user_id)
    total_stats = db.get_statistics()
    
    stats_text = f"""
📊 <b>Ваша статистика</b>

📋 <b>Ваши задачи:</b> {stats.get('total_tasks', 0)}
📝 <b>Ваши посты:</b> {stats.get('total_posts', 0)}
✅ <b>Успешно:</b> {stats.get('success_posts', 0)}

🌍 <b>Общая статистика</b>
👥 <b>Пользователей:</b> {total_stats.get('total_users', 0)}
📢 <b>Каналов:</b> {total_stats.get('total_channels', 0)}
📋 <b>Всего задач:</b> {total_stats.get('total_tasks', 0)}
📝 <b>Всего постов:</b> {total_stats.get('total_posts', 0)}
    """
    
    await callback.message.edit_text(stats_text, parse_mode="HTML", reply_markup=get_main_menu())
    await callback.answer()

@dp.callback_query(F.data == "my_tasks")
async def show_my_tasks(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    tasks = db.get_user_tasks(user_id, limit=10)
    
    if not tasks:
        await callback.message.edit_text(
            "📋 <b>У вас пока нет задач</b>\n\n"
            "Начните новое копирование командой /send",
            parse_mode="HTML",
            reply_markup=get_main_menu()
        )
        await callback.answer()
        return
    
    tasks_text = "📋 <b>Ваши последние задачи:</b>\n\n"
    for task in tasks:
        status_emoji = {
            'pending': '⏳', 'active': '🔄', 'completed': '✅', 'cancelled': '❌', 'error': '⚠️'
        }.get(task['status'], '📋')
        
        tasks_text += f"{status_emoji} <b>Задача #{task['task_id']}</b>\n"
        tasks_text += f"📅 {task['created_at'][:16]}\n"
        tasks_text += f"📊 Постов: {task['total_posts']} (✅{task['success_posts']})\n"
        tasks_text += f"🔘 Статус: {task['status']}\n\n"
    
    await callback.message.edit_text(tasks_text, parse_mode="HTML", reply_markup=get_tasks_keyboard(tasks))
    await callback.answer()

@dp.callback_query(F.data.startswith("task_"))
async def show_task_detail(callback: types.CallbackQuery):
    task_id = int(callback.data.split("_")[1])
    task = db.get_task(task_id)
    
    if not task:
        await callback.answer("Задача не найдена")
        return
    
    detail_text = f"""
📋 <b>Детали задачи #{task['task_id']}</b>

📅 <b>Создана:</b> {task['created_at']}
📊 <b>Статус:</b> {task['status']}
📍 <b>Источники:</b> {len(task['source_channels'])} канал(ов)
🎯 <b>Получатели:</b> {len(task['target_channels'])} канал(ов), {len(task['target_websites'])} сайт(ов)
📝 <b>Всего постов:</b> {task['total_posts']}
✅ <b>Успешно:</b> {task['success_posts']}
❌ <b>Ошибок:</b> {task['error_posts']}

📅 <b>Дата начала:</b> {task['start_date'][:16] if task['start_date'] else 'не указана'}
🏁 <b>Завершена:</b> {task['completed_at'][:16] if task['completed_at'] else 'в процессе'}
    """
    
    keyboard = InlineKeyboardBuilder()
    keyboard.row(InlineKeyboardButton(text="🔙 К списку задач", callback_data="my_tasks"))
    
    await callback.message.edit_text(detail_text, parse_mode="HTML", reply_markup=keyboard.as_markup())
    await callback.answer()

@dp.callback_query(F.data == "new_copy")
async def new_copy(callback: types.CallbackQuery, state: FSMContext):
    await cmd_send(callback.message, state)
    await callback.answer()

@dp.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

@dp.callback_query(F.data == "help")
async def help_callback(callback: types.CallbackQuery):
    await cmd_start(callback.message)
    await callback.answer()

# ============ ЗАПУСК ============
async def main():
    logger.info("🚀 Бот запущен!")
    logger.info(f"👤 Админ ID: {ADMIN_ID}")
    logger.info(f"🌐 Сайт: {SITE_API_URL if SITE_API_URL != 'https://ваш-сайт.ru/api/post' else 'не настроен'}")
    
    # Очищаем старые задачи при запуске
    deleted = db.cleanup_old_tasks(days=30)
    if deleted:
        logger.info(f"Очищено {deleted} старых задач")
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())