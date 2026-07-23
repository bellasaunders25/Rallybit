from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from discord.ext import tasks

from commands.quizzes import active_quizzes, start_quiz
from config.config import (
    ACTIVE_CHECKS_FILE,
    ACTIVE_PULSES_FILE,
    ACTIVE_QUIZZES_FILE,
    BANNED_SERVERS_FILE,
    QUIZ_SETTINGS_FILE,
)
from storage.json_store import load_json, save_json

_bot = None


@tasks.loop(minutes=1)
async def auto_quiz_loop() -> None:
    if _bot is None:
        return
    settings_data = load_json(QUIZ_SETTINGS_FILE) or {}
    banned = load_json(BANNED_SERVERS_FILE) or {}
    persisted_checks = load_json(ACTIVE_CHECKS_FILE) or {}
    persisted_quizzes = load_json(ACTIVE_QUIZZES_FILE) or {}
    persisted_pulses = load_json(ACTIVE_PULSES_FILE) or {}
    from commands.activity import active_guild_checks
    from commands.community import active_pulses
    now = datetime.now(timezone.utc)
    changed = False

    for guild in list(_bot.guilds):
        gid = str(guild.id)
        if (
            gid in banned
            or gid in persisted_checks
            or gid in persisted_quizzes
            or gid in persisted_pulses
            or guild.id in active_quizzes
            or guild.id in active_guild_checks
            or guild.id in active_pulses
        ):
            continue
        settings = settings_data.get(gid)
        if not isinstance(settings, dict) or not settings.get("enabled"):
            continue
        try:
            channel = guild.get_channel(int(settings.get("channel_id")))
        except (TypeError, ValueError):
            channel = None
        if channel is None or guild.me is None:
            continue
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            continue
        try:
            interval = max(1, min(168, int(settings.get("interval_hours", 12))))
            last = datetime.fromisoformat(str(settings.get("last_run", "2000-01-01T00:00:00+00:00")))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            interval = 12
            last = datetime(2000, 1, 1, tzinfo=timezone.utc)
        if now - last < timedelta(hours=interval):
            continue

        settings["last_run"] = now.isoformat()
        settings_data[gid] = settings
        changed = True

        async def launch(target_guild=guild, target_channel=channel, cfg=dict(settings)) -> None:
            try:
                ping_role = None
                try:
                    role_id = cfg.get("ping_role_id")
                    if role_id:
                        ping_role = target_guild.get_role(int(role_id))
                except (TypeError, ValueError):
                    ping_role = None
                await start_quiz(
                    target_guild,
                    target_channel,
                    target_guild.me,
                    str(cfg.get("category", "mixed")),
                    int(cfg.get("duration_seconds", 30)),
                    automatic=True,
                    ping_role=ping_role,
                )
            except Exception as exc:
                active_quizzes.pop(target_guild.id, None)
                print(f"[AUTO QUIZ] {target_guild.id}: {exc}")

        asyncio.create_task(launch(), name=f"rallybit-auto-quiz-{guild.id}")

    if changed:
        save_json(QUIZ_SETTINGS_FILE, settings_data)


def setup_auto_quiz(bot) -> None:
    global _bot
    _bot = bot
    if not auto_quiz_loop.is_running():
        auto_quiz_loop.start()
