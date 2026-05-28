"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
DEFAULT_RUNTIME_DIR = Path(__file__).resolve().parent.parent / ".runtime"


class Settings(BaseSettings):
    """Runtime configuration for the SwineDesk service."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    port: int = Field(default=3000, alias="PORT")

    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    model_name: str = Field(default="anthropic:claude-sonnet-4-20250514", alias="MODEL_NAME")

    twilio_account_sid: str = Field(default="", alias="TWILIO_ACCOUNT_SID")
    twilio_auth_token: str = Field(default="", alias="TWILIO_AUTH_TOKEN")
    twilio_phone_number: str = Field(default="", alias="TWILIO_PHONE_NUMBER")

    backend_api_url: str = Field(default="", alias="BACKEND_API_URL")
    backend_api_token: str = Field(default="", alias="BACKEND_API_TOKEN")
    backend_timeout_seconds: int = Field(default=15, alias="BACKEND_TIMEOUT_SECONDS")

    partner_email: str = Field(default="", alias="PARTNER_EMAIL")
    partner_phone: str = Field(default="", alias="PARTNER_PHONE")
    broker_alert_phone: str = Field(default="", alias="BROKER_ALERT_PHONE")
    vet_notify_phone: str = Field(default="", alias="VET_NOTIFY_PHONE")
    freight_notify_phone: str = Field(default="", alias="FREIGHT_NOTIFY_PHONE")
    docs_email: str = Field(default="docs@elmpork.com", alias="DOCS_EMAIL")

    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_pass: str = Field(default="", alias="SMTP_PASS")

    session_timeout_minutes: int = Field(default=30, alias="SESSION_TIMEOUT_MINUTES")
    session_max_messages: int = Field(default=30, alias="SESSION_MAX_MESSAGES")
    session_cleanup_interval_minutes: int = Field(
        default=10, alias="SESSION_CLEANUP_INTERVAL_MINUTES"
    )
    broker_alert_throttle_minutes: int = Field(
        default=30, alias="BROKER_ALERT_THROTTLE_MINUTES"
    )
    session_store_path: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "sessions.json",
        alias="SESSION_STORE_PATH",
    )

    @property
    def effective_broker_alert_phone(self) -> str:
        """Broker alert phone with fallback to partner phone."""
        return self.broker_alert_phone or self.partner_phone


settings = Settings()
