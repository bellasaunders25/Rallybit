from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

import commands.prettfy as prettfy
import core.plan_branding as plan_branding
from storage.json_store import load_json


class _Response:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class PrettfyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_history = prettfy.PRETTFY_HISTORY_FILE
        prettfy.PRETTFY_HISTORY_FILE = str(Path(self.temp_dir.name) / "prettfy.json")

    def tearDown(self) -> None:
        prettfy.PRETTFY_HISTORY_FILE = self.original_history
        self.temp_dir.cleanup()

    def test_proposals_are_limited_to_known_ids_and_safe_unique_names(self) -> None:
        payload = {
            "renames": [
                {"id": "1", "new_name": "『📢』shouts", "reason": "Consistent"},
                {"id": "2", "new_name": "『📢』shouts", "reason": "Duplicate"},
                {"id": "999", "new_name": "unknown", "reason": "Injected"},
                {"id": "3", "new_name": "@everyone\nnews", "reason": "Clean"},
            ],
            "permission_notes": ["Review staff visibility manually"],
        }
        rows, notes = prettfy.validate_proposals(payload, {"1": "announcements", "2": "updates", "3": "news"})
        self.assertEqual([row["id"] for row in rows], ["1", "3"])
        self.assertEqual(rows[1]["new_name"], "everyonenews")
        self.assertEqual(notes, ["Review staff visibility manually"])

    def test_history_records_approved_names_and_marks_undo(self) -> None:
        record_id = prettfy.create_history(123, 456)
        self.assertTrue(prettfy.add_history_item(123, record_id, "channels", 7, "announcements"))
        active = prettfy.latest_active_history(123)
        self.assertEqual(active["channels"], [{"id": "7", "name": "announcements"}])
        self.assertTrue(prettfy.mark_history_undone(123, record_id, 456))
        self.assertIsNone(prettfy.latest_active_history(123))
        stored = load_json(prettfy.PRETTFY_HISTORY_FILE)
        self.assertEqual(stored["123"][0]["status"], "undone")

    def test_openrouter_request_uses_structured_output_without_exposing_key(self) -> None:
        content = {
            "summary": "Clean plan",
            "renames": [],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        captured = {}

        def fake_urlopen(request, timeout):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _Response({"choices": [{"message": {"content": json.dumps(content)}}]})

        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request, "urlopen", side_effect=fake_urlopen
        ):
            result = prettfy.request_plan(
                item_type="channels",
                inventory=[{"id": "1", "name": "general", "kind": "text"}],
                brief="Modern",
                style="Clean",
            )
        self.assertEqual(result, content)
        self.assertEqual(captured["body"]["response_format"]["type"], "json_schema")
        self.assertTrue(captured["body"]["provider"]["require_parameters"])
        self.assertNotIn("private-test-key", json.dumps(captured["body"]))

    def test_response_parser_accepts_fenced_and_mixed_json(self) -> None:
        content = {
            "summary": "Clean plan",
            "renames": [{"id": "1", "new_name": "『📢』news", "reason": "Consistent"}],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        fenced = {"choices": [{"message": {"content": f"```json\n{json.dumps(content)}\n```"}}]}
        mixed = {"choices": [{"message": {"content": f"Here is the plan:\n{json.dumps(content)}\nDone."}}]}
        self.assertEqual(prettfy._response_plan(fenced), content)
        self.assertEqual(prettfy._response_plan(mixed), content)

    def test_response_parser_accepts_structured_content_blocks(self) -> None:
        content = {
            "renames": [],
            "permission_notes": [],
        }
        response = {
            "choices": [{
                "message": {
                    "content": [{"type": "text", "text": {"value": json.dumps(content)}}],
                }
            }]
        }
        parsed = prettfy._response_plan(response)
        self.assertEqual(parsed["renames"], [])
        self.assertEqual(parsed["summary"], "Naming plan ready.")
        self.assertFalse(parsed["permission_review_recommended"])

    def test_openrouter_retries_one_malformed_plan(self) -> None:
        valid = {
            "summary": "Retry worked",
            "renames": [],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        responses = [
            _Response({"choices": [{"message": {"content": "not valid json"}}]}),
            _Response({"choices": [{"message": {"content": json.dumps(valid)}}]}),
        ]
        retries: list[str] = []
        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = prettfy.request_plan(
                item_type="channels",
                inventory=[{"id": "1", "name": "general", "kind": "text"}],
                brief="Modern",
                style="Clean",
                retry_callback=retries.append,
            )
        self.assertEqual(result, valid)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(len(retries), 1)

    def test_openrouter_reports_two_malformed_plans_safely(self) -> None:
        responses = [
            _Response({"choices": [{"message": {"content": "not json"}}]}),
            _Response({"choices": [{"message": {"content": "still not json"}}]}),
        ]
        retries: list[str] = []
        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            with self.assertRaisesRegex(prettfy.PrettfyError, "malformed naming plan twice") as raised:
                prettfy.request_plan(
                    item_type="channels",
                    inventory=[{"id": "1", "name": "general", "kind": "text"}],
                    brief="Modern",
                    style="Clean",
                    retry_callback=retries.append,
                )
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(len(retries), 1)
        self.assertNotIn("private-test-key", str(raised.exception))

    def test_openrouter_retries_unsupported_content_shape(self) -> None:
        valid = {
            "summary": "Recovered",
            "renames": [],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        responses = [
            _Response({"choices": [{"message": {"content": 123}}]}),
            _Response({"choices": [{"message": {"content": json.dumps(valid)}}]}),
        ]
        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = prettfy.request_plan(
                item_type="roles",
                inventory=[{"id": "1", "name": "Moderator", "kind": "role"}],
                brief="Modern",
                style="Clean",
            )
        self.assertEqual(result, valid)
        self.assertEqual(urlopen.call_count, 2)

    def test_progress_tracker_uses_requested_status_icons(self) -> None:
        message = SimpleNamespace(edit=AsyncMock())
        user = SimpleNamespace(send=AsyncMock(return_value=message))
        guild = SimpleNamespace(name="Example Server")
        tracker = prettfy.PrettfyProgress(user, guild, prettfy.DESIGN_PROGRESS_STEPS)

        async def run_tracker() -> None:
            await tracker.start()
            await tracker.running("preferences", "Waiting for your theme.")
            await tracker.complete("preferences", "Preferences saved.")
            await tracker.failed("channel_scan", "Channel scan failed.")

        asyncio.run(run_tracker())
        self.assertEqual(user.send.await_count, 1)
        rendered = message.edit.await_args.kwargs["embed"]
        checklist = rendered.fields[0].value
        self.assertIn(prettfy.PROGRESS_COMPLETE, checklist)
        self.assertIn(prettfy.PROGRESS_FAILED, checklist)
        self.assertIn("Channel scan failed.", rendered.description)

    def test_command_registers_as_direct_pro_command(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        prettfy.setup_prettfy_command(tree)
        command = tree.get_command("prettfy")
        self.assertIsNotNone(command)
        self.assertEqual(command.name, "prettfy")

    def test_channel_apply_passes_only_name_and_audit_reason(self) -> None:
        channel = SimpleNamespace(id=7, name="announcements", edit=AsyncMock())
        guild = SimpleNamespace(id=123, get_channel=lambda _item_id: channel)
        user = SimpleNamespace(id=456)
        record_id = prettfy.create_history(guild.id, user.id)
        proposals = [{"id": "7", "old_name": "announcements", "new_name": "『📢』shouts", "reason": ""}]
        with patch.object(prettfy, "_is_authorised", return_value=True):
            changed, failures = asyncio.run(prettfy._apply_channels(guild, user, proposals, record_id))
        self.assertEqual((changed, failures), (1, []))
        kwargs = channel.edit.await_args.kwargs
        self.assertEqual(kwargs["name"], "『📢』shouts")
        self.assertEqual(set(kwargs), {"name", "reason"})

    def test_plan_avatar_assets_match_each_paid_tier(self) -> None:
        for plan in ("community", "pro", "network"):
            payload, digest = plan_branding._avatar_payload(plan)
            self.assertIsInstance(payload, bytes)
            self.assertGreater(len(payload), 1000)
            self.assertEqual(len(digest), 64)
        payload, digest = plan_branding._avatar_payload("free")
        self.assertIsNone(payload)
        self.assertEqual(digest, "global")


if __name__ == "__main__":
    unittest.main()
