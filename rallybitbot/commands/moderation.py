from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import discord
from discord import app_commands

from config.config import MOD_HISTORY_FILE, MOD_PERMISSIONS_FILE, WARNINGS_FILE
from core.logging import log_action_to_channel, log_server_event
from storage.json_store import load_json, save_json

PANEL_COLOUR = 0x5865F2
SUCCESS = 0x57F287
DANGER = 0xED4245
WARNING = 0xFEE75C

CONFIGURABLE_ACTIONS = ("warn", "timeout", "kick", "ban")
ACTION_PERMISSION_NAMES = {
    "warn": "manage_messages",
    "timeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "nickname": "manage_nicknames",
    "role": "manage_roles",
}
CONTROL_ACTIONS = {
    "warn_add": "warn",
    "warn_remove": "warn",
    "warn_view": "warn",
    "history": "warn",
    "reports": "warn",
    "clear": "warn",
    "timeout": "timeout",
    "untimeout": "timeout",
    "kick": "kick",
    "ban": "ban",
    "nickname": "nickname",
    "role_add": "role",
    "role_remove": "role",
}


def _moderation_permissions(guild_id: int) -> dict[str, list[str]]:
    data = load_json(MOD_PERMISSIONS_FILE) or {}
    saved = data.get(str(guild_id), {}) if isinstance(data, dict) else {}
    result: dict[str, list[str]] = {}
    for action in CONFIGURABLE_ACTIONS:
        values = saved.get(f"{action}_role_ids", []) if isinstance(saved, dict) else []
        result[action] = list(dict.fromkeys(str(value) for value in values if str(value).isdigit())) if isinstance(values, list) else []
    return result


def can_use_moderation_action(member: discord.Member, action: str) -> bool:
    action = CONTROL_ACTIONS.get(action, action)
    permissions = member.guild_permissions
    if member.id == member.guild.owner_id or permissions.administrator:
        return True
    permission_name = ACTION_PERMISSION_NAMES.get(action)
    if not permission_name or not bool(getattr(permissions, permission_name, False)):
        return False
    configured_roles = _moderation_permissions(member.guild.id).get(action, [])
    if action not in CONFIGURABLE_ACTIONS or not configured_roles:
        return True
    member_role_ids = {str(role.id) for role in member.roles}
    return bool(member_role_ids.intersection(configured_roles))


def moderation_denial(action: str) -> str:
    action = CONTROL_ACTIONS.get(action, action)
    labels = {"warn": "warnings", "timeout": "timeouts", "kick": "kicks", "ban": "bans", "nickname": "nicknames", "role": "roles"}
    return f"You do not have the configured role and Discord permission for {labels.get(action, 'that action')}."


