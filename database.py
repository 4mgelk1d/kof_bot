import sqlite3
import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

class Database:
    """Класс для работы с базой данных бота"""
    
    def __init__(self, db_path: str = "bot_database.db"):
        self.db_path = db_path
        self.init_database()
    
    @contextmanager
    def get_connection(self):
        """Контекстный менеджер для соединения с БД"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Ошибка БД: {e}")
            raise
        finally:
            conn.close()
    
    def init_database(self):
        """Инициализация всех таблиц"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Таблица пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    is_admin BOOLEAN DEFAULT 0,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP
                )
            ''')
            
            # Таблица задач на копирование
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS copy_tasks (
                    task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    source_channels TEXT,      -- JSON массив
                    target_channels TEXT,      -- JSON массив
                    target_websites TEXT,      -- JSON массив
                    start_date TIMESTAMP,
                    schedule_date TIMESTAMP,
                    status TEXT DEFAULT 'pending', -- pending, active, completed, cancelled, error
                    total_posts INTEGER DEFAULT 0,
                    success_posts INTEGER DEFAULT 0,
                    error_posts INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Таблица скопированных постов
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS copied_posts (
                    post_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    source_channel_id INTEGER,
                    source_message_id INTEGER,
                    target_channel_id INTEGER,
                    target_website TEXT,
                    post_text TEXT,
                    media_type TEXT,
                    media_file_id TEXT,
                    copied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_success BOOLEAN DEFAULT 1,
                    error_message TEXT,
                    FOREIGN KEY (task_id) REFERENCES copy_tasks(task_id)
                )
            ''')
            
            # Таблица каналов (для быстрого доступа)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id INTEGER PRIMARY KEY,
                    username TEXT,
                    title TEXT,
                    is_source BOOLEAN DEFAULT 0,
                    is_target BOOLEAN DEFAULT 0,
                    last_checked TIMESTAMP,
                    added_by INTEGER,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (added_by) REFERENCES users(user_id)
                )
            ''')
            
            # Таблица настроек пользователей
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    default_start_date TIMESTAMP,
                    auto_copy_enabled BOOLEAN DEFAULT 0,
                    notification_enabled BOOLEAN DEFAULT 1,
                    language TEXT DEFAULT 'ru',
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            # Индексы для быстрого поиска
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_user ON copy_tasks(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON copy_tasks(status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_posts_task ON copied_posts(task_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_channels_id ON channels(channel_id)')
            
            logger.info("База данных успешно инициализирована")
    
    # ============ РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ============
    def register_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None):
        """Регистрация или обновление пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_active)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, username, first_name, last_name, datetime.now()))
            
            # Проверяем, есть ли настройки у пользователя
            cursor.execute('SELECT 1 FROM user_settings WHERE user_id = ?', (user_id,))
            if not cursor.fetchone():
                cursor.execute('INSERT INTO user_settings (user_id) VALUES (?)', (user_id,))
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        """Получить данные пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT u.*, us.default_start_date, us.auto_copy_enabled, us.notification_enabled
                FROM users u
                LEFT JOIN user_settings us ON u.user_id = us.user_id
                WHERE u.user_id = ?
            ''', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_last_active(self, user_id: int):
        """Обновить время последней активности"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (datetime.now(), user_id))
    
    # ============ РАБОТА С ЗАДАЧАМИ ============
    def create_copy_task(self, user_id: int, source_channels: List[int], target_channels: List[int], 
                         target_websites: List[str], start_date: datetime, schedule_date: datetime = None) -> int:
        """Создать новую задачу на копирование"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO copy_tasks (user_id, source_channels, target_channels, target_websites, 
                                       start_date, schedule_date, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                user_id,
                json.dumps(source_channels),
                json.dumps(target_channels),
                json.dumps(target_websites),
                start_date,
                schedule_date,
                'pending' if schedule_date else 'active'
            ))
            return cursor.lastrowid
    
    def update_task_status(self, task_id: int, status: str, completed_at: datetime = None):
        """Обновить статус задачи"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if completed_at:
                cursor.execute('''
                    UPDATE copy_tasks SET status = ?, completed_at = ? WHERE task_id = ?
                ''', (status, completed_at, task_id))
            else:
                cursor.execute('UPDATE copy_tasks SET status = ? WHERE task_id = ?', (status, task_id))
    
    def update_task_stats(self, task_id: int, total_posts: int, success_posts: int, error_posts: int):
        """Обновить статистику задачи"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE copy_tasks 
                SET total_posts = ?, success_posts = ?, error_posts = ?
                WHERE task_id = ?
            ''', (total_posts, success_posts, error_posts, task_id))
    
    def get_task(self, task_id: int) -> Optional[Dict]:
        """Получить задачу по ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM copy_tasks WHERE task_id = ?', (task_id,))
            row = cursor.fetchone()
            if row:
                task = dict(row)
                task['source_channels'] = json.loads(task['source_channels'])
                task['target_channels'] = json.loads(task['target_channels'])
                task['target_websites'] = json.loads(task['target_websites'])
                return task
            return None
    
    def get_user_tasks(self, user_id: int, status: str = None, limit: int = 10) -> List[Dict]:
        """Получить задачи пользователя"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute('''
                    SELECT * FROM copy_tasks 
                    WHERE user_id = ? AND status = ? 
                    ORDER BY created_at DESC LIMIT ?
                ''', (user_id, status, limit))
            else:
                cursor.execute('''
                    SELECT * FROM copy_tasks 
                    WHERE user_id = ? 
                    ORDER BY created_at DESC LIMIT ?
                ''', (user_id, limit))
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['source_channels'] = json.loads(task['source_channels'])
                task['target_channels'] = json.loads(task['target_channels'])
                task['target_websites'] = json.loads(task['target_websites'])
                tasks.append(task)
            return tasks
    
    def get_pending_tasks(self) -> List[Dict]:
        """Получить задачи, ожидающие выполнения"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM copy_tasks 
                WHERE status = 'active' OR (status = 'pending' AND schedule_date <= ?)
            ''', (datetime.now(),))
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                task['source_channels'] = json.loads(task['source_channels'])
                task['target_channels'] = json.loads(task['target_channels'])
                task['target_websites'] = json.loads(task['target_websites'])
                tasks.append(task)
            return tasks
    
    # ============ РАБОТА С ПОСТАМИ ============
    def add_copied_post(self, task_id: int, source_channel_id: int, source_message_id: int,
                        target_channel_id: int = None, target_website: str = None,
                        post_text: str = None, media_type: str = None, media_file_id: str = None,
                        is_success: bool = True, error_message: str = None):
        """Записать информацию о скопированном посте"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO copied_posts 
                (task_id, source_channel_id, source_message_id, target_channel_id, 
                 target_website, post_text, media_type, media_file_id, is_success, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (task_id, source_channel_id, source_message_id, target_channel_id,
                  target_website, post_text, media_type, media_file_id, is_success, error_message))
    
    def is_post_copied(self, source_channel_id: int, source_message_id: int, task_id: int = None) -> bool:
        """Проверить, был ли уже скопирован пост"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if task_id:
                cursor.execute('''
                    SELECT 1 FROM copied_posts 
                    WHERE source_channel_id = ? AND source_message_id = ? AND task_id = ?
                ''', (source_channel_id, source_message_id, task_id))
            else:
                cursor.execute('''
                    SELECT 1 FROM copied_posts 
                    WHERE source_channel_id = ? AND source_message_id = ?
                ''', (source_channel_id, source_message_id))
            return cursor.fetchone() is not None
    
    # ============ РАБОТА С КАНАЛАМИ ============
    def add_channel(self, channel_id: int, username: str = None, title: str = None, 
                    is_source: bool = False, is_target: bool = False, added_by: int = None):
        """Добавить или обновить канал"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO channels 
                (channel_id, username, title, is_source, is_target, last_checked, added_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (channel_id, username, title, is_source, is_target, datetime.now(), added_by))
    
    def get_channel(self, channel_id: int) -> Optional[Dict]:
        """Получить информацию о канале"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM channels WHERE channel_id = ?', (channel_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    # ============ СТАТИСТИКА ============
    def get_statistics(self, user_id: int = None) -> Dict:
        """Получить статистику"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            stats = {}
            
            if user_id:
                cursor.execute('SELECT COUNT(*) FROM copy_tasks WHERE user_id = ?', (user_id,))
                stats['total_tasks'] = cursor.fetchone()[0]
                
                cursor.execute('SELECT SUM(total_posts), SUM(success_posts) FROM copy_tasks WHERE user_id = ?', (user_id,))
                row = cursor.fetchone()
                stats['total_posts'] = row[0] or 0
                stats['success_posts'] = row[1] or 0
            else:
                cursor.execute('SELECT COUNT(*) FROM copy_tasks')
                stats['total_tasks'] = cursor.fetchone()[0]
                
                cursor.execute('SELECT SUM(total_posts), SUM(success_posts) FROM copy_tasks')
                row = cursor.fetchone()
                stats['total_posts'] = row[0] or 0
                stats['success_posts'] = row[1] or 0
            
            cursor.execute('SELECT COUNT(*) FROM users')
            stats['total_users'] = cursor.fetchone()[0]
            
            cursor.execute('SELECT COUNT(*) FROM channels')
            stats['total_channels'] = cursor.fetchone()[0]
            
            return stats
    
    def cleanup_old_tasks(self, days: int = 30):
        """Очистить старые задачи"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cutoff_date = datetime.now() - timedelta(days=days)
            cursor.execute('DELETE FROM copy_tasks WHERE created_at < ? AND status IN ("completed", "cancelled")', (cutoff_date,))
            deleted = cursor.rowcount
            logger.info(f"Очищено {deleted} старых задач")
            return deleted

# Создаем глобальный экземпляр БД
db = Database()

if __name__ == "__main__":
    # Тестирование БД
    db.init_database()
    print("✅ База данных готова к работе!")