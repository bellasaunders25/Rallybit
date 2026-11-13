import asyncio
import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import discord

from storage.json_store import load_json, save_json
from commands import quizzes, tickets
from tasks import auto_quiz


class StorageIncidentTests(unittest.TestCase):
    def test_removed_role_loses_ticket_grants_but_keeps_other_overwrites(self):
        role = MagicMock(spec=discord.Role)
        role.id = 10
        role.is_default.return_value = False
        channel = MagicMock(spec=discord.TextChannel)
        channel.set_permissions = AsyncMock()
        channel.overwrites_for.return_value = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            manage_messages=True, manage_webhooks=False)
        guild = MagicMock()
        guild.id = 123
        guild.get_channel.return_value = channel
        guild.get_role.return_value = role
        data = {'123': {'456': {'panel_id': 'P', 'option_id': 'B', 'managed_support_role_ids': ['10']}}}
        with patch.object(tickets, '_open', return_value=data), \
             patch.object(tickets, '_ticket_support_role_ids', return_value=set()), \
             patch.object(tickets, '_save_open'):
            asyncio.run(tickets.reconcile_ticket_support_roles(guild))
        channel.set_permissions.assert_awaited_once()
        overwrite = channel.set_permissions.call_args.kwargs['overwrite']
        self.assertIsNone(overwrite.view_channel)
        self.assertIsNone(overwrite.manage_messages)
        self.assertIs(overwrite.manage_webhooks, False)
        self.assertEqual(data['123']['456']['managed_support_role_ids'], [])

    def test_disk_full_is_an_error_and_preserves_previous_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings.json'
            save_json(path, {'enabled': True})
            with patch('storage.json_store.os.fsync', side_effect=OSError(errno.ENOSPC, 'Disk full')):
                with self.assertRaisesRegex(RuntimeError, 'not saved'):
                    save_json(path, {'enabled': False})
            self.assertEqual(load_json(path), {'enabled': True})
            self.assertEqual(list(Path(folder).glob('*.tmp')), [])

    def test_quiz_never_sends_if_dispatch_reservation_cannot_persist(self):
        class Guild:
            id = 123
        class Channel:
            send = AsyncMock()
        channel = Channel()
        with patch.object(quizzes, 'active_quizzes', {}), patch.object(quizzes, '_active_quiz_store', return_value={}), \
             patch.object(quizzes, 'load_json', return_value={}), \
             patch.object(quizzes, 'save_json', side_effect=RuntimeError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'disk full'):
                asyncio.run(quizzes.start_quiz(Guild(), channel, None, automatic=True))
        channel.send.assert_not_awaited()

    def test_automatic_quiz_shared_cooldown_blocks_other_scheduler(self):
        class Guild:
            id = 123
        with patch.object(quizzes, 'active_quizzes', {}), patch.object(quizzes, '_active_quiz_store', return_value={}), \
             patch.object(quizzes, 'load_json', return_value={'123': quizzes.time.time()}):
            with self.assertRaisesRegex(RuntimeError, 'recently'):
                asyncio.run(quizzes.start_quiz(Guild(), None, None, automatic=True))

    def test_dropdown_first_role_does_not_leak_to_other_options(self):
        panel = {'support_role_ids': ['10'], 'options': [
            {'option_id': 'A', 'name': 'A', 'support_role_ids': ['10']},
            {'option_id': 'B', 'name': 'B', 'support_role_ids': []}]}
        effective, _ = tickets._effective_ticket_panel(panel, 'B')
        self.assertEqual(effective['support_role_ids'], [])
        with patch.object(tickets, '_settings', return_value={'support_role_ids': []}), \
             patch.object(tickets, '_panels', return_value={'123': {'P': panel}}):
            self.assertEqual(tickets._ticket_support_role_ids(123, {'panel_id': 'P', 'option_id': 'B'}), set())
