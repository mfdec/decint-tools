"""Fixtures for the dark-web engine tests. Everything here is offline: engine pages are replayed
from `fixtures/replay/` and the network is never touched."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
from pathlib import Path

import pytest

import app.services.darkweb as dw
from app.services.darkweb.aggregator import Aggregator
from app.services.darkweb.config import Settings
from app.services.darkweb.engines import load_catalog
from app.services.darkweb.health import HealthStore
from app.services.darkweb.tor import ReplayFetcher

FIXTURES = Path(__file__).parent / "fixtures"
REPLAY = FIXTURES / "replay"


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run `async def` tests on a fresh event loop, so no pytest-asyncio plugin is needed."""
    if inspect.iscoroutinefunction(pyfuncitem.obj):
        args = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
        asyncio.run(pyfuncitem.obj(**args))
        return True
    return None


def fake_onion(seed: str) -> str:
    """Deterministic, checksum-valid v3 onion host for tests."""
    pk = hashlib.sha256(seed.encode()).digest()
    version = b"\x03"
    checksum = hashlib.sha3_256(b".onion checksum" + pk + version).digest()[:2]
    return base64.b32encode(pk + checksum + version).decode().lower() + ".onion"


def read_fixture(name: str) -> str:
    return (REPLAY / name).read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        transport="tor",
        cache_dir=tmp_path / "cache",
        health_enabled=False,
        deadline=20,
        retries=0,
    )


@pytest.fixture
def replay_settings(tmp_path: Path) -> Settings:
    return Settings(
        replay_dir=REPLAY,
        cache_dir=tmp_path / "cache",
        health_enabled=False,
        deadline=20,
    )


@pytest.fixture
def replay_aggregator(replay_settings: Settings) -> Aggregator:
    return Aggregator(
        replay_settings,
        engines=load_catalog(),
        fetcher=ReplayFetcher(REPLAY),
        health=HealthStore(None),
    )


@pytest.fixture
def replay(monkeypatch, tmp_path):
    """Point the service layer at saved engine pages (no network) with fresh aggregators."""
    monkeypatch.setattr(dw.app_settings, "darkweb_replay_dir", str(REPLAY))
    monkeypatch.setattr(dw.app_settings, "darkweb_data_dir", str(tmp_path))
    dw._aggregators.clear()
    yield
    dw._aggregators.clear()
