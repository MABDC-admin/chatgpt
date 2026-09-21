from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    app_name: str = "Teacher AI Cloud Platform"
    environment: str = "production"
    cors_origins: str = "http://localhost:3000"

    # Database / cache
    database_url: str = "postgresql+asyncpg://teacherai:teacherai@postgres:5432/teacherai"
    redis_url: str = "redis://redis:6379/0"

    # Auth
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 60 * 12

    # First boot bootstrap account
    seed_admin_email: str = "admin@school.local"
    seed_admin_password: str = "change-me-now"

    # OpenAI
    openai_api_key: str
    text_model: str = "gpt-5.6-terra"
    cheap_text_model: str = "gpt-5.6-luna"
    image_model: str = "gpt-image-1"
    embedding_model: str = "text-embedding-3-small"

    # Credits: default monthly allocation in cents of real spend
    default_monthly_allocation_cents: int = 500

    # SMTP for approval emails
    smtp_host: str = "mail.mabdc.ae"
    smtp_port: int = 587
    smtp_username: str = "admin@mabdc.ae"
    smtp_password: str = "Denskie123"
    smtp_use_tls: bool = True
    app_base_url: str = "https://chat.mabdc.com"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
