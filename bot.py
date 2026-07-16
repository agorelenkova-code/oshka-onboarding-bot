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
from urllib.parse import parse_qsl, urlencode

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
COURSE_URL = os.environ.get("COURSE_URL", "https://example.netlify.app").rstrip("/")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "0") or "0")
# Чат кураторов: куда падают вопросы «Не получилось». Если не задан — личка админа.
CURATORS_CHAT_ID = int(os.environ.get("CURATORS_CHAT_ID", "0") or "0") or ADMIN_CHAT_ID
PORT = int(os.environ.get("PORT", "8080"))
REMINDER_HOUR = int(os.environ.get("REMINDER_HOUR", "10"))  # час рассылки, МСК
CRON_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")      # защита ручного триггера

MSK = timezone(timedelta(hours=3))
REMIND_AFTER_DAYS = 1  # одно дружеское напоминание через N дней после открытия этапа

# Расписание напоминаний — ПО ЭТАПАМ (источник: лист «расписание» в таблице контента).
# Бот шлёт до 4 напоминаний: в «день открытия» этапа зовёт к его первому непройденному блоку.
#   day     — день онбординга (сегодня − старт + 1), с которого этап считается открытым.
#   blocks  — task-id блоков этапа В ПОРЯДКЕ КУРСА (первый непройденный станет ссылкой пинга).
# При изменении дней/состава — синхронизировать с листом «расписание» и порядком курса.
STAGES = [
    {"key": "S1", "day": 1, "emoji": "👋", "title": "Знакомство и первый вход",
     "teaser": "С этого начинается учёба: знакомимся со школой, входим на платформу, встречаем куратора, осваиваем личный кабинет и Zoom.",
     "blocks": [1, 2, 4, 21, 3]},
    {"key": "S2", "day": 2, "emoji": "🎬", "title": "Первые уроки",
     "teaser": "Готовимся к занятиям: как устроено обучение, первые уроки и домашние задания.",
     "blocks": [18, 5, 8]},
    {"key": "S3", "day": 5, "emoji": "📂", "title": "Зачисление",
     "teaser": "Что нужно для зачисления: какие документы собрать и как их загрузить.",
     "blocks": [9]},
    {"key": "S4", "day": 10, "emoji": "📋", "title": "Аттестация и рефераты",
     "teaser": "Аттестация без сюрпризов: порядок и правила, доклады и рефераты, подготовка к ГИА.",
     "blocks": [13, 14, 15, 16, 19, 20]},
]

# Матрица видимости блоков — КОПИЯ VIS из курса (index.html / tg-onboarding.js).
# Нужна, чтобы напоминания не звали в скрытый под тариф/роль/класс блок.
# При изменении матрицы в курсе — синхронизировать здесь.
_ALL_T = ["sz", "chsz", "bz", "veb", "oa", "dostup"]
VIS = {
    1:  {"t": _ALL_T, "r": ["parent", "student"]},
    2:  {"t": _ALL_T, "r": ["parent", "student"]},
    4:  {"t": _ALL_T, "r": ["parent", "student"]},
    21: {"t": _ALL_T, "r": ["parent", "student"]},
    3:  {"t": ["sz", "chsz", "bz", "veb"], "r": ["parent", "student"]},
    18: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    5:  {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    8:  {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    9:  {"t": ["sz", "chsz", "veb", "oa"], "r": ["parent"]},
    13: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    14: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    15: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    16: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
    19: {"t": ["sz", "veb"], "r": ["parent", "student"], "cls": [9, 11]},
    20: {"t": ["sz", "chsz", "bz", "veb", "oa"], "r": ["parent", "student"]},
}


TARIFF_LABEL = {
    "sz": "СЗ", "chsz": "ЧСЗ", "bz": "БЗ", "veb": "Вебинар",
    "oa": "Орг. аттестации", "dostup": "Доступ",
}


def _class_nums(s) -> list:
    return [int(x) for x in re.findall(r"\d+", str(s or ""))]


def is_visible(task_id, role: str, tariff: str, group) -> bool:
    """Виден ли блок этому профилю. Пустые тариф/роль/класс НЕ подавляют
    (у старых записей их нет — тогда ведём себя как раньше, без фильтра)."""
    rule = VIS.get(int(task_id))
    if not rule:
        return True
    if tariff and rule.get("t") and tariff not in rule["t"]:
        return False
    if role and rule.get("r") and role not in rule["r"]:
        return False
    if rule.get("cls"):
        ns = _class_nums(group)
        if ns and not any(n in rule["cls"] for n in ns):
            return False
    return True

if not BOT_TOKEN:
    raise SystemExit("Не задан BOT_TOKEN. Смотри .env.example / README.md")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


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
async def on_start(message: Message) -> None:
    # Имя/класс спрашиваем не в чате, а в приложении (на стартовом экране курса).
    if message.from_user:
        await storage.touch_user(message.from_user.model_dump())
    who = message.from_user.first_name if message.from_user else ""
    await message.answer(intro_text(who))
    await send_course(message)


@dp.message(Command("reset"))
async def on_reset(message: Message) -> None:
    """Сброс прогресса для тестов."""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🧹 Сбросить прогресс уроков",
                             web_app=WebAppInfo(url=f"{COURSE_URL}?reset=1"))
    ]])
    await message.answer(
        "<b>Сброс для теста</b>\n"
        "1) Нажми кнопку ниже — очистит галочки прохождения уроков в приложении.\n"
        "2) Чтобы заново пройти регистрацию (ФИО/класс) и обнулить напоминания — "
        "удали свою строку в Google-таблице.",
        reply_markup=kb,
    )


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
    status = body.get("status", "done")  # "done" | "question" | "register"

    # Регистрация из приложения: ФИО + класс + тариф + роль со стартового экрана курса
    if status == "register":
        fio = str(body.get("fio", ""))[:200]
        group = str(body.get("class", "") or body.get("group", ""))[:100]
        tariff = str(body.get("tariff", ""))[:20]
        role = str(body.get("role", ""))[:20]
        await storage.register(user, fio, group, tariff, role)
        return web.json_response({"ok": True}, headers=CORS_HEADERS)

    task = str(body.get("task", "?"))
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


