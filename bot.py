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
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
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
COURSE_URL = os.environ.get("COURSE_URL", "https://example.netlify.app").rstrip("/")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or "0")
# Чат кураторов: куда падают вопросы «Не получилось». Если не задан — личка админа.
CURATORS_CHAT_ID = int(os.environ.get("CURATORS_CHAT_ID", "0") or "0") or ADMIN_CHAT_ID
PORT = int(os.environ.get("PORT", "8080"))
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "10"))  # час рассылки, МСК
CRON_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")      # защита ручного триггера

MSK = timezone(timedelta(hours=3))
FOLLOWUP_GAP_DAYS = 2  # повторное напоминание через N дней, если урок не пройден

# Порядок уроков, их день по расписанию и название
LESSON_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 13, 14, 15, 16]
LESSON_DAY = {1: 1, 2: 2, 3: 3, 4: 5, 5: 7, 6: 10, 7: 14, 8: 20,
              9: 22, 13: 35, 14: 38, 15: 42, 16: 45}
LESSON_TITLE = {
    1: "Добро пожаловать", 2: "Входим на платформу", 3: "Покоряем Zoom",
    4: "Познакомься с куратором", 5: "Первые уроки", 6: "Тесты и оценки",
    7: "Самопроверка", 8: "Письменные домашки", 9: "Загружаем документы",
    13: "Порядок аттестации", 14: "Правила аттестации", 15: "Доклады и рефераты",
    16: "Стратегия аттестации",
}

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN. Смотри .env.example / README.md")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())


class Reg(StatesGroup):
    fio = State()
    group = State()


# --- Команды бота -----------------------------------------------------------
def intro_text(who: str = "") -> str:
    hi = f"👋 Привет, {who}!" if who else "👋 Привет!"
    return (
        f"{hi} Добро пожаловать в <b>онлайн-школу</b> 🎓\n\n"
        "Я бот-помощник по онбордингу. <b>Моя задача — провести тебя через все этапы "
        "адаптации в школе</b>, чтобы с первых дней всё было понятно и ничего важного "
        "не потерялось.\n\n"
        "Вместе шаг за шагом разберёмся, как войти на платформу, настроить Zoom, "
        "проходить уроки и сдавать аттестации. А ещё я сориентирую по "
        "<b>важным этапам зачисления</b>, чтобы не пропустить ничего по срокам."
    )


async def send_course(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🚀 Открыть онбординг", web_app=WebAppInfo(url=COURSE_URL))
    ]])
    await message.answer(
        "<b>Как это работает:</b>\n"
        "• Нажми кнопку ниже — откроется интерактивный курс.\n"
        "• Проходи шаги по порядку и отмечай выполненные ✅\n"
        "• Что-то не получается? Жми «Не получилось? Спросить наставника» — "
        "я передам вопрос, и тебе помогут.\n"
        "• По дням буду напоминать, какой урок пройти, чтобы ничего не пропустить.\n\n"
        "Поехали 👇",
        reply_markup=kb,
    )


@dp.message(CommandStart())
async def on_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    if message.from_user:
        await storage.touch_user(message.from_user.model_dump())
        user = await storage.get_user(message.from_user.id)
        if user and str(user.get("fio", "")).strip():
            # уже знакомы — сразу приветствие + курс
            await message.answer(intro_text(user["fio"].split()[0]))
            await send_course(message)
            return
    # сначала приветствие, потом знакомство
    who = message.from_user.first_name if message.from_user else ""
    await message.answer(intro_text(who))
    await message.answer(
        "Прежде чем начать, давай познакомимся 🙌\n"
        "Напиши, пожалуйста, своё <b>имя и фамилию</b>:"
    )
    await state.set_state(Reg.fio)


@dp.message(Reg.fio)
async def reg_fio(message: Message, state: FSMContext) -> None:
    fio = (message.text or "").strip()
    if len(fio) < 2:
        await message.answer("Напиши, пожалуйста, имя и фамилию текстом 🙂")
        return
    await state.update_data(fio=fio)
    await message.answer("Отлично! Теперь напиши свой <b>класс / группу</b>:")
    await state.set_state(Reg.group)


@dp.message(Reg.group)
async def reg_group(message: Message, state: FSMContext) -> None:
    group = (message.text or "").strip()
    data = await state.get_data()
    fio = data.get("fio", "")
    await state.clear()
    if message.from_user:
        await storage.register(message.from_user.model_dump(), fio, group)
    await message.answer(f"Готово! Записал: <b>{fio}</b>, {group} ✅\n\nТеперь можно начинать 👇")
    await send_course(message)


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

    if status == "question" and CURATORS_CHAT_ID:
        rec = await storage.get_user(user.get("id"))
        fio = (rec.get("fio") if rec else "") or user.get("first_name", "Новичок")
        grp = (rec.get("group") if rec else "") or ""
        uname = f"@{user['username']}" if user.get("username") else f"id {user.get('id')}"
        who = f"{fio} ({grp})" if grp else fio
        text = (
            "❓ <b>Вопрос по онбордингу</b>\n"
            f"От: <b>{who}</b> — {uname}\n"
            f"Задание: <b>{task}</b>"
        )
        if step_title:
            text += f"\nШаг: {step_title}"
        if comment:
            text += f"\nКомментарий: {comment}"
        try:
            await bot.send_message(CURATORS_CHAT_ID, text)
        except Exception as e:  # уведомление не должно ронять запись прогресса
            log.warning("Не смог уведомить наставника: %s", e)

    return web.json_response({"ok": True}, headers=CORS_HEADERS)


