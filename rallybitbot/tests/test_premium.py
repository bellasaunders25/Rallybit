from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import discord
from discord import app_commands

import commands.premium as premium_commands
import core.premium as premium
import core.service_notice as service_notice
from core.bot_profile import validate_avatar_url, validate_profile_name
from core.presence import discord_presence_status, normalise_presence_status
from storage.json_store import save_json


class PremiumEntitlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.originals = {
            "entitlements": premium.PREMIUM_ENTITLEMENTS_FILE,
            "admins": premium.ADMINS_FILE,
            "owner": premium.OWNER_ID,
        }
        premium.PREMIUM_ENTITLEMENTS_FILE = str(root / "premium.json")
        premium.ADMINS_FILE = str(root / "admins.json")
        premium.OWNER_ID = 999

    def tearDown(self) -> None:
        premium.PREMIUM_ENTITLEMENTS_FILE = self.originals["entitlements"]
        premium.ADMINS_FILE = self.originals["admins"]
        premium.OWNER_ID = self.originals["owner"]
        self.temp_dir.cleanup()

    def test_server_and_network_ownership_rules(self) -> None:
        premium.grant_entitlement(
            subject_type="server",
            subject_id="123456789012345678",
            plan="community",
            granted_by="999",
        )
        server = premium.resolve_entitlement(
            user_id=5,
            guild_id=123456789012345678,
            guild_owner_id=6,
        )
        self.assertEqual(server["plan"], "community")

        premium.grant_entitlement(
            subject_type="user",
            subject_id="111111111111111111",
            plan="network",
            expires_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            granted_by="999",
        )
        owned = premium.resolve_entitlement(
            user_id=7,
            guild_id=222222222222222222,
            guild_owner_id=111111111111111111,
        )
        not_owned = premium.resolve_entitlement(
            user_id=111111111111111111,
            guild_id=222222222222222222,
            guild_owner_id=333333333333333333,
        )
        self.assertEqual(owned["plan"], "network")
        self.assertEqual(not_owned["plan"], "free")

    def test_invalid_user_plan_expiry_history_and_revoke(self) -> None:
        with self.assertRaises(ValueError):
            premium.grant_entitlement(
                subject_type="user",
                subject_id="111111111111111111",
                plan="pro",
                granted_by="999",
            )
        with self.assertRaises(ValueError):
            premium.grant_entitlement(
                subject_type="server",
                subject_id="222222222222222222",
                plan="pro",
                expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
                granted_by="999",
            )

        premium.grant_entitlement(
            subject_type="server",
            subject_id="222222222222222222",
            plan="pro",
            granted_by="999",
        )
        self.assertTrue(premium.revoke_entitlement(
            subject_type="server",
            subject_id="222222222222222222",
            revoked_by="999",
        ))
        history = premium.load_entitlements()["history"]
        self.assertEqual([entry["action"] for entry in history], ["granted", "revoked"])


class PremiumInsightTests(unittest.TestCase):
    def test_aggregation(self) -> None:
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            originals = {
                name: getattr(premium_commands, name)
                for name in (
                    "ACTIVITY_AUDIT_FILE",
                    "TICKET_HISTORY_FILE",
                    "MOD_HISTORY_FILE",
                    "OPEN_TICKETS_FILE",
                    "STAFF_SHIFTS_FILE",
                )
            }
            try:
                for name, filename in (
                    ("ACTIVITY_AUDIT_FILE", "activity.json"),
                    ("TICKET_HISTORY_FILE", "tickets.json"),
                    ("MOD_HISTORY_FILE", "moderation.json"),
                    ("OPEN_TICKETS_FILE", "open.json"),
                    ("STAFF_SHIFTS_FILE", "shifts.json"),
                ):
                    setattr(premium_commands, name, str(root / filename))
                guild_id = "123456789012345678"
                save_json(premium_commands.ACTIVITY_AUDIT_FILE, {guild_id: {"A": {"start_time": now.isoformat(), "participants": [{}, {}]}}})
                save_json(premium_commands.TICKET_HISTORY_FILE, {guild_id: [{"channel_id": "1", "closed_at": now.isoformat(), "deleted_at": now.isoformat()}]})
                save_json(premium_commands.MOD_HISTORY_FILE, {guild_id: {"7": [{"timestamp": now.isoformat(), "action": "warn"}]}})
                save_json(premium_commands.OPEN_TICKETS_FILE, {guild_id: {"2": {"status": "Open"}, "3": {"status": "Closed"}}})
                save_json(premium_commands.STAFF_SHIFTS_FILE, {guild_id: {"8": {"display_name": "Staff", "active": None, "history": [{"ended_at": now.isoformat(), "seconds": 5400}]}}})

                result = premium_commands.collect_insights(int(guild_id), 30)
                self.assertEqual(result["activity_checks"], 1)
                self.assertEqual(result["participants"], 2)
                self.assertEqual(result["tickets_closed"], 1)
                self.assertEqual(result["tickets_deleted"], 1)
                self.assertEqual(result["tickets_open"], 1)
                self.assertEqual(result["moderation_actions"], 1)
                self.assertEqual(result["staff_seconds"], 5400)
            finally:
                for name, value in originals.items():
                    setattr(premium_commands, name, value)


