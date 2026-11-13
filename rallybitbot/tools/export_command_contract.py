"""Export registration schemas without connecting a second Discord gateway.

Run from rallybitbot: python tools/export_command_contract.py OUTPUT.json
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.bot import BotClient
from discord import app_commands, Intents


async def export():
    client = BotClient(Intents.none())
    await client.setup_hook()
    roots = client.tree.get_commands()
    leaves = []

    def walk(command):
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                walk(child)
        else:
            leaves.append(command.qualified_name)

    for command in roots:
        walk(command)
    result = {
        'version': 1,
        'root_count': len(roots),
        'command_count': len(leaves),
        'paths': sorted(leaves),
        'registration': [command.to_dict(client.tree) for command in roots],
    }
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    # AutoShardedClient's gateway queue is not initialised without login.
    await client.http.close()


if __name__ == '__main__':
    asyncio.run(export())
