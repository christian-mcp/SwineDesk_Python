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

    # Voice (inbound phone calls). The caller talks to the same agent as SMS;
    # Twilio transcribes speech, ElevenLabs speaks the agent's reply back.
    voice_enabled: bool = Field(default=True, alias="VOICE_ENABLED")
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    # Default voice is "Rachel", a stock ElevenLabs voice. Override per deployment.
    elevenlabs_voice_id: str = Field(default="21m00Tcm4TlvDq8ikWAM", alias="ELEVENLABS_VOICE_ID")
    # Turbo model keeps round-trip latency low enough for a live call.
    elevenlabs_model_id: str = Field(default="eleven_turbo_v2_5", alias="ELEVENLABS_MODEL_ID")
    # Absolute, publicly reachable base URL Twilio uses to fetch generated audio
    # (e.g. https://swinedesk.up.railway.app). Falls back to the inbound request host.
    public_base_url: str = Field(default="", alias="PUBLIC_BASE_URL")
    voice_greeting: str = Field(
        default="Thanks for calling ELM Pork. How can I help you today?",
        alias="VOICE_GREETING",
    )

    backend_api_url: str = Field(default="", alias="BACKEND_API_URL")
    backend_api_token: str = Field(default="", alias="BACKEND_API_TOKEN")
    backend_timeout_seconds: int = Field(default=15, alias="BACKEND_TIMEOUT_SECONDS")

    partner_email: str = Field(default="", alias="PARTNER_EMAIL")
    partner_phone: str = Field(default="", alias="PARTNER_PHONE")
    broker_alert_phone: str = Field(default="", alias="BROKER_ALERT_PHONE")
    broker_sms_phones: str = Field(default="", alias="BROKER_SMS_PHONES")
    vet_notify_phone: str = Field(default="", alias="VET_NOTIFY_PHONE")
    freight_notify_phone: str = Field(default="", alias="FREIGHT_NOTIFY_PHONE")
    docs_email: str = Field(default="docs@elmpork.com", alias="DOCS_EMAIL")

    hellosign_api_key: str = Field(default="", alias="HELLOSIGN_API_KEY")

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

    daily_summary_enabled: bool = Field(default=True, alias="DAILY_SUMMARY_ENABLED")
    daily_summary_hour: int = Field(default=9, alias="DAILY_SUMMARY_HOUR")
    daily_summary_minute: int = Field(default=0, alias="DAILY_SUMMARY_MINUTE")
    daily_summary_timezone: str = Field(
        default="America/Mexico_City", alias="DAILY_SUMMARY_TIMEZONE"
    )
    daily_summary_phone: str = Field(default="", alias="DAILY_SUMMARY_PHONE")
    session_store_path: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "sessions.json",
        alias="SESSION_STORE_PATH",
    )
    reminder_store_path: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "reminders.json",
        alias="REMINDER_STORE_PATH",
    )
    reminder_poll_seconds: int = Field(default=30, alias="REMINDER_POLL_SECONDS")
    reminder_retention_days: int = Field(default=7, alias="REMINDER_RETENTION_DAYS")
    negotiation_store_path: Path = Field(
        default=DEFAULT_RUNTIME_DIR / "negotiations.json",
        alias="NEGOTIATION_STORE_PATH",
    )

    @property
    def effective_broker_alert_phone(self) -> str:
        """Broker alert phone with fallback to partner phone."""
        return self.broker_alert_phone or self.partner_phone

    @property
    def daily_summary_recipient(self) -> str:
        """Phone for the scheduled daily summary, falling back to the broker alert phone."""
        return self.daily_summary_phone or self.effective_broker_alert_phone

    @property
    def broker_sms_phone_set(self) -> set[str]:
        """Digit-only phone numbers that should resolve to the broker role over SMS."""
        out: set[str] = set()
        for entry in self.broker_sms_phones.split(","):
            digits = "".join(ch for ch in entry if ch.isdigit())
            if digits:
                out.add(digits)
        return out


settings = Settings()