async def handle_web_question(request: web.Request) -> web.Response:
    """Вопрос от веб-пользователя (без Telegram). Дёргается из Apps Script с общим
    секретом; бот пересылает вопрос в чат кураторов (токен остаётся только в боте)."""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    if body.get("secret") != CRON_SECRET:
        return web.json_response({"ok": False, "error": "bad secret"}, status=403)
    if not CURATORS_CHAT_ID:
        return web.json_response({"ok": True, "note": "no curators chat"})
    name = str(body.get("name", ""))[:200] or "Без имени"
    email = str(body.get("email", ""))[:200]
    group = str(body.get("group", ""))[:100]
    task = str(body.get("task", "?"))[:20]
    step_title = str(body.get("stepTitle", ""))[:200]
    comment = str(body.get("comment", ""))[:500]
    who = f"{name} ({group})" if group else name
    text = (
        "❓ <b>Вопрос по онбордингу</b> — 🌐 без Telegram\n"
        f"От: <b>{who}</b>" + (f" — ✉️ {email}" if email else "") + "\n"
        f"Задание: <b>{task}</b>"
    )
    if step_title:
        text += f"\nШаг: {step_title}"
    if comment:
        text += f"\nКомментарий: {comment}"
    try:
        await bot.send_message(CURATORS_CHAT_ID, text)
    except Exception as e:
        log.warning("Веб-вопрос не переслан кураторам: %s", e)
        return web.json_response({"ok": False, "error": "send failed"})
    return web.json_response({"ok": True})


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


async def send_reminder(chat_id: int, stage: dict, target_block: int) -> bool:
    """Дружеское напоминание в Telegram (одно, на следующий день после открытия)."""
    url = f"{COURSE_URL}/task-{target_block}.html"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="▶️ Открыть этап", web_app=WebAppInfo(url=url))
    ]])
    text = (
        f"👋 Дружеское напоминание\n\n"
        f"{stage['emoji']} <b>{stage['title']}</b>\n\n"
        f"{stage['teaser']}\n\n"
        "Загляни в приложение, когда будет минутка — продолжим отсюда 👇"
    )
    try:
        await bot.send_message(chat_id, text, reply_markup=kb)
        return True
    except Exception as e:  # человек мог не нажимать Start / заблокировать бота
        log.warning("Напоминание %s не отправлено: %s", chat_id, e)
        return False


def _course_link(u: dict, email: str) -> str:
    """Ссылка на курс с подставленными данными профиля — чтобы человек с почты
    вернулся уже залогиненным (прогресс пишется по этой же почте)."""
    params = {"e": email}
    if u.get("fio"):
        params["n"] = u["fio"]
    if u.get("group"):
        params["k"] = u["group"]
    if u.get("tariff"):
        params["t"] = u["tariff"]
    if u.get("role"):
        params["r"] = u["role"]
    return f"{COURSE_URL}/?{urlencode(params)}"


