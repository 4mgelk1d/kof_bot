# ===== НАСТРОЙКИ - ОБЯЗАТЕЛЬНО ЗАМЕНИ =====
# Токен бота (получить у @BotFather в Telegram)
BOT_TOKEN = "8924285335:AAFdPfErLdSSi9a2soS8_LaazeUWTK1mH00"

# Твой Telegram ID (узнать у @userinfobot командой /start)
ADMIN_ID = 5584463063

# Настройки сайта (если не нужно - оставь как есть)
SITE_API_URL = "https://ваш-сайт.ru/api/post"
SITE_API_KEY = "ваш_ключ"

# Настройки базы данных
DATABASE_PATH = "bot_database.db"

# Настройки бота
MAX_POSTS_PER_SOURCE = 1000  # Максимальное количество постов за один раз
DEFAULT_TIMEZONE = "Europe/Moscow"
# =========================================

# Проверка обязательных настроек
def validate_config():
    if BOT_TOKEN == "8924285335:AAFdPfErLdSSi9a2soS8_LaazeUWTK1mH00":
        print("⚠️ ВНИМАНИЕ: Используется тестовый токен! Замените его на свой!")
    if ADMIN_ID == 5584463063:
        print("⚠️ ВНИМАНИЕ: Используется тестовый ADMIN_ID! Замените на свой!")
    if SITE_API_URL == "https://ваш-сайт.ru/api/post":
        print("ℹ️ Отправка на сайт не настроена. Замените SITE_API_URL если нужна эта функция.")

# Импорт для использования в других модулях
if __name__ == "__main__":
    validate_config()