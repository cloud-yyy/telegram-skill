"""Session files, client construction and chat resolution."""

import json
import os
import re
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession

from .guard import READ_METHODS, install_guard

HOME = Path(os.environ.get("TG_SKILL_HOME") or Path.home() / ".config" / "telegram-skill")
CONFIG_FILE = HOME / "config.json"
SESSION_PATH = HOME / "session"  # Telethon appends ".session"


class CliError(Exception):
    pass


def load_api_credentials():
    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    return (os.environ.get("TG_API_ID") or cfg.get("api_id"),
            os.environ.get("TG_API_HASH") or cfg.get("api_hash"))


def make_client(allowed, api_id, api_hash, receive_updates=False):
    session_string = os.environ.get("TG_SESSION_STRING")
    session = StringSession(session_string) if session_string else str(SESSION_PATH)
    client = TelegramClient(session, int(api_id), api_hash,
                            receive_updates=receive_updates, flood_sleep_threshold=30)
    install_guard(client, allowed)
    return client


async def open_client(allowed=READ_METHODS):
    api_id, api_hash = load_api_credentials()
    if not api_id or not api_hash:
        raise CliError("not configured; run `tg.py login` first")
    client = make_client(allowed, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise CliError("session is not authorized; run `tg.py login`")
    return client


async def resolve_chat(client, ref):
    ref = ref.strip()
    if ref.lower() in ("me", "self", "saved"):
        return await client.get_me()
    if ref.startswith("@") or "t.me/" in ref:
        try:
            return await client.get_entity(ref)
        except ValueError as e:
            raise CliError(f"cannot resolve {ref!r}: {e}")
    if re.fullmatch(r"-?\d+", ref):
        try:
            return await client.get_entity(int(ref))
        except ValueError:
            pass  # not cached yet; fall through to scanning dialogs
        async for d in client.iter_dialogs():
            if d.id == int(ref):
                return d.entity
        raise CliError(f"no chat with id {ref}")

    needle = ref.casefold()
    exact, partial = [], []
    async for d in client.iter_dialogs():
        title = d.name.casefold()
        if title == needle:
            exact.append(d)
        elif needle in title:
            partial.append(d)
    matches = exact or partial
    if len(matches) == 1:
        return matches[0].entity
    if len(matches) > 1:
        options = "\n".join(f"  {d.id}\t{d.name}" for d in matches[:20])
        raise CliError(f"{ref!r} matches several chats, pass an id instead:\n{options}")
    try:  # maybe a bare username
        return await client.get_entity(ref)
    except ValueError:
        raise CliError(f"no chat matching {ref!r}; try `tg.py chats {ref}`")
