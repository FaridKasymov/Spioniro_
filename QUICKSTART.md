# Быстрый старт

## 1. Настройка окружения

Создайте файл `.env` из примера:
```bash
cp env.example .env
```

Отредактируйте `.env` и укажите:
- `TELEGRAM_BOT_TOKEN` - ваш токен бота от [@BotFather](https://t.me/BotFather)
- `ADMIN_ID` - ваш Telegram ID (можно узнать у [@userinfobot](https://t.me/userinfobot))

## 2. Установка зависимостей

```bash
pip install -r requirements.txt
```

Или с виртуальным окружением:
```bash
python3 -m venv venv
source venv/bin/activate  # На Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 3. Настройка Business Bot

1. Откройте Telegram
2. Перейдите в **Настройки** → **Telegram для бизнеса**
3. В разделе **Боты** нажмите **Добавить бота**
4. Выберите вашего бота из списка

## 4. Запуск

```bash
python bot.py
```

Готово! Бот начнет мониторить все сообщения в ваших Business чатах.

