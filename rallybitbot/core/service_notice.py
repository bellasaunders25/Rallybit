from __future__ import annotations

from typing import Any

from config.config import NOTICE_FILE
from storage.json_store import load_json


DEFAULT_NOTICE_TITLE = "Rallybit is temporarily unavailable"
DEFAULT_NOTICE_MESSAGE = "Commands are currently paused. Please try again later."


def get_service_notice() -> dict[str, Any]:
    raw = load_json(NOTICE_FILE) or {}
    if not isinstance(raw, dict):
        raw = {}
    title = str(raw.get("title") or DEFAULT_NOTICE_TITLE).strip()[:80]
    message = str(raw.get("message") or DEFAULT_NOTICE_MESSAGE).strip()[:400]
    return {
        "active": bool(raw.get("active", False)),
        "title": title or DEFAULT_NOTICE_TITLE,
        "message": message or DEFAULT_NOTICE_MESSAGE,
        "updated_at": raw.get("updated_at"),
    }


def service_notice_active() -> bool:
    return bool(get_service_notice()["active"])