async def send_curator_digest(items: list) -> bool:
    """Сводка кураторам: кому из веб-пользователей (без Telegram) написать письмо.
    Куратор пишет со своей почты — бот только собирает список + шаблон."""
    if not CURATORS_CHAT_ID:
        log.warning("CURATORS_CHAT_ID не задан — сводку напоминаний не отправить")
        return False
    lines = [
        f"📬 <b>Напомнить родителям на почту</b> (нет Telegram) — {len(items)} чел.",
        "Напишите со своей почты тем, кто ещё не прошёл этап онбординга:\n",
    ]
    for i, d in enumerate(items, 1):
        tlabel = TARIFF_LABEL.get(d["tariff"], d["tariff"])
        extra = " · ".join(x for x in [f"{d['group']} кл." if d["group"] else "",
                                       tlabel] if x)
        link = _course_link(d, d["email"])
        lines.append(
            f"{i}. <b>{d['name']}</b>\n"
            f"   ✉️ {d['email']}" + (f" · {extra}" if extra else "") + "\n"
            f"   Этап {d['stage']['key'][1:]}: {d['stage']['title']} — не пройден\n"
            f"   🔗 {link}"
        )
    lines.append(
        "\n<b>Шаблон письма:</b>\n"
        "<i>Тема: Продолжите вводный курс — Онлайн-школа №1\n"
        "Здравствуйте! Напоминаю про шаг вводного курса — он занимает пару минут. "
        "Откройте по ссылке из письма-приглашения (или по ссылке выше). "
        "Если будут вопросы — я на связи.</i>"
    )
    text = "\n".join(lines)
    try:
        await bot.send_message(CURATORS_CHAT_ID, text, disable_web_page_preview=True)
        return True
    except Exception as e:
        log.warning("Сводку кураторам не отправить: %s", e)
        return False


async def run_reminders() -> int:
    """Один проход по ЭТАПАМ. Telegram-юзерам шлём напоминание сразу; веб-юзеров
    (без Telegram) собираем в сводку кураторам. Возвращает число обработанных."""
    users = await storage.fetch_users()
    today = datetime.now(MSK).date()
    sent = 0
    digest = []  # веб-пользователи, кому куратор должен написать письмо
    for u in users:
        uid = str(u.get("user_id") or "")
        if not uid:
            continue
        start = _parse_start(u.get("start"))
        if not start:
            continue
        day_num = (today - start).days + 1
        done = {str(x) for x in u.get("done", [])}
        sent_keys = set(re.split(r"[\s,;]+", str(u.get("reminders", "")).strip()))
        role = str(u.get("role", ""))
        tariff = str(u.get("tariff", ""))
        group = u.get("group", "")

        # Ищем самый ранний открытый и НЕпройденный этап — только по ВИДИМЫМ
        # блокам профиля (роль·тариф·класс). Этап без видимых блоков пропускаем
        # (напр. тариф «Доступ» видит только этап 1, ученик — не видит «Документы»).
        # Этап «пройден» = все его видимые блоки сделаны ЛИБО начат более поздний этап.
        target = None
        target_block = None
        later_blocks: set = set()
        for st in reversed(STAGES):
            vis_blocks = [str(b) for b in st["blocks"]
                          if is_visible(b, role, tariff, group)]
            if not vis_blocks:
                continue  # этап целиком не для этого профиля
            cleared = all(b in done for b in vis_blocks) or any(b in done for b in later_blocks)
            if not cleared and day_num >= st["day"]:
                target = st  # перезапишется более ранним этапом на след. итерациях
                target_block = next((b for b in vis_blocks if b not in done), vis_blocks[0])
            later_blocks.update(vis_blocks)
        if target is None:
            continue

        # Одно дружеское напоминание — на следующий день после открытия этапа.
        if day_num < target["day"] + REMIND_AFTER_DAYS:
            continue
        key = f"{target['key']}:r"
        if key in sent_keys:
            continue

        if uid.startswith("web:"):
            # Веб-пользователь: письмо шлёт куратор — добавляем в сводку.
            digest.append({
                "uid": uid, "key": key,
                "email": uid[4:] or str(u.get("username", "")),
                "name": u.get("fio") or u.get("name") or (uid[4:]),
                "group": group, "tariff": tariff, "role": role,
                "stage": target, "block": target_block,
            })
        else:
            try:
                chat_id = int(uid)
            except ValueError:
                continue
            if await send_reminder(chat_id, target, target_block):
                await storage.mark_reminder(uid, key)
                sent += 1

    # Сводка кураторам по веб-пользователям (отмечаем только если сводка ушла).
    if digest and await send_curator_digest(digest):
        for d in digest:
            await storage.mark_reminder(d["uid"], d["key"])
            sent += 1

    log.info("Рассылка: обработано %s (TG + сводка веб=%s)", sent, len(digest))
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
    app.router.add_post("/web-question", handle_web_question)
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
