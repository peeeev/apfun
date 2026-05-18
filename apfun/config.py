"""Settings loaded from environment with the APFUN_ prefix."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APFUN_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 4000
    db_url: str = "sqlite:///data/apfun.db"
    anthropic_api_key: str = ""

    @field_validator("host")
    @classmethod
    def reject_localhost(cls, v: str) -> str:
        if v in ("127.0.0.1", "localhost"):
            raise ValueError(
                "APFUN_HOST is bound to localhost. The dev container is reverse-proxied "
                "from the host at 0.0.0.0:4000 (see CLAUDE.md → Networking). Use 0.0.0.0."
            )
        return v


settings = Settings()
