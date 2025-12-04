import os
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

# --- Настройки бота ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("❌ TELEGRAM_BOT_TOKEN не установлен в .env файле!")

# --- Настройки Ollama ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "qwen2.5:7b-instruct-q4_K_M")
DEEP_MODEL = os.getenv("DEEP_MODEL", "mistral:7b-instruct-q4_K_M")

# --- Таймауты ---
MAX_STREAM_TIMEOUT = int(os.getenv("MAX_STREAM_TIMEOUT", "600"))

# --- База данных ---
DB_PATH = os.getenv("DB_PATH", "elaya_cache.db")
