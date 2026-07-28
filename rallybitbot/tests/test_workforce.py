from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discord

from commands.workforce import (
    _active_seconds,
    _format_duration,
    default_workforce_settings,
    is_hr_member,
    is_staff_member,
    setup_workforce_commands,
)


class WorkforceTests(unittest.TestCase):
    def test_active_time_excludes_completed_and_current_breaks(self) -> None:
        end = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        active = {
            "started_at": "2026-07-28T10:00:00+00:00",
            "break_seconds": 900,
            "break_started_at": "2026-07-28T11:45:00+00:00",
        }
        self.assertEqual(_active_seconds(active, end), 5400)
        self.assertEqual(_format_duration(5400), "1h 30m")

    def test_configured_roles_control_staff_and_hr_access(self) -> None:
        staff_role = SimpleNamespace(id=10)
        hr_role = SimpleNamespace(id=20)
        permissions = SimpleNamespace(administrator=False, manage_guild=False, manage_messages=False)
        guild = SimpleNamespace(id=1)
        staff = SimpleNamespace(guild=guild, roles=[staff_role], guild_permissions=permissions)
        hr = SimpleNamespace(guild=guild, roles=[hr_role], guild_permissions=permissions)
        with patch("commands.workforce.get_workforce_settings", return_value={**default_workforce_settings(), "staff_role_ids": ["10"], "hr_role_ids": ["20"]}):
            self.assertTrue(is_staff_member(staff))
            self.assertFalse(is_hr_member(staff))
            self.assertTrue(is_hr_member(hr))
            self.assertTrue(is_staff_member(hr))

    def test_every_requested_command_is_registered(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = discord.app_commands.CommandTree(client)
        setup_workforce_commands(tree)
        names = {command.qualified_name for command in tree.walk_commands() if isinstance(command, discord.app_commands.Command)}
        expected = {
            "loa request", "loa status", "loa cancel", "loa setstatus", "loa end", "loa settings",
            "roa request", "roa status", "roa cancel", "roa setstatus", "roa end", "roa settings",
            "clockin", "clockout", "break start", "break end", "shift", "timesheet", "staffhours", "duty", "forceclockout",
        }
        self.assertEqual(expected, names)


if __name__ == "__main__":
    unittest.main()
