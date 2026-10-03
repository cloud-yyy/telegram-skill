"""Read-only guard: only allowlisted MTProto methods may leave the process."""

from telethon import utils


READ_METHODS = frozenset({
    "help.GetConfigRequest",
    "updates.GetStateRequest",
    # Telethon's background update loop fetches missed updates; read-only.
    "updates.GetDifferenceRequest",
    "updates.GetChannelDifferenceRequest",
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

# Extra methods needed only by `download`. ExportAuthorization is how Telethon
# reaches a media file stored in another data center; it is requested on the
# main connection and imported by a short-lived second connection (also guarded).
DOWNLOAD_METHODS = READ_METHODS | {
    "upload.GetFileRequest",
    "auth.ExportAuthorizationRequest",
}

# Extra methods needed only by `login` to create the session.
LOGIN_METHODS = READ_METHODS | {
    "auth.SendCodeRequest",
    "auth.ResendCodeRequest",
    "auth.ExportLoginTokenRequest",
    "auth.ImportLoginTokenRequest",
    "auth.SignInRequest",
    "auth.CheckPasswordRequest",
    "account.GetPasswordRequest",
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


def guard_sender(sender, allowed):
    send = sender.send

    def guarded_send(request, ordered=False):
        for r in request if utils.is_list_like(request) else [request]:
            name = method_name(r)
            if name not in allowed:
                raise ReadOnlyViolation(f"blocked non-read Telegram method: {name}")
        return send(request, ordered)

    sender.send = guarded_send


def install_guard(client, allowed):
    """Wrap the client's network senders so only `allowed` methods go out.

    Besides the main sender this covers the extra connections Telethon opens to
    other data centers when downloading media.
    """
    guard_sender(client._sender, allowed)
    create = client._create_exported_sender

    async def guarded_create(dc_id):
        sender = await create(dc_id)  # only sends the fixed ImportAuthorization
        guard_sender(sender, allowed)
        return sender

    client._create_exported_sender = guarded_create
