"""argon2id password hashing and policy."""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256
_ph = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
# Constant-time-ish dummy verify for unknown accounts.
_DUMMY = _ph.hash("not-a-real-password-for-timing")


def policy_error(password: str, email: str | None = None) -> str | None:
    if len(password) < MIN_LENGTH:
        return f"Password must be at least {MIN_LENGTH} characters."
    if len(password) > MAX_LENGTH:
        return "Password is too long."
    if email and password.lower() == email.lower():
        return "Password must not be your email address."
    if len(set(password)) < 4:
        return "Password is too repetitive."
    return None


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str | None, password: str) -> bool:
    if not stored:
        try:
            _ph.verify(_DUMMY, password)
        except VerificationError:
            pass
        return False
    try:
        return _ph.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _ph.check_needs_rehash(stored)
    except InvalidHashError:
        return True
