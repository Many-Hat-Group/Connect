"""Builds every VDO.Ninja URL server-side (playbook Section 7, confirmed
parameters in docs/vdo-notes.md). Everything goes in the URL fragment and is
fully percent-encoded. Only authorised parties ever receive these URLs."""
from __future__ import annotations

import secrets
from urllib.parse import quote

from ..config import get_config
from . import turn_creds

ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
ROOM_LEN = 30        # VDO.Ninja truncates room names at 30 characters
PASSWORD_LEN = 40
STREAM_LEN = 24


def rand(n: int) -> str:
    return "".join(secrets.choice(ALNUM) for _ in range(n))


def new_room() -> str:
    return rand(ROOM_LEN)


def new_password() -> str:
    return rand(PASSWORD_LEN)


def new_stream_id() -> str:
    return rand(STREAM_LEN)


def fragment(params: list[tuple[str, str | None]]) -> str:
    return "#" + "&".join(k if v is None else f"{k}={quote(str(v), safe='')}" for k, v in params)


def _common(room: str, password: str, signal_token: str, turn_host: str, session_id: str,
            relay: bool = False, own_origin_events: bool = True) -> list[tuple[str, str | None]]:
    user, cred = turn_creds.credentials(session_id)
    p: list[tuple[str, str | None]] = [
        ("room", room),
        ("password", password),
        ("wss2", f"{get_config().public_host}/signal?t={signal_token}"),
        ("stun", f"{user};{cred};turn:{turn_host}:3478"),
        ("addstun", f"{user};{cred};turn:{turn_host}:3478?transport=tcp"),
        ("turn", f"{user};{cred};turns:{turn_host}:5349"),
        ("cleanoutput", None),
    ]
    if own_origin_events:
        # Post iframe events only to our own origin. Not for the sandboxed
        # tech viewer: its origin is opaque, so the parent checks
        # event.source instead.
        p.append(("iframetarget", None))
    if relay:
        p.append(("relay", None))
    return p


def label_param(label: str) -> str:
    # VDO.Ninja turns underscores into spaces; an empty label opens a prompt.
    return (label or "Stream").replace(" ", "_")


def publisher_url(*, room, password, stream_id, signal_token, turn_host, session_id, share_type, label,
                  audio_ok: bool = True, max_framerate: int | None = None, relay=False) -> str:
    p = _common(room, password, signal_token, turn_host, session_id, relay)
    p += [("push", stream_id), ("label", label_param(label))]
    if share_type == "screen":
        # No autostart: getDisplayMedia needs a click inside the frame, so the
        # customer presses VDO.Ninja's own "Select screen to share" button.
        p += [("screenshare", None), ("audiodevice", "0")]
        if audio_ok:
            p.append(("systemaudio", "include"))
        else:
            p.append(("ssvo", None))  # second screen: video only, avoids echo
    else:
        p += [("webcam", None), ("autostart", None)]
    if max_framerate:
        p.append(("maxframerate", str(max_framerate)))
    return "/vdo/" + fragment(p)


def viewer_url(*, room, password, stream_id, signal_token, turn_host, session_id, scale: int | None = None,
               bitrate: int | None = None, width: int | None = None, audio: bool = True, start_muted=True,
               relay=False) -> str:
    p = _common(room, password, signal_token, turn_host, session_id, relay, own_origin_events=False)
    p += [("view", stream_id), ("scene", None), ("nomeshcast", None)]
    if start_muted:
        p.append(("volume", "0"))
    if scale:
        p.append(("scale", str(scale)))
    if bitrate:
        p.append(("bitrate", str(bitrate)))
    if width:
        p.append(("vw", str(width)))
    if not audio:
        p.append(("noaudio", None))
    return "/vdo/" + fragment(p)
