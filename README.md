# Бот онбординга Ошки

Telegram-бот, который открывает курс-онбординг как Mini App, принимает от курса
статусы «Выполнил / Есть вопрос», пишет прогресс в Google-таблицу и уведомляет
наставника в личку.

```
[Бот] --кнопка--> [Курс (Mini App)] --статусы--> [HTTP /progress] --> [Google-таблица]
                                                          \--> уведомление наставнику
```

## Файлы
- `bot.py` — бот (aiogram) + HTTP-API в одном процессе.
- `storage.py` — запись прогресса в Google-таблицу (или в `progress.json` локально).
- `requirements.txt`, `Procfile`, `.env.example`.

---

## Шаг 1. Создать бота в @BotFather
1. Открой [@BotFather](https://t.me/BotFather) → `/newbot`.
2. Имя (любое) и username (заканчивается на `bot`).
3. BotFather пришлёт **токен** вида `123456:ABC...` → это `BOT_TOKEN`.

## Шаг 2. Узнать свой chat_id (для уведомлений)
Запустишь бота (шаг 4/5) → напиши ему `/id` → впиши число в `ADMIN_CHAT_ID`.

## Шаг 3. Google-таблица (прогресс) — через Apps Script
Без Google Cloud и сервис-аккаунта.
1. Создай пустую Google-таблицу.
2. В ней: **Расширения → Apps Script** → вставь код из `apps-script.gs` → сохрани.
3. **Развернуть (Deploy) → Новое развёртывание → Веб-приложение**:
   запуск «от имени Я», доступ «Все (Anyone)» → Развернуть → подтверди доступ.
4. Скопируй URL веб-приложения (заканчивается на `/exec`) → `APPS_SCRIPT_URL`.
5. Секрет `APPS_SCRIPT_SECRET` в `.env` должен совпадать с `SECRET` в `apps-script.gs`.

> Если `APPS_SCRIPT_URL` не задан — прогресс пишется в локальный `progress.json`.

## Шаг 4. Локальный запуск (для проверки)
```bash
cd onboarding-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполни BOT_TOKEN (минимум)
python bot.py
```

## Шаг 5. Деплой на Railway / Render (бесплатно)
1. Залей папку в репозиторий GitHub.
2. **Railway**: New Project → Deploy from GitHub → выбери репозиторий.
   В разделе **Variables** добавь `BOT_TOKEN`, `COURSE_URL`, `ADMIN_CHAT_ID`,
   `APPS_SCRIPT_URL`, `APPS_SCRIPT_SECRET`. Start command берётся из `Procfile`.
3. Сервису нужен публичный URL (Railway → Settings → Generate Domain) — он
   понадобится курсу как адрес API (`/progress`).

## Переменные окружения
| Переменная | Зачем |
|---|---|
| `BOT_TOKEN` | токен из BotFather (обязательно) |
| `COURSE_URL` | адрес курса на Netlify (Mini App) |
| `ADMIN_CHAT_ID` | твой chat_id для уведомлений |
| `APPS_SCRIPT_URL` | URL веб-приложения Apps Script (.../exec) |
| `APPS_SCRIPT_SECRET` | секрет, общий с apps-script.gs |
| `PORT` | порт API (хостинг подставляет сам) |
