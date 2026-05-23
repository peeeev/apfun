"""Settings loaded from environment with the APFUN_ prefix."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APFUN_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 4000
    db_url: str = "sqlite:///data/apfun.db"
    anthropic_api_key: str = ""
    # Residential proxy for Reddit ingestion (task 005c). Reddit blocks
    # datacenter IPs at the network layer; this routes Reddit traffic through a
    # residential proxy. Loud-failure: empty default here, `apfun.sourcing.
    # reddit._build_client()` raises with a CLAUDE.md-pointing message at the
    # call site when missing (per CLAUDE.md → Auth secret discipline).
    reddit_http_proxy: str = ""
    producthunt_token: str = ""

    @field_validator("host")
    @classmethod
    def reject_localhost(cls, v: str) -> str:
        if v in ("127.0.0.1", "localhost"):
            raise ValueError(
                "APFUN_HOST is bound to localhost. The dev container is reverse-proxied "
                "from the host at 0.0.0.0:4000 (see CLAUDE.md → Networking). Use 0.0.0.0."
            )
        return v

    @field_validator("reddit_http_proxy", mode="after")
    @classmethod
    def _validate_reddit_proxy(cls, v: str) -> str:
        # Empty is allowed — the call site (`_build_client`) decides whether a
        # missing proxy is fatal. If non-empty, it must look like a proxy URL
        # so a typo fails at construction rather than as an opaque httpx error.
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(
                "APFUN_REDDIT_HTTP_PROXY must be a URL starting with http:// or https://. "
                "Format: http://username:password@host:port. See CLAUDE.md → Networking."
            )
        return v


settings = Settings()
