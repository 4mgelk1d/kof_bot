import sqlite3
import json
from datetime import datetime
from typing import Dict, List, Optional
from contextlib import contextmanager

class Database:
    def __init__(self, db_path: str = "copy_bot.db"):
        self.db_path = db_path
        self.init_db()
    
    @contextmanager
    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def init_db(self):
        with self.get_conn() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT,
                    source_channels TEXT,
                    target_channels TEXT,
                    schedule_date TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS copied_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER,
                    source_channel INTEGER,
                    source_message_id INTEGER,
                    copied_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            ''')
    
    def create_profile(self, user_id: int, name: str) -> int:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO profiles (user_id, name, source_channels, target_channels) VALUES (?, ?, ?, ?)",
                (user_id, name, "[]", "[]")
            )
            return cursor.lastrowid
    
    def get_profiles(self, user_id: int) -> List[Dict]:
        with self.get_conn() as conn:
            rows = conn.execute("SELECT * FROM profiles WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
            profiles = []
            for row in rows:
                p = dict(row)
                p['source_channels'] = json.loads(p['source_channels'] or '[]')
                p['target_channels'] = json.loads(p['target_channels'] or '[]')
                profiles.append(p)
            return profiles
    
    def get_profile(self, profile_id: int) -> Optional[Dict]:
        with self.get_conn() as conn:
            row = conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)).fetchone()
            if row:
                p = dict(row)
                p['source_channels'] = json.loads(p['source_channels'] or '[]')
                p['target_channels'] = json.loads(p['target_channels'] or '[]')
                return p
            return None
    
    def update_sources(self, profile_id: int, sources: List[int]):
        with self.get_conn() as conn:
            conn.execute("UPDATE profiles SET source_channels = ? WHERE id = ?", (json.dumps(sources), profile_id))
    
    def update_targets(self, profile_id: int, targets: List[int]):
        with self.get_conn() as conn:
            conn.execute("UPDATE profiles SET target_channels = ? WHERE id = ?", (json.dumps(targets), profile_id))
    
    def update_schedule(self, profile_id: int, schedule_date: str):
        with self.get_conn() as conn:
            conn.execute("UPDATE profiles SET schedule_date = ? WHERE id = ?", (schedule_date, profile_id))
    
    def update_active(self, profile_id: int, is_active: int):
        with self.get_conn() as conn:
            conn.execute("UPDATE profiles SET is_active = ? WHERE id = ?", (is_active, profile_id))
    
    def delete_profile(self, profile_id: int):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    
    def is_post_copied(self, profile_id: int, source_channel: int, message_id: int) -> bool:
        with self.get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM copied_posts WHERE profile_id = ? AND source_channel = ? AND source_message_id = ?",
                (profile_id, source_channel, message_id)
            ).fetchone()
            return row is not None
    
    def mark_post_copied(self, profile_id: int, source_channel: int, message_id: int):
        with self.get_conn() as conn:
            conn.execute(
                "INSERT INTO copied_posts (profile_id, source_channel, source_message_id) VALUES (?, ?, ?)",
                (profile_id, source_channel, message_id)
            )

db = Database()
