from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from commands.audit_logs import setup_audit_log_commands
from core import audit
from storage.json_store import load_json


class AuditTests(unittest.TestCase):
    def test_defaults_cover_every_category(self) -> None:
        settings = audit.default_audit_settings()
        self.assertEqual(set(settings["enabled_events"]), set(audit.AUDIT_EVENT_TYPES))
        self.assertTrue(all(settings["enabled_events"].values()))

    def test_event_is_retained_without_a_discord_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events_path = str(Path(directory) / "events.json")
            settings_path = str(Path(directory) / "settings.json")
            guild = SimpleNamespace(id=42, get_channel=lambda _channel_id: None, me=None)
            with patch.object(audit, "AUDIT_EVENTS_FILE", events_path), patch.object(audit, "AUDIT_SETTINGS_FILE", settings_path):
                delivered = asyncio.run(audit.emit_audit_event(guild, "commands", "Command used", "`/help` was used."))
            self.assertFalse(delivered)
            rows = load_json(events_path)["42"]
            self.assertEqual(rows[0]["type"], "commands")
            self.assertEqual(rows[0]["title"], "Command used")

    def test_logging_commands_are_registered(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = discord.app_commands.CommandTree(client)
        setup_audit_log_commands(tree)
        names = {command.qualified_name for command in tree.walk_commands() if isinstance(command, discord.app_commands.Command)}
        self.assertEqual(names, {"logs channel", "logs toggle", "logs overview", "logs test"})

    def test_disabled_category_is_not_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events_path = str(Path(directory) / "events.json")
            settings_path = str(Path(directory) / "settings.json")
            guild = SimpleNamespace(id=7, get_channel=lambda _channel_id: None, me=None)
            settings = audit.default_audit_settings()
            settings["enabled_events"]["messages"] = False
            with patch.object(audit, "AUDIT_EVENTS_FILE", events_path), patch.object(audit, "AUDIT_SETTINGS_FILE", settings_path):
                audit.save_audit_settings(7, settings)
                delivered = asyncio.run(audit.emit_audit_event(guild, "messages", "Deleted", "Hidden"))
            self.assertFalse(delivered)
            self.assertEqual(load_json(events_path), {})


if __name__ == "__main__":
    unittest.main()
