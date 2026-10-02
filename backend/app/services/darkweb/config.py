"""Search-engine settings for the dark-web package.

Plain pydantic model on purpose: it does not read the environment. DECINT keeps
every tunable in `app/config.py`, and `Settings.from_app()` is the only bridge,
so there is one place to look for what an operator can change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    from ...config import Settings as AppSettings

# Tor Browser (Firefox ESR 128) user agent: blends in with real Tor traffic.
TOR_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; rv:128.0) Gecko/20100101 Firefox/128.0"

Transport = Literal["tor", "direct"]


def _split(value: str) -> list[str]:
    return [v.strip().lower() for v in value.split(",") if v.strip()]


class Settings(BaseModel):
    # Transport
    transport: Transport = "tor"
    tor_proxy: str = "socks5h://127.0.0.1:9050"
    isolate_circuits: bool = True
    user_agent: str = TOR_BROWSER_UA
    connect_timeout: float = 30.0
    read_timeout: float = 40.0
    deadline: float = 75.0
    max_concurrency: int = 32
    max_body_bytes: int = 2_000_000
    retries: int = 1

    # Engine selection
    pages: int = Field(1, ge=1, le=10)
    engines: str = ""
    disabled_engines: str = ""
    include_experimental: bool = False
    catalog_path: Path | None = None

    # Health tracking / circuit breaker
    cache_dir: Path = Path("data/darkweb")
    health_enabled: bool = True
    breaker_failures: int = 3
    breaker_cooldown: float = 1800.0

    # Safety. The child-safety filter itself is not configurable; this only controls
    # whether Ahmia's published ban list is fetched as an extra layer on top of it.
    use_ahmia_banlist: bool = True

    # Result shaping
    max_results: int = 50
    max_per_host: int = 2
    min_quality: float = 0.35
    w_relevance: float = 0.40
    w_fusion: float = 0.25
    w_consensus: float = 0.20
    w_quality: float = 0.15

    # Development: serve saved engine pages from this directory instead of the network.
    replay_dir: Path | None = None
    replay_delay: float = 0.0

    @property
    def engine_allowlist(self) -> list[str]:
        return _split(self.engines)

    @property
    def engine_denylist(self) -> list[str]:
        return _split(self.disabled_engines)

    @classmethod
    def from_app(cls, app: AppSettings, transport: Transport) -> Settings:
        """Build the engine settings for one transport from DECINT's central config.

        Each transport gets its own cache directory, so the circuit breaker for an engine's
        onion mirrors is never confused with the one for its clearnet gateway.
        """
        return cls(
            transport=transport,
            tor_proxy=f"socks5h://{app.tor_host}:{app.tor_port}",
            deadline=app.darkweb_deadline if transport == "tor" else app.darkweb_gateway_deadline,
            engines=app.darkweb_engines,
            disabled_engines=app.darkweb_disabled_engines,
            catalog_path=Path(app.darkweb_catalog_path) if app.darkweb_catalog_path else None,
            cache_dir=Path(app.darkweb_data_dir) / transport,
            breaker_failures=app.darkweb_breaker_failures,
            breaker_cooldown=app.darkweb_breaker_cooldown,
            max_per_host=app.darkweb_max_per_host,
            min_quality=app.darkweb_min_quality,
            replay_dir=Path(app.darkweb_replay_dir) if app.darkweb_replay_dir else None,
        )
