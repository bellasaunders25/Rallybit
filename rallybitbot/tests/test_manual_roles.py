from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

from commands import manual_roles


class _Rank:
    def __init__(self, position: int, *, role_id: int = 1, managed: bool = False, default: bool = False) -> None:
        self.position = position
        self.id = role_id
        self.managed = managed
        self._default = default
        self.name = "Temporary"

    def is_default(self) -> bool:
        return self._default

    def __ge__(self, other) -> bool:
        return self.position >= other.position

    def __lt__(self, other) -> bool:
        return self.position < other.position


class _MemberRank:
    def __init__(self, member_id: int, position: int) -> None:
        self.id = member_id
        self.position = position

    def __ge__(self, role) -> bool:
        return self.position >= role.position


class ManualRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_file = manual_roles.TEMPORARY_ROLES_FILE
        manual_roles.TEMPORARY_ROLES_FILE = str(Path(self.temp_dir.name) / "temporary_roles.json")

    def tearDown(self) -> None:
        manual_roles.TEMPORARY_ROLES_FILE = self.original_file
        self.temp_dir.cleanup()

    def test_hierarchy_checks_actor_target_and_bot(self) -> None:
        bot_member = _MemberRank(999, 20)
        bot_member.top_role = _Rank(20)
        guild = SimpleNamespace(id=123, owner_id=50, me=bot_member)
        actor = SimpleNamespace(id=10, guild=guild, top_role=_Rank(10))
        target = _MemberRank(20, 5)
        self.assertIsNone(manual_roles._actor_can_manage(actor, target, _Rank(9)))
        self.assertIn("below your highest", manual_roles._actor_can_manage(actor, target, _Rank(10)))
        self.assertIn("above yours", manual_roles._actor_can_manage(actor, _MemberRank(20, 11), _Rank(9)))
        owner = SimpleNamespace(id=50, guild=guild, top_role=_Rank(30))
        self.assertIn("Rallybit", manual_roles._actor_can_manage(owner, target, _Rank(20)))

    def test_countdown_is_compact_and_nickname_stays_within_discord_limit(self) -> None:
        now = datetime(2026, 7, 27, tzinfo=timezone.utc)
        self.assertEqual(manual_roles._countdown_label(now + timedelta(days=8), now), "8d")
        self.assertEqual(manual_roles._countdown_label(now + timedelta(minutes=61), now), "2h")
        nickname = manual_roles._countdown_nick("A very long member nickname that must be trimmed", now + timedelta(days=8), now)
        self.assertLessEqual(len(nickname), 32)
        self.assertTrue(nickname.endswith(" | 8d"))

    def test_temp_record_removal_is_scoped_to_exact_role(self) -> None:
        records = [
            {"guild_id": "1", "member_id": "2", "role_id": "3", "original_nick": "Bella"},
            {"guild_id": "1", "member_id": "2", "role_id": "4", "original_nick": "Bella"},
        ]
        kept, original, removed = manual_roles._drop_temp_record(records, 1, 2, 3)
        self.assertTrue(removed)
        self.assertEqual(original, "Bella")
        self.assertEqual([row["role_id"] for row in kept], ["4"])

    def test_role_group_registers_all_three_commands(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        manual_roles.setup_manual_role_commands(tree)
        group = tree.get_command("role")
        self.assertIsNotNone(group)
        self.assertEqual({command.name for command in group.commands}, {"give", "remove", "temp"})

    def test_expired_role_is_removed_and_original_nickname_restored(self) -> None:
        role = _Rank(5, role_id=3)
        bot_member = _MemberRank(999, 20)
        bot_member.top_role = _Rank(20)
        member = _MemberRank(2, 4)
        member.nick = "Bella | 1m"
        member.roles = [role]
        member.remove_roles = AsyncMock()
        member.edit = AsyncMock()
        guild = SimpleNamespace(id=1, me=bot_member)
        guild.get_member = lambda member_id: member if member_id == 2 else None
        guild.get_role = lambda role_id: role if role_id == 3 else None
        member.guild = guild
        bot = SimpleNamespace(get_guild=lambda guild_id: guild if guild_id == 1 else None)
        manual_roles._save_records([{
            "guild_id": "1",
            "member_id": "2",
            "role_id": "3",
            "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
            "original_nick": "Bella",
            "base_name": "Bella",
        }])
        with patch.object(manual_roles, "log_server_event"):
            result = asyncio.run(manual_roles.process_temporary_roles(bot))
        self.assertEqual(result, {"active": 0, "removed": 1})
        member.remove_roles.assert_awaited_once_with(role, reason="Rallybit temporary role expired")
        member.edit.assert_awaited_once_with(nick="Bella", reason="Rallybit temporary role countdown")
        self.assertEqual(manual_roles._records(), [])


if __name__ == "__main__":
    unittest.main()
