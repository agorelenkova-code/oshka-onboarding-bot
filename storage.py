"""
Хранилище прогресса.

Бэкенды (выбирается по переменным окружения, по приоритету):
  1) APPS_SCRIPT_URL  — POST на Google Apps Script Web App (рекомендуемый путь,
     без сервис-аккаунта и Google Cloud). Скрипт сам раскладывает строки в таблице.
  2) иначе — локальный progress.json (для отладки).
"""

import asyncio
import datetime
import json
import logging
import os
from typing import Optional

import aiohttp

log = logging.getLogger("storage")

APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "")
APPS_SCRIPT_SECRET = os.environ.get("APPS_SCRIPT_SECRET", "")
LOCAL_FILE = os.environ.get("LOCAL_FILE", "progress.json")

_lock = asyncio.Lock()
_session: Optional[aiohttp.ClientSession] = None


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


async def init() -> None:
    global _session
    if APPS_SCRIPT_URL:
        _session = aiohttp.ClientSession()
        log.info("Прогресс пишется в Google-таблицу через Apps Script")
    else:
        log.warning("APPS_SCRIPT_URL не задан — прогресс пишется в %s", LOCAL_FILE)


async def touch_user(user: dict) -> None:
    """Человек открыл бота — завести/обновить строку."""
    await record(user, task=None, status="start", comment="", step=None, step_title="")


async def record(
    user: dict,
    task: Optional[str],
    status: str,
    comment: str,
    step: Optional[str] = None,
    step_title: str = "",
) -> None:
    """
    status: "start" | "done" | "question".
    step / step_title — для пошаговых уроков (необязательно).
    """
    payload = {
        "secret": APPS_SCRIPT_SECRET,
        "user_id": str(user.get("id", "")),
        "name": user.get("first_name", ""),
        "username": user.get("username", ""),
        "task": "" if task is None else str(task),
        "step": "" if step is None else str(step),
        "stepTitle": step_title or "",
        "status": status,
        "comment": comment or "",
        "ts": _now(),
    }
    async with _lock:
        if _session is not None:
            await _record_apps_script(payload)
        else:
            _record_local(payload)


async def _record_apps_script(payload: dict) -> None:
    try:
        async with _session.post(
            APPS_SCRIPT_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as r:
            text = await r.text()
            if r.status >= 400 or '"ok":false' in text.replace(" ", ""):
                log.warning("Apps Script ответил %s: %s", r.status, text[:200])
    except Exception as e:
        log.error("Не смог записать в таблицу: %s", e)


def _record_local(payload: dict) -> None:
    uid = payload["user_id"]
    try:
        with open(LOCAL_FILE, "r", encoding="utf-8") as f:
            db = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        db = {}

    rec = db.setdefault(uid, {
        "имя": payload["name"],
        "username": payload["username"],
        "старт": payload["ts"],
        "задания": {},
        "вопросы": [],
    })
    rec["обновлено"] = payload["ts"]
    if payload["status"] == "done" and not payload["step"] and payload["task"]:
        rec["задания"][payload["task"]] = "✅"
    if payload["status"] == "question":
        rec["вопросы"].append({
            "задание": payload["task"],
            "шаг": payload["stepTitle"],
            "комментарий": payload["comment"],
            "когда": payload["ts"],
        })

    with open(LOCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    log.info("[local] %s task=%s status=%s", uid, payload["task"], payload["status"])


async def fetch_users() -> list:
    """Прочитать из таблицы всех пользователей: user_id, старт, что пройдено,
    какие напоминания уже отправлены. Нужен doGet в apps-script.gs."""
    if not APPS_SCRIPT_URL or _session is None:
        return []
    try:
        async with _session.get(
            APPS_SCRIPT_URL,
            params={"secret": APPS_SCRIPT_SECRET, "action": "users"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            text = await r.text()
        data = json.loads(text)
        return data.get("users", []) if data.get("ok") else []
    except Exception as e:
        log.error("fetch_users: %s", e)
        return []


async def get_user(uid) -> Optional[dict]:
    """Найти одного пользователя в таблице по user_id (для проверки регистрации)."""
    for u in await fetch_users():
        if str(u.get("user_id")) == str(uid):
            return u
    return None


async def register(user: dict, fio: str, group: str) -> None:
    """Записать ФИО и класс/группу (мини-регистрация при первом /start)."""
    await _record_apps_script({
        "secret": APPS_SCRIPT_SECRET,
        "user_id": str(user.get("id", "")),
        "name": user.get("first_name", ""),
        "username": user.get("username", ""),
        "status": "register",
        "fio": fio,
        "group": group,
        "ts": _now(),
        "task": "", "step": "", "stepTitle": "", "comment": "", "remind_key": "",
    })


async def mark_reminder(user_id: str, key: str) -> None:
    """Отметить в таблице, что напоминание `key` этому пользователю отправлено."""
    if _session is None:
        return
    await _record_apps_script({
        "secret": APPS_SCRIPT_SECRET,
        "user_id": str(user_id),
        "status": "remind",
        "remind_key": key,
        "ts": _now(),
        "task": "", "step": "", "stepTitle": "",
        "name": "", "username": "", "comment": "",
    })


async def close() -> None:
    if _session is not None:
        await _session.close()
