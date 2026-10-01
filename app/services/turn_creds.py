"""coturn use-auth-secret (TURN REST API) ephemeral credentials:
username = <unix expiry>:<session id>, password = base64(HMAC-SHA1(secret, username))."""
import base64
import hashlib
import hmac
import time

from ..config import get_config


def credentials(session_id: str, ttl: int = 4 * 3600, now: int | None = None) -> tuple[str, str]:
    username = f"{int(now if now is not None else time.time()) + ttl}:{session_id}"
    mac = hmac.new(get_config().turn_shared_secret.encode(), username.encode(), hashlib.sha1).digest()
    return username, base64.b64encode(mac).decode()
