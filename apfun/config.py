"""Settings loaded from environment with the APFUN_ prefix."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APFUN_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 4000
    db_url: str = "sqlite:///data/apfun.db"
    anthropic_api_key: str = ""
    reddit_username: str = ""
    # OAuth client-credentials (task 005b). Loud-failure: empty default,
    # `apfun.sourcing.reddit` raises with a CLAUDE.md-pointing message at the
    # first call site when missing. Per the auth-secret discipline (CLAUDE.md
    # → Auth secret discipline) — Reddit's OAuth endpoint returns a clear 401
    # on missing/bad creds, so call-site failure is the right shape.
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
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

    @field_validator("reddit_username", mode="after")
    @classmethod
    def _validate_reddit_username(cls, v: str) -> str:
        # Fail-loud at Settings() construction — Reddit silently blocks
        # non-conformant User-Agents, so phantom-empty fetches are worse than
        # a crash. Per docs/tasks/005-reddit-ingester.md → Config and
        # orchestrator feedback 011 Q1.
        if not v or not v.strip():
            raise ValueError(
                "APFUN_REDDIT_USERNAME is required. Reddit silently blocks "
                "non-conformant User-Agents — an empty username produces "
                "phantom-empty results, not errors. Set the env var to your "
                "Reddit handle. See CLAUDE.md → Networking for context."
            )
        return v.strip()


settings = Settings()
