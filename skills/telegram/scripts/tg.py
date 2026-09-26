#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["telethon>=1.40,<2", "qrcode>=7"]
# ///
"""Read-only Telegram CLI for AI agents.

Every MTProto request leaving this process is checked against READ_METHODS.
Anything else (sending, editing, deleting, marking as read, joining, ...)
is rejected before it reaches the network. The script also has no commands
that would try to write in the first place.

Usage:
  tg.py login [--qr | --code]              one-time sign-in (asks which way)
  tg.py whoami
  tg.py chats [QUERY] [--limit N]
  tg.py history CHAT [--limit N] [--before-id ID | --after-id ID]
                     [--since WHEN] [--until WHEN] [--thread MSG_ID]
  tg.py search QUERY [--chat CHAT] [--from WHO] [--limit N]
                     [--since WHEN] [--until WHEN]
  tg.py context (CHAT MSG_ID | LINK) [--before N] [--after N]

CHAT is a title (or part of it), @username, t.me link, numeric id, or "me".
WHEN is YYYY-MM-DD, "YYYY-MM-DD HH:MM", or relative: 30m, 12h, 7d.
Add --json to any command for machine-readable output.

Files live in $TG_SKILL_HOME (default ~/.config/telegram-skill).
Env overrides: TG_API_ID, TG_API_HASH, TG_SESSION_STRING.
"""

import argparse
import asyncio
import sys

from telethon import errors

from tgskill.client import CliError, open_client
from tgskill.commands import cmd_chats, cmd_context, cmd_history, cmd_search, cmd_whoami
from tgskill.guard import ReadOnlyViolation
from tgskill.login import cmd_login


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = argparse.ArgumentParser(prog="tg.py", description="Read-only Telegram CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", parents=[common], help="interactive one-time sign-in")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--qr", dest="method", action="store_const", const="qr", help="scan a QR code in the app")
    g.add_argument("--code", dest="method", action="store_const", const="code", help="phone number + login code")
    sub.add_parser("whoami", parents=[common], help="show the signed-in account")

    s = sub.add_parser("chats", parents=[common], help="list chats, optionally filtered by title")
    s.add_argument("query", nargs="?")
    s.add_argument("--limit", type=int, default=50)

    s = sub.add_parser("history", parents=[common], help="recent messages of a chat")
    s.add_argument("chat")
    s.add_argument("--limit", type=int, default=30)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--before-id", type=int, help="only messages older than this id")
    g.add_argument("--after-id", type=int, help="first N messages newer than this id")
    s.add_argument("--since", help="stop at messages older than this")
    s.add_argument("--until", help="only messages before this")
    s.add_argument("--thread", type=int, help="replies to this message (comments, forum topic)")

    s = sub.add_parser("search", parents=[common], help="search messages in one chat or globally")
    s.add_argument("query")
    s.add_argument("--chat")
    s.add_argument("--from", dest="sender", help="only messages from this user (needs --chat)")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--since")
    s.add_argument("--until")

    s = sub.add_parser("context", parents=[common], help="messages around a given message")
    s.add_argument("target", help="chat, or a t.me message link")
    s.add_argument("msg_id", type=int, nargs="?")
    s.add_argument("--before", type=int, default=10)
    s.add_argument("--after", type=int, default=10)
    return p


COMMANDS = {"whoami": cmd_whoami, "chats": cmd_chats, "history": cmd_history,
            "search": cmd_search, "context": cmd_context}


async def run(args):
    if args.command == "login":
        return await cmd_login(args)
    client = await open_client()
    try:
        await COMMANDS[args.command](client, args)
    finally:
        await client.disconnect()


def main():
    args = build_parser().parse_args()
    try:
        asyncio.run(run(args))
    except (CliError, ReadOnlyViolation) as e:
        sys.exit(f"error: {e}")
    except errors.FloodWaitError as e:
        sys.exit(f"error: Telegram rate limit, retry in {e.seconds}s")
    except errors.RPCError as e:
        sys.exit(f"error: Telegram API: {e}")


if __name__ == "__main__":
    main()
