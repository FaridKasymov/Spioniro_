import asyncio
import logging
import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot, Dispatcher
from aiogram.types import Message, BusinessMessagesDeleted, FSInputFile
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv

# ИМПОРТЫ ДЛЯ ШИФРОВАНИЯ
from security import MessageEncryptor
from cryptography.fernet import InvalidToken

# Загружаем переменные окружения
load_dotenv()

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_NAME = os.getenv("DB_NAME", "business_logs.db")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "downloads")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")

# Проверка обязательных параметров
if not API_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN не установлен! Установите его в переменной окружения или в .env файле")

if not ADMIN_ID:
    raise ValueError("ADMIN_ID не установлен! Установите его в переменной окружения или в .env файле")

if not ENCRYPTION_KEY:
    raise ValueError("ENCRYPTION_KEY не установлен! Установите его в .env файле")

# --- ИНИЦИАЛИЗАЦИЯ ---
bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
encryptor = MessageEncryptor(ENCRYPTION_KEY)

def format_date(iso_date_str: str) -> str:
    """Берет дату из базы данных и делает её красивой."""
    if not iso_date_str:
        return "Неизвестно"
    try:
        # Превращаем строку в объект времени
        dt = datetime.fromisoformat(iso_date_str)
        # Задаем нужный формат: День.Месяц.Год, Часы:Минуты:Секунды
        return dt.strftime("%d.%m.%Y, %H:%M:%S")
    except Exception:
        # Если что-то пойдет не так, возвращаем как было, чтобы бот не сломался
        return iso_date_str

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                message_id INTEGER,
                chat_id INTEGER,
                user_id INTEGER,
                user_name TEXT,
                message_text TEXT,
                file_path TEXT, 
                media_type TEXT,
                date TEXT,
                PRIMARY KEY (message_id, chat_id)
            )
        """)
        # Попытка миграции для старых баз
        try:
            await db.execute("ALTER TABLE messages ADD COLUMN media_type TEXT")
        except:
            pass
        await db.commit()

async def save_message(message: Message):
    text = message.text or message.caption or ""
    file_path = None
    media_type = "text"
    file_id = None
    file_ext = ""

    # --- ОПРЕДЕЛЕНИЕ ТИПА МЕДИА ---
    if message.photo:
        media_type = "photo"
        file_id = message.photo[-1].file_id
        file_ext = ".jpg"
    elif message.video:
        media_type = "video"
        file_id = message.video.file_id
        file_ext = ".mp4"
    elif message.voice:
        media_type = "voice"
        file_id = message.voice.file_id
        file_ext = ".ogg"
    elif message.video_note:
        media_type = "video_note"
        file_id = message.video_note.file_id
        file_ext = ".mp4"
    elif message.document:
        media_type = "document"
        file_id = message.document.file_id
        file_ext = os.path.splitext(message.document.file_name)[1] if message.document.file_name else ""

    # --- ЗАГРУЗКА ФАЙЛА ---
    if file_id:
        file_name = f"{message.chat.id}_{message.message_id}{file_ext}"
        destination = os.path.join(DOWNLOAD_DIR, file_name)
        
        # Скачиваем файл только если его еще нет или это новое сообщение
        if not os.path.exists(destination):
            try:
                file_info = await bot.get_file(file_id)
                if file_info.file_size and file_info.file_size < 20 * 1024 * 1024:
                    await bot.download_file(file_info.file_path, destination)
                    file_path = destination
                    if not text: text = f"[{media_type.upper()}]"
                else:
                    text += f"\n[Файл слишком большой]"
            except Exception as e:
                logging.error(f"Ошибка загрузки: {e}")
                text += f"\n[Ошибка загрузки]"
        else:
            file_path = destination

    if message.from_user:
        name = message.from_user.full_name
        user_id = message.from_user.id
    else:
        name = "Неизвестный"
        user_id = 0

    encrypted_text = encryptor.encrypt_message(text)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO messages (message_id, chat_id, user_id, user_name, message_text, file_path, media_type, datetime.now(ZoneInfo("Europe/Moscow")).isoformat()) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (message.message_id, message.chat.id, user_id, name, encrypted_text, file_path, media_type, datetime.now().isoformat())
        )
        await db.commit()

async def get_message_from_db(chat_id: int, message_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT user_id, user_name, message_text, file_path, media_type, date FROM messages WHERE chat_id = ? AND message_id = ?", 
            (chat_id, message_id)
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None

            user_id, user_name, encrypted_text, file_path, media_type, date = row
            
            # --- ИСПРАВЛЕННЫЙ БЛОК ---
            try:
                decrypted_text = encryptor.decrypt_message(encrypted_text)
            except InvalidToken:
                # Поддержка старых незашифрованных записей в базе.
                decrypted_text = encrypted_text
                
            return (user_id, user_name, decrypted_text, file_path, media_type, date)

# --- ХЭНДЛЕРЫ ---

@dp.business_message()
async def monitor_business_messages(message: Message):
    await save_message(message)

@dp.edited_business_message()
async def handle_edited_messages(message: Message):
    # Если это вы (Админ), ничего не делаем
    if message.from_user and message.from_user.id == ADMIN_ID:
        return

    # 1. Сначала достаем старую версию из базы
    old_msg = await get_message_from_db(message.chat.id, message.message_id)
    
    # Новый текст (учитываем и подписи к медиа)
    new_text = message.text or message.caption or ""
    
    # 2. Если сообщение было в базе, сравниваем и уведомляем
    if old_msg:
        # Распаковываем данные
        _, user_name, old_text, file_path, media_type, date_str = old_msg
        
        # Если текст изменился
        if old_text != new_text:
            # Делаем дату красивой
            beautiful_date = format_date(date_str)
            
            # Формируем презентабельный отчет
            report = (
                f"✏️ <b>Сообщение отредактировано!</b>\n\n"
                f"👤 <b>Автор:</b> {user_name}\n"
                f"🕒 <b>Дата оригинала:</b> {beautiful_date}\n"
                f"📁 <b>Тип:</b> {media_type}\n\n"
                f"❌ <b>БЫЛО:</b>\n<code>{old_text}</code>\n\n"
                f"✅ <b>СТАЛО:</b>\n<code>{new_text}</code>"
            )
            
            try:
                await bot.send_message(chat_id=ADMIN_ID, text=report)
            except Exception as e:
                logging.error(f"Ошибка отправки отчета о редактировании: {e}")
                
    # 3. В конце обновляем запись в базе данных на новую версию
    await save_message(message)

@dp.deleted_business_messages()
async def handle_deleted_messages(event: BusinessMessagesDeleted):
    for msg_id in event.message_ids:
        saved_msg = await get_message_from_db(event.chat.id, msg_id)
        
        if saved_msg:
            saved_user_id, user_name, text, file_path, media_type, date_str = saved_msg
            
            # Если удалил админ (вы) - пропускаем
            if saved_user_id == ADMIN_ID:
                continue
            
            # --- ПРИМЕНЯЕМ НАШУ ФУНКЦИЮ ЗДЕСЬ ---
            beautiful_date = format_date(date_str)
            
            caption_text = (
                f"🗑 <b>Сообщение удалили!</b>\n\n" 
                f"👤 <b>От:</b> {user_name}\n"
                f"📁 <b>Тип:</b> {media_type}\n"
                f"🕒 <b>Дата:</b> {beautiful_date}\n\n"
                f"📝 <b>Текст:</b> {text}"
            )

            try:
                if file_path and os.path.exists(file_path):
                    file_to_send = FSInputFile(file_path)
                    
                    if media_type == "photo":
                        await bot.send_photo(chat_id=ADMIN_ID, photo=file_to_send, caption=caption_text)
                    elif media_type == "video":
                        await bot.send_video(chat_id=ADMIN_ID, video=file_to_send, caption=caption_text)
                    elif media_type == "voice":
                        await bot.send_voice(chat_id=ADMIN_ID, voice=file_to_send, caption=caption_text)
                    elif media_type == "video_note":
                        await bot.send_video_note(chat_id=ADMIN_ID, video_note=file_to_send)
                        await bot.send_message(chat_id=ADMIN_ID, text=caption_text)
                    else:
                        await bot.send_document(chat_id=ADMIN_ID, document=file_to_send, caption=caption_text)
                else:
                    await bot.send_message(chat_id=ADMIN_ID, text=caption_text)
            except Exception as e:
                logging.error(f"Ошибка отправки отчета: {e}")
                await bot.send_message(chat_id=ADMIN_ID, text=caption_text)

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout
    )
    await init_db()
    logging.info("Бот запущен. Все системы активны.")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Ошибка: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Стоп.")