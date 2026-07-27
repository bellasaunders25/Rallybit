from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

import discord
from discord import app_commands

from core.bot import BotClient
from core.command_visibility import (
    add_private_options,
    begin_command_visibility,
    command_ephemeral,
    force_command_visibility,
    requested_private,
    reset_command_visibility,
)


class CommandVisibilityTests(unittest.TestCase):
    def test_private_option_is_read_from_nested_slash_data(self) -> None:
        public = SimpleNamespace(data={"options": [{"name": "member", "value": "1"}]})
        private = SimpleNamespace(data={
            "options": [{
                "name": "warn",
                "options": [{"name": "private", "value": True}],
            }],
        })
        self.assertFalse(requested_private(public))
        self.assertTrue(requested_private(private))

    def test_visibility_overrides_existing_ephemeral_choice_only_during_command(self) -> None:
        interaction = SimpleNamespace(data={"options": []})
        token = begin_command_visibility(interaction)
        try:
            self.assertFalse(command_ephemeral(True))
            protected_token = force_command_visibility(True)
            try:
                self.assertTrue(command_ephemeral(False))
            finally:
                reset_command_visibility(protected_token)
            self.assertFalse(command_ephemeral(True))
        finally:
            reset_command_visibility(token)
        self.assertTrue(command_ephemeral(True))

        interaction = SimpleNamespace(data={"options": [{"name": "private", "value": True}]})
        token = begin_command_visibility(interaction)
        try:
            self.assertTrue(command_ephemeral(False))
        finally:
            reset_command_visibility(token)

    def test_private_option_is_injected_and_not_forwarded_to_legacy_callback(self) -> None:
        received: list[bool] = []

        async def callback(interaction: discord.Interaction) -> None:
            received.append(command_ephemeral(False))

        command = app_commands.Command(name="example", description="Example", callback=callback)
        client = discord.Client(intents=discord.Intents.none())
        tree = app_commands.CommandTree(client)
        tree.add_command(command)
        self.assertEqual(add_private_options(tree), 1)
        self.assertEqual(command.parameters[-1].name, "private")
        asyncio.run(command._do_call(SimpleNamespace(), {"private": True}))
        self.assertEqual(received, [True])

    def test_every_registered_slash_command_has_private_option(self) -> None:
        async def inspect_commands() -> tuple[int, list[str], list[str], int]:
            client = BotClient(discord.Intents.none())
            await client.setup_hook()
            commands = [
                command
                for command in client.tree.walk_commands()
                if isinstance(command, app_commands.Command)
            ]
            missing = [
                command.qualified_name
                for command in commands
                if not any(parameter.name == "private" for parameter in command.parameters)
            ]
            invalid = []
            for command in commands:
                parameter = command.parameters[-1]
                if (
                    parameter.name != "private"
                    or parameter.type is not discord.AppCommandOptionType.boolean
                    or parameter.required
                ):
                    invalid.append(command.qualified_name)
            maximum = max(len(command.parameters) for command in commands)
            return len(commands), missing, invalid, maximum

        total, missing, invalid, maximum = asyncio.run(inspect_commands())
        self.assertEqual(total, 174)
        self.assertEqual(missing, [])
        self.assertEqual(invalid, [])
        self.assertLessEqual(maximum, 25)


if __name__ == "__main__":
    unittest.main()
