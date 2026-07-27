from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import discord
from discord import app_commands

from commands import tickets


class _Guild:
    id = 123456789012345678
    name = "Rallybit Test Server"
    icon = None
    banner = None
    splash = None

    @staticmethod
    def get_channel(_channel_id: int):
        return None


class TicketPanelTests(unittest.TestCase):
    def test_legacy_button_panel_becomes_one_dropdown_option(self) -> None:
        panel = {
            "name": "Billing",
            "category_id": "111",
            "button_emoji": "💳",
            "support_role_ids": ["222"],
            "ticket_name": "billing-{username}",
        }
        options = tickets._panel_options(panel)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["option_id"], "DEFAULT")
        self.assertEqual(options[0]["name"], "Billing")
        self.assertEqual(options[0]["category_id"], "111")
        self.assertEqual(options[0]["support_role_ids"], ["222"])

    def test_dropdown_uses_custom_names_descriptions_and_icons(self) -> None:
        panel = {
            "select_placeholder": "What do you need?",
            "options": [
                {"option_id": "A", "name": "General Support", "description": "Questions and assistance", "emoji": "🎫"},
                {"option_id": "B", "name": "Management", "description": "Partnerships and purchases", "emoji": "<:staff:123456789012345678>"},
            ],
        }
        view = tickets.TicketPanelView(_Guild.id, "ABC12345", panel)
        select = view.children[0]
        self.assertIsInstance(select, discord.ui.Select)
        self.assertEqual(select.placeholder, "What do you need?")
        self.assertEqual([option.label for option in select.options], ["General Support", "Management"])
        self.assertEqual([option.description for option in select.options], ["Questions and assistance", "Partnerships and purchases"])
        self.assertEqual(select.custom_id, f"rallybit:ticket:select:{_Guild.id}:ABC12345")

    def test_rich_panel_builds_header_and_configured_media(self) -> None:
        panel = {
            "title": "Assistance",
            "description": "Choose the right team.",
            "color": "#287CFF",
            "header_image_url": "https://example.com/header.png",
            "thumbnail_url": "https://example.com/thumb.png",
            "image_url": "https://example.com/body.png",
            "footer_text": "Rallybit Support",
            "footer_icon_url": "https://example.com/footer.png",
            "show_workload": False,
            "show_guidance": False,
            "show_timestamp": False,
            "options": [{"option_id": "A", "name": "Support", "description": "General questions", "emoji": "🎫"}],
        }
        with patch.object(tickets, "_panel_workload", return_value=0):
            embeds = tickets._ticket_panel_embeds(_Guild(), "ABC12345", panel)
        self.assertEqual(len(embeds), 2)
        self.assertEqual(str(embeds[0].image.url), "https://example.com/header.png")
        self.assertEqual(embeds[1].title, "Assistance")
        self.assertEqual(str(embeds[1].thumbnail.url), "https://example.com/thumb.png")
        self.assertEqual(str(embeds[1].image.url), "https://example.com/body.png")
        self.assertEqual(embeds[1].footer.text, "Rallybit Support")
        self.assertEqual(embeds[1].color.value, 0x287CFF)

    def test_selected_option_overrides_ticket_routing(self) -> None:
        panel = {
            "category_id": "100",
            "support_role_ids": ["200"],
            "options": [
                {
                    "option_id": "STAFF",
                    "name": "Staff Report",
                    "description": "Report a team member",
                    "category_id": "300",
                    "support_role_ids": ["400"],
                    "ticket_name": "staff-{username}",
                }
            ],
        }
        effective, selected = tickets._effective_ticket_panel(panel, "staff")
        self.assertEqual(selected["option_id"], "STAFF")
        self.assertEqual(effective["category_id"], "300")
        self.assertEqual(effective["support_role_ids"], ["200", "400"])
        self.assertEqual(effective["ticket_name"], "staff-{username}")

    def test_option_limit_and_invalid_text_icon_are_safe(self) -> None:
        panel = {
            "options": [
                {"option_id": str(index), "name": f"Option {index}", "description": "Help", "emoji": "not-an-emoji"}
                for index in range(30)
            ]
        }
        self.assertEqual(len(tickets._panel_options(panel)), 25)
        self.assertIsNone(tickets._select_option_emoji("not-an-emoji"))
        self.assertIsNone(tickets._select_option_emoji("https://example.com/icon.png"))
        self.assertIsNotNone(tickets._select_option_emoji("🛟"))

    def test_embed_character_budget_and_https_media_validation(self) -> None:
        panel = {
            "title": "T" * 256,
            "description": "D" * 1800,
            "footer_text": "F" * 1800,
            "options": [
                {
                    "option_id": str(index),
                    "name": "N" * 100,
                    "description": "V" * 100,
                    "emoji": "🎫",
                }
                for index in range(25)
            ],
        }
        embed = tickets._ticket_panel_embeds(_Guild(), "ABC12345", panel)[-1]
        total = len(embed.title or "") + len(embed.description or "")
        total += len(embed.author.name or "") + len(embed.footer.text or "")
        total += sum(len(field.name) + len(field.value) for field in embed.fields)
        self.assertLessEqual(total, 6000)
        with self.assertRaisesRegex(RuntimeError, "public HTTPS URL"):
            asyncio.run(
                tickets.create_ticket_panel(
                    _Guild(), None, None, "Support", header_image_url="http://example.com/banner.png"
                )
            )

    def test_ticket_panel_commands_register(self) -> None:
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        tickets.setup_ticket_commands(tree)
        ticket_group = next(command for command in tree.get_commands() if command.name == "ticket")
        panel_group = next(command for command in ticket_group.commands if command.name == "panel")
        self.assertEqual(
            {command.name for command in panel_group.commands},
            {"create", "add-option", "remove-option", "list", "delete"},
        )


if __name__ == "__main__":
    unittest.main()
