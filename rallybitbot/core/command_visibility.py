from __future__ import annotations

from contextvars import ContextVar, Token
from copy import copy
from functools import wraps
from typing import Any, Callable

import discord
from discord import app_commands
from discord.app_commands.commands import _extract_parameters_from_callback

PRIVATE_OPTION_NAME = "private"
PRIVATE_OPTION_DESCRIPTION = "Show Rallybit's command replies only to you."
_COMMAND_PRIVATE: ContextVar[bool | None] = ContextVar("rallybit_command_private", default=None)
_PATCHED = False


@app_commands.describe(private=PRIVATE_OPTION_DESCRIPTION)
async def _private_option_template(interaction: discord.Interaction, private: bool = False) -> None:
    pass


_PRIVATE_PARAMETER = _extract_parameters_from_callback(
    _private_option_template,
    _private_option_template.__globals__,
)[PRIVATE_OPTION_NAME]


def _option_value(options: list[dict[str, Any]]) -> bool:
    for option in options:
        if option.get("name") == PRIVATE_OPTION_NAME:
            return option.get("value") is True
        nested = option.get("options")
        if isinstance(nested, list) and _option_value(nested):
            return True
    return False


def requested_private(interaction: discord.Interaction) -> bool:
    data = interaction.data
    if not isinstance(data, dict):
        return False
    options = data.get("options", [])
    return _option_value(options) if isinstance(options, list) else False


def begin_command_visibility(interaction: discord.Interaction) -> Token[bool | None]:
    """Apply the slash command's visibility choice for its complete dispatch."""
    return _COMMAND_PRIVATE.set(requested_private(interaction))


def force_command_visibility(private: bool) -> Token[bool | None]:
    """Temporarily override visibility for a protected system response."""
    return _COMMAND_PRIVATE.set(private)


def reset_command_visibility(token: Token[bool | None]) -> None:
    _COMMAND_PRIVATE.reset(token)


def command_ephemeral(original: bool) -> bool:
    """Resolve an existing response choice against the active slash-command option."""
    private = _COMMAND_PRIVATE.get()
    return original if private is None else private


def _wrap_callback(callback: Callable[..., Any], forwards_private: bool) -> Callable[..., Any]:
    @wraps(callback)
    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        private = bool(kwargs.get(PRIVATE_OPTION_NAME, False))
        if not forwards_private:
            kwargs.pop(PRIVATE_OPTION_NAME, None)
        token = _COMMAND_PRIVATE.set(private)
        try:
            return await callback(*args, **kwargs)
        finally:
            _COMMAND_PRIVATE.reset(token)

    wrapped.__rallybit_visibility_wrapped__ = True
    return wrapped


def add_private_options(tree: app_commands.CommandTree) -> int:
    """Add the shared private switch to every registered slash-command leaf."""
    changed = 0
    for command in tree.walk_commands():
        if not isinstance(command, app_commands.Command):
            continue
        if getattr(command._callback, "__rallybit_visibility_wrapped__", False):
            continue
        forwards_private = PRIVATE_OPTION_NAME in command._params
        if not forwards_private:
            if len(command._params) >= 25:
                raise RuntimeError(f"/{command.qualified_name} has no room for Rallybit's private option")
            command._params[PRIVATE_OPTION_NAME] = copy(_PRIVATE_PARAMETER)
        command._callback = _wrap_callback(command._callback, forwards_private)
        changed += 1
    return changed


def install_response_visibility() -> None:
    """Make command responses public by default and private only when requested."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    original_send_message = discord.InteractionResponse.send_message
    original_defer = discord.InteractionResponse.defer
    original_webhook_send = discord.Webhook.send

    @wraps(original_send_message)
    async def send_message(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        if _COMMAND_PRIVATE.get() is not None:
            kwargs["ephemeral"] = command_ephemeral(bool(kwargs.get("ephemeral", False)))
        return await original_send_message(self, *args, **kwargs)

    @wraps(original_defer)
    async def defer(self: discord.InteractionResponse, *args: Any, **kwargs: Any) -> Any:
        if _COMMAND_PRIVATE.get() is not None:
            kwargs["ephemeral"] = command_ephemeral(bool(kwargs.get("ephemeral", False)))
        return await original_defer(self, *args, **kwargs)

    @wraps(original_webhook_send)
    async def webhook_send(self: discord.Webhook, *args: Any, **kwargs: Any) -> Any:
        if _COMMAND_PRIVATE.get() is not None and self.type is discord.WebhookType.application:
            kwargs["ephemeral"] = command_ephemeral(bool(kwargs.get("ephemeral", False)))
        return await original_webhook_send(self, *args, **kwargs)

    discord.InteractionResponse.send_message = send_message
    discord.InteractionResponse.defer = defer
    discord.Webhook.send = webhook_send
