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
  tg.py login [--code]                     one-time sign-in (QR by default)
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
import getpass
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient, errors, utils
from telethon.sessions import StringSession
from telethon.tl import functions, types

HOME = Path(os.environ.get("TG_SKILL_HOME") or Path.home() / ".config" / "telegram-skill")
CONFIG_FILE = HOME / "config.json"
SESSION_PATH = HOME / "session"  # Telethon appends ".session"

# ---------------------------------------------------------------------------
# Read-only guard
# ---------------------------------------------------------------------------

READ_METHODS = frozenset({
    "help.GetConfigRequest",
    "updates.GetStateRequest",
    "users.GetUsersRequest",
    "users.GetFullUserRequest",
    "contacts.ResolveUsernameRequest",
    "contacts.GetContactsRequest",
    "messages.GetChatsRequest",
    "messages.GetFullChatRequest",
    "channels.GetChannelsRequest",
    "channels.GetFullChannelRequest",
    "messages.CheckChatInviteRequest",
    "messages.GetDialogsRequest",
    "messages.GetPeerDialogsRequest",
    "messages.GetHistoryRequest",
    "messages.SearchRequest",
    "messages.SearchGlobalRequest",
    "messages.GetRepliesRequest",
    "messages.GetMessagesRequest",
    "channels.GetMessagesRequest",
    "messages.GetDiscussionMessageRequest",
})

# Extra methods needed only by `login` to create the session.
LOGIN_METHODS = READ_METHODS | {
    "auth.SendCodeRequest",
    "auth.ResendCodeRequest",
    "auth.ExportLoginTokenRequest",
    "auth.ImportLoginTokenRequest",
    "auth.SignInRequest",
    "auth.CheckPasswordRequest",
    "account.GetPasswordRequest",
    "updates.GetDifferenceRequest",
    "updates.GetChannelDifferenceRequest",
}

# Transport wrappers Telethon puts around the real request.
WRAPPERS = {"InvokeWithLayerRequest", "InitConnectionRequest", "InvokeWithoutUpdatesRequest"}


class ReadOnlyViolation(RuntimeError):
    pass


def method_name(request):
    while type(request).__name__ in WRAPPERS:
        request = request.query
    ns = type(request).__module__.removeprefix("telethon.tl.functions").lstrip(".")
    return f"{ns}.{type(request).__name__}" if ns else type(request).__name__


def install_guard(client, allowed):
    """Wrap the client's single network sender so only `allowed` methods go out."""
    send = client._sender.send

    def guarded_send(request, ordered=False):
        for r in request if utils.is_list_like(request) else [request]:
            name = method_name(r)
            if name not in allowed:
                raise ReadOnlyViolation(f"blocked non-read Telegram method: {name}")
        return send(request, ordered)

    client._sender.send = guarded_send


# ---------------------------------------------------------------------------
# Client setup
# ---------------------------------------------------------------------------

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


