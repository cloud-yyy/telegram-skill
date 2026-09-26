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
