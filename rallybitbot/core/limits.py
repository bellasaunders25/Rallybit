"""Abuse protection and global command availability.

Rallybit no longer uses feature tiers. Commands can still be disabled globally and
reasonable anti-spam cooldowns can be configured, but every server receives the
same product capabilities.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from config.config import LIMITS_FILE, BANNED_SERVERS_FILE, BANNED_USERS_FILE
from storage.json_store import load_json, save_json
from core.bot_settings import get_bot_settings

banned_servers = load_json(BANNED_SERVERS_FILE) or {}
banned_users = load_json(BANNED_USERS_FILE) or {}


def check_limit(guild_id, command):
    guild_id = str(guild_id)
    server_bans = load_json(BANNED_SERVERS_FILE) or {}
    if guild_id in server_bans:
        return False, -2

    config = get_bot_settings().get(command, {"active": True, "is_unlimited": True})
    if not config.get("active", True):
        return False, -1
    if config.get("is_unlimited", True):
        return True, 0

    max_uses = max(1, int(config.get("max_uses", 10)))
    cooldown_hours = max(1, float(config.get("cooldown_hours", 1)))
    ledger = load_json(LIMITS_FILE) or {}
    command_ledger = ledger.setdefault(guild_id, {}).setdefault(command, {"count": 0, "last": datetime.utcnow().isoformat()})

    try:
        last = datetime.fromisoformat(command_ledger.get("last", "2000-01-01T00:00:00"))
    except ValueError:
        last = datetime(2000, 1, 1)
    if datetime.utcnow() - last >= timedelta(hours=cooldown_hours):
        command_ledger.update({"count": 0, "last": datetime.utcnow().isoformat()})
        save_json(LIMITS_FILE, ledger)
    return command_ledger.get("count", 0) < max_uses, command_ledger.get("count", 0)


def increment_limit(guild_id, command):
    config = get_bot_settings().get(command, {"is_unlimited": True})
    if config.get("is_unlimited", True):
        return
    ledger = load_json(LIMITS_FILE) or {}
    entry = ledger.setdefault(str(guild_id), {}).setdefault(command, {"count": 0, "last": datetime.utcnow().isoformat()})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last"] = datetime.utcnow().isoformat()
    save_json(LIMITS_FILE, ledger)