async def open_client():
    api_id, api_hash = load_api_credentials()
    if not api_id or not api_hash:
        raise CliError("not configured; run `tg.py login` first")
    client = make_client(READ_METHODS, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise CliError("session is not authorized; run `tg.py login`")
    return client


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_when(value, end_of_day=False):
    if value is None:
        return None
    m = re.fullmatch(r"(\d+)([mhd])", value.strip())
    if m:
        unit = {"m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return datetime.now().astimezone() - timedelta(**{unit: int(m.group(1))})
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            dt += timedelta(days=1)
        return dt.astimezone()
    raise CliError(f"cannot parse date {value!r}; use YYYY-MM-DD, 'YYYY-MM-DD HH:MM', or 30m/12h/7d")


def parse_message_link(link):
    """t.me/<user>/<id>, t.me/c/<internal_id>/<id>, optionally with a topic id."""
    m = re.search(r"t\.me/(c/)?([\w\d]+)/(?:\d+/)?(\d+)", link)
    if not m:
        return None
    chat = f"-100{m.group(2)}" if m.group(1) else f"@{m.group(2)}"
    return chat, int(m.group(3))


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


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def entity_name(entity):
    if entity is None:
        return None
    if isinstance(entity, types.User):
        name = " ".join(filter(None, [entity.first_name, entity.last_name])) or "Deleted Account"
    else:
        name = getattr(entity, "title", None) or "?"
    username = getattr(entity, "username", None)
    return f"{name} (@{username})" if username else name


def sender_name(msg):
    return entity_name(msg.sender) or msg.post_author or (f"id{msg.sender_id}" if msg.sender_id else "?")


def media_tag(msg):
    if isinstance(msg, types.MessageService) or msg.action:
        return f"[service: {type(msg.action).__name__.removeprefix('MessageAction')}]"
    if not msg.media or isinstance(msg.media, types.MessageMediaWebPage):
        return None
    if msg.photo:
        return "[photo]"
    if msg.voice:
        return f"[voice {msg.file.duration or 0:.0f}s]"
    if msg.video_note:
        return "[video message]"
    if msg.gif:
        return "[gif]"
    if msg.video:
        return "[video]"
    if msg.sticker:
        return f"[sticker {msg.file.emoji or ''}]".replace(" ]", "]")
    if msg.audio:
        return f"[audio: {msg.file.title or msg.file.name or 'untitled'}]"
    if msg.document:
        return f"[file: {msg.file.name or 'unnamed'}]"
    if msg.poll:
        q = msg.poll.poll.question
        return f"[poll: {getattr(q, 'text', q)}]"
    if msg.geo:
        return "[location]"
    if msg.contact:
        return "[contact]"
    return f"[{type(msg.media).__name__.removeprefix('MessageMedia').lower()}]"


def forward_label(msg):
    fwd = msg.fwd_from
    if not fwd:
        return None
    if fwd.from_name:
        return fwd.from_name
    source = msg.forward and (msg.forward.sender or msg.forward.chat)
    return entity_name(source) or "unknown"


def message_record(msg, chat=None):
    return {
        "id": msg.id,
        "date": msg.date.astimezone().isoformat(timespec="seconds"),
        "chat_id": msg.chat_id,
        "chat": entity_name(chat) if chat is not None else None,
        "sender_id": msg.sender_id,
        "sender": sender_name(msg),
        "reply_to": msg.reply_to_msg_id,
        "forwarded_from": forward_label(msg),
        "media": media_tag(msg),
        "text": msg.message or "",
    }


def format_record(rec, marker="", with_chat=False):
    date = rec["date"][:16].replace("T", " ")
    head = f"{marker}#{rec['id']} {date}"
    if with_chat:
        head += f" [{rec['chat'] or rec['chat_id']} | {rec['chat_id']}]"
    head += f" {rec['sender']}"
    tags = []
    if rec["reply_to"]:
        tags.append(f"reply to #{rec['reply_to']}")
    if rec["forwarded_from"]:
        tags.append(f"fwd from {rec['forwarded_from']}")
    if tags:
        head += f" ({', '.join(tags)})"
    body = " ".join(filter(None, [rec["media"], rec["text"]]))
    lines = body.split("\n") if body else [""]
    out = f"{head}: {lines[0]}"
    for line in lines[1:]:
        out += f"\n    {line}"
    return out


def tz_label():
    return datetime.now().astimezone().strftime("UTC%z")


def print_messages(args, header, records, marker_id=None, with_chat=False):
    if args.json:
        print(json.dumps({"header": header, "messages": records}, ensure_ascii=False, indent=1))
        return
    print(f"# {header} (times in {tz_label()})")
    if not records:
        print("(no messages)")
    for rec in records:
        marker = ">> " if rec["id"] == marker_id else ""
        print(format_record(rec, marker, with_chat))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def describe_code_delivery(sent):
    t = sent.type
    where = {
        types.auth.SentCodeTypeApp: "in the Telegram app on your other devices (chat \"Telegram\")",
        types.auth.SentCodeTypeSms: "by SMS",
        types.auth.SentCodeTypeFirebaseSms: "by SMS",
        types.auth.SentCodeTypeCall: "by phone call",
        types.auth.SentCodeTypeFlashCall: "by flash call (the code is in the caller's number)",
        types.auth.SentCodeTypeMissedCall: "by missed call (the code is the last digits of the caller's number)",
        types.auth.SentCodeTypeFragmentSms: "via fragment.com (anonymous number)",
    }.get(type(t))
    if isinstance(t, types.auth.SentCodeTypeEmailCode):
        where = f"to email {t.email_pattern}"
    elif isinstance(t, types.auth.SentCodeTypeSetUpEmailRequired):
        where = "nowhere yet: Telegram requires a login email; set it up in the official app first"
    msg = f"Code sent {where or type(t).__name__}."
    if sent.next_type:
        msg += f" If it doesn't arrive, press Enter to resend ({type(sent.next_type).__name__.removeprefix('CodeType')})."
    return msg


async def login_with_qr(client):
    import qrcode

    qr = await client.qr_login()
    while True:
        print("\nScan with the Telegram app: Settings -> Devices -> Link Desktop Device")
        code = qrcode.QRCode(border=1)
        code.add_data(qr.url)
        code.print_ascii(invert=True)
        try:
            await qr.wait(timeout=max(5, (qr.expires - datetime.now(qr.expires.tzinfo)).total_seconds()))
            return
        except asyncio.TimeoutError:
            await qr.recreate()


async def login_with_code(client):
    phone = input("Phone number (+...): ").strip()
    sent = await client.send_code_request(phone)
    while True:
        print(describe_code_delivery(sent))
        code = input("Login code (empty = resend another way): ").strip()
        if code:
            break
        sent = await client(functions.auth.ResendCodeRequest(phone, sent.phone_code_hash))
    await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)


async def cmd_login(args):
    HOME.mkdir(parents=True, exist_ok=True)
    HOME.chmod(0o700)
    api_id, api_hash = load_api_credentials()
    if not api_id or not api_hash:
        print("Create an app at https://my.telegram.org -> API development tools.")
        api_id = input("api_id: ").strip()
        api_hash = input("api_hash: ").strip()
        CONFIG_FILE.write_text(json.dumps({"api_id": int(api_id), "api_hash": api_hash}))
        CONFIG_FILE.chmod(0o600)

    # QR login waits for an UpdateLoginToken, so this client listens for updates.
    client = make_client(LOGIN_METHODS, api_id, api_hash, receive_updates=True)
    await client.connect()
    if not await client.is_user_authorized():
        try:
            await (login_with_code(client) if args.code else login_with_qr(client))
        except errors.SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("2FA password: "))
    me = await client.get_me()
    await client.disconnect()
    session_file = SESSION_PATH.with_suffix(".session")
    if session_file.exists():
        session_file.chmod(0o600)
    print(f"Logged in as {entity_name(me)}. Session: {session_file}")


