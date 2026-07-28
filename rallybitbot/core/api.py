from __future__ import annotations

import asyncio
import hmac
import json
import os
import platform
import re
import sys
import time
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import discord
import psutil
from flask import Flask, jsonify, request

from config.config import API_HOST, API_PORT, API_SECRET, DATA_DIR, TOPGG_WEBHOOK_TOKEN
from core.bot_profile import (
    apply_bot_profile,
    validate_avatar_url,
    validate_profile_name,
)
from core.bot_settings import get_bot_settings, save_bot_settings
from core.plan_branding import sync_plan_avatars
from core.premium import grant_entitlement, load_entitlements, revoke_entitlement
from core.presence import normalise_presence_status

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024
START_TIME = time.time()
discord_bot = None
ALLOWED_FILES = {
    "activity_settings.json", "bot_settings.json", "activity_log.json", "notice.json", "limits.json",
    "bot_guilds.json", "admins.json", "badges.json", "global_stats.json", "status_history.json",
    "win_badges.json", "activity_audit_logs.json", "votes.json", "server_analytics.json",
    "last_seen.json", "member_events.json", "quiz_settings.json", "quiz_stats.json", "quiz_history.json",
    "community_settings.json", "pulse_history.json", "moderation_history.json", "moderation_warnings.json", "moderation_permissions.json",
    "security_settings.json", "security_history.json", "security_quarantine.json", "security_lockdown.json",
    "giveaway_settings.json", "giveaway_history.json", "welcome_settings.json", "invite_tracking.json",
    "level_settings.json", "level_stats.json", "autorole_settings.json", "reaction_roles.json",
    "verification_settings.json", "ticket_settings.json", "ticket_panels.json", "open_tickets.json",
    "ticket_history.json", "automation_schedules.json", "afk_status.json",
    "report_settings.json", "reports.json", "review_settings.json",
    "premium_entitlements.json", "staff_shifts.json", "workforce_settings.json", "staff_requests.json",
    "audit_settings.json", "audit_events.json"
}



def _run_bot_coro(coro, timeout: int = 30):
    if discord_bot is None or discord_bot.loop is None:
        raise RuntimeError("bot unavailable")
    future = asyncio.run_coroutine_threadsafe(coro, discord_bot.loop)
    return future.result(timeout=timeout)


def _guild_from_payload(payload):
    if discord_bot is None:
        return None
    try:
        return discord_bot.get_guild(int(payload.get("guild_id", 0)))
    except (TypeError, ValueError):
        return None


def _dashboard_https_url(value: Any, label: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or len(url) > 2048:
        raise RuntimeError(f"{label} must be a valid public HTTPS URL.")
    return url


def _dashboard_embed_from_params(params: dict[str, Any]) -> discord.Embed:
    title = str(params.get("title") or "").strip()[:256]
    description = str(params.get("description") or "").strip()[:4096]
    colour_text = str(params.get("color") or "#7C6CFF").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", colour_text):
        raise RuntimeError("Embed colour must be a six-digit hex colour such as #7C6CFF.")
    title_url = _dashboard_https_url(params.get("title_url"), "Title link")
    embed = discord.Embed(
        title=title or None,
        url=title_url or None,
        description=description or None,
        colour=int(colour_text, 16),
    )
    author_name = str(params.get("author_name") or "").strip()[:256]
    author_url = _dashboard_https_url(params.get("author_url"), "Author link")
    author_icon = _dashboard_https_url(params.get("author_icon_url"), "Author icon")
    if author_name:
        embed.set_author(name=author_name, url=author_url or None, icon_url=author_icon or None)
    elif author_url or author_icon:
        raise RuntimeError("Enter an author name before adding an author link or icon.")
    thumbnail = _dashboard_https_url(params.get("thumbnail_url"), "Thumbnail")
    image = _dashboard_https_url(params.get("image_url"), "Image")
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if image:
        embed.set_image(url=image)
    footer_text = str(params.get("footer_text") or "").strip()[:2048]
    footer_icon = _dashboard_https_url(params.get("footer_icon_url"), "Footer icon")
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_icon or None)
    elif footer_icon:
        raise RuntimeError("Enter footer text before adding a footer icon.")
    fields = params.get("fields")
    if isinstance(fields, list):
        for raw_field in fields[:25]:
            if not isinstance(raw_field, dict):
                continue
            name = str(raw_field.get("name") or "").strip()[:256]
            value = str(raw_field.get("value") or "").strip()[:1024]
            if not name and not value:
                continue
            if not name or not value:
                raise RuntimeError("Every embed field needs both a name and a value.")
            embed.add_field(name=name, value=value, inline=bool(raw_field.get("inline")))
    if params.get("show_timestamp"):
        timestamp_text = str(params.get("timestamp") or "").strip()
        timestamp = discord.utils.parse_time(timestamp_text) if timestamp_text else None
        embed.timestamp = timestamp or discord.utils.utcnow()
    character_count = len(embed.title or "") + len(embed.description or "")
    character_count += len(embed.author.name or "") + len(embed.footer.text or "")
    character_count += sum(len(field.name) + len(field.value) for field in embed.fields)
    if character_count > 6000:
        raise RuntimeError("This embed exceeds Discord's 6,000-character total limit.")
    if not any((embed.title, embed.description, embed.fields, embed.image.url, embed.thumbnail.url, embed.footer.text)):
        raise RuntimeError("The embed cannot be empty.")
    return embed


