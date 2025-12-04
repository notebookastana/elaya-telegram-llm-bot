# -*- coding: utf-8 -*-
"""
ELAYA GPT - AI-ассистент на базе знаний Элайи
Telegram Bot на базе Ollama LLM

Версия 1.0 - Форк TOR с автоматическим RAG
- Автоинициализация RAG при старте
- RAG по умолчанию (без /ask)
- Умная работа в группах (reply, вопросы "в воздух")

На основе проекта TOR: https://github.com/notebookastana/tor-bot
"""

import logging
import hashlib
import json
import aiosqlite 
import requests
import asyncio
import os
from datetime import datetime
from collections import deque
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command 
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup 
from aiogram.exceptions import TelegramBadRequest
from config import (
    TELEGRAM_BOT_TOKEN, OLLAMA_URL, DEFAULT_MODEL,
    DEEP_MODEL, DB_PATH, MAX_STREAM_TIMEOUT
)
from rag_manager import rag_manager

# === КОНФИГУРАЦИЯ ELAYA ===
BOT_NAME = "ELAYA"
BOT_DESCRIPTION = "AI-ассистент на базе знаний Элайи"
CONTEXT_WINDOW = 10
MAX_TELEGRAM_LENGTH = 4096
CURRENT_TEMPERATURE = 0.7  # Чуть ниже для более точных ответов по базе

# RAG НАСТРОЙКИ
RAG_ENABLED = False  # Включится автоматически при старте
RAG_AUTO_INIT = True  # Автоинициализация при старте сервера
RAG_ALWAYS_SEARCH = True  # Всегда искать в документах (без /ask)
RAG_RELEVANCE_THRESHOLD = 1.5  # Порог релевантности (меньше = строже)
RAG_TOP_K = 5  # Количество чанков для контекста

# === НАСТРОЙКИ ОЧЕРЕДЕЙ ===
MAX_CONCURRENT_REQUESTS = 1  # ВАЖНО: 1 для CPU!
MAX_QUEUE_SIZE = 10
REQUEST_TIMEOUT = 600  # 10 минут

# === НАСТРОЙКИ ДЛЯ ГРУПП ===
# Режимы: "mention_only" | "all" | "smart"
GROUP_RESPONSE_MODE = "smart"
GROUP_CONTEXT_ENABLED = True
GROUP_ADMIN_ONLY_COMMANDS = ["clear", "temp", "stats", "rag_clear"]

# === ДОСТУПНЫЕ МОДЕЛИ ===
AVAILABLE_MODELS = [
    "qwen2.5:7b-instruct-q4_K_M",
    "mistral:7b-instruct-q4_K_M"
]

# Создаём папку data
os.makedirs("./data", exist_ok=True)

# === Логирование ===
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("elaya")

