"""Settings loaded from environment with the APFUN_ prefix."""

from typing import Literal

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

    # DataForSEO (task 015 / orchestrator request 033). Two creds — login is the
    # account email; password is the DEDICATED API password from
    # https://app.dataforseo.com/api-access, NOT the account login password
    # (the #1 source of integration failures per DataForSEO's own guides).
    # Empty defaults — fail-loud at first client construction (per the
    # auth-secret discipline: 401s are clear, no silent degradation).
    dataforseo_login: str = ""
    dataforseo_password: str = ""
    # Monthly soft cap (USD). At full Stage 3+4 throughput ~$15/mo; $25 leaves
    # ~60% headroom. Crossing it raises DataForSEOBudgetExceededError; operator
    # must explicitly raise the cap (env var + restart) to resume.
    dataforseo_budget_usd_per_month: float = 25.0
    # Sandbox-first build pattern (spec Q2). The PR ships pointed at Sandbox so
    # runbook 005 can validate parsing/auth shape free of charge; operator
    # switches to https://api.dataforseo.com/v3/ after a green smoke test.
    dataforseo_base_url: str = "https://sandbox.dataforseo.com/v3/"
    # Almost always "standard" (~5min latency, cheapest). Override per-call via
    # the client kwarg if a particular run needs Priority/Live; the global
    # default sets the floor.
    dataforseo_serp_queue_mode: Literal["standard", "priority", "live"] = "standard"

    @field_validator("host")
    @classmethod
    def reject_localhost(cls, v: str) -> str:
        if v in ("127.0.0.1", "localhost"):
            raise ValueError(
                "APFUN_HOST is bound to localhost. The dev container is reverse-proxied "
                "from the host at 0.0.0.0:4000 (see CLAUDE.md → Networking). Use 0.0.0.0."
            )
        return v

    @field_validator("dataforseo_base_url", mode="after")
    @classmethod
    def _normalize_dataforseo_base_url(cls, v: str) -> str:
        """Guarantee exactly one trailing slash so `httpx.Client(base_url=...)`
        joins endpoint paths cleanly regardless of how the operator wrote the
        env var. Empty → use the default sandbox URL."""
        if not v:
            return "https://sandbox.dataforseo.com/v3/"
        return v.rstrip("/") + "/"

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
