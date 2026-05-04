from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import aiosqlite
from pathlib import Path
import os
import hmac
import hashlib
import urllib.parse
import json
from dotenv import load_dotenv

# --- НОВЫЕ ИМПОРТЫ ДЛЯ ШИФРОВАНИЯ ---
from security import MessageEncryptor
from cryptography.fernet import InvalidToken

app = FastAPI(title="Telegram Bot Admin API")

# Разрешаем CORS (нужно для Railway)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = FRONTEND_DIR.parent
DB_NAME = PROJECT_ROOT / "business_logs.db"
DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
INDEX_FILE = FRONTEND_DIR / "index.html"

app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")

# Загружаем настройки и ключи
load_dotenv(PROJECT_ROOT / ".env")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Инициализируем дешифратор
if ENCRYPTION_KEY:
    encryptor = MessageEncryptor(ENCRYPTION_KEY)
else:
    encryptor = None

# --- ЗАЩИТА ---
def verify_telegram_web_app_data(init_data: str = Header(None, alias="X-Telegram-Init-Data")):
    """Проверяет подпись от Телеграма и убеждается, что это админ."""
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing init data")

    try:
        # Парсим строку initData
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
        if 'hash' not in parsed_data:
             raise HTTPException(status_code=401, detail="Missing hash")
        
        received_hash = parsed_data.pop('hash')
        
        # Сортируем ключи и создаем строку проверки
        data_check_string = "\n".join([f"{k}={v}" for k, v in sorted(parsed_data.items())])
        
        # Генерируем секретный ключ с помощью токена бота
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        
        # Считаем хеш и сравниваем с тем, что прислал Телеграм
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        if calculated_hash != received_hash:
            raise HTTPException(status_code=403, detail="Invalid signature")

        # Достаем юзера и проверяем, что он админ
        user_data_str = parsed_data.get('user', '{}')
        user_data = json.loads(user_data_str)
        user_id = user_data.get('id')
        
        if user_id != ADMIN_ID:
            raise HTTPException(status_code=403, detail="Access denied. You are not the admin.")
            
        return user_data

    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {e}")

def get_directory_size(path: str) -> int:
    """Считает размер всех файлов в папке (в байтах)."""
    total_size = 0
    if os.path.exists(path):
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
    return total_size

@app.get("/")
async def root():
    """Отдаем HTML. Он публичный, но данные он не получит без проверки."""
    return FileResponse(INDEX_FILE)

# --- ТЕПЕРЬ РОУТЫ ЗАЩИЩЕНЫ ---
# Добавили параметр user_data: dict = Depends(verify_telegram_web_app_data)
# Если юзер не прошел проверку в функции выше, код ниже даже не запустится.

@app.get("/api/stats")
async def get_stats(user_data: dict = Depends(verify_telegram_web_app_data)):
    """Собирает цифры из базы данных и папки для нашей админки."""
    try:
        async with aiosqlite.connect(str(DB_NAME)) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE status IN ('edited', 'deleted') AND user_id != ?", 
                (ADMIN_ID,)
            ) as cursor:
                row = await cursor.fetchone()
                total_messages = row[0] if row else 0
                
            async with db.execute("""
                SELECT user_name, COUNT(*) as msg_count 
                FROM messages 
                WHERE status IN ('edited', 'deleted') AND user_id != ?
                GROUP BY user_name 
                ORDER BY msg_count DESC 
                LIMIT 3
            """, (ADMIN_ID,)) as cursor:
                top_users = await cursor.fetchall()

        size_bytes = get_directory_size(DOWNLOAD_DIR)
        size_mb = round(size_bytes / (1024 * 1024), 2)

        return {
            "status": "ok",
            "total_messages": total_messages,
            "top_users": [{"name": user[0], "count": user[1]} for user in top_users],
            "folder_size_mb": size_mb
        }
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/api/media")
async def get_media(user_data: dict = Depends(verify_telegram_web_app_data)):
    """Список медиафайлов из БД."""
    try:
        async with aiosqlite.connect(str(DB_NAME)) as db:
            async with db.execute("""
                SELECT user_name, media_type, file_path, date
                FROM messages
                WHERE file_path IS NOT NULL
                  AND status IN ('edited', 'deleted')
                ORDER BY date DESC
            """) as cursor:
                rows = await cursor.fetchall()

        media = []
        for row in rows:
            user_name, media_type, file_path, date = row
            if not file_path or not os.path.exists(file_path):
                continue
            name = os.path.basename(file_path)
            url = f"/downloads/{name}"
            media.append({
                "user_name": user_name,
                "type": media_type,
                "url": url,
                "date": date,
            })

        return {"status": "ok", "media": media}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@app.get("/api/messages/{user_name}")
async def get_user_messages(user_name: str, user_data: dict = Depends(verify_telegram_web_app_data)):
    """Возвращает сообщения."""
    try:
        async with aiosqlite.connect(str(DB_NAME)) as db:
            async with db.execute("""
                SELECT message_text, media_type, date, status, old_message_text, file_path
                FROM messages
                WHERE user_name = ? AND status IN ('edited', 'deleted')
                ORDER BY date DESC
            """, (user_name,)) as cursor:
                messages = await cursor.fetchall()
        
        result = []
        for msg in messages:
            raw_text = msg[0]
            raw_old_text = msg[4]
            decrypted_text = raw_text
            decrypted_old = raw_old_text
            url = f"/downloads/{os.path.basename(msg[5])}" if msg[5] else None

            if encryptor and raw_text:
                try:
                    decrypted_text = encryptor.decrypt_message(raw_text)
                except InvalidToken:
                    pass

            if encryptor and raw_old_text:
                try:
                    decrypted_old = encryptor.decrypt_message(raw_old_text)
                except InvalidToken:
                    decrypted_old = raw_old_text

            result.append({
                "text": decrypted_text,
                "old_text": decrypted_old if raw_old_text else "",
                "type": msg[1],
                "date": msg[2],
                "status": msg[3],
                "url": url,
            })
            
        return {"status": "ok", "messages": result}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

import uvicorn
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("frontend.api:app", host="0.0.0.0", port=port)
