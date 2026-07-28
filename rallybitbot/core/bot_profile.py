from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import urllib.request
from typing import Any
from urllib.parse import urlparse

import discord

from core.bot_settings import get_bot_settings, save_bot_settings
from core.presence import apply_presence

MAX_AVATAR_BYTES = 8 * 1024 * 1024


def validate_profile_name(value: object) -> str:
    name = str(value or "").strip()
    if not name:
        return ""
    if not 2 <= len(name) <= 32:
        raise ValueError("Bot names must contain between 2 and 32 characters.")
    return name


def validate_avatar_url(value: object) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if len(url) > 500:
        raise ValueError("The profile picture URL must contain 500 characters or fewer.")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("The profile picture must use a public HTTPS URL.")
    return url


def _validate_public_host(url: str) -> None:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    try:
        addresses = socket.getaddrinfo(hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("The profile picture host could not be resolved.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("The profile picture must be hosted on a public internet address.")


def _download_avatar(url: str) -> bytes:
    _validate_public_host(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Rallybit/8.1"})
    with urllib.request.urlopen(request, timeout=12) as response:
        final_url = validate_avatar_url(response.geturl())
        _validate_public_host(final_url)
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if not content_type.startswith("image/"):
            raise ValueError("The profile picture URL did not return an image.")
        payload = response.read(MAX_AVATAR_BYTES + 1)
    if len(payload) > MAX_AVATAR_BYTES:
        raise ValueError("The profile picture must be smaller than 8 MB.")
    if not payload:
        raise ValueError("The profile picture URL returned an empty file.")
    return payload


async def apply_bot_profile(client: discord.Client, *, include_identity: bool = True) -> dict[str, Any]:
    settings = get_bot_settings()
    global_cfg = settings.setdefault("global", {})
    result: dict[str, Any] = {"identity_updated": False, "presence_updated": False}

    if include_identity and client.user:
        desired_name = validate_profile_name(global_cfg.get("profile_name"))
        avatar_url = validate_avatar_url(global_cfg.get("profile_avatar_url"))
        edit_kwargs: dict[str, Any] = {}
        avatar_hash = ""

        if desired_name and client.user.name != desired_name:
            edit_kwargs["username"] = desired_name
        if avatar_url:
            avatar_payload = await asyncio.to_thread(_download_avatar, avatar_url)
            avatar_hash = hashlib.sha256(avatar_payload).hexdigest()
            if avatar_hash != str(global_cfg.get("profile_avatar_hash") or ""):
                edit_kwargs["avatar"] = avatar_payload

        if edit_kwargs:
            await client.user.edit(**edit_kwargs)
            result["identity_updated"] = True
            if avatar_hash:
                global_cfg["profile_avatar_hash"] = avatar_hash
                save_bot_settings(settings)

    await apply_presence(client, advance=False)
    result["presence_updated"] = True
    return result
