"""Shared fixtures for unit and integration tests.

Integration tests need Postgres and Redis. By default they use the dev
containers started by `make test-deps` (ports 55432 and 56379)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://connect:connect@127.0.0.1:55432/connect_test")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:56379/15")
os.environ.setdefault("PUBLIC_URL", "https://connect.test")
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "kD0b6xS2v0u1o4n3B8Vt0QW3m2o5J2Q2yJm6V6zZ0kY=")
os.environ.setdefault("TURN_SHARED_SECRET", "test-turn-secret")
os.environ.setdefault("SETUP_TOKEN", "test-setup-token")
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("MEDIA_DIR", "/tmp/connect-test-media")
os.environ.setdefault("RUSTDESK_DIR", "/tmp/connect-test-rustdesk")
