from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./poolsmart.db"
    google_maps_server_key: SecretStr = SecretStr("")
    google_maps_browser_key: SecretStr = SecretStr("")
    google_maps_map_id: str = ""
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "poolsmart"
    token_minutes: int = 60
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_starttls: bool = True
    public_base_url: str = ""
    cors_origins: str = "https://localhost"
    service_area_radius_km: float = 85.0
    match_interval_seconds: int = 45
    maps_daily_element_limit: int = 10000
    sos_webhook_url: str = ""
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""
    vapid_public_key: str = ""
    vapid_private_key: SecretStr = SecretStr("")
    vapid_subject: str = ""
    razorpay_key_id: str = ""
    razorpay_key_secret: SecretStr = SecretStr("")
    razorpay_webhook_secret: SecretStr = SecretStr("")
    upi_payee_id: str = ""
    upi_payee_name: str = ""
    fare_base_inr: float = 25.0
    fare_per_km_inr: float = 12.0
    fare_per_minute_inr: float = 1.0
    fare_surge_cap: float = 1.5
    fare_commission_percent: float = 10.0
    co2_kg_per_km: float | None = None
    weather_buffer_minutes: int = 10
    razorpay_test_mode: bool = True

    def missing_runtime_settings(self):
        required = {
            "GOOGLE_MAPS_SERVER_KEY": self.google_maps_server_key.get_secret_value(),
            "GOOGLE_MAPS_BROWSER_KEY": self.google_maps_browser_key.get_secret_value(),
            "GOOGLE_MAPS_MAP_ID": self.google_maps_map_id,
            "JWT_SECRET": self.jwt_secret.get_secret_value(),
            "SMTP_HOST": self.smtp_host,
            "SMTP_USER": self.smtp_user,
            "SMTP_PASSWORD": self.smtp_password.get_secret_value(),
            "SMTP_FROM": self.smtp_from,
            "SOS_WEBHOOK_URL": self.sos_webhook_url,
            "VAPID_PUBLIC_KEY": self.vapid_public_key,
            "VAPID_PRIVATE_KEY": self.vapid_private_key.get_secret_value(),
            "VAPID_SUBJECT": self.vapid_subject,
            "PUBLIC_BASE_URL": self.public_base_url,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if not self.smtp_starttls:
            missing.append("SMTP_STARTTLS (must be true)")
        if self.sos_webhook_url and not self.sos_webhook_url.startswith("https://"):
            missing.append("SOS_WEBHOOK_URL (must use HTTPS)")
        if self.public_base_url and not self.public_base_url.startswith("https://"):
            missing.append("PUBLIC_BASE_URL (must use HTTPS)")
        if self.jwt_secret.get_secret_value() and len(self.jwt_secret.get_secret_value()) < 32:
            missing.append("JWT_SECRET (must contain at least 32 characters)")
        if (
            self.razorpay_key_id
            and self.razorpay_test_mode
            and not self.razorpay_key_id.startswith("rzp_test_")
        ):
            missing.append("RAZORPAY_KEY_ID (must be a test key while RAZORPAY_TEST_MODE=true)")
        return missing

    def setup_instructions(self, missing):
        lines = [
            "PoolSmart cannot start until required services are configured.",
            "Create PoolSmart/.env from PoolSmart/.env.example and set:",
        ]
        lines.extend("  - " + name for name in missing)
        lines.extend([
            "Enable Places API (New), Routes API, and Maps JavaScript API in Google Cloud.",
            "Restrict the browser key to your HTTPS host and Maps JavaScript API.",
            "Restrict the server key to your server and Places API (New), Routes API, and Geocoding API.",
            "Set an SMTP account for university email OTP and an HTTPS SOS webhook.",
            "Set JWT_SECRET to at least 32 random bytes, then run: python app.py",
        ])
        return "\n".join(lines)

    @property
    def allowed_origins(self):
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings():
    return Settings()
