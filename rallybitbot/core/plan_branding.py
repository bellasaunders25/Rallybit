from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import discord

from config.config import BASE_DIR, PLAN_AVATAR_STATE_FILE
from core.premium import resolve_entitlement
from storage.json_store import load_json, save_json

PLAN_AVATAR_DIR = Path(BASE_DIR) / "assets" / "plan-avatars"
PLAN_AVATAR_PATHS = {
    "community": PLAN_AVATAR_DIR / "community.png",
    "pro": PLAN_AVATAR_DIR / "pro.png",
    "network": PLAN_AVATAR_DIR / "network.png",
}
SYNC_INTERVAL_SECONDS = 300


def effective_guild_plan(guild: discord.Guild) -> str:
    # Resolve as the owner so a developer-owned server receives the same
    # unrestricted Network preview shown in the dashboard.
    entitlement = resolve_entitlement(user_id=guild.owner_id, guild_id=guild.id, guild_owner_id=guild.owner_id)
    plan = str(entitlement.get("plan") or "free").lower()
    return plan if plan in {"community", "pro", "network"} else "free"


def _avatar_payload(plan: str) -> tuple[bytes | None, str]:
    if plan == "free":
        return None, "global"
    path = PLAN_AVATAR_PATHS.get(plan)
    if path is None or not path.is_file():
        raise RuntimeError(f"The {plan} plan avatar asset is missing.")
    payload = path.read_bytes()
    if not payload or len(payload) > 10 * 1024 * 1024:
        raise RuntimeError(f"The {plan} plan avatar asset is invalid.")
    return payload, hashlib.sha256(payload).hexdigest()


def _state() -> dict[str, dict[str, Any]]:
    data = load_json(PLAN_AVATAR_STATE_FILE) or {}
    return data if isinstance(data, dict) else {}


async def sync_guild_plan_avatar(guild: discord.Guild, *, force: bool = False) -> dict[str, Any]:
    member = guild.me
    if member is None:
        return {"guild_id": guild.id, "ok": False, "error": "bot member unavailable"}
    plan = effective_guild_plan(guild)
    try:
        avatar, asset_hash = _avatar_payload(plan)
    except RuntimeError as exc:
        return {"guild_id": guild.id, "ok": False, "plan": plan, "error": str(exc)}
    state = _state()
    saved = state.get(str(guild.id), {})
    has_expected_kind = member.guild_avatar is None if plan == "free" else member.guild_avatar is not None
    if not force and has_expected_kind and saved.get("plan") == plan and saved.get("asset_hash") == asset_hash:
        return {"guild_id": guild.id, "ok": True, "plan": plan, "changed": False}
    try:
        await member.edit(avatar=avatar, reason=f"Rallybit {plan.title()} plan branding")
    except (discord.Forbidden, discord.HTTPException, TypeError) as exc:
        return {"guild_id": guild.id, "ok": False, "plan": plan, "error": f"{type(exc).__name__}: {exc}"}
    state[str(guild.id)] = {"plan": plan, "asset_hash": asset_hash}
    if not save_json(PLAN_AVATAR_STATE_FILE, state):
        return {"guild_id": guild.id, "ok": False, "plan": plan, "error": "avatar applied but state could not be saved"}
    return {"guild_id": guild.id, "ok": True, "plan": plan, "changed": True}


async def sync_plan_avatars(
    bot: discord.Client,
    *,
    server_id: int | None = None,
    owner_id: int | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for guild in bot.guilds:
        if server_id is not None and guild.id != server_id:
            continue
        if owner_id is not None and guild.owner_id != owner_id:
            continue
        result = await sync_guild_plan_avatar(guild, force=force)
        results.append(result)
        if result.get("changed"):
            await asyncio.sleep(1)
    return results


async def plan_avatar_sync_loop(bot: discord.Client) -> None:
    while not bot.is_closed():
        try:
            results = await sync_plan_avatars(bot)
            failures = [row for row in results if not row.get("ok")]
            if failures:
                print(f"[PLAN AVATARS] {len(failures)} guild avatar update(s) failed: {failures!r}")
        except Exception as exc:
            print(f"[PLAN AVATARS] Background sync failed: {exc!r}")
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)