def _dashboard_embed_payload(message: discord.Message, embed_index: int) -> dict[str, Any]:
    embed = message.embeds[embed_index]
    payload = embed.to_dict()
    colour = embed.colour.value if embed.colour else 0x7C6CFF
    return {
        "channel_id": str(message.channel.id),
        "channel_name": str(getattr(message.channel, "name", "channel")),
        "message_id": str(message.id),
        "embed_index": embed_index + 1,
        "embed_count": len(message.embeds),
        "jump_url": message.jump_url,
        "content": str(message.content or ""),
        "title": str(payload.get("title") or ""),
        "title_url": str(payload.get("url") or ""),
        "description": str(payload.get("description") or ""),
        "color": f"#{colour:06X}",
        "author_name": str((payload.get("author") or {}).get("name") or ""),
        "author_url": str((payload.get("author") or {}).get("url") or ""),
        "author_icon_url": str((payload.get("author") or {}).get("icon_url") or ""),
        "thumbnail_url": str((payload.get("thumbnail") or {}).get("url") or ""),
        "image_url": str((payload.get("image") or {}).get("url") or ""),
        "footer_text": str((payload.get("footer") or {}).get("text") or ""),
        "footer_icon_url": str((payload.get("footer") or {}).get("icon_url") or ""),
        "timestamp": str(payload.get("timestamp") or ""),
        "show_timestamp": bool(payload.get("timestamp")),
        "fields": [
            {
                "name": str(field.get("name") or ""),
                "value": str(field.get("value") or ""),
                "inline": bool(field.get("inline")),
            }
            for field in payload.get("fields", [])
            if isinstance(field, dict)
        ],
    }


def _ticket_panel_dashboard_payload(
    guild: discord.Guild,
    panel_id: str,
    panel: dict[str, Any],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    channel_id = str(panel.get("channel_id") or "")
    message_id = str(panel.get("message_id") or "")
    return {
        "panel_id": panel_id,
        "message_id": message_id,
        "channel_id": channel_id,
        "jump_url": (
            f"https://discord.com/channels/{guild.id}/{channel_id}/{message_id}"
            if channel_id.isdigit() and message_id.isdigit()
            else ""
        ),
        "name": str(panel.get("name") or "Support"),
        "title": str(panel.get("title") or "How can we help?"),
        "description": str(panel.get("description") or ""),
        "select_placeholder": str(panel.get("select_placeholder") or "Select a ticket type…"),
        "color": str(panel.get("color") or "#7C6CFF"),
        "author_name": str(panel.get("author_name") or ""),
        "author_icon_url": str(panel.get("author_icon_url") or ""),
        "header_image_url": str(panel.get("header_image_url") or ""),
        "thumbnail_url": str(panel.get("thumbnail_url") or ""),
        "image_url": str(panel.get("image_url") or ""),
        "footer_text": str(panel.get("footer_text") or ""),
        "footer_icon_url": str(panel.get("footer_icon_url") or ""),
        "show_author": bool(panel.get("show_author", True)),
        "show_option_details": bool(panel.get("show_option_details", True)),
        "show_workload": bool(panel.get("show_workload", True)),
        "show_guidance": bool(panel.get("show_guidance", True)),
        "show_timestamp": bool(panel.get("show_timestamp", True)),
        "options": [
            {
                "option_id": str(option.get("option_id") or ""),
                "name": str(option.get("name") or "Support"),
                "description": str(option.get("description") or ""),
                "emoji": str(option.get("emoji") or ""),
                "category_id": str(option.get("category_id") or ""),
                "support_role_id": str((option.get("support_role_ids") or [""])[0]),
            }
            for option in options
        ],
    }


async def _find_dashboard_message(
    guild: discord.Guild,
    message_id: int,
    preferred_channel: Any = None,
) -> discord.Message:
    candidates: list[Any] = []
    if preferred_channel is not None and hasattr(preferred_channel, "fetch_message"):
        candidates.append(preferred_channel)
    else:
        candidates.extend(guild.text_channels)
        candidates.extend(getattr(guild, "threads", []))
    seen: set[int] = set()
    for candidate in candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        try:
            return await candidate.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            continue
        except discord.HTTPException:
            continue
    if preferred_channel is not None:
        raise RuntimeError("That message was not found in the selected channel.")
    raise RuntimeError("That message ID was not found in any accessible text channel or active thread.")

def auth(req) -> bool:
    return bool(API_SECRET) and hmac.compare_digest(req.headers.get("X-Api-Key", "").strip(), API_SECRET)


def get_file_path(filename):
    safe_name = os.path.basename(str(filename or ""))
    if safe_name not in ALLOWED_FILES:
        return None
    return Path(DATA_DIR) / safe_name


@app.get("/")
@app.get("/api/health")
def health():
    bot = discord_bot
    return jsonify({
        "ok": True,
        "status": "healthy",
        "engine": "Rallybit API 8.1",
        "uptime_seconds": int(time.time() - START_TIME),
        "metrics": {
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent,
            "python": sys.version.split()[0],
            "platform": f"{platform.system()} {platform.release()}",
        },
        "bot": {
            "guilds": len(bot.guilds) if bot else 0,
            "users": sum(g.member_count or 0 for g in bot.guilds) if bot else 0,
            "latency_ms": round(bot.latency * 1000) if bot and bot.latency else None,
        },
    })


@app.post("/api/json/read")
def read_json_route():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    path = get_file_path((request.get_json(silent=True) or {}).get("file"))
    if not path or not path.exists(): return jsonify({"data": {}})
    try: return jsonify({"data": json.loads(path.read_text(encoding="utf-8"))})
    except Exception as exc: return jsonify({"error": str(exc)}), 500


@app.post("/api/json/write")
def write_json_route():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    path = get_file_path(payload.get("file"))
    if not path: return jsonify({"error": "invalid file"}), 400
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload.get("data", {}), indent=2), encoding="utf-8")
    temp.replace(path)
    return jsonify({"ok": True})


