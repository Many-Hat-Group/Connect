"""Process configuration from the environment (.env). Brand defaults and
runtime-editable values live in defaults.yaml and the settings table."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_DIR = Path(__file__).resolve().parent


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    public_url: str = Field(default="https://connect.test:8443")
    database_url: str = "postgresql+asyncpg://connect:connect@postgres:5432/connect"
    redis_url: str = "redis://redis:6379/0"
    session_secret: str = Field(default="dev-insecure-session-secret-change-me")
    totp_encryption_key: str = Field(default="")  # Fernet key (urlsafe base64, 32 bytes)
    turn_shared_secret: str = "dev-insecure-turn-secret"
    turn_host: str = ""  # overrides settings.turn.host when set
    setup_token: str = ""
    media_dir: Path = Path("/data/media")
    rustdesk_dir: Path = Path("/data/rustdesk")
    backup_dir: Path = Path("/backups")
    # Cookie name must start with __Host- in production (Secure, Path=/, no Domain).
    session_cookie: str = "__Host-connect"
    customer_cookie: str = "__Host-connect-cust"
    session_absolute_seconds: int = 12 * 3600
    session_idle_seconds: int = 3600
    # Test hooks: shorten timeouts, allow http in tests.
    testing: bool = False
    github_api: str = "https://api.github.com"
    rustdesk_repo: str = "rustdesk/rustdesk"
    worker_sweep_seconds: int = 15

    @property
    def public_host(self) -> str:
        return self.public_url.split("://", 1)[1].rstrip("/")


@lru_cache
def get_config() -> Config:
    return Config()
