"""Turning Telethon messages into compact text or JSON records."""

import json
from datetime import datetime

from telethon.tl import types


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


def human_size(n):
    if not n:
        return ""
    for unit in ("B", "KB", "MB"):
        if n < 1024 or unit == "MB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024


def media_kind(msg):
    """Downloadable media class of a message, or None."""
    if not msg.media or isinstance(msg.media, types.MessageMediaWebPage):
        return None
    for kind in ("photo", "voice", "video_note", "gif", "video", "sticker", "audio", "document"):
        if getattr(msg, kind):
            return kind
    return None


def size_suffix(msg):
    size = human_size(msg.file.size)
    return f", {size}" if size else ""


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
        return f"[video{size_suffix(msg)}]"
    if msg.sticker:
        return f"[sticker {msg.file.emoji or ''}]".replace(" ]", "]")
    if msg.audio:
        return f"[audio: {msg.file.title or msg.file.name or 'untitled'}{size_suffix(msg)}]"
    if msg.document:
        return f"[file: {msg.file.name or 'unnamed'}{size_suffix(msg)}]"
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
        "media_kind": media_kind(msg),
        "media_size": msg.file.size if media_kind(msg) else None,
        "media_mime": msg.file.mime_type if media_kind(msg) else None,
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