async def handle_options(request: web.Request) -> web.Response:
    return web.Response(headers=CORS_HEADERS)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "onboarding-bot"})


# --- Напоминания по расписанию -------------------------------------------
def _parse_start(value) -> Optional[date]:
    """Понимает разные форматы даты из таблицы:
    '2026-06-25 13:30', ISO, 'Thu Jun 25 2026 13:30:00 GMT+0300', '25.06.2026'."""
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)               # 2026-06-25 / ISO
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"[A-Za-z]{3}\s+([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})", s)  # Thu Jun 25 2026
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
        except ValueError:
            pass
    m = re.match(r"(\d{2})\.(\d{2})\.(\d{4})", s)             # 25.06.2026
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    return None


async def send_reminder(chat_id: int, lesson: int, kind: str) -> bool:
    url = f"{COURSE_URL}/task-{lesson}.html"
    title = LESSON_TITLE.get(lesson, "урок")
    day = LESSON_DAY.get(lesson, "?")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ Открыть урок", web_app=WebAppInfo(url=url))
    ]])
    head = "👋 Напоминаем" if kind == "due" else "🙌 Ещё раз напомним"
    text = (
        f"{head}: пора пройти урок онбординга\n"
        f"<b>День {day} — {title}</b>\n\n"
        "Нажми кнопку ниже, чтобы открыть 👇"
    )
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
        return True
    except Exception as e:  # человек мог не нажимать Start / заблокировать бота
        log.warning("Напоминание %s не отправлено: %s", chat_id, e)
        return False


async def run_reminders() -> int:
    """Один проход рассылки. Возвращает число отправленных напоминаний."""
    users = await storage.fetch_users()
    today = datetime.now(MSK).date()
    sent = 0
    for u in users:
        try:
            chat_id = int(u.get("user_id"))
        except (TypeError, ValueError):
            continue
        start = _parse_start(u.get("start"))
        if not start:
            continue
        day_num = (today - start).days + 1
        done = {str(x) for x in u.get("done", [])}
        sent_keys = set(re.split(r"[\s,;]+", str(u.get("reminders", "")).strip()))

        # ищем самый ранний НЕпройденный урок, чей день уже наступил
        target = None
        for n in LESSON_ORDER:
            if str(n) in done:
                continue
            if day_num >= LESSON_DAY[n]:
                target = n
            break
        if target is None:
            continue

        due_key = f"L{target}:due"
        follow_key = f"L{target}:follow"
        if due_key not in sent_keys:
            key, kind = due_key, "due"
        elif (day_num >= LESSON_DAY[target] + FOLLOWUP_GAP_DAYS
              and follow_key not in sent_keys):
            key, kind = follow_key, "follow"
        else:
            continue

        if await send_reminder(chat_id, target, kind):
            await storage.mark_reminder(str(chat_id), key)
            sent += 1
    log.info("Рассылка напоминаний: отправлено %s", sent)
    return sent


def _seconds_until(hour: int) -> float:
    now = datetime.now(MSK)
    nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def reminder_loop() -> None:
    while True:
        await asyncio.sleep(_seconds_until(REMINDER_HOUR))
        try:
            await run_reminders()
        except Exception as e:
            log.error("reminder_loop: %s", e)
        await asyncio.sleep(60)  # подстраховка от двойного срабатывания в тот же час


async def handle_cron_reminders(request: web.Request) -> web.Response:
    if request.query.get("secret") != CRON_SECRET:
        return web.json_response({"ok": False}, status=403)
    sent = await run_reminders()
    return web.json_response({"ok": True, "sent": sent})


async def run_api() -> None:
    app = web.Application()
    app.router.add_post("/progress", handle_progress)
    app.router.add_options("/progress", handle_options)
    app.router.add_get("/", handle_health)
    app.router.add_get("/cron/reminders", handle_cron_reminders)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info("HTTP-API слушает порт %s", PORT)


# --- Запуск -----------------------------------------------------------------
async def main() -> None:
    await storage.init()
    await run_api()
    asyncio.create_task(reminder_loop())
    log.info("Бот запущен. Курс: %s. Напоминания в %s:00 МСК", COURSE_URL, REMINDER_HOUR)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
