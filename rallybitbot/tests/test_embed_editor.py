from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from core import api


class EmbedEditorTests(unittest.TestCase):
    def test_dashboard_embed_builder_supports_complete_rich_embed(self) -> None:
        embed = api._dashboard_embed_from_params({
            "title": "Service update",
            "title_url": "https://example.com/update",
            "description": "Everything is operational.",
            "color": "#45C486",
            "author_name": "Rallybit Status",
            "author_url": "https://example.com",
            "author_icon_url": "https://example.com/author.png",
            "thumbnail_url": "https://example.com/thumb.png",
            "image_url": "https://example.com/image.png",
            "footer_text": "Last checked",
            "footer_icon_url": "https://example.com/footer.png",
            "show_timestamp": True,
            "fields": [{"name": "API", "value": "Online", "inline": True}],
        })
        self.assertEqual(embed.title, "Service update")
        self.assertEqual(embed.colour.value, 0x45C486)
        self.assertEqual(embed.fields[0].name, "API")
        self.assertTrue(embed.fields[0].inline)
        self.assertIsNotNone(embed.timestamp)
        self.assertEqual(str(embed.image.url), "https://example.com/image.png")

    def test_dashboard_embed_builder_rejects_unsafe_or_incomplete_content(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "HTTPS URL"):
            api._dashboard_embed_from_params({"title": "Unsafe", "image_url": "http://example.com/image.png"})
        with self.assertRaisesRegex(RuntimeError, "both a name and a value"):
            api._dashboard_embed_from_params({
                "title": "Fields",
                "fields": [{"name": "Missing value", "value": ""}],
            })
        with self.assertRaisesRegex(RuntimeError, "cannot be empty"):
            api._dashboard_embed_from_params({})

    def test_loaded_embed_payload_round_trips_editor_fields(self) -> None:
        embed = discord.Embed(
            title="Loaded",
            url="https://example.com",
            description="Existing message",
            colour=0x7567EE,
        )
        embed.set_author(name="Rallybit", icon_url="https://example.com/icon.png")
        embed.add_field(name="One", value="First", inline=False)
        embed.set_footer(text="Footer")
        message = SimpleNamespace(
            id=123,
            channel=SimpleNamespace(id=456, name="updates"),
            embeds=[embed],
            jump_url="https://discord.com/channels/1/456/123",
        )
        payload = api._dashboard_embed_payload(message, 0)
        self.assertEqual(payload["message_id"], "123")
        self.assertEqual(payload["channel_id"], "456")
        self.assertEqual(payload["title"], "Loaded")
        self.assertEqual(payload["color"], "#7567EE")
        self.assertEqual(payload["fields"], [{"name": "One", "value": "First", "inline": False}])

    def test_message_lookup_uses_selected_channel_or_scans_accessible_channels(self) -> None:
        wanted = SimpleNamespace(id=999)
        selected = SimpleNamespace(id=10, fetch_message=AsyncMock(return_value=wanted))
        guild = SimpleNamespace(text_channels=[], threads=[])
        found = asyncio.run(api._find_dashboard_message(guild, 999, selected))
        self.assertIs(found, wanted)
        selected.fetch_message.assert_awaited_once_with(999)


if __name__ == "__main__":
    unittest.main()
