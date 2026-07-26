from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import hmac
import os
import platform
import sys
import time
from pathlib import Path

import psutil
import discord
from flask import Flask, jsonify, request

from config.config import API_HOST, API_PORT, API_SECRET, DATA_DIR, TOPGG_WEBHOOK_TOKEN
from core.bot_profile import apply_bot_profile, validate_avatar_url, validate_profile_name
from core.bot_settings import get_bot_settings, save_bot_settings
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
    "premium_entitlements.json", "staff_shifts.json"
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
        "engine": "Rallybit API 7.1",
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
    return jsonify({"ok": True, "record": record})


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
    return jsonify({"ok": True})


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
        from commands.community import ICEBREAKERS
        import random
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
        from commands.security import get_security_settings, save_security_settings, _create_or_repair_trap
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
        from commands.moderation import _add_warning, _append_history, _hierarchy_error, _send_log, can_use_moderation_action, moderation_denial
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
        from commands.moderation import _append_history, _hierarchy_error, _send_log, can_use_moderation_action, moderation_denial
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
    if action == "ticket.panel":
        from commands.tickets import create_ticket_panel
        from config.config import TICKET_SETTINGS_FILE
        from storage.json_store import load_json

        cfg = (load_json(TICKET_SETTINGS_FILE) or {}).get(str(guild.id), {})
        target = channel
        category_id = params.get("category_id") or cfg.get("default_category_id")
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
        panel_id = await create_ticket_panel(guild, target, category, str(params.get("name") or "Support"), str(params.get("title") or "How can we help?"), str(params.get("description") or "Open a private ticket to speak with the support team. Choose the button below when you are ready."), support_role, str(params.get("button_label") or "Open ticket"))
        return {"message": f"Ticket panel {panel_id} published in #{target.name}.", "panel_id": panel_id}
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
        from commands.levels import _member_record, _save_stats, level_from_xp, _apply_reward_roles, _settings
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


@app.post("/api/dashboard/action")
def dashboard_action():
    if not auth(request): return jsonify({"error": "unauthorized"}), 401
    payload = request.get_json(silent=True) or {}
    try:
        result = _run_bot_coro(_dashboard_action_async(payload), timeout=45)
        return jsonify({"ok": True, **(result or {})})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

def start_api(bot_instance):
    global discord_bot
    discord_bot = bot_instance
    app.run(host=API_HOST, port=API_PORT, debug=False, threaded=True)
