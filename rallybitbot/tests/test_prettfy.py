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
        self.original_preview_dir = prettfy.PRETTFY_PREVIEW_DIR
        prettfy.PRETTFY_HISTORY_FILE = str(Path(self.temp_dir.name) / "prettfy.json")
        prettfy.PRETTFY_PREVIEW_DIR = Path(self.temp_dir.name) / "previews"

    def tearDown(self) -> None:
        prettfy.PRETTFY_HISTORY_FILE = self.original_history
        prettfy.PRETTFY_PREVIEW_DIR = self.original_preview_dir
        self.temp_dir.cleanup()

    def test_channel_proposals_are_complete_safe_and_normalised(self) -> None:
        payload = {
            "renames": [
                {"id": "1", "new_name": "【📢】Shouts Here", "reason": "Consistent"},
                {"id": "2", "new_name": "【📢】Shouts Here", "reason": "Duplicate allowed"},
                {"id": "999", "new_name": "unknown", "reason": "Injected"},
                {"id": "3", "new_name": "@everyone\nnews", "reason": "Clean"},
                {"id": "4", "new_name": "Staff Lounge & Chat", "reason": "Voice casing"},
            ],
            "permission_notes": ["Review staff visibility manually"],
        }
        eligible = {"1": "announcements", "2": "updates", "3": "news", "4": "Staff"}
        kinds = {"1": "text", "2": "forum", "3": "forum", "4": "voice"}
        rows, notes = prettfy.validate_proposals(payload, eligible, kinds)
        self.assertEqual([row["id"] for row in rows], ["1", "2", "3", "4"])
        self.assertEqual(rows[0]["new_name"], "『📢』shouts-here")
        self.assertEqual(rows[1]["new_name"], "『📢』shouts-here")
        self.assertEqual(rows[2]["new_name"], "everyonenews")
        self.assertEqual(rows[3]["new_name"], "Staff-Lounge-and-Chat")
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
            "renames": [{"id": "1", "new_name": "general", "reason": "Already consistent"}],
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
            "renames": [{"id": "1", "new_name": "general", "reason": "Already consistent"}],
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

    def test_openrouter_retries_an_incomplete_channel_plan(self) -> None:
        incomplete = {
            "summary": "Missed one",
            "renames": [{"id": "1", "new_name": "general", "reason": "Done"}],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        complete = {
            "summary": "All included",
            "renames": [
                {"id": "1", "new_name": "general", "reason": "Done"},
                {"id": "2", "new_name": "support", "reason": "Done"},
            ],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        responses = [
            _Response({"choices": [{"message": {"content": json.dumps(incomplete)}}]}),
            _Response({"choices": [{"message": {"content": json.dumps(complete)}}]}),
        ]
        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request,
            "urlopen",
            side_effect=responses,
        ) as urlopen:
            result = prettfy.request_plan(
                item_type="channels",
                inventory=[
                    {"id": "1", "name": "general", "kind": "text"},
                    {"id": "2", "name": "support", "kind": "forum"},
                ],
                brief="Modern",
                style="Clean",
            )
        self.assertEqual(result, complete)
        self.assertEqual(urlopen.call_count, 2)

    def test_large_channel_inventory_is_batched_without_omissions(self) -> None:
        inventory = [
            {"id": str(index), "name": f"channel-{index}", "kind": "forum" if index == 51 else "text"}
            for index in range(1, 52)
        ]
        progress: list[str] = []

        def fake_urlopen(request, timeout):
            body = json.loads(request.data.decode("utf-8"))
            user_payload = json.loads(body["messages"][1]["content"])
            batch = user_payload["inventory"]
            content = {
                "summary": "Batch",
                "renames": [
                    {"id": item["id"], "new_name": item["name"], "reason": "Covered"}
                    for item in batch
                ],
                "permission_review_recommended": False,
                "permission_notes": [],
            }
            return _Response({"choices": [{"message": {"content": json.dumps(content)}}]})

        with patch.object(prettfy, "OPENROUTER_API_KEY", "private-test-key"), patch.object(
            prettfy.urllib.request,
            "urlopen",
            side_effect=fake_urlopen,
        ) as urlopen:
            result = prettfy.request_plan(
                item_type="channels",
                inventory=inventory,
                brief="Modern",
                style="Clean",
                retry_callback=progress.append,
            )
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(len(result["renames"]), 51)
        self.assertEqual({row["id"] for row in result["renames"]}, {row["id"] for row in inventory})
        self.assertTrue(any("batch 2/2" in message for message in progress))

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
        self.assertEqual(prettfy.PROGRESS_COMPLETE, "✅")
        self.assertEqual(prettfy.PROGRESS_LOADING, "🔁")
        self.assertEqual(prettfy.PROGRESS_FAILED, "❎")
        self.assertEqual(prettfy.PROGRESS_WAITING, "❎")
        self.assertEqual(prettfy.PROGRESS_SKIPPED, "❎")

    def test_forum_channels_are_merged_into_inventory(self) -> None:
        text_channel = SimpleNamespace(
            id=1,
            name="general",
            type=SimpleNamespace(name="text"),
            category=None,
        )
        forum_channel = SimpleNamespace(
            id=2,
            name="help-forum",
            type=SimpleNamespace(name="forum"),
            category=SimpleNamespace(name="Support"),
        )
        guild = SimpleNamespace(channels=[text_channel], forums=[forum_channel])
        inventory = prettfy._channel_inventory(guild, include_categories=False)
        self.assertEqual([row["id"] for row in inventory], ["1", "2"])
        self.assertEqual(inventory[1]["kind"], "forum")

    def test_channel_html_preview_lists_every_channel_and_is_deleted(self) -> None:
        inventory = [
            {"id": "1", "name": "General & News", "kind": "text", "category": "Info"},
            {"id": "2", "name": "Support Forum", "kind": "forum", "category": "Support"},
        ]
        proposals = [{
            "id": "1",
            "old_name": "General & News",
            "new_name": "『📢』general-and-news",
            "reason": "Consistent",
        }]
        with patch.object(prettfy, "DASHBOARD_URL", "https://rallybits.com/dashboard"):
            url, path = prettfy.create_channel_preview("Example <Server>", inventory, proposals)
        document = path.read_text(encoding="utf-8")
        self.assertRegex(url, r"^https://rallybits\.com/prettfy-preview\?token=[a-f0-9]{32}$")
        self.assertIn("Example &lt;Server&gt;", document)
        self.assertIn("General &amp; News", document)
        self.assertIn("Support Forum", document)
        self.assertIn("1 forum/media channels included", document)
        prettfy.delete_channel_preview(path)
        self.assertFalse(path.exists())

    def test_channel_html_preview_is_removed_after_dm_review(self) -> None:
        inventory = [{"id": "1", "name": "General Chat", "kind": "text", "category": "Community"}]
        payload = {
            "summary": "Ready",
            "renames": [{"id": "1", "new_name": "『💬』general-chat", "reason": "Consistent"}],
            "permission_review_recommended": False,
            "permission_notes": [],
        }
        user = SimpleNamespace(send=AsyncMock())
        with patch.object(prettfy, "request_plan", return_value=payload), patch.object(
            prettfy,
            "_ask",
            new=AsyncMock(return_value="APPLY"),
        ):
            result = asyncio.run(prettfy._generate_approved_plan(
                SimpleNamespace(),
                user,
                label="Channels",
                inventory=inventory,
                brief="Community",
                style="Clean",
                guild_name="Example Server",
            ))
        self.assertIsNotNone(result)
        self.assertEqual(list(prettfy.PRETTFY_PREVIEW_DIR.glob("*.html")), [])

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