class PremiumApiTests(unittest.TestCase):
    def test_rejected_profile_update_restores_saved_settings(self) -> None:
        import core.api as api

        baseline = {
            "global": {
                "profile_name": "Rallybit",
                "profile_avatar_url": "https://example.com/old.png",
                "presence_status": "online",
            }
        }
        with (
            patch.object(api, "API_SECRET", "test-secret"),
            patch.object(api, "discord_bot", object()),
            patch("core.api.get_bot_settings", return_value={"global": dict(baseline["global"])}),
            patch("core.api.save_bot_settings") as save_settings,
            patch("core.api.apply_bot_profile", new=lambda *_args, **_kwargs: "profile-operation"),
            patch("core.api._run_bot_coro", side_effect=[RuntimeError("rejected"), {}]),
        ):
            response = api.app.test_client().post(
                "/api/bot/profile",
                headers={"X-Api-Key": "test-secret"},
                json={"name": "Preview", "avatar_url": "https://example.com/new.png", "status": "idle"},
            )
        self.assertEqual(response.status_code, 502)
        self.assertEqual(save_settings.call_args_list[-1].args[0], baseline)


class PresentationAndRegistrationTests(unittest.TestCase):
    def test_global_notice_gate(self) -> None:
        import core.bot as bot

        with patch("core.bot.get_service_notice", return_value={"active": True}):
            self.assertFalse(asyncio.run(bot.RallybitCommandTree.interaction_check(None, None)))
        with patch("core.bot.get_service_notice", return_value={"active": False}):
            self.assertTrue(asyncio.run(bot.RallybitCommandTree.interaction_check(None, None)))

    def test_notice_fallback_profile_and_presence_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            original = service_notice.NOTICE_FILE
            try:
                service_notice.NOTICE_FILE = str(Path(directory) / "notice.json")
                save_json(service_notice.NOTICE_FILE, {"active": True, "title": "", "message": ""})
                notice = service_notice.get_service_notice()
                self.assertTrue(notice["active"])
                self.assertTrue(notice["title"])
                self.assertTrue(notice["message"])
            finally:
                service_notice.NOTICE_FILE = original

        self.assertEqual(validate_profile_name("Rallybit"), "Rallybit")
        with self.assertRaises(ValueError):
            validate_profile_name("x")
        self.assertEqual(validate_avatar_url("https://example.com/avatar.png"), "https://example.com/avatar.png")
        with self.assertRaises(ValueError):
            validate_avatar_url("http://example.com/avatar.png")
        self.assertEqual(normalise_presence_status("DND"), "dnd")
        self.assertIs(discord_presence_status("offline"), discord.Status.invisible)

    def test_premium_command_registration(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        premium_commands.setup_premium_commands(tree)
        self.assertEqual({command.name for command in tree.get_commands()}, {"premium", "insights", "staff"})
        self.assertEqual({command.name for command in tree.get_command("premium").commands}, {"plans", "status"})
        self.assertEqual({command.name for command in tree.get_command("insights").commands}, {"overview", "export"})
        self.assertEqual({command.name for command in tree.get_command("staff").commands}, {"clockin", "clockout", "status", "leaderboard"})


if __name__ == "__main__":
    unittest.main()
