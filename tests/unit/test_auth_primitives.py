import time

import pyotp
import pytest

from app.auth import passwords, totp


def test_argon2id_hash_and_verify():
    h = passwords.hash_password("correct horse battery")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse battery")
    assert not passwords.verify_password(h, "wrong horse battery")
    assert not passwords.verify_password(None, "anything")
    assert not passwords.verify_password("not-a-hash", "anything")


@pytest.mark.parametrize("pw,ok", [
    ("short", False), ("elevenchars", False), ("twelve chars", True), ("aaaaaaaaaaaa", False),
    ("x" * 300, False),
])
def test_password_policy(pw, ok):
    assert (passwords.policy_error(pw) is None) == ok


def test_password_not_email():
    assert passwords.policy_error("me@example.com", "ME@example.com") is not None


def test_totp_secret_encryption_roundtrip():
    s = totp.new_secret()
    enc = totp.encrypt_secret(s)
    assert s not in enc
    assert totp.decrypt_secret(enc) == s


def test_totp_verify_current_and_drift():
    s = totp.new_secret()
    t = pyotp.TOTP(s)
    now = int(time.time())
    assert totp.verify_code(s, t.at(now)) is not None
    assert totp.verify_code(s, t.at(now - 30)) is not None   # one step behind
    assert totp.verify_code(s, t.at(now - 120)) is None      # too old
    assert totp.verify_code(s, "12345") is None
    assert totp.verify_code(s, "abcdef") is None


def test_totp_replay_rejected():
    s = totp.new_secret()
    code = pyotp.TOTP(s).now()
    step = totp.verify_code(s, code)
    assert step is not None
    assert totp.verify_code(s, code, last_step=step) is None


def test_totp_qr_svg():
    svg = totp.qr_svg(totp.provisioning_uri(totp.new_secret(), "a@b.c", "Issuer"))
    assert svg.startswith("<?xml") or "<svg" in svg[:200]
    assert "<script" not in svg


def test_recovery_codes_single_use():
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    hashes = totp.hash_recovery_codes(codes)
    remaining = totp.use_recovery_code(hashes, codes[3].upper())
    assert remaining is not None and len(remaining) == 9
    assert totp.use_recovery_code(remaining, codes[3]) is None  # consumed
    assert totp.use_recovery_code(remaining, "zzzzz-zzzzz") is None
    assert totp.use_recovery_code(None, codes[0]) is None