def _action_check(action: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.guild and isinstance(interaction.user, discord.Member) and can_use_moderation_action(interaction.user, action):
            return True
        raise app_commands.CheckFailure(moderation_denial(action))
    return predicate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _warnings_data() -> dict[str, Any]:
    data = load_json(WARNINGS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _member_warnings(guild_id: int, user_id: int, active_only: bool = False) -> list[dict[str, Any]]:
    records = _warnings_data().get(str(guild_id), {}).get(str(user_id), [])
    if not isinstance(records, list):
        return []
    return [record for record in records if record.get("active", True)] if active_only else records


def _add_warning(guild_id: int, member: discord.Member, moderator: discord.Member, reason: str) -> dict[str, Any]:
    data = _warnings_data()
    guild_records = data.setdefault(str(guild_id), {})
    records = guild_records.setdefault(str(member.id), [])
    warning = {
        "id": uuid.uuid4().hex[:6].upper(),
        "reason": reason[:500],
        "moderator_id": moderator.id,
        "moderator_name": str(moderator),
        "created_at": _now_iso(),
        "active": True,
    }
    records.append(warning)
    save_json(WARNINGS_FILE, data)
    return warning


def _remove_warning(guild_id: int, user_id: int, warning_id: str, moderator: discord.Member) -> dict[str, Any] | None:
    data = _warnings_data()
    records = data.get(str(guild_id), {}).get(str(user_id), [])
    warning_id = warning_id.upper().strip()
    candidates = [record for record in records if record.get("active", True)]
    target = None
    if warning_id in {"LATEST", "LAST", ""}:
        target = candidates[-1] if candidates else None
    else:
        target = next((record for record in records if str(record.get("id", "")).upper() == warning_id and record.get("active", True)), None)
    if target:
        target["active"] = False
        target["removed_at"] = _now_iso()
        target["removed_by"] = moderator.id
        save_json(WARNINGS_FILE, data)
    return target


def _append_history(guild_id: int, target_id: int, action: str, moderator: discord.Member, reason: str = "", details: str = "") -> None:
    data = load_json(MOD_HISTORY_FILE) or {}
    guild_history = data.setdefault(str(guild_id), {})
    records = guild_history.setdefault(str(target_id), [])
    records.append({
        "action": action,
        "moderator_id": moderator.id,
        "moderator_name": str(moderator),
        "reason": reason[:500],
        "details": details[:500],
        "timestamp": _now_iso(),
    })
    guild_history[str(target_id)] = records[-100:]
    save_json(MOD_HISTORY_FILE, data)
    log_server_event(guild_id, f"Moderation: {action} target={target_id} moderator={moderator.id} reason={reason[:160]}")


def _history(guild_id: int, target_id: int) -> list[dict[str, Any]]:
    data = load_json(MOD_HISTORY_FILE) or {}
    records = data.get(str(guild_id), {}).get(str(target_id), [])
    return records if isinstance(records, list) else []


def _parse_duration(value: str) -> timedelta | None:
    match = re.fullmatch(r"\s*(\d+)\s*([smhdw])\s*", value.lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit] * amount
    # Discord timeouts may not exceed 28 days.
    if seconds < 10 or seconds > 28 * 86400:
        return None
    return timedelta(seconds=seconds)


def _is_staff(member: discord.Member) -> bool:
    return any(can_use_moderation_action(member, action) for action in (*CONFIGURABLE_ACTIONS, "nickname", "role"))


async def _staff_check(interaction: discord.Interaction) -> bool:
    if interaction.guild and isinstance(interaction.user, discord.Member) and _is_staff(interaction.user):
        return True
    raise app_commands.CheckFailure("You do not have access to any moderation panel actions.")


def _hierarchy_error(actor: discord.Member, target: discord.Member, bot_member: discord.Member, *, allow_self: bool = False) -> str | None:
    if target.id == actor.id and not allow_self:
        return "You cannot use that action on yourself."
    if target.id == actor.guild.owner_id:
        return "The server owner cannot be moderated."
    if actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
        return "That member has an equal or higher role than you."
    if target.top_role >= bot_member.top_role:
        return "Rallybit's role must be above that member's highest role."
    return None


def _format_timeout(member: discord.Member) -> str:
    until = member.timed_out_until
    if until and until > datetime.now(timezone.utc):
        return f"Timed out until {discord.utils.format_dt(until, style='R')}"
    return "No active timeout"


def build_panel_embed(member: discord.Member) -> discord.Embed:
    warnings = _member_warnings(member.guild.id, member.id, active_only=True)
    created = discord.utils.format_dt(member.created_at, style="f")
    joined = discord.utils.format_dt(member.joined_at, style="f") if member.joined_at else "Unknown"
    roles = [role.mention for role in member.roles[1:]][-4:]
    embed = discord.Embed(colour=PANEL_COLOUR)
    embed.set_author(name=f"Moderation • {member.guild.name}", icon_url=member.guild.icon.url if member.guild.icon else None)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.description = f"## {discord.utils.escape_markdown(member.display_name)}\n{member.mention} • `{member.id}`"
    embed.add_field(name="Account", value=f"**Created:** {created}\n**Joined:** {joined}", inline=True)
    embed.add_field(name="Member state", value=f"**Warnings:** `{len(warnings)}`\n**Timeout:** {_format_timeout(member)}", inline=True)
    embed.add_field(name="Highest role", value=member.top_role.mention if member.top_role != member.guild.default_role else "No assigned role", inline=True)
    embed.add_field(name="Recent roles", value=" ".join(roles) if roles else "No assigned roles", inline=False)
    embed.add_field(name="Quick actions", value="Use the controls below. Every action is permission-checked, hierarchy-checked and recorded in Rallybit's moderation history.", inline=False)
    embed.set_footer(text="Rallybit Moderation Panel • Actions are never automatic")
    return embed


async def _send_log(guild: discord.Guild, moderator: discord.Member, command: str, member: discord.abc.User, details: str, channel: discord.abc.GuildChannel | None) -> None:
    await log_action_to_channel(guild, moderator, command, f"**Target:** {member} (`{member.id}`)\n{details}", channel)


class PanelModal(discord.ui.Modal):
    def __init__(self, panel: "ModerationPanelView", title: str):
        super().__init__(title=title)
        self.panel = panel

    async def fail(self, interaction: discord.Interaction, message: str) -> None:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)

    async def refresh(self) -> None:
        await self.panel.refresh_message()


class AddWarnModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView"):
        super().__init__(panel, "Add warning")
        self.reason = discord.ui.TextInput(label="Reason", placeholder="Explain what happened", style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not can_use_moderation_action(actor, "warn"):
            return await self.fail(interaction, moderation_denial("warn"))
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await self.fail(interaction, error)
        warning = _add_warning(self.panel.guild.id, self.panel.member, actor, str(self.reason))
        _append_history(self.panel.guild.id, self.panel.member.id, "Warning added", actor, str(self.reason), f"Warning ID {warning['id']}")
        await _send_log(self.panel.guild, actor, "mod warn", self.panel.member, f"**Warning:** `{warning['id']}`\n**Reason:** {self.reason}", interaction.channel)
        await interaction.response.send_message(f"⚠️ Warning `{warning['id']}` added to {self.panel.member.mention}.", ephemeral=True)
        try:
            await self.panel.member.send(f"You received a warning in **{self.panel.guild.name}**.\n**Reason:** {self.reason}\n**Warning ID:** `{warning['id']}`")
        except (discord.Forbidden, discord.HTTPException):
            pass
        await self.refresh()


class RemoveWarnModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView"):
        super().__init__(panel, "Remove warning")
        self.warning_id = discord.ui.TextInput(label="Warning ID", placeholder="Enter an ID, or type LATEST", max_length=16, default="LATEST")
        self.note = discord.ui.TextInput(label="Removal note", placeholder="Optional note", required=False, max_length=300)
        self.add_item(self.warning_id)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not can_use_moderation_action(actor, "warn"):
            return await self.fail(interaction, moderation_denial("warn"))
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await self.fail(interaction, error)
        removed = _remove_warning(self.panel.guild.id, self.panel.member.id, str(self.warning_id), actor)
        if not removed:
            return await self.fail(interaction, "No active warning matched that ID.")
        _append_history(self.panel.guild.id, self.panel.member.id, "Warning removed", actor, str(self.note), f"Warning ID {removed['id']}")
        await interaction.response.send_message(f"✅ Warning `{removed['id']}` removed.", ephemeral=True)
        await self.refresh()


class TimeoutModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView"):
        super().__init__(panel, "Timeout member")
        self.duration = discord.ui.TextInput(label="Duration", placeholder="Examples: 10m, 2h, 1d", default="10m", max_length=8)
        self.reason = discord.ui.TextInput(label="Reason", placeholder="Reason for the timeout", style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.duration)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not can_use_moderation_action(actor, "timeout"):
            return await self.fail(interaction, moderation_denial("timeout"))
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await self.fail(interaction, error)
        duration = _parse_duration(str(self.duration))
        if not duration:
            return await self.fail(interaction, "Use a duration from 10 seconds to 28 days, such as `10m`, `2h` or `1d`.")
        try:
            await self.panel.member.timeout(duration, reason=f"{actor}: {self.reason}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await self.fail(interaction, f"Discord rejected the timeout: {exc}")
        _append_history(self.panel.guild.id, self.panel.member.id, "Timeout applied", actor, str(self.reason), str(self.duration))
        await _send_log(self.panel.guild, actor, "mod timeout", self.panel.member, f"**Duration:** {self.duration}\n**Reason:** {self.reason}", interaction.channel)
        await interaction.response.send_message(f"⏳ {self.panel.member.mention} was timed out for `{self.duration}`.", ephemeral=True)
        await self.refresh()


class ReasonActionModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView", action: str):
        super().__init__(panel, f"{action.title()} member")
        self.action = action
        self.reason = discord.ui.TextInput(label="Reason", placeholder=f"Reason for the {action}", style=discord.TextStyle.paragraph, max_length=500)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member):
            return await self.fail(interaction, "This action must be used inside a server.")
        if not can_use_moderation_action(actor, self.action):
            return await self.fail(interaction, moderation_denial(self.action))
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await self.fail(interaction, error)
        try:
            if self.action == "kick":
                await self.panel.member.kick(reason=f"{actor}: {self.reason}")
            else:
                await self.panel.member.ban(reason=f"{actor}: {self.reason}", delete_message_seconds=0)
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await self.fail(interaction, f"Discord rejected the action: {exc}")
        label = "Kicked" if self.action == "kick" else "Banned"
        _append_history(self.panel.guild.id, self.panel.member.id, label, actor, str(self.reason))
        await _send_log(self.panel.guild, actor, f"mod {self.action}", self.panel.member, f"**Reason:** {self.reason}", interaction.channel)
        await interaction.response.send_message(f"🔨 {self.panel.member} was {label.lower()}.", ephemeral=True)
        await self.panel.mark_closed(label)


class ClearMessagesModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView"):
        super().__init__(panel, "Clear member messages")
        self.amount = discord.ui.TextInput(label="Number of messages", placeholder="1–100", default="20", max_length=3)
        self.reason = discord.ui.TextInput(label="Audit note", placeholder="Optional reason", required=False, max_length=300)
        self.add_item(self.amount)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        channel = interaction.channel
        if not isinstance(actor, discord.Member) or not can_use_moderation_action(actor, "warn"):
            return await self.fail(interaction, moderation_denial("warn"))
        if not isinstance(channel, discord.TextChannel):
            return await self.fail(interaction, "This action only works in a server text channel.")
        try:
            amount = int(str(self.amount))
        except ValueError:
            amount = 0
        if amount < 1 or amount > 100:
            return await self.fail(interaction, "Choose an amount from 1 to 100.")
        await interaction.response.defer(ephemeral=True)
        selected = 0

        def check(message: discord.Message) -> bool:
            nonlocal selected
            if message.author.id == self.panel.member.id and selected < amount:
                selected += 1
                return True
            return False

        try:
            deleted = await channel.purge(limit=min(500, max(50, amount * 10)), check=check, reason=f"Rallybit purge by {actor}: {self.reason}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await interaction.followup.send(f"❌ Discord rejected the purge: {exc}", ephemeral=True)
        _append_history(self.panel.guild.id, self.panel.member.id, "Messages cleared", actor, str(self.reason), f"{len(deleted)} messages in #{channel.name}")
        await _send_log(self.panel.guild, actor, "mod clear", self.panel.member, f"**Deleted:** {len(deleted)} messages\n**Channel:** {channel.mention}\n**Note:** {self.reason or 'None'}", channel)
        await interaction.followup.send(f"🧹 Removed `{len(deleted)}` recent message(s) from {self.panel.member.mention} in {channel.mention}.", ephemeral=True)


class NicknameModal(PanelModal):
    def __init__(self, panel: "ModerationPanelView"):
        super().__init__(panel, "Change nickname")
        self.nickname = discord.ui.TextInput(label="New nickname", placeholder="Leave blank to reset", required=False, max_length=32)
        self.reason = discord.ui.TextInput(label="Audit note", placeholder="Optional reason", required=False, max_length=300)
        self.add_item(self.nickname)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not (actor.guild_permissions.manage_nicknames or actor.guild_permissions.administrator):
            return await self.fail(interaction, "You need Manage Nicknames.")
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await self.fail(interaction, error)
        nickname = str(self.nickname).strip() or None
        try:
            await self.panel.member.edit(nick=nickname, reason=f"{actor}: {self.reason}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await self.fail(interaction, f"Discord rejected the nickname change: {exc}")
        _append_history(self.panel.guild.id, self.panel.member.id, "Nickname changed", actor, str(self.reason), nickname or "Reset")
        await interaction.response.send_message(f"✏️ Nickname {'changed' if nickname else 'reset'}.", ephemeral=True)
        await self.refresh()


class RolePicker(discord.ui.View):
    def __init__(self, panel: "ModerationPanelView", mode: str):
        super().__init__(timeout=90)
        self.panel = panel
        self.mode = mode
        select = discord.ui.RoleSelect(placeholder=f"Choose a role to {mode}", min_values=1, max_values=1)
        select.callback = self.role_selected
        self.select = select
        self.add_item(select)

    async def role_selected(self, interaction: discord.Interaction) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member) or not (actor.guild_permissions.manage_roles or actor.guild_permissions.administrator):
            return await interaction.response.send_message("You need Manage Roles.", ephemeral=True)
        role = self.select.values[0]
        if not isinstance(role, discord.Role):
            role = self.panel.guild.get_role(role.id)
        if role is None:
            return await interaction.response.send_message("That role no longer exists.", ephemeral=True)
        if role.is_default() or role.managed:
            return await interaction.response.send_message("That role cannot be manually changed.", ephemeral=True)
        if actor.id != self.panel.guild.owner_id and role >= actor.top_role:
            return await interaction.response.send_message("You cannot manage a role equal to or above your highest role.", ephemeral=True)
        if role >= self.panel.guild.me.top_role:
            return await interaction.response.send_message("Rallybit's role must be above the selected role.", ephemeral=True)
        error = _hierarchy_error(actor, self.panel.member, self.panel.guild.me)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        if self.mode == "add" and role in self.panel.member.roles:
            return await interaction.response.send_message("That member already has the selected role.", ephemeral=True)
        if self.mode == "remove" and role not in self.panel.member.roles:
            return await interaction.response.send_message("That member does not have the selected role.", ephemeral=True)
        try:
            if self.mode == "add":
                await self.panel.member.add_roles(role, reason=f"Rallybit role action by {actor}")
            else:
                await self.panel.member.remove_roles(role, reason=f"Rallybit role action by {actor}")
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await interaction.response.send_message(f"Discord rejected the role change: {exc}", ephemeral=True)
        label = "Role added" if self.mode == "add" else "Role removed"
        _append_history(self.panel.guild.id, self.panel.member.id, label, actor, details=f"{role.name} ({role.id})")
        await interaction.response.send_message(f"✅ {role.mention} was {'added to' if self.mode == 'add' else 'removed from'} {self.panel.member.mention}.", ephemeral=True)
        await self.panel.refresh_message()
        self.stop()


class ModActionButton(discord.ui.Button):
    def __init__(self, *, label: str, emoji: str, style: discord.ButtonStyle, action: str, row: int):
        super().__init__(label=label, emoji=emoji, style=style, row=row)
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, ModerationPanelView):
            await view.handle_action(interaction, self.action)


class ModerationPanelView(discord.ui.View):
    def __init__(self, member: discord.Member, owner: discord.Member):
        super().__init__(timeout=600)
        self.member = member
        self.guild = member.guild
        self.owner_id = owner.id
        self.message: discord.Message | None = None
        controls = [
            ("Add Warn", "⚠️", discord.ButtonStyle.primary, "warn_add", 0),
            ("Remove Warn", "🗑️", discord.ButtonStyle.secondary, "warn_remove", 0),
            ("View Warns", "📋", discord.ButtonStyle.secondary, "warn_view", 0),
            ("History", "🕘", discord.ButtonStyle.secondary, "history", 0),
            ("Timeout", "⏳", discord.ButtonStyle.secondary, "timeout", 1),
            ("End Timeout", "🔊", discord.ButtonStyle.success, "untimeout", 1),
            ("Kick", "🥾", discord.ButtonStyle.danger, "kick", 1),
            ("Ban", "🔨", discord.ButtonStyle.danger, "ban", 1),
            ("Clear Messages", "🧹", discord.ButtonStyle.secondary, "clear", 2),
            ("Nickname", "✏️", discord.ButtonStyle.secondary, "nickname", 2),
            ("Add Role", "➕", discord.ButtonStyle.secondary, "role_add", 2),
            ("Remove Role", "➖", discord.ButtonStyle.secondary, "role_remove", 2),
            ("Reports", "🚩", discord.ButtonStyle.secondary, "reports", 3),
            ("Refresh", "🔄", discord.ButtonStyle.secondary, "refresh", 3),
            ("Close", "✖️", discord.ButtonStyle.secondary, "close", 3),
        ]
        for label, emoji, style, action, row in controls:
            permission_action = CONTROL_ACTIONS.get(action)
            if permission_action is None or can_use_moderation_action(owner, permission_action):
                self.add_item(ModActionButton(label=label, emoji=emoji, style=style, action=action, row=row))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("Only the moderator who opened this panel can use its controls.", ephemeral=True)
        return False

    async def refresh_message(self) -> None:
        if self.message:
            try:
                fresh = self.guild.get_member(self.member.id)
                if fresh:
                    self.member = fresh
                await self.message.edit(embed=build_panel_embed(self.member), view=self)
            except (discord.NotFound, discord.HTTPException):
                pass

    async def mark_closed(self, reason: str = "Closed") -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            embed = build_panel_embed(self.member)
            embed.colour = DANGER
            embed.add_field(name="Panel closed", value=reason, inline=False)
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass
        self.stop()

    async def handle_action(self, interaction: discord.Interaction, action: str) -> None:
        actor = interaction.user
        if not isinstance(actor, discord.Member):
            return await interaction.response.send_message("This panel only works inside a server.", ephemeral=True)
        permission_action = CONTROL_ACTIONS.get(action)
        if permission_action and not can_use_moderation_action(actor, permission_action):
            return await interaction.response.send_message(moderation_denial(permission_action), ephemeral=True)
        if action == "warn_add":
            return await interaction.response.send_modal(AddWarnModal(self))
        if action == "warn_remove":
            return await interaction.response.send_modal(RemoveWarnModal(self))
        if action == "warn_view":
            return await send_warnings(interaction, self.member)
        if action == "history":
            return await send_history(interaction, self.member)
        if action == "reports":
            from commands.reports import _reports
            guild_reports = _reports().get(str(self.guild.id), {})
            rows = [(report_id, record) for report_id, record in guild_reports.items() if isinstance(record, dict) and str(record.get("target_id")) == str(self.member.id)]
            rows.sort(key=lambda item: str(item[1].get("created_at", "")), reverse=True)
            embed = discord.Embed(title=f"Reports • {self.member}", colour=PANEL_COLOUR)
            embed.description = "\n".join(
                f"`{report_id}` • **{record.get('status', 'Pending')}** • {str(record.get('reason', ''))[:100]}"
                for report_id, record in rows[:15]
            ) or "No reports have been submitted against this member."
            return await interaction.response.send_message(embed=embed, ephemeral=True)
        if action == "timeout":
            return await interaction.response.send_modal(TimeoutModal(self))
        if action == "untimeout":
            error = _hierarchy_error(actor, self.member, self.guild.me)
            if error:
                return await interaction.response.send_message(error, ephemeral=True)
            try:
                await self.member.timeout(None, reason=f"Timeout removed by {actor}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                return await interaction.response.send_message(f"Discord rejected the action: {exc}", ephemeral=True)
            _append_history(self.guild.id, self.member.id, "Timeout removed", actor)
            await interaction.response.send_message("🔊 Timeout removed.", ephemeral=True)
            return await self.refresh_message()
        if action in {"kick", "ban"}:
            return await interaction.response.send_modal(ReasonActionModal(self, action))
        if action == "clear":
            return await interaction.response.send_modal(ClearMessagesModal(self))
        if action == "nickname":
            return await interaction.response.send_modal(NicknameModal(self))
        if action in {"role_add", "role_remove"}:
            mode = "add" if action == "role_add" else "remove"
            return await interaction.response.send_message(f"Choose a role to **{mode}**:", view=RolePicker(self, mode), ephemeral=True)
        if action == "refresh":
            await interaction.response.defer(ephemeral=True)
            await self.refresh_message()
            return await interaction.followup.send("🔄 Panel refreshed.", ephemeral=True)
        if action == "close":
            await interaction.response.defer(ephemeral=True)
            await self.mark_closed("Closed by moderator")
            return await interaction.followup.send("Panel closed.", ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


async def send_warnings(interaction: discord.Interaction, member: discord.Member) -> None:
    records = _member_warnings(member.guild.id, member.id)
    active = [record for record in records if record.get("active", True)]
    embed = discord.Embed(title=f"⚠️ Warnings • {member}", colour=WARNING)
    if not active:
        embed.description = "This member has no active warnings."
    else:
        lines = []
        for record in reversed(active[-10:]):
            try:
                timestamp = datetime.fromisoformat(record["created_at"])
                when = discord.utils.format_dt(timestamp, style="R")
            except (ValueError, TypeError, KeyError):
                when = "Unknown time"
            lines.append(f"**`{record.get('id', '??????')}`** • {record.get('reason', 'No reason')}\nBy <@{record.get('moderator_id')}> • {when}")
        embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"Active: {len(active)} • All-time: {len(records)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


async def send_history(interaction: discord.Interaction, member: discord.Member) -> None:
    records = _history(member.guild.id, member.id)
    embed = discord.Embed(title=f"🕘 Moderation history • {member}", colour=PANEL_COLOUR)
    if not records:
        embed.description = "No Rallybit moderation actions are recorded for this member."
    else:
        lines = []
        for record in reversed(records[-12:]):
            try:
                timestamp = datetime.fromisoformat(record["timestamp"])
                when = discord.utils.format_dt(timestamp, style="R")
            except (ValueError, TypeError, KeyError):
                when = "Unknown time"
            extra = record.get("reason") or record.get("details") or "No additional note"
            lines.append(f"**{record.get('action', 'Action')}** • {when}\n{extra} • by <@{record.get('moderator_id')}>")
        embed.description = "\n\n".join(lines)
    embed.set_footer(text="Shows actions performed through Rallybit")
    await interaction.response.send_message(embed=embed, ephemeral=True)


def setup_moderation_commands(tree: app_commands.CommandTree) -> None:
    mod = app_commands.Group(name="mod", description="Rallybit moderation panel and warning tools.")

    @mod.command(name="panel", description="Open an interactive moderation panel for a member.")
    @app_commands.guild_only()
    @app_commands.check(_staff_check)
    @app_commands.describe(member="Member to manage")
    async def panel(interaction: discord.Interaction, member: discord.Member):
        if member.bot and member.id == interaction.client.user.id:
            return await interaction.response.send_message("Rallybit cannot moderate itself.", ephemeral=True)
        view = ModerationPanelView(member, interaction.user)
        await interaction.response.send_message(embed=build_panel_embed(member), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @mod.command(name="warn", description="Add a warning to a member.")
    @app_commands.guild_only()
    @app_commands.check(_action_check("warn"))
    async def warn(interaction: discord.Interaction, member: discord.Member, reason: str):
        error = _hierarchy_error(interaction.user, member, interaction.guild.me)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        warning = _add_warning(interaction.guild.id, member, interaction.user, reason)
        _append_history(interaction.guild.id, member.id, "Warning added", interaction.user, reason, f"Warning ID {warning['id']}")
        await _send_log(interaction.guild, interaction.user, "mod warn", member, f"**Warning:** `{warning['id']}`\n**Reason:** {reason}", interaction.channel)
        await interaction.response.send_message(f"⚠️ Warning `{warning['id']}` added to {member.mention}.", ephemeral=True)

    @mod.command(name="warnings", description="View a member's Rallybit warnings.")
    @app_commands.guild_only()
    @app_commands.check(_action_check("warn"))
    async def warnings(interaction: discord.Interaction, member: discord.Member):
        await send_warnings(interaction, member)

    @mod.command(name="unwarn", description="Remove a warning by its ID, or remove the latest warning.")
    @app_commands.guild_only()
    @app_commands.check(_action_check("warn"))
    async def unwarn(interaction: discord.Interaction, member: discord.Member, warning_id: str = "LATEST"):
        error = _hierarchy_error(interaction.user, member, interaction.guild.me)
        if error:
            return await interaction.response.send_message(error, ephemeral=True)
        removed = _remove_warning(interaction.guild.id, member.id, warning_id, interaction.user)
        if not removed:
            return await interaction.response.send_message("No active warning matched that ID.", ephemeral=True)
        _append_history(interaction.guild.id, member.id, "Warning removed", interaction.user, details=f"Warning ID {removed['id']}")
        await interaction.response.send_message(f"✅ Warning `{removed['id']}` removed.", ephemeral=True)

    @mod.command(name="unban", description="Unban a Discord user by ID.")
    @app_commands.guild_only()
    @app_commands.check(_action_check("ban"))
    async def unban(interaction: discord.Interaction, user_id: str, reason: str = "Unbanned through Rallybit"):
        if not user_id.isdigit():
            return await interaction.response.send_message("Enter a valid numeric Discord user ID.", ephemeral=True)
        user = discord.Object(id=int(user_id))
        try:
            await interaction.guild.unban(user, reason=f"{interaction.user}: {reason}")
        except discord.NotFound:
            return await interaction.response.send_message("That user is not currently banned.", ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            return await interaction.response.send_message(f"Discord rejected the unban: {exc}", ephemeral=True)
        _append_history(interaction.guild.id, int(user_id), "Unbanned", interaction.user, reason)
        await interaction.response.send_message(f"🔓 User `{user_id}` was unbanned.", ephemeral=True)

    tree.add_command(mod)
