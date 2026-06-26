"""
Бот онбординга Ошки.

Что делает:
  • /start  — показывает кнопку, открывающую курс как Telegram Mini App;
  • /id     — присылает твой chat_id (нужен один раз, чтобы прописать ADMIN_CHAT_ID);
  • HTTP /progress — курс дёргает этот эндпоинт, когда новичок жмёт
                     «Выполнил» или «Есть вопрос». Бот проверяет подпись
                     Telegram, пишет статус в Google-таблицу и при вопросе
                     пишет наставнику (тебе) в личку.

Бот и HTTP-API работают в одном процессе (хватает бесплатного Railway/Render).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Optional
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiohttp import web

# Локально подхватываем .env ДО импорта storage (он читает env при импорте)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("onboarding-bot")

# --- Настройки (берём из переменных окружения) ------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
COURSE_URL = os.environ.get("COURSE_URL", "https://example.netlify.app")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or "0")
PORT = int(os.environ.get("PORT", "8080"))

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN. Смотри .env.example / README.md")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# --- Команды бота -----------------------------------------------------------
@dp.message(CommandStart())
async def on_start(message: Message) -> None:
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🚀 Открыть онбординг",
                web_app=WebAppInfo(url=COURSE_URL),
            )]
        ]
    )
    await message.answer(
        "Привет! 👋 Это онбординг-курс <b>Ошки</b>.\n\n"
        "Нажми кнопку ниже — курс откроется прямо здесь, в Telegram.\n"
        "Проходи задания по порядку и отмечай «Выполнил».\n"
        "Если что-то непонятно — жми «Есть вопрос», и я передам наставнику.",
        reply_markup=kb,
    )
    if message.from_user:
        await storage.touch_user(message.from_user.model_dump())


@dp.message(Command("id"))
async def on_id(message: Message) -> None:
    """Помощник: узнать свой chat_id для ADMIN_CHAT_ID."""
    await message.answer(
        f"Твой chat_id: <code>{message.chat.id}</code>\n"
        "Впиши его в переменную окружения <b>ADMIN_CHAT_ID</b>."
    )


# --- Проверка подписи Telegram WebApp initData ------------------------------
def validate_init_data(init_data: str) -> Optional[dict]:
    """
    Проверяем, что initData действительно пришёл из Telegram и не подделан.
    Возвращаем разобранный словарь (с распарсенным user) или None.
    Алгоритм описан в доке Telegram: Validating data received via the Mini App.
    """
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(
        f"{k}={parsed[k]}" for k in sorted(parsed.keys())
    )
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    if "user" in parsed:
        try:
            parsed["user"] = json.loads(parsed["user"])
        except json.JSONDecodeError:
            return None
    return parsed


# --- HTTP-API, который дёргает курс -----------------------------------------
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


async def handle_progress(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response(
            {"ok": False, "error": "bad json"}, status=400, headers=CORS_HEADERS
        )

    data = validate_init_data(body.get("initData", ""))
    if not data or "user" not in data:
        return web.json_response(
            {"ok": False, "error": "bad initData"}, status=403, headers=CORS_HEADERS
        )

    user = data["user"]
    task = str(body.get("task", "?"))
    status = body.get("status", "done")  # "done" | "question"
    comment = str(body.get("comment", ""))[:500]
    step = body.get("step")
    step = str(step) if step else None
    step_title = str(body.get("stepTitle", ""))[:200]

    await storage.record(user, task, status, comment, step, step_title)

    if status == "question" and ADMIN_CHAT_ID:
        name = user.get("first_name", "Новичок")
        uname = f"@{user['username']}" if user.get("username") else f"id {user.get('id')}"
        text = (
            "❓ <b>Вопрос по онбордингу</b>\n"
            f"От: {name} ({uname})\n"
            f"Задание: <b>{task}</b>"
        )
        if step_title:
            text += f"\nШаг: {step_title}"
        if comment:
            text += f"\nКомментарий: {comment}"
        try:
            await bot.send_message(ADMIN_CHAT_ID, text)
        except Exception as e:  # уведомление не должно ронять запись прогресса
            log.warning("Не смог уведомить наставника: %s", e)

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def handle_options(request: web.Request) -> web.Response:
    return web.Response(headers=CORS_HEADERS)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "onboarding-bot"})


async def run_api() -> None:
    app = web.Application()
    app.router.add_post("/progress", handle_progress)
    app.router.add_options("/progress", handle_options)
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP-API слушает порт %s", PORT)


# --- Запуск -----------------------------------------------------------------
async def main() -> None:
    await storage.init()
    await run_api()
    log.info("Бот запущен. Курс: %s", COURSE_URL)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