async def cmd_whoami(client, args):
    me = await client.get_me()
    if args.json:
        print(json.dumps({"id": me.id, "name": entity_name(me), "phone": me.phone}, ensure_ascii=False))
    else:
        print(f"{entity_name(me)} id={me.id}")


def dialog_kind(d):
    if d.is_user:
        return "bot" if getattr(d.entity, "bot", False) else "user"
    if d.is_channel and not d.is_group:
        return "channel"
    return "group"


async def cmd_chats(client, args):
    rows = []
    needle = args.query.casefold() if args.query else None
    async for d in client.iter_dialogs(limit=None if needle else args.limit):
        if needle and needle not in d.name.casefold():
            continue
        rows.append({"id": d.id, "type": dialog_kind(d), "title": d.name,
                     "username": getattr(d.entity, "username", None), "unread": d.unread_count})
        if len(rows) >= args.limit:
            break
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    for r in rows:
        username = f" @{r['username']}" if r["username"] else ""
        print(f"{r['id']}\t{r['type']}\t{r['title']}{username}\tunread={r['unread']}")
    if not rows:
        print("(no chats)")


async def cmd_history(client, args):
    chat = await resolve_chat(client, args.chat)
    since = parse_when(args.since)
    until = parse_when(args.until, end_of_day=True)
    kwargs = {"limit": args.limit, "reply_to": args.thread}
    if args.after_id:
        # First N messages after the given id, oldest first.
        msgs = [m async for m in client.iter_messages(chat, min_id=args.after_id, reverse=True, **kwargs)]
    else:
        # Latest N messages in the window, fetched newest first.
        msgs = []
        async for m in client.iter_messages(chat, offset_id=args.before_id or 0, offset_date=until, **kwargs):
            if since and m.date < since:
                break
            msgs.append(m)
        msgs.reverse()
    header = f"History of {entity_name(chat)} id={utils.get_peer_id(chat)}"
    if args.thread:
        header += f", thread #{args.thread}"
    print_messages(args, header, [message_record(m, chat) for m in msgs])


async def cmd_search(client, args):
    chat = await resolve_chat(client, args.chat) if args.chat else None
    if args.sender and chat is None:
        raise CliError("--from requires --chat (Telegram global search can't filter by sender)")
    sender = await resolve_chat(client, args.sender) if args.sender else None
    since = parse_when(args.since)
    until = parse_when(args.until, end_of_day=True)
    records = []
    async for m in client.iter_messages(chat, search=args.query, from_user=sender,
                                        limit=args.limit, offset_date=until):
        if since and m.date < since:
            break
        records.append(message_record(m, chat if chat is not None else m.chat))
    where = entity_name(chat) if chat is not None else "all chats"
    header = f"Search {args.query!r} in {where}, newest first"
    print_messages(args, header, records, with_chat=chat is None)


async def cmd_context(client, args):
    if args.msg_id is None:
        link = parse_message_link(args.target)
        if not link:
            raise CliError("pass CHAT MSG_ID or a t.me message link")
        chat_ref, msg_id = link
    else:
        chat_ref, msg_id = args.target, args.msg_id
    chat = await resolve_chat(client, chat_ref)

    older = [m async for m in client.iter_messages(chat, limit=args.before, offset_id=msg_id)]
    target = await client.get_messages(chat, ids=msg_id)
    newer = [m async for m in client.iter_messages(chat, limit=args.after, min_id=msg_id, reverse=True)]
    msgs = list(reversed(older)) + ([target] if target else []) + newer

    header = f"Context around #{msg_id} in {entity_name(chat)} id={utils.get_peer_id(chat)}"
    if not target:
        header += " (target message not found or deleted)"
    print_messages(args, header, [message_record(m, chat) for m in msgs], marker_id=msg_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    p = argparse.ArgumentParser(prog="tg.py", description="Read-only Telegram CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("login", parents=[common], help="interactive one-time sign-in (QR code)")
    s.add_argument("--code", action="store_true", help="sign in with a code sent to your phone instead")
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
