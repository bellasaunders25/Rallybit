from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord
from discord import app_commands

from config.config import INVITE_TRACKING_FILE, WELCOME_SETTINGS_FILE
from storage.json_store import load_json, save_json

BRAND = 0x5865F2
invite_cache: dict[int, dict[str, int]] = {}
vanity_cache: dict[int, tuple[str, int]] = {}
delivery_guard: dict[tuple[str, int, int], float] = {}
DELIVERY_GUARD_SECONDS = 30.0
_last_marker_cleanup = 0.0


def _claim_delivery(kind: str, guild_id: int, member_id: int) -> bool:
    global _last_marker_cleanup
    now = time.monotonic()
    stale = [key for key, timestamp in delivery_guard.items() if now - timestamp > DELIVERY_GUARD_SECONDS]
    for key in stale:
        delivery_guard.pop(key, None)
    key = (kind, guild_id, member_id)
    previous = delivery_guard.get(key)
    if previous is not None and now - previous <= DELIVERY_GUARD_SECONDS:
        return False

    # The disk marker also protects against duplicate Discord gateway events when
    # two Rallybit processes briefly overlap during a deployment or migration.
    marker_dir = Path(WELCOME_SETTINGS_FILE).parent / ".welcome-delivery"
    wall_time = time.time()
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        if wall_time - _last_marker_cleanup > 300:
            for marker in marker_dir.glob("*.lock"):
                try:
                    if wall_time - marker.stat().st_mtime > 300:
                        marker.unlink(missing_ok=True)
                except OSError:
                    continue
            _last_marker_cleanup = wall_time
        marker_path = marker_dir / f"{kind}-{guild_id}-{member_id}.lock"
        try:
            descriptor = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if wall_time - marker_path.stat().st_mtime <= DELIVERY_GUARD_SECONDS:
                    return False
                marker_path.unlink(missing_ok=True)
                descriptor = os.open(marker_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except (FileExistsError, OSError):
                return False
        try:
            os.write(descriptor, str(wall_time).encode("ascii"))
        finally:
            os.close(descriptor)
    except OSError:
        # In-memory protection still works if the data directory is read-only.
        pass
    delivery_guard[key] = now
    return True


@dataclass(frozen=True)
class InviteAttribution:
    kind: str
    inviter: discord.User | discord.Member | None = None
    code: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "oauth":
            return "OAuth bot authorization"
        if self.kind == "vanity":
            return f"Server vanity invite (`discord.gg/{self.code}`)" if self.code else "Server vanity invite"
        if self.kind == "invite":
            inviter = self.inviter.mention if self.inviter else "Unknown inviter"
            code = f" (`{self.code}`)" if self.code else ""
            return f"Invite from {inviter}{code}"
        return "Invite source unavailable"


def _all_settings() -> dict[str, Any]:
    data = load_json(WELCOME_SETTINGS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _settings(guild_id: int) -> dict[str, Any]:
    defaults = {
        "welcome_enabled": False,
        "welcome_channel_id": None,
        "welcome_message": "Welcome {user} to **{server}**! You are member **#{member_count}**.",
        "welcome_embed": True,
        "welcome_dm": False,
        "goodbye_enabled": False,
        "goodbye_channel_id": None,
        "goodbye_message": "**{username}** has left **{server}**. We now have {member_count} members.",
        "goodbye_embed": True,
        "invite_tracking": True,
    }
    saved = _all_settings().get(str(guild_id), {})
    if isinstance(saved, dict):
        defaults.update(saved)
    return defaults


def _save(guild_id: int, settings: dict[str, Any]) -> None:
    data = _all_settings()
    data[str(guild_id)] = settings
    save_json(WELCOME_SETTINGS_FILE, data)


def _invite_data() -> dict[str, Any]:
    data = load_json(INVITE_TRACKING_FILE) or {}
    return data if isinstance(data, dict) else {}


def _render(
    template: str,
    member: discord.Member,
    inviter: discord.User | discord.Member | None = None,
    invite_count: int = 0,
    attribution: InviteAttribution | None = None,
) -> str:
    attribution = attribution or InviteAttribution("unknown", inviter=inviter)
    inviter_text = inviter.mention if inviter else (
        "the server vanity invite" if attribution.kind == "vanity" else
        "OAuth" if attribution.kind == "oauth" else
        "an unknown invite"
    )
    values = {
        "user": member.mention,
        "username": member.display_name,
        "tag": str(member),
        "server": member.guild.name,
        "member_count": str(member.guild.member_count or len(member.guild.members)),
        "inviter": inviter_text,
        "inviter_name": inviter.display_name if isinstance(inviter, discord.Member) else (inviter.name if inviter else ("Vanity invite" if attribution.kind == "vanity" else "OAuth" if attribution.kind == "oauth" else "Unknown")),
        "invite_count": str(invite_count),
        "invite_code": attribution.code or "Unknown",
        "invite_type": attribution.kind,
        "invite_source": attribution.label,
        "account_age_days": str(max(0, (datetime.now(timezone.utc) - member.created_at).days)),
    }
    result = str(template or "")
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result[:4000]


async def _fetch_invites(guild: discord.Guild) -> dict[str, int]:
    try:
        invites = await guild.invites()
        return {invite.code: int(invite.uses or 0) for invite in invites}
    except (discord.Forbidden, discord.HTTPException):
        return {}


async def _fetch_vanity(guild: discord.Guild) -> tuple[str, int] | None:
    try:
        invite = await guild.vanity_invite()
        if invite and invite.code:
            return invite.code, int(invite.uses or 0)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        pass
    return None


async def refresh_invite_cache(guild: discord.Guild) -> None:
    invite_cache[guild.id] = await _fetch_invites(guild)
    vanity = await _fetch_vanity(guild)
    if vanity:
        vanity_cache[guild.id] = vanity
    else:
        vanity_cache.pop(guild.id, None)


async def _detect_invite(member: discord.Member) -> InviteAttribution:
    if member.bot:
        return InviteAttribution("oauth")
    guild = member.guild
    before = invite_cache.get(guild.id, {})
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        invites = []
    used = None
    for invite in invites:
        if int(invite.uses or 0) > int(before.get(invite.code, 0)):
            used = invite
            break
    invite_cache[guild.id] = {invite.code: int(invite.uses or 0) for invite in invites}
    if used:
        return InviteAttribution("invite", inviter=used.inviter, code=used.code)

    before_vanity = vanity_cache.get(guild.id)
    current_vanity = await _fetch_vanity(guild)
    if current_vanity:
        vanity_cache[guild.id] = current_vanity
        if before_vanity and current_vanity[0] == before_vanity[0] and current_vanity[1] > before_vanity[1]:
            return InviteAttribution("vanity", code=current_vanity[0])
    return InviteAttribution("unknown")


async def _send_welcome(member: discord.Member, inviter: discord.User | discord.Member | None, invite_count: int, attribution: InviteAttribution) -> None:
    cfg = _settings(member.guild.id)
    if not cfg.get("welcome_enabled"):
        return
    channel = member.guild.get_channel(int(cfg.get("welcome_channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        return
    text = _render(str(cfg.get("welcome_message", "")), member, inviter, invite_count, attribution)
    allowed = discord.AllowedMentions(users=[member], roles=False, everyone=False)
    try:
        if cfg.get("welcome_embed", True):
            embed = discord.Embed(description=text, color=BRAND, timestamp=datetime.now(timezone.utc))
            embed.set_author(name=f"Welcome to {member.guild.name}", icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Joined via", value=attribution.label, inline=False)
            embed.set_footer(text=f"Member #{member.guild.member_count or len(member.guild.members)}")
            await channel.send(embed=embed, allowed_mentions=allowed)
        else:
            await channel.send(f"{text}\n**Joined via:** {attribution.label}", allowed_mentions=allowed)
    except discord.HTTPException as exc:
        print(f"[WELCOME] {member.guild.id}: {exc}")
    if cfg.get("welcome_dm"):
        try:
            await member.send(f"{text}\nJoined via: {attribution.label}", allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


async def _send_goodbye(member: discord.Member, inviter: discord.User | discord.Member | None, invite_count: int) -> None:
    cfg = _settings(member.guild.id)
    if not cfg.get("goodbye_enabled"):
        return
    channel = member.guild.get_channel(int(cfg.get("goodbye_channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        return
    text = _render(str(cfg.get("goodbye_message", "")), member, inviter, invite_count)
    try:
        if cfg.get("goodbye_embed", True):
            embed = discord.Embed(description=text, color=0xED4245, timestamp=datetime.now(timezone.utc))
            embed.set_author(name=f"Member left {member.guild.name}", icon_url=member.display_avatar.url)
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        else:
            await channel.send(text, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


async def on_member_join(member: discord.Member) -> None:
    if not _claim_delivery("join", member.guild.id, member.id):
        return
    # Give security age-gate/anti-bot listeners a moment to reject unsafe joins.
    await asyncio.sleep(2)
    if member.guild.get_member(member.id) is None:
        return
    cfg = _settings(member.guild.id)
    inviter = None
    code = None
    attribution = InviteAttribution("oauth" if member.bot else "unknown")
    count = 0
    data = _invite_data()
    guild_data = data.setdefault(str(member.guild.id), {"inviter_counts": {}, "members": {}})
    if cfg.get("invite_tracking", True):
        attribution = await _detect_invite(member)
        inviter, code = attribution.inviter, attribution.code
        if inviter:
            counts = guild_data.setdefault("inviter_counts", {})
            counts[str(inviter.id)] = int(counts.get(str(inviter.id), 0)) + 1
            count = counts[str(inviter.id)]
        guild_data.setdefault("members", {})[str(member.id)] = {
            "inviter_id": str(inviter.id) if inviter else None,
            "invite_code": code,
            "invite_type": attribution.kind,
            "invite_source": attribution.label,
            "joined_at": datetime.now(timezone.utc).isoformat(),
        }
        data[str(member.guild.id)] = guild_data
        save_json(INVITE_TRACKING_FILE, data)
    await _send_welcome(member, inviter, count, attribution)


async def on_member_remove(member: discord.Member) -> None:
    if not _claim_delivery("leave", member.guild.id, member.id):
        return
    data = _invite_data()
    guild_data = data.get(str(member.guild.id), {}) if isinstance(data.get(str(member.guild.id), {}), dict) else {}
    record = guild_data.get("members", {}).get(str(member.id), {}) if isinstance(guild_data.get("members", {}), dict) else {}
    inviter_id = record.get("inviter_id") if isinstance(record, dict) else None
    inviter = member.guild.get_member(int(inviter_id)) if inviter_id and str(inviter_id).isdigit() else None
    count = int(guild_data.get("inviter_counts", {}).get(str(inviter_id), 0)) if inviter_id else 0
    await _send_goodbye(member, inviter, count)


async def on_ready_refresh(bot: discord.Client) -> None:
    for guild in bot.guilds:
        await refresh_invite_cache(guild)


def setup_welcome_commands(tree: app_commands.CommandTree) -> None:
    welcome = app_commands.Group(name="welcome", description="Configure welcome messages and invite tracking.")
    goodbye = app_commands.Group(name="goodbye", description="Configure goodbye messages.")
    invites = app_commands.Group(name="invites", description="View invite tracking statistics.")

    @welcome.command(name="setup", description="Enable and configure welcome messages.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_setup(
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str | None = None,
        embed: bool = True,
        dm_member: bool = False,
        track_invites: bool = True,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        cfg = _settings(interaction.guild.id)
        cfg.update({
            "welcome_enabled": True,
            "welcome_channel_id": channel.id,
            "welcome_message": message or cfg["welcome_message"],
            "welcome_embed": embed,
            "welcome_dm": dm_member,
            "invite_tracking": track_invites,
        })
        _save(interaction.guild.id, cfg)
        await refresh_invite_cache(interaction.guild)
        await interaction.response.send_message(f"Welcome messages will be sent in {channel.mention}.", ephemeral=True)

    @welcome.command(name="disable", description="Disable welcome messages.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_disable(interaction: discord.Interaction) -> None:
        if interaction.guild:
            cfg = _settings(interaction.guild.id); cfg["welcome_enabled"] = False; _save(interaction.guild.id, cfg)
        await interaction.response.send_message("Welcome messages disabled.", ephemeral=True)

    @welcome.command(name="preview", description="Preview the configured welcome message.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome_preview(interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        cfg = _settings(interaction.guild.id)
        text = _render(cfg["welcome_message"], interaction.user, interaction.user, 1)
        embed = discord.Embed(description=text, color=BRAND) if cfg.get("welcome_embed", True) else None
        await interaction.response.send_message(content=None if embed else text, embed=embed, ephemeral=True)

    @goodbye.command(name="setup", description="Enable and configure goodbye messages.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def goodbye_setup(interaction: discord.Interaction, channel: discord.TextChannel, message: str | None = None, embed: bool = True) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        cfg = _settings(interaction.guild.id)
        cfg.update({"goodbye_enabled": True, "goodbye_channel_id": channel.id, "goodbye_message": message or cfg["goodbye_message"], "goodbye_embed": embed})
        _save(interaction.guild.id, cfg)
        await interaction.response.send_message(f"Goodbye messages will be sent in {channel.mention}.", ephemeral=True)

    @goodbye.command(name="disable", description="Disable goodbye messages.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def goodbye_disable(interaction: discord.Interaction) -> None:
        if interaction.guild:
            cfg = _settings(interaction.guild.id); cfg["goodbye_enabled"] = False; _save(interaction.guild.id, cfg)
        await interaction.response.send_message("Goodbye messages disabled.", ephemeral=True)

    @invites.command(name="user", description="View a member's tracked invites.")
    async def invite_user(interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        member = member or interaction.user  # type: ignore[assignment]
        data = _invite_data().get(str(interaction.guild.id), {})
        count = int(data.get("inviter_counts", {}).get(str(member.id), 0)) if isinstance(data, dict) else 0
        record = data.get("members", {}).get(str(member.id), {}) if isinstance(data, dict) and isinstance(data.get("members", {}), dict) else {}
        source = str(record.get("invite_source", "Invite source unavailable")) if isinstance(record, dict) else "Invite source unavailable"
        await interaction.response.send_message(
            f"**{member.display_name}** has **{count}** tracked invite{'s' if count != 1 else ''}.\n"
            f"**Joined via:** {source}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @invites.command(name="leaderboard", description="Show the server invite leaderboard.")
    async def invite_leaderboard(interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Use this in a server.", ephemeral=True); return
        data = _invite_data().get(str(interaction.guild.id), {})
        counts = data.get("inviter_counts", {}) if isinstance(data, dict) else {}
        rows = sorted(((int(uid), int(value)) for uid, value in counts.items() if str(uid).isdigit()), key=lambda x: x[1], reverse=True)[:15]
        if not rows:
            await interaction.response.send_message("No invite data has been recorded yet.", ephemeral=True); return
        text = "\n".join(f"**{i}.** <@{uid}> — **{count}**" for i, (uid, count) in enumerate(rows, 1))
        await interaction.response.send_message(embed=discord.Embed(title="Invite leaderboard", description=text, color=BRAND))

    tree.add_command(welcome)
    tree.add_command(goodbye)
    tree.add_command(invites)
