"""Interactive one-time sign-in: QR code or phone number + code."""

import asyncio
import getpass
import json
from datetime import datetime

from telethon import errors
from telethon.tl import functions, types

from .client import CONFIG_FILE, HOME, SESSION_PATH, load_api_credentials, make_client
from .formatting import entity_name
from .guard import LOGIN_METHODS


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


def choose_login_method():
    print("How do you want to sign in?\n"
          "  1) QR code - scan it in the Telegram app (recommended)\n"
          "  2) Phone number + login code")
    while True:
        choice = input("Choose 1 or 2 [1]: ").strip() or "1"
        if choice in ("1", "2"):
            return "qr" if choice == "1" else "code"


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
        method = args.method or choose_login_method()
        try:
            await (login_with_qr(client) if method == "qr" else login_with_code(client))
        except errors.SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("2FA password: "))
    me = await client.get_me()
    await client.disconnect()
    session_file = SESSION_PATH.with_suffix(".session")
    if session_file.exists():
        session_file.chmod(0o600)
    print(f"Logged in as {entity_name(me)}. Session: {session_file}")
