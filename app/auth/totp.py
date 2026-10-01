"""TOTP enrolment and verification, encrypted secrets, recovery codes."""
from __future__ import annotations

import io
import secrets
import time

import pyotp
import qrcode
import qrcode.image.svg
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_config
from .passwords import hash_password, verify_password

RECOVERY_CODE_COUNT = 10
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i


def _fernet() -> Fernet:
    key = get_config().totp_encryption_key
    if not key:
        raise RuntimeError("TOTP_ENCRYPTION_KEY is not set")
    return Fernet(key.encode())


def new_secret() -> str:
    return pyotp.random_base32(length=32)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("TOTP secret cannot be decrypted (wrong TOTP_ENCRYPTION_KEY?)") from e


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def qr_svg(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def verify_code(secret: str, code: str, last_step: int | None = None) -> int | None:
    """Returns the matched time step, or None. Rejects a step at or before
    `last_step` (replay protection). Allows one step of clock drift."""
    code = "".join(ch for ch in code if ch.isdigit())
    if len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = int(time.time()) // totp.interval
    for step in (now, now - 1, now + 1):
        if last_step is not None and step <= last_step:
            continue
        if secrets.compare_digest(totp.generate_otp(step), code):
            return step
    return None


def new_recovery_codes() -> list[str]:
    def one() -> str:
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        return f"{raw[:5]}-{raw[5:]}"
    return [one() for _ in range(RECOVERY_CODE_COUNT)]


def hash_recovery_codes(codes: list[str]) -> list[str]:
    return [hash_password(normalise_recovery(c)) for c in codes]


def normalise_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch in _ALPHABET)


def use_recovery_code(hashes: list[str] | None, code: str) -> list[str] | None:
    """Returns the remaining hashes if `code` matched one (it is consumed)."""
    norm = normalise_recovery(code)
    if not hashes or len(norm) != 10:
        return None
    for i, h in enumerate(hashes):
        if verify_password(h, norm):
            return hashes[:i] + hashes[i + 1:]
    return None