@app.post("/api/bot/profile")
def update_bot_profile():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    if discord_bot is None: return jsonify({"error": "bot unavailable"}), 503
    payload = request.get_json(silent=True) or {}
    try:
        profile_name = validate_profile_name(payload.get("name"))
        avatar_url = validate_avatar_url(payload.get("avatar_url"))
        requested_status = str(payload.get("status") or "online").strip().lower()
        presence_status = normalise_presence_status(requested_status)
        if requested_status != presence_status:
            return jsonify({"error": "invalid presence status"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    settings = get_bot_settings()
    previous_settings = deepcopy(settings)
    global_cfg = settings.setdefault("global", {})
    global_cfg.update({
        "profile_name": profile_name,
        "profile_avatar_url": avatar_url,
        "presence_status": presence_status,
    })
    save_bot_settings(settings)
    try:
        result = _run_bot_coro(apply_bot_profile(discord_bot, include_identity=True))
    except Exception as exc:
        save_bot_settings(previous_settings)
        try:
            _run_bot_coro(apply_bot_profile(discord_bot, include_identity=True))
        except Exception as rollback_exc:
            print(f"[BOT PROFILE] Rollback could not be applied live: {rollback_exc!r}")
        return jsonify({"error": f"Discord rejected the profile update; the saved settings were restored: {exc}"}), 502
    return jsonify({"ok": True, "status": presence_status, "result": result})


@app.post("/api/premium/entitlements")
def premium_entitlements():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": True, "data": load_entitlements()})


@app.post("/api/premium/grant")
def premium_grant():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        record = grant_entitlement(
            subject_type=payload.get("subject_type"),
            subject_id=payload.get("subject_id"),
            plan=str(payload.get("plan") or "").lower(),
            expires_at=payload.get("expires_at"),
            granted_by=payload.get("actor_id"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    try:
        if str(payload.get("subject_type")) == "server":
            avatar_result = _run_bot_coro(sync_plan_avatars(discord_bot, server_id=int(payload.get("subject_id")), force=True))
        else:
            avatar_result = _run_bot_coro(sync_plan_avatars(discord_bot, owner_id=int(payload.get("subject_id")), force=True))
    except Exception as exc:
        avatar_result = [{"ok": False, "error": f"Plan granted, but the server avatar could not refresh: {exc}"}]
    return jsonify({"ok": True, "record": record, "avatar_result": avatar_result})


@app.post("/api/premium/revoke")
def premium_revoke():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        removed = revoke_entitlement(
            subject_type=payload.get("subject_type"),
            subject_id=payload.get("subject_id"),
            revoked_by=payload.get("actor_id"),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    if not removed:
        return jsonify({"error": "No active entitlement was found for that ID."}), 404
    try:
        if str(payload.get("subject_type")) == "server":
            avatar_result = _run_bot_coro(sync_plan_avatars(discord_bot, server_id=int(payload.get("subject_id")), force=True))
        else:
            avatar_result = _run_bot_coro(sync_plan_avatars(discord_bot, owner_id=int(payload.get("subject_id")), force=True))
    except Exception as exc:
        avatar_result = [{"ok": False, "error": f"Plan revoked, but the server avatar could not refresh: {exc}"}]
    return jsonify({"ok": True, "avatar_result": avatar_result})


@app.post("/api/logs/read")
def read_logs():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    guild_id = "".join(c for c in str((request.get_json(silent=True) or {}).get("guild_id", "")) if c.isdigit())
    path = Path(DATA_DIR) / "server_logs" / f"{guild_id}.txt"
    return jsonify({"data": path.read_text(errors="replace") if path.exists() else "No activity logs yet."})


@app.post("/api/bot/restart_shard")
def restart_shard():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    if discord_bot is None: return jsonify({"error": "bot unavailable"}), 503
    try:
        shard_id = int((request.get_json(silent=True) or {}).get("shard_id"))
        asyncio.run_coroutine_threadsafe(discord_bot.restart_cluster(shard_id), discord_bot.loop)
        return jsonify({"ok": True})
    except Exception as exc: return jsonify({"error": str(exc)}), 400


@app.post("/api/topgg/webhook")
def topgg_webhook():
    if not TOPGG_WEBHOOK_TOKEN or request.headers.get("Authorization", "") != TOPGG_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    user_id = str((request.get_json(silent=True) or {}).get("user", ""))
    if not user_id: return jsonify({"error": "missing user"}), 400
    path = Path(DATA_DIR) / "votes.json"
    try: votes = json.loads(path.read_text()) if path.exists() else []
    except Exception: votes = []
    votes.append({"user_id": user_id, "timestamp": int(time.time()), "claimed": False})
    path.write_text(json.dumps(votes, indent=2))
    return jsonify({"ok": True})



@app.post("/api/guild/resources")
def guild_resources():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    guild = _guild_from_payload(payload)
    if guild is None: return jsonify({"error": "guild unavailable"}), 404
    channels = []
    for channel in guild.channels:
        if hasattr(channel, "type"):
            channels.append({
                "id": str(channel.id), "name": channel.name,
                "type": str(channel.type), "position": channel.position,
                "category_id": str(channel.category_id) if getattr(channel, "category_id", None) else None,
            })
    roles = [{
        "id": str(role.id), "name": role.name, "position": role.position,
        "managed": role.managed, "mentionable": role.mentionable, "color": role.color.value,
    } for role in reversed(guild.roles) if not role.is_default()]
    me = guild.me
    return jsonify({
        "ok": True, "guild": {"id": str(guild.id), "name": guild.name, "member_count": guild.member_count or 0},
        "channels": channels, "roles": roles,
        "bot_permissions": dict(me.guild_permissions) if me else {},
        "bot_top_role_position": me.top_role.position if me else 0,
    })


async def _dashboard_action_async(payload):
    guild = _guild_from_payload(payload)
    if guild is None:
        raise RuntimeError("The bot is not connected to that server.")
    action = str(payload.get("action", "")).strip().lower()
    params = payload.get("params", {}) if isinstance(payload.get("params"), dict) else {}
    channel = guild.get_channel(int(params.get("channel_id", 0))) if params.get("channel_id") else None
    actor_id = int(payload.get("actor_id", 0) or 0)
    actor = guild.get_member(actor_id)
    if not isinstance(actor, discord.Member):
        raise RuntimeError("You must still be a member of this server to run dashboard actions.")

    if action == "logging.dashboard":
        if not (actor.guild_permissions.manage_guild or actor.guild_permissions.administrator):
            raise RuntimeError("You need Manage Server to record dashboard configuration changes.")
        return {"message": str(params.get("message") or "Dashboard configuration updated.")[:500]}

    if action in {"embed.load", "embed.update", "embed.send"}:
        if not (actor.guild_permissions.manage_messages or actor.guild_permissions.administrator):
            raise RuntimeError("You need Manage Messages to use the embed editor.")
        if action == "embed.send":
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError("Choose a text channel to send the embed to.")
            bot_member = guild.me
            permissions = channel.permissions_for(bot_member) if bot_member else None
            if permissions is None or not permissions.send_messages or not permissions.embed_links:
                raise RuntimeError(f"Rallybit needs Send Messages and Embed Links in #{channel.name}.")
            embed = _dashboard_embed_from_params(params)
            content = str(params.get("content") or "").strip()[:2000]
            try:
                message = await channel.send(
                    content=content or None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden as exc:
                raise RuntimeError("Rallybit no longer has permission to send messages in that channel.") from exc
            except discord.HTTPException as exc:
                raise RuntimeError(f"Discord rejected the embed message: {exc}") from exc
            return {
                "message": f"Sent a new embed message in #{channel.name}.",
                "embed": _dashboard_embed_payload(message, 0),
            }
        try:
            message_id = int(params.get("message_id", 0))
            embed_index = int(params.get("embed_index", 1)) - 1
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Enter a valid Discord message ID and embed number.") from exc
        if message_id <= 0 or embed_index < 0 or embed_index > 9:
            raise RuntimeError("Enter a valid Discord message ID and an embed number from 1 to 10.")
        message = await _find_dashboard_message(guild, message_id, channel)
        if guild.me is None or message.author.id != guild.me.id:
            raise RuntimeError("Rallybit can only edit embeds on messages it originally sent.")
        if not message.embeds:
            raise RuntimeError("That Rallybit message does not contain an embed.")
        if embed_index >= len(message.embeds):
            raise RuntimeError(f"That message contains {len(message.embeds)} embed(s). Choose an available embed number.")
        if action == "embed.update":
            replacement = _dashboard_embed_from_params(params)
            embeds = list(message.embeds)
            embeds[embed_index] = replacement
            content = str(params.get("content") or "").strip()[:2000]
            try:
                message = await message.edit(
                    content=content or None,
                    embeds=embeds,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden as exc:
                raise RuntimeError("Rallybit no longer has permission to edit that message.") from exc
            except discord.HTTPException as exc:
                raise RuntimeError(f"Discord rejected the embed update: {exc}") from exc
            return {
                "message": f"Updated embed {embed_index + 1} on message {message.id} in #{getattr(message.channel, 'name', 'channel')}.",
                "embed": _dashboard_embed_payload(message, embed_index),
            }
        return {
            "message": f"Loaded embed {embed_index + 1} from #{getattr(message.channel, 'name', 'channel')}.",
            "embed": _dashboard_embed_payload(message, embed_index),
        }

    if action == "activity.start":
        from commands.activity import get_guild_settings, launch_activity_check
        if not hasattr(channel, "send"): raise RuntimeError("Choose a text channel.")
        settings = get_guild_settings(guild.id)
        if params.get("ping_role_id"):
            role = guild.get_role(int(params["ping_role_id"])); settings["ping_target"] = role.mention if role else settings.get("ping_target")
        await launch_activity_check(guild, channel, actor, settings_override=settings)
        return {"message": f"Activity check started in #{channel.name}."}
    if action == "activity.stop":
        from commands.activity import active_guild_checks
        event = active_guild_checks.get(guild.id)
        if not event: raise RuntimeError("No activity check is active.")
        event.set(); return {"message": "Activity check stopping."}
    if action == "quiz.start":
        from commands.quizzes import start_quiz
        role = guild.get_role(int(params.get("ping_role_id", 0))) if params.get("ping_role_id") else None
        await start_quiz(guild, channel, actor, str(params.get("category", "mixed")), int(params.get("duration_seconds", 30)), False, role)
        return {"message": f"Quiz started in #{channel.name}."}
    if action == "quiz.stop":
        from commands.quizzes import active_quizzes
        session = active_quizzes.get(guild.id)
        if not session: raise RuntimeError("No quiz is active.")
        await session.finish("dashboard"); return {"message": "Quiz ended."}
    if action == "pulse.start":
        from commands.community import start_pulse
        role = guild.get_role(int(params.get("ping_role_id", 0))) if params.get("ping_role_id") else None
        await start_pulse(guild, channel, actor, int(params.get("duration_minutes", 10)), str(params.get("prompt") or "How is everyone feeling right now?"), role)
        return {"message": f"Pulse started in #{channel.name}."}
    if action == "pulse.stop":
        from commands.community import active_pulses
        session = active_pulses.get(guild.id)
        if not session: raise RuntimeError("No pulse is active.")
        await session.finish("dashboard"); return {"message": "Pulse ended."}
    if action == "icebreaker.post":
        import random

        from commands.community import ICEBREAKERS
        category = str(params.get("category", "casual")); pool = ICEBREAKERS.get(category, sum(ICEBREAKERS.values(), []))
        role = guild.get_role(int(params.get("ping_role_id", 0))) if params.get("ping_role_id") else None
        content = role.mention if role and (role.mentionable or channel.permissions_for(guild.me).mention_everyone) else None
        await channel.send(content=content, embed=__import__('discord').Embed(title="💬 Conversation starter", description=f"## {random.choice(pool)}", color=0x5865F2), allowed_mentions=__import__('discord').AllowedMentions(roles=[role] if content and role else False, users=False, everyone=False))
        return {"message": f"Icebreaker posted in #{channel.name}."}
    if action == "giveaway.start":
        from commands.giveaways import start_giveaway
        role = guild.get_role(int(params.get("ping_role_id", 0))) if params.get("ping_role_id") else None
        required = guild.get_role(int(params.get("required_role_id", 0))) if params.get("required_role_id") else None
        session = await start_giveaway(guild, channel, actor, str(params.get("prize") or "Community giveaway"), int(params.get("duration_minutes", 60)), int(params.get("winners", 1)), required, role)
        return {"message": f"Giveaway {session.giveaway_id} started.", "id": session.giveaway_id}
    if action == "giveaway.end":
        from commands.giveaways import active_giveaways
        session = active_giveaways.get(str(params.get("giveaway_id", "")).upper())
        if not session or session.guild.id != guild.id: raise RuntimeError("Giveaway not found.")
        await session.finish("dashboard"); return {"message": "Giveaway ended."}
    if action == "security.trap":
        from commands.security import (
            _create_or_repair_trap,
            get_security_settings,
            save_security_settings,
        )
        settings = get_security_settings(guild.id)
        settings["trap"]["enabled"] = True
        if params.get("action"): settings["trap"]["action"] = str(params["action"])
        channel_obj = await _create_or_repair_trap(guild, settings)
        settings["trap"]["channel_id"] = channel_obj.id
        save_security_settings(guild.id, settings)
        return {"message": f"Security trap ready in #{channel_obj.name}."}
    if action == "security.lockdown":
        from commands.security import get_security_settings
        settings = get_security_settings(guild.id)
        enabled = bool(params.get("enabled", True))
        changed = 0
        for target in guild.text_channels:
            overwrite = target.overwrites_for(guild.default_role)
            overwrite.send_messages = False if enabled else None
            try: await target.set_permissions(guild.default_role, overwrite=overwrite, reason=f"Rallybit dashboard lockdown by {actor}"); changed += 1
            except Exception: pass
        return {"message": f"{'Locked' if enabled else 'Unlocked'} {changed} text channels."}
    if action == "moderation.warn":
        from commands.moderation import (
            _add_warning,
            _append_history,
            _hierarchy_error,
            _send_log,
            can_use_moderation_action,
            moderation_denial,
        )
        member = guild.get_member(int(params.get("user_id", 0)))
        if not member: raise RuntimeError("Member not found.")
        if not can_use_moderation_action(actor, "warn"): raise RuntimeError(moderation_denial("warn"))
        hierarchy_error = _hierarchy_error(actor, member, guild.me)
        if hierarchy_error: raise RuntimeError(hierarchy_error)
        reason = str(params.get("reason") or "No reason")
        warning = _add_warning(guild.id, member, actor, reason)
        _append_history(guild.id, member.id, "Warning added", actor, reason, f"Warning ID {warning['id']}")
        await _send_log(guild, actor, "mod warn", member, f"**Warning:** `{warning['id']}`\n**Reason:** {reason}", channel)
        try: await member.send(f"You were warned in **{guild.name}**: {params.get('reason') or 'No reason'}")
        except Exception: pass
        return {"message": f"Warned {member}."}
    if action in {"moderation.timeout", "moderation.kick", "moderation.ban"}:
        from datetime import timedelta

        from commands.moderation import (
            _append_history,
            _hierarchy_error,
            _send_log,
            can_use_moderation_action,
            moderation_denial,
        )
        member = guild.get_member(int(params.get("user_id", 0)))
        if not member: raise RuntimeError("Member not found.")
        action_name = action.split(".")[1]
        if not can_use_moderation_action(actor, action_name): raise RuntimeError(moderation_denial(action_name))
        hierarchy_error = _hierarchy_error(actor, member, guild.me)
        if hierarchy_error: raise RuntimeError(hierarchy_error)
        reason = str(params.get("reason") or f"Dashboard action by {actor}")
        if action == "moderation.timeout":
            await member.timeout(timedelta(minutes=max(1, int(params.get("minutes", 10)))), reason=reason)
        elif action == "moderation.kick": await member.kick(reason=reason)
        else: await member.ban(reason=reason, delete_message_seconds=0)
        label = {"timeout": "Timeout applied", "kick": "Kicked", "ban": "Banned"}[action_name]
        _append_history(guild.id, member.id, label, actor, reason)
        await _send_log(guild, actor, f"mod {action_name}", member, f"**Reason:** {reason}", channel)
        return {"message": f"Completed {action_name} for {member}."}
    if action == "verification.publish":
        from commands.roles import publish_verification_panel
        from config.config import VERIFICATION_SETTINGS_FILE
        from storage.json_store import load_json
        cfg = (load_json(VERIFICATION_SETTINGS_FILE) or {}).get(str(guild.id), {})
        target = channel or guild.get_channel(int(cfg.get("channel_id", 0)))
        role = guild.get_role(int(params.get("role_id") or cfg.get("role_id") or 0))
        remove_role = guild.get_role(int(params.get("remove_role_id") or cfg.get("remove_role_id") or 0)) if (params.get("remove_role_id") or cfg.get("remove_role_id")) else None
        if not isinstance(target, discord.TextChannel) or role is None: raise RuntimeError("Configure a panel channel and verified role first.")
        message = await publish_verification_panel(guild, target, role, remove_role, str(params.get("title") or cfg.get("title") or "Verify your account"), str(params.get("description") or cfg.get("description") or "Press the button below to verify."), str(params.get("button_label") or cfg.get("button_label") or "Verify"))
        return {"message": f"Verification panel published in #{target.name}.", "message_id": str(message.id)}
    if action in {"ticket.panel", "ticket.panel.load", "ticket.panel.update"}:
        from commands.tickets import (
            _panel_options,
            _panels,
            create_ticket_panel,
            ticket_panel_by_message,
            update_ticket_panel,
        )
        from config.config import TICKET_SETTINGS_FILE
        from storage.json_store import load_json

        if not (actor.guild_permissions.manage_messages or actor.guild_permissions.administrator):
            raise RuntimeError("You need Manage Messages to publish or edit ticket panels.")
        if action == "ticket.panel.load":
            try:
                message_id = int(params.get("message_id") or 0)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("Enter a valid Discord message ID.") from exc
            found = ticket_panel_by_message(guild.id, message_id)
            if found is None:
                raise RuntimeError("That message is not a saved Rallybit ticket panel in this server.")
            panel_id, panel = found
            panel_channel = guild.get_channel(int(panel.get("channel_id") or 0))
            if not isinstance(panel_channel, discord.TextChannel):
                raise RuntimeError("The saved ticket panel channel is no longer available.")
            try:
                panel_message = await panel_channel.fetch_message(message_id)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                raise RuntimeError("Rallybit could not access that saved ticket panel message.") from exc
            if guild.me is None or panel_message.author.id != guild.me.id:
                raise RuntimeError("Rallybit can only edit ticket panels it originally sent.")
            return {
                "message": f"Loaded ticket panel {panel_id} from #{panel_channel.name}.",
                "panel": _ticket_panel_dashboard_payload(guild, panel_id, panel, _panel_options(panel)),
            }

        cfg = (load_json(TICKET_SETTINGS_FILE) or {}).get(str(guild.id), {})
        panel_id = str(params.get("panel_id") or "").strip().upper()
        if action == "ticket.panel.update":
            existing_panel = _panels().get(str(guild.id), {}).get(panel_id)
            if not isinstance(existing_panel, dict):
                raise RuntimeError("That ticket panel is no longer available. Load it again.")
            if str(params.get("message_id") or "") != str(existing_panel.get("message_id") or ""):
                raise RuntimeError("The loaded ticket message changed. Load the panel again before saving.")
            target = guild.get_channel(int(existing_panel.get("channel_id") or 0))
        else:
            target = channel
        configured_options = params.get("options")
        first_option_category = None
        if isinstance(configured_options, list):
            first_option = next((row for row in configured_options if isinstance(row, dict) and row.get("category_id")), None)
            first_option_category = first_option.get("category_id") if first_option else None
        category_id = params.get("category_id") or first_option_category or cfg.get("default_category_id")
        category = guild.get_channel(int(category_id or 0))
        configured_roles = cfg.get("support_role_ids", []) if isinstance(cfg, dict) else []
        support_role_id = params.get("support_role_id") or (configured_roles[0] if configured_roles else None)
        support_role = guild.get_role(int(support_role_id)) if support_role_id else None
        if not isinstance(target, discord.TextChannel):
            raise RuntimeError("Choose a text channel for the ticket panel.")
        if not isinstance(category, discord.CategoryChannel):
            raise RuntimeError("Choose a ticket category or save a default category first.")
        bot_member = guild.me
        permissions = target.permissions_for(bot_member) if bot_member else None
        if permissions is None or not permissions.send_messages or not permissions.embed_links:
            raise RuntimeError(f"Rallybit needs Send Messages and Embed Links in #{target.name}.")
        panel_options: list[dict[str, Any]] = []
        if isinstance(configured_options, list):
            for raw_option in configured_options[:25]:
                if not isinstance(raw_option, dict):
                    continue
                option_name = str(raw_option.get("name") or "").strip()
                if not option_name:
                    continue
                option_category = guild.get_channel(int(raw_option.get("category_id") or category.id))
                if not isinstance(option_category, discord.CategoryChannel):
                    raise RuntimeError(f"Choose a valid category for the {option_name[:100]} ticket option.")
                option_role_id = raw_option.get("support_role_id")
                option_role = guild.get_role(int(option_role_id)) if option_role_id else support_role
                if option_role_id and option_role is None:
                    raise RuntimeError(f"Choose a valid support role for the {option_name[:100]} ticket option.")
                panel_option = {
                    "name": option_name,
                    "description": str(raw_option.get("description") or "Speak privately with the support team."),
                    "emoji": str(raw_option.get("emoji") or ""),
                    "category_id": option_category.id,
                    "support_role_ids": [option_role.id] if option_role else [],
                    "ticket_name": str(raw_option.get("ticket_name") or ""),
                }
                if str(raw_option.get("option_id") or "").strip():
                    panel_option["option_id"] = str(raw_option["option_id"]).strip()
                panel_options.append(panel_option)
        if not panel_options:
            panel_options = [{
                "name": str(params.get("name") or "Support"),
                "description": str(params.get("option_description") or "Speak privately with the support team."),
                "emoji": str(params.get("option_emoji") or "🎫"),
                "category_id": category.id,
                "support_role_ids": [support_role.id] if support_role else [],
            }]
        if action == "ticket.panel.update":
            saved_panel = await update_ticket_panel(
                guild,
                panel_id,
                name=str(params.get("name") or panel_options[0]["name"]),
                title=str(params.get("title") or "How can we help?"),
                description=str(params.get("description") or "Choose the ticket type that best matches what you need. Your conversation will be private."),
                options=panel_options,
                select_placeholder=str(params.get("select_placeholder") or "Select a ticket type..."),
                color=str(params.get("color") or "#7C6CFF"),
                author_name=str(params.get("author_name") or ""),
                author_icon_url=str(params.get("author_icon_url") or ""),
                header_image_url=str(params.get("header_image_url") or ""),
                thumbnail_url=str(params.get("thumbnail_url") or ""),
                image_url=str(params.get("image_url") or ""),
                footer_text=str(params.get("footer_text") or ""),
                footer_icon_url=str(params.get("footer_icon_url") or ""),
                show_author=bool(params.get("show_author", True)),
                show_option_details=bool(params.get("show_option_details", True)),
                show_workload=bool(params.get("show_workload", True)),
                show_guidance=bool(params.get("show_guidance", True)),
                show_timestamp=bool(params.get("show_timestamp", True)),
            )
            return {
                "message": f"Ticket dropdown {panel_id} with {len(panel_options)} option(s) updated in #{target.name}.",
                "panel_id": panel_id,
                "panel": _ticket_panel_dashboard_payload(guild, panel_id, saved_panel, _panel_options(saved_panel)),
            }

        panel_id = await create_ticket_panel(
            guild, target, category, str(params.get("name") or panel_options[0]["name"]),
            str(params.get("title") or "How can we help?"),
            str(params.get("description") or "Choose the ticket type that best matches what you need. Your conversation will be private."),
            support_role, str(params.get("button_label") or "Open ticket"),
            options=panel_options,
            select_placeholder=str(params.get("select_placeholder") or "Select a ticket type…"),
            color=str(params.get("color") or "#7C6CFF"),
            author_name=str(params.get("author_name") or ""),
            author_icon_url=str(params.get("author_icon_url") or ""),
            header_image_url=str(params.get("header_image_url") or ""),
            thumbnail_url=str(params.get("thumbnail_url") or ""),
            image_url=str(params.get("image_url") or ""),
            footer_text=str(params.get("footer_text") or ""),
            footer_icon_url=str(params.get("footer_icon_url") or ""),
            show_author=bool(params.get("show_author", True)),
            show_option_details=bool(params.get("show_option_details", True)),
            show_workload=bool(params.get("show_workload", True)),
            show_guidance=bool(params.get("show_guidance", True)),
            show_timestamp=bool(params.get("show_timestamp", True)),
        )
        return {
            "message": f"Ticket dropdown {panel_id} with {len(panel_options)} option(s) published in #{target.name}.",
            "panel_id": panel_id,
            "panel": _ticket_panel_dashboard_payload(
                guild,
                panel_id,
                _panels()[str(guild.id)][panel_id],
                _panel_options(_panels()[str(guild.id)][panel_id]),
            ),
        }
    if action == "reactionrole.add":
        from config.config import REACTION_ROLES_FILE
        from storage.json_store import load_json, save_json
        target = channel
        role = guild.get_role(int(params.get("role_id", 0)))
        if not isinstance(target, discord.TextChannel) or role is None: raise RuntimeError("Choose a channel and role.")
        message = await target.fetch_message(int(params.get("message_id", 0)))
        emoji = discord.PartialEmoji.from_str(str(params.get("emoji") or "✅"))
        await message.add_reaction(emoji)
        key = f"id:{emoji.id}" if emoji.id else f"unicode:{emoji.name}"
        data = load_json(REACTION_ROLES_FILE) or {}; data.setdefault(str(guild.id), {}).setdefault(str(message.id), {})[key] = role.id; save_json(REACTION_ROLES_FILE, data)
        return {"message": f"Reaction role added to message {message.id}."}
    if action == "level.setxp":
        from commands.levels import (
            _apply_reward_roles,
            _member_record,
            _save_stats,
            _settings,
            level_from_xp,
        )
        member = guild.get_member(int(params.get("user_id", 0)))
        if not member: raise RuntimeError("Member not found.")
        xp = max(0, int(params.get("xp", 0)))
        data, record = _member_record(guild.id, member.id, member.display_name); record["xp"] = xp; record["level"] = level_from_xp(xp); data[str(guild.id)][str(member.id)] = record; _save_stats(data); await _apply_reward_roles(member, record["level"], _settings(guild.id))
        return {"message": f"Set {member} to {xp} XP."}
    if action == "automation.run":
        from commands.automation import _guild, run_schedule
        schedule_id = str(params.get("schedule_id", "")).upper(); schedule = _guild(guild.id).get(schedule_id)
        if not isinstance(schedule, dict): raise RuntimeError("Schedule not found.")
        return {"message": await run_schedule(discord_bot, guild, schedule_id, schedule)}
    raise RuntimeError("That dashboard action is not supported by this build.")


async def _dashboard_action_with_audit(payload: dict[str, Any]) -> dict[str, Any]:
    result = await _dashboard_action_async(payload)
    guild = _guild_from_payload(payload)
    if guild is None:
        return result
    actor = guild.get_member(int(payload.get("actor_id", 0) or 0))
    action = str(payload.get("action") or "dashboard action").strip().lower()
    event_type = (
        "moderation" if action.startswith("moderation.") else
        "tickets" if action.startswith("ticket.") else
        "security" if action.startswith("security.") else
        "configuration"
    )
    try:
        from core.audit import emit_audit_event

        await emit_audit_event(
            guild,
            event_type,
            "Dashboard action completed",
            f"`{action}` completed through the Rallybit dashboard.\n{str(result.get('message') or '')[:1000]}",
            actor=actor,
        )
    except Exception as exc:
        print(f"[AUDIT] Dashboard action could not be recorded: {exc!r}")
    return result


@app.post("/api/dashboard/action")
def dashboard_action():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        result = _run_bot_coro(_dashboard_action_with_audit(payload), timeout=45)
        return jsonify({"ok": True, **(result or {})})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

def start_api(bot_instance):
    global discord_bot
    discord_bot = bot_instance
    app.run(host=API_HOST, port=API_PORT, debug=False, threaded=True)
