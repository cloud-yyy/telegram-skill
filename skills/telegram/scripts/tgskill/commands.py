"""Read commands: whoami, chats, history, search, context."""

import json
import re
from datetime import datetime, timedelta

from telethon import utils

from .client import CliError, resolve_chat
from .formatting import entity_name, message_record, print_messages


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