# === Инициализация ===
bot = Bot(token=TELEGRAM_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
db_conn = None

# === FSM States ===
class BotStates(StatesGroup):
    deep_mode = State()

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def escape_html(text: str) -> str:
    """Экранирует специальные символы HTML"""
    if not text:
        return ""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

# ============================================
# СИСТЕМА ОЧЕРЕДЕЙ
# ============================================

class RequestQueue:
    """Управление очередью запросов к LLM"""
    
    def __init__(self, max_concurrent: int = 2, max_queue_size: int = 10):
        self.max_concurrent = max_concurrent
        self.max_queue_size = max_queue_size
        self.active_requests = 0
        self.queue = deque()
        self.lock = asyncio.Lock()
        self.queue_stats = {
            'total_processed': 0,
            'total_queued': 0,
            'total_rejected': 0,
            'avg_wait_time': 0
        }
    
    async def can_process(self) -> bool:
        async with self.lock:
            return self.active_requests < self.max_concurrent
    
    async def add_to_queue(self, request_data: dict) -> int:
        async with self.lock:
            if len(self.queue) >= self.max_queue_size:
                self.queue_stats['total_rejected'] += 1
                return -1
            
            request_data['queued_at'] = datetime.now()
            self.queue.append(request_data)
            self.queue_stats['total_queued'] += 1
            position = len(self.queue)
            logger.info(f"📋 Request queued. Position: {position}")
            return position
    
    async def start_processing(self):
        async with self.lock:
            self.active_requests += 1
            logger.info(f"🔄 Active: {self.active_requests}/{self.max_concurrent}")
    
    async def finish_processing(self):
        async with self.lock:
            self.active_requests = max(0, self.active_requests - 1)
            self.queue_stats['total_processed'] += 1
            logger.info(f"✅ Finished. Active: {self.active_requests}/{self.max_concurrent}")
    
    async def get_next_request(self):
        async with self.lock:
            if self.queue:
                request = self.queue.popleft()
                wait_time = (datetime.now() - request['queued_at']).total_seconds()
                
                total = self.queue_stats['total_processed']
                if total > 0:
                    avg = self.queue_stats['avg_wait_time']
                    self.queue_stats['avg_wait_time'] = (avg * total + wait_time) / (total + 1)
                else:
                    self.queue_stats['avg_wait_time'] = wait_time
                
                logger.info(f"⏱️ Waited {wait_time:.1f}s in queue")
                return request
            return None
    
    async def get_queue_info(self) -> dict:
        async with self.lock:
            return {
                'active': self.active_requests,
                'queued': len(self.queue),
                'max_concurrent': self.max_concurrent,
                'stats': self.queue_stats.copy()
            }

# Глобальная очередь
request_queue = RequestQueue(max_concurrent=MAX_CONCURRENT_REQUESTS, max_queue_size=MAX_QUEUE_SIZE)

async def queue_processor():
    """Фоновый процесс обработки очереди"""
    logger.info("🔄 Queue processor started")
    
    while True:
        try:
            if await request_queue.can_process():
                request_data = await request_queue.get_next_request()
                
                if request_data:
                    asyncio.create_task(process_queued_request(request_data))
            
            await asyncio.sleep(0.5)
            
        except Exception as e:
            logger.exception(f"Error in queue processor: {e}")
            await asyncio.sleep(1)

async def process_queued_request(request_data: dict):
    """Обрабатывает запрос из очереди"""
    await request_queue.start_processing()
    
    try:
        await process_message(
            request_data['message'],
            request_data['model'],
            request_data['is_deep']
        )
    except Exception as e:
        logger.exception(f"Error processing queued request: {e}")
        try:
            await request_data['message'].reply(f"❌ Ошибка: {escape_html(str(e))}", parse_mode="HTML")
        except:
            pass
    finally:
        await request_queue.finish_processing()

# ============================================
# УМНАЯ РАБОТА В ГРУППАХ
# ============================================

async def is_group_chat(message: types.Message) -> bool:
    """Проверяет, является ли чат групповым."""
    return message.chat.type in ["group", "supergroup"]

async def is_user_admin(message: types.Message) -> bool:
    """Проверяет, является ли пользователь администратором."""
    if not await is_group_chat(message):
        return True
    
    try:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in ["creator", "administrator"]
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

def is_bot_mentioned(message: types.Message) -> bool:
    """Проверяет прямое упоминание @username"""
    if not message.text:
        return False
    
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention = message.text[entity.offset:entity.offset + entity.length]
                bot_username = bot._me.username if hasattr(bot, '_me') and bot._me else None
                if bot_username and mention.lower() == f"@{bot_username.lower()}":
                    return True
    
    return False

def is_reply_to_bot(message: types.Message) -> bool:
    """Проверяет, является ли сообщение ответом на сообщение бота"""
    if message.reply_to_message:
        if message.reply_to_message.from_user:
            bot_id = bot._me.id if hasattr(bot, '_me') and bot._me else None
            if bot_id and message.reply_to_message.from_user.id == bot_id:
                return True
    return False

def is_question_in_air(message: types.Message) -> bool:
    """Проверяет, является ли сообщение вопросом "в воздух" (не адресованным конкретному человеку)"""
    if not message.text:
        return False
    
    text = message.text.strip()
    
    # Должен заканчиваться на вопросительный знак
    if not text.endswith("?"):
        return False
    
    # Не должен быть reply на другое сообщение (кроме сообщения бота)
    if message.reply_to_message:
        if not is_reply_to_bot(message):
            return False
    
    # Не должен содержать упоминания других пользователей
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention = message.text[entity.offset:entity.offset + entity.length]
                bot_username = bot._me.username if hasattr(bot, '_me') and bot._me else ""
                # Если упоминание не бота — это вопрос к другому человеку
                if bot_username and mention.lower() != f"@{bot_username.lower()}":
                    return False
    
    return True

def should_respond_in_group(message: types.Message) -> bool:
    """Определяет, нужно ли боту отвечать в группе"""
    
    if GROUP_RESPONSE_MODE == "all":
        return True
    
    if GROUP_RESPONSE_MODE == "mention_only":
        return is_bot_mentioned(message) or is_reply_to_bot(message)
    
    # === SMART MODE ===
    # 1. Прямое упоминание @ELAYA_GPT_bot
    if is_bot_mentioned(message):
        logger.info("📣 Triggered: direct mention")
        return True
    
    # 2. Ответ на сообщение бота (reply)
    if is_reply_to_bot(message):
        logger.info("↩️ Triggered: reply to bot")
        return True
    
    # 3. Вопрос "в воздух" (заканчивается на ?, не адресован другому)
    if is_question_in_air(message):
        logger.info("❓ Triggered: question in air")
        return True
    
    return False

def remove_bot_mention(text: str, bot_username: str = None) -> str:
    """Удаляет упоминание бота из текста."""
    if not bot_username:
        return text
    # Удаляем упоминание (регистронезависимо)
    import re
    pattern = re.compile(re.escape(f"@{bot_username}"), re.IGNORECASE)
    text = pattern.sub("", text).strip()
    return text

async def get_group_context_id(message: types.Message) -> int:
    """Возвращает ID для контекста."""
    if await is_group_chat(message):
        return message.chat.id
    return message.from_user.id

# ============================================
# ФУНКЦИИ РАБОТЫ С OLLAMA
# ============================================

async def check_ollama() -> bool:
    """Проверяет доступность Ollama."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.ok:
            models = response.json().get('models', [])
            logger.info(f"✅ Ollama доступна. Моделей: {len(models)}")
            
            model_names = [m.get('name', '') for m in models]
            
            if not any(DEFAULT_MODEL in name for name in model_names):
                logger.warning(f"⚠️ Модель {DEFAULT_MODEL} не найдена")
            
            return True
    except requests.exceptions.ConnectionError:
        logger.error("❌ Ollama недоступна! Запусти: ollama serve")
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки Ollama: {e}")
        return False

def call_ollama_stream(model: str, prompt: str, timeout: int = REQUEST_TIMEOUT, temperature: float = 0.7) -> str:
    """Отправляет запрос к Ollama."""
    logger.info(f"🔗 Ollama: {model}, temp: {temperature}")
    
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": temperature,
        "top_p": 0.95,
        "top_k": 50,
        "num_ctx": 8192,
        "stream": True
    }
    
    try:
        response = requests.post(OLLAMA_URL, json=payload, stream=True, timeout=timeout)
        response.raise_for_status()
        
        full_response = ""
        chunk_count = 0
        
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                obj = json.loads(line)
                if "response" in obj:
                    full_response += obj["response"]
                    chunk_count += 1
                if obj.get("error"):
                    logger.error(f"❌ Ollama error: {obj['error']}")
                    return f"Ошибка Ollama: {obj['error']}"
            except json.JSONDecodeError:
                continue
        
        logger.info(f"✅ Response: {len(full_response)} chars")
        return full_response.strip()
        
    except requests.exceptions.Timeout:
        logger.error(f"⏱️ Timeout {timeout}s")
        return "⏱️ Превышен таймаут ответа."
    except requests.exceptions.ConnectionError:
        logger.error("❌ Connection error")
        return "❌ Не удалось подключиться к Ollama."
    except Exception as e:
        logger.exception(f"❌ Ollama error: {e}")
        return f"❌ Ошибка: {e}"

def call_ollama_with_context(model: str, prompt: str, context_docs: list, timeout: int = REQUEST_TIMEOUT, temperature: float = 0.7) -> str:
    """Отправляет запрос к Ollama с RAG контекстом"""
    context_parts = []
    for i, doc in enumerate(context_docs, 1):
        source = doc['source']
        content = doc['content']
        context_parts.append(f"[Источник {i}: {source}]\n{content}\n")
    
    context_text = "\n---\n".join(context_parts)
    
    # Языковые якоря для предотвращения переключения на китайский
    full_prompt = (
        "[ЯЗЫК: РУССКИЙ. Отвечай ТОЛЬКО на русском языке.]\n\n"
        f"Ты - {BOT_NAME}, русскоязычный AI-ассистент на базе знаний Элайи. "
        f"Отвечай тепло, мудро и с любовью, как это делает Элайя.\n\n"
        f"У тебя есть доступ к следующим материалам:\n\n"
        f"{context_text}\n"
        f"---\n\n"
        f"Используя эту информацию, ответь на вопрос пользователя.\n"
        f"Если в материалах нет точного ответа, поделись мудростью на эту тему.\n"
        f"Указывай источники, если это уместно.\n\n"
        f"Вопрос: {prompt}\n\n"
        "[Помни: отвечай ТОЛЬКО на русском языке!]\n"
        f"Ответ на русском:"
    )
    
    return call_ollama_stream(model, full_prompt, timeout, temperature)

# ============================================
# ФУНКЦИИ РАБОТЫ С БД
# ============================================

async def init_db():
    """Инициализирует базу данных."""
    global db_conn
    db_conn = await aiosqlite.connect(DB_PATH)
    
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            prompt_hash TEXT PRIMARY KEY,
            prompt TEXT,
            response TEXT,
            model TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            prompt TEXT,
            response TEXT,
            model TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    await db_conn.execute("""
        CREATE TABLE IF NOT EXISTS user_activity (
            user_id INTEGER PRIMARY KEY,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0
        )
    """)
    
    await db_conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_user_id ON logs(user_id)")
    await db_conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON cache(timestamp)")
    
    await db_conn.commit()
    logger.info("✅ База данных готова")

def prompt_hash(prompt: str, model: str) -> str:
    return hashlib.sha256((prompt + "|" + model).encode("utf-8")).hexdigest()

async def get_cached(prompt: str, model: str):
    if db_conn is None:
        return None
    
    h = prompt_hash(prompt, model)
    try:
        async with db_conn.execute("SELECT response FROM cache WHERE prompt_hash = ?", (h,)) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"❌ Cache read error: {e}")
        return None

async def save_cache(prompt: str, model: str, response: str):
    if db_conn is None:
        return
    
    h = prompt_hash(prompt, model)
    try:
        await db_conn.execute(
            "INSERT OR REPLACE INTO cache (prompt_hash, prompt, response, model) VALUES (?, ?, ?, ?)",
            (h, prompt, response, model)
        )
        await db_conn.commit()
    except Exception as e:
        logger.error(f"❌ Cache save error: {e}")

async def log_dialog(context_id: int, prompt: str, response: str, model: str):
    if db_conn is None:
        return
    
    try:
        await db_conn.execute(
            "INSERT INTO logs (user_id, prompt, response, model) VALUES (?, ?, ?, ?)",
            (context_id, prompt, response, model)
        )
        await db_conn.commit()
    except Exception as e:
        logger.error(f"❌ Log error: {e}")

async def update_user_activity(user_id: int):
    if db_conn is None:
        return
    
    try:
        await db_conn.execute("""
            INSERT INTO user_activity (user_id, last_seen, message_count) 
            VALUES (?, CURRENT_TIMESTAMP, 1)
            ON CONFLICT(user_id) DO UPDATE SET 
                last_seen = CURRENT_TIMESTAMP,
                message_count = message_count + 1
        """, (user_id,))
        await db_conn.commit()
    except Exception as e:
        logger.error(f"❌ Activity update error: {e}")

async def get_dialogue_context(context_id: int) -> str:
    if db_conn is None:
        return ""
    
    query = """
        SELECT prompt, response FROM logs
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ?
    """
    try:
        async with db_conn.execute(query, (context_id, CONTEXT_WINDOW * 2)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return ""
        
        rows.reverse()
        
        context_parts = []
        for prompt, response in rows:
            cleaned_response = response.replace(" (cache)", "").replace(" (RAG)", "")
            context_parts.append(f"Пользователь: {prompt}\n")
            context_parts.append(f"{BOT_NAME}: {cleaned_response}\n")
            
        return "".join(context_parts)
    except Exception as e:
        logger.error(f"❌ Context error: {e}")
        return ""

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def split_text(text: str, max_length: int = MAX_TELEGRAM_LENGTH) -> list[str]:
    if not text:
        return [""]
    
    chunks = []
    while len(text) > max_length:
        split_index = text.rfind('\n\n', 0, max_length)
        if split_index == -1:
            split_index = text.rfind('. ', 0, max_length)
        if split_index == -1:
            split_index = text.rfind(' ', 0, max_length)
        if split_index == -1 or split_index == 0:
            split_index = max_length

        chunks.append(text[:split_index].strip())
        text = text[split_index:].strip()
    
    if text:
        chunks.append(text)
    
    return chunks

async def send_long_message(message: types.Message, text: str, parse_mode: str = "HTML"):
    """Отправляет длинное сообщение частями."""
    chunks = split_text(text)
    
    for i, chunk in enumerate(chunks):
        try:
            if i == 0:
                await message.reply(chunk, parse_mode=parse_mode)
            else:
                await message.answer(chunk, parse_mode=parse_mode)
        except TelegramBadRequest as e:
            logger.warning(f"Format error in part {i+1}: {e}")
            try:
                if i == 0:
                    await message.reply(chunk, parse_mode=None)
                else:
                    await message.answer(chunk, parse_mode=None)
            except Exception as e2:
                logger.error(f"❌ Send error: {e2}")

async def show_typing_periodic(chat_id: int, stop_event: asyncio.Event):
    """Периодически отправляет индикатор набора"""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
            break
        except asyncio.TimeoutError:
            continue

# ============================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    is_group = await is_group_chat(message)
    
    if is_group:
        bot_username = bot._me.username if hasattr(bot, '_me') and bot._me else "бота"
        await message.reply(
            f"✨ Привет! Я <b>{BOT_NAME}</b> — {BOT_DESCRIPTION}.\n\n"
            f"💫 Как общаться со мной:\n"
            f"• Упомяни меня: @{escape_html(bot_username)}\n"
            f"• Ответь на моё сообщение\n"
            f"• Задай вопрос (с ? в конце)\n\n"
            "🌸 Я здесь, чтобы помочь тебе!",
            parse_mode="HTML"
        )
    else:
        stats = rag_manager.get_stats() if RAG_ENABLED else {'total_chunks': 0, 'total_sources': 0}
        
        await message.reply(
            f"✨ <b>{BOT_NAME}</b> — {BOT_DESCRIPTION}\n\n"
            "🌸 Привет, дорогой друг!\n\n"
            "Я — твой проводник в мир знаний Элайи. "
            "Просто задавай вопросы — я найду ответы в базе знаний.\n\n"
            "💫 <b>Как я работаю:</b>\n"
            "• Просто пиши — я отвечу\n"
            "• Ищу ответы в материалах Элайи\n"
            "• Помню контекст нашего диалога\n\n"
            "📚 <b>Команды:</b>\n"
            "/deep — глубокий режим (дольше, умнее)\n"
            "/clear — очистить историю\n"
            "/stats — статистика\n"
            "/rag_stats — статистика базы знаний\n"
            "/help — справка\n\n"
            f"📖 База знаний: {stats['total_chunks']} фрагментов из {stats['total_sources']} документов\n\n"
            "💜 Спрашивай — я с радостью помогу!",
            parse_mode="HTML"
        )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    is_group = await is_group_chat(message)
    
    group_help = ""
    if is_group:
        group_help = (
            "\n\n<b>💬 В группе я отвечаю на:</b>\n"
            "• Прямые упоминания (@...)\n"
            "• Ответы на мои сообщения\n"
            "• Вопросы с ? в конце"
        )
    
    await message.reply(
        f"📖 <b>Справка {BOT_NAME}</b>\n\n"
        "<b>Основное:</b>\n"
        "💬 Просто напиши — я отвечу\n"
        "/start — приветствие\n"
        "/help — эта справка\n\n"
        "<b>Режимы:</b>\n"
        "/deep — глубокий режим\n"
        "/clear — очистить историю\n\n"
        "<b>Статистика:</b>\n"
        "/stats — общая статистика\n"
        "/rag_stats — база знаний\n"
        "/queue — очередь запросов"
        f"{group_help}",
        parse_mode="HTML"
    )

@dp.message(Command("queue"))
async def cmd_queue(message: types.Message):
    info = await request_queue.get_queue_info()
    stats = info['stats']
    
    await message.reply(
        f"📋 <b>Очередь:</b>\n\n"
        f"🔄 Активных: {info['active']}/{info['max_concurrent']}\n"
        f"⏳ В очереди: {info['queued']}\n\n"
        f"📊 Обработано: {stats['total_processed']}\n"
        f"⏱️ Среднее ожидание: {stats['avg_wait_time']:.1f}с",
        parse_mode="HTML"
    )

@dp.message(Command("clear"))
async def cmd_clear(message: types.Message):
    if await is_group_chat(message):
        if not await is_user_admin(message):
            await message.reply("⛔ Только для админов")
            return
    
    if db_conn is None:
        await message.reply("❌ База данных не готова")
        return
    
    context_id = await get_group_context_id(message)
    
    try:
        await db_conn.execute("DELETE FROM logs WHERE user_id = ?", (context_id,))
        await db_conn.commit()
        
        chat_type = "группы" if await is_group_chat(message) else "диалога"
        await message.reply(f"🗑️ История {chat_type} очищена!")
    except Exception as e:
        logger.error(f"❌ Clear error: {e}")
        await message.reply("❌ Ошибка очистки")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if db_conn is None:
        await message.reply("❌ База данных не готова")
        return
    
    context_id = await get_group_context_id(message)
    
    try:
        async with db_conn.execute("SELECT COUNT(*) FROM logs WHERE user_id = ?", (context_id,)) as cursor:
            messages_count = (await cursor.fetchone())[0]
        
        async with db_conn.execute("SELECT COUNT(*) FROM cache") as cursor:
            cache_count = (await cursor.fetchone())[0]
        
        rag_stats = rag_manager.get_stats() if RAG_ENABLED else {'total_chunks': 0}
        
        await message.reply(
            f"📊 <b>Статистика:</b>\n\n"
            f"💬 Сообщений: {messages_count}\n"
            f"🗄️ В кэше: {cache_count}\n"
            f"📚 Фрагментов RAG: {rag_stats.get('total_chunks', 0)}\n"
            f"🌡️ Температура: {CURRENT_TEMPERATURE}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"❌ Stats error: {e}")
        await message.reply("❌ Ошибка статистики")

@dp.message(Command("rag_stats"))
async def cmd_rag_stats(message: types.Message):
    if not RAG_ENABLED:
        await message.reply("⚠️ RAG не активирована")
        return
    
    stats = rag_manager.get_stats()
    
    if stats['status'] == 'ready':
        sources_lines = []
        for source, count in stats.get('sources', {}).items():
            safe_source = escape_html(source)
            sources_lines.append(f"   • <code>{safe_source}</code>: {count}")
        sources_text = "\n".join(sources_lines) if sources_lines else "   (пусто)"
        
        await message.reply(
            f"📚 <b>База знаний {BOT_NAME}:</b>\n\n"
            f"📦 Всего фрагментов: {stats['total_chunks']}\n"
            f"📄 Документов: {stats['total_sources']}\n\n"
            f"<b>Источники:</b>\n{sources_text}",
            parse_mode="HTML"
        )
    else:
        await message.reply(f"❌ Статус: {escape_html(stats['status'])}", parse_mode="HTML")

@dp.message(Command("deep"))
async def cmd_deep(message: types.Message, state: FSMContext):
    await state.set_state(BotStates.deep_mode)
    
    await message.reply(
        f"🔮 <b>Глубокий режим активирован!</b>\n\n"
        f"Модель: <b>{escape_html(DEEP_MODEL)}</b>\n\n"
        f"⚠️ <i>Ответ может занять несколько минут</i>\n\n"
        f"Задавай свой вопрос:",
        parse_mode="HTML"
    )

@dp.message(Command("rag_clear"))
async def cmd_rag_clear(message: types.Message):
    if await is_group_chat(message):
        if not await is_user_admin(message):
            await message.reply("⛔ Только для админов")
            return
    
    if not RAG_ENABLED:
        await message.reply("⚠️ RAG не активирована")
        return
    
    await message.reply(
        "⚠️ <b>Внимание!</b>\n\n"
        "Все документы будут удалены из базы!\n\n"
        "Для подтверждения отправьте: <code>да, удалить</code>",
        parse_mode="HTML"
    )

# ============================================
# ОБРАБОТКА СООБЩЕНИЙ
# ============================================

async def process_message(message: types.Message, model: str, is_deep: bool = False):
    """Главная логика обработки сообщений с автоматическим RAG"""
    user_text = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    
    # Удаляем упоминание бота из текста
    if hasattr(bot, '_me') and bot._me:
        user_text = remove_bot_mention(user_text, bot._me.username)
    
    if not user_text:
        await message.reply("❓ Напиши свой вопрос")
        return
    
    logger.info(f"📨 {username}: '{user_text[:50]}...'")
    
    await update_user_activity(user_id)
    
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(show_typing_periodic(message.chat.id, stop_typing))
    
    try:
        context_id = await get_group_context_id(message)
        
        # === АВТОМАТИЧЕСКИЙ RAG ===
        use_rag = False
        relevant_docs = []
        
        if RAG_ENABLED and RAG_ALWAYS_SEARCH:
            logger.info("🔍 Searching in RAG...")
            relevant_docs = rag_manager.search(user_text, k=RAG_TOP_K)
            
            # Проверяем релевантность
            if relevant_docs:
                best_score = relevant_docs[0]['score']
                logger.info(f"📊 Best RAG score: {best_score}")
                
                if best_score < RAG_RELEVANCE_THRESHOLD:
                    use_rag = True
                    logger.info(f"✅ Using RAG context ({len(relevant_docs)} docs)")
                else:
                    logger.info(f"⚠️ RAG score too low, using general mode")
        
        timeout = REQUEST_TIMEOUT * 2 if is_deep else REQUEST_TIMEOUT
        mode_emoji = "🔮" if is_deep else "💭"
        
        # Статус сообщение
        if use_rag:
            await message.reply(
                f"{mode_emoji} Ищу в базе знаний... <i>(найдено: {len(relevant_docs)})</i>",
                parse_mode="HTML"
            )
        else:
            await message.reply(
                f"{mode_emoji} Думаю...",
                parse_mode="HTML"
            )
        
        loop = asyncio.get_event_loop()
        
        if use_rag:
            # Ответ с RAG контекстом
            response = await loop.run_in_executor(
                None,
                call_ollama_with_context,
                model,
                user_text,
                relevant_docs,
                timeout,
                CURRENT_TEMPERATURE
            )
            model_tag = f"{model} (RAG)"
        else:
            # Обычный ответ
            dialogue_context = await get_dialogue_context(context_id)
            
            system_instruction = (
                f"Ты - {BOT_NAME}, мудрый и тёплый AI-ассистент. "
                f"Отвечай с любовью и заботой, как это делает Элайя. "
                "Отвечай ТОЛЬКО на последний вопрос пользователя. "
                "Не повторяй историю диалога. "
                "Пиши на русском языке."
            )
            
            full_prompt = (
                f"{system_instruction}\n\n"
                f"{dialogue_context}"
                f"Пользователь: {user_text}\n"
                f"{BOT_NAME}:"
            )
            
            response = await loop.run_in_executor(
                None,
                call_ollama_stream,
                model,
                full_prompt,
                timeout,
                CURRENT_TEMPERATURE
            )
            model_tag = model
        
        stop_typing.set()
        await typing_task
        
        if not response:
            response = "❌ Не получилось сформировать ответ"
        
        # Логируем
        await log_dialog(context_id, user_text, response, model_tag)
        
        # Формируем финальный ответ
        safe_response = escape_html(response)
        
        if use_rag:
            # Добавляем источники
            sources_list = list(set([doc['source'] for doc in relevant_docs[:3]]))
            sources_text = ", ".join([f"<i>{escape_html(s)}</i>" for s in sources_list])
            final_response = f"{safe_response}\n\n<b>📚</b> {sources_text}"
        else:
            final_response = safe_response
        
        await send_long_message(message, final_response, parse_mode="HTML")
        logger.info(f"✅ Response sent to {context_id}")
        
    except Exception as e:
        stop_typing.set()
        await typing_task
        logger.exception(f"❌ Error: {e}")
        await message.reply(f"❌ Ошибка: {escape_html(str(e))}", parse_mode="HTML")

@dp.message(BotStates.deep_mode)
async def handle_deep_mode(message: types.Message, state: FSMContext):
    if not message.text:
        await state.clear()
        return
    
    if await request_queue.can_process():
        await state.clear()
        await request_queue.start_processing()
        try:
            await process_message(message, DEEP_MODEL, is_deep=True)
        finally:
            await request_queue.finish_processing()
    else:
        position = await request_queue.add_to_queue({
            'message': message,
            'model': DEEP_MODEL,
            'is_deep': True
        })
        await state.clear()
        
        if position == -1:
            await message.reply("❌ Очередь переполнена!")
        else:
            await message.reply(f"⏳ В очереди. Позиция: {position}")

@dp.message()
async def handle_default(message: types.Message):
    if not message.text:
        return
    
    # Неизвестные команды
    if message.text.startswith('/'):
        await message.reply("❓ Неизвестная команда. /help для справки")
        return
    
    # Подтверждение удаления RAG
    if message.text.lower() == "да, удалить" and RAG_ENABLED:
        try:
            if rag_manager.clear_database():
                await message.reply("✅ База знаний очищена!")
            else:
                await message.reply("❌ Ошибка очистки")
        except Exception as e:
            await message.reply(f"❌ Ошибка: {escape_html(str(e))}", parse_mode="HTML")
        return
    
    # В группах — умная логика
    if await is_group_chat(message):
        if not should_respond_in_group(message):
            return
    
    logger.info(f"🎯 Processing from {message.from_user.id}")
    
    model = DEFAULT_MODEL
    
    if await request_queue.can_process():
        await request_queue.start_processing()
        try:
            await process_message(message, model)
        finally:
            await request_queue.finish_processing()
    else:
        position = await request_queue.add_to_queue({
            'message': message,
            'model': model,
            'is_deep': False
        })
        
        if position == -1:
            await message.reply("❌ Очередь переполнена!")
        else:
            await message.reply(f"⏳ В очереди. Позиция: {position}")

# ============================================
# ЗАПУСК БОТА
# ============================================

async def main():
    global RAG_ENABLED
    
    logger.info(f"🚀 Запуск {BOT_NAME}...")
    
    # Проверка Ollama
    if not await check_ollama():
        logger.error("🛑 Ollama недоступна!")
        return
    
    # Инициализация БД
    await init_db()
    
    # === АВТОИНИЦИАЛИЗАЦИЯ RAG ===
    if RAG_AUTO_INIT:
        logger.info("🔄 Автоинициализация RAG...")
        if rag_manager.initialize():
            RAG_ENABLED = True
            stats = rag_manager.get_stats()
            logger.info(f"✅ RAG активна: {stats['total_chunks']} чанков из {stats['total_sources']} документов")
        else:
            logger.warning("⚠️ RAG не инициализирована (возможно, нет документов)")
    
    # Получаем информацию о боте
    me = await bot.get_me()
    bot._me = me
    
    logger.info(f"🤖 Bot: @{me.username}")
    logger.info(f"📚 RAG: {'✅ Active' if RAG_ENABLED else '❌ Inactive'}")
    logger.info(f"🌡️ Temperature: {CURRENT_TEMPERATURE}")
    logger.info(f"👥 Group mode: {GROUP_RESPONSE_MODE}")
    
    # Запуск обработчика очереди
    queue_task = asyncio.create_task(queue_processor())
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        logger.info("⏹️ Остановка...")
        queue_task.cancel()
        try:
            await queue_task
        except asyncio.CancelledError:
            pass
        if db_conn:
            await db_conn.close()
        await bot.session.close()
        logger.info("✅ Ресурсы освобождены")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Остановлен (Ctrl+C)")
    except Exception as e:
        logger.exception(f"❌ Ошибка: {e}")
