from __future__ import annotations

from copy import deepcopy
from storage.json_store import load_json, save_json
from config.config import (
    BOT_PRESENCE_STATUS,
    BOT_PROFILE_AVATAR_URL,
    BOT_PROFILE_NAME,
    BOT_SETTINGS_FILE,
    DASHBOARD_URL,
    SUPPORT_SERVER_URL,
)

DEFAULT_BOT_SETTINGS = {
    "activitycheck": {"active": True, "is_unlimited": True},
    "leaderboard": {"active": True, "is_unlimited": True},
    "setactivitytext": {"active": True, "is_unlimited": True},
    "settings": {"active": True, "is_unlimited": True},
    "global": {
        "support_server": SUPPORT_SERVER_URL,
        "dashboard_url": DASHBOARD_URL,
        "version": "7.2.1",
        "max_winners": 100,
        "max_duration": 1440,
        "presence_status": BOT_PRESENCE_STATUS if BOT_PRESENCE_STATUS in {"online", "idle", "dnd", "offline"} else "online",
        "profile_name": BOT_PROFILE_NAME,
        "profile_avatar_url": BOT_PROFILE_AVATAR_URL,
        "webhooks": {},
    },
}


def _merge(defaults: dict, current: dict) -> dict:
    merged = deepcopy(defaults)
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def get_bot_settings() -> dict:
    current = load_json(BOT_SETTINGS_FILE)
    if not isinstance(current, dict):
        current = {}
    settings = _merge(DEFAULT_BOT_SETTINGS, current)
    # Remove retired commerce configuration if an older file is reused.
    global_settings = settings.setdefault("global", {})
    # Deployment URLs are owned by the environment. Do not let stale persisted
    # values (especially an old localhost development URL) override them.
    global_settings["dashboard_url"] = DASHBOARD_URL
    global_settings["support_server"] = SUPPORT_SERVER_URL
    for key in list(global_settings):
        if any(word in key.lower() for word in ("premium", "price", "payment", "coupon", "trial", "billing")):
            global_settings.pop(key, None)
    save_json(BOT_SETTINGS_FILE, settings)
    return settings


def save_bot_settings(settings: dict) -> None:
    save_json(BOT_SETTINGS_FILE, settings)


def get_branding() -> tuple[str, str]:
    version = get_bot_settings().get("global", {}).get("version", "7.2.1")
    return "Rallybit", f"Build {version}"


def get_webhook(key: str, fallback: str | None = None) -> str | None:
    value = get_bot_settings().get("global", {}).get("webhooks", {}).get(key)
    return value or fallback
