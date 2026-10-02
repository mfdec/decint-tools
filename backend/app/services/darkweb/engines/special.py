"""Pre-request hooks for engines that need a token or a discovered URL before searching."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin

from ..tor import Fetcher
from .parsers import soup_of


@dataclass
class PrepResult:
    extra_params: dict[str, str] = field(default_factory=dict)
    search_url: str | None = None  # full URL template overriding the catalog search_path


PrepHook = Callable[[Fetcher, str, str, float], Awaitable[PrepResult]]


async def ahmia_token(fetcher: Fetcher, engine: str, base: str, timeout: float) -> PrepResult:
    """Ahmia rejects searches without a rotating hidden form field (name and value both rotate,
    valid for 60 minutes). Read it from any page that carries the search form."""
    resp = await fetcher.get(f"{base}/", engine=engine, kind="prep", timeout=timeout)
    soup = soup_of(resp.text)
    form = soup.select_one("form#searchForm") or soup.find("form", action=re.compile(r"search"))
    params: dict[str, str] = {}
    if form is not None:
        for field_ in form.select("input[type=hidden][name]"):
            params[field_["name"]] = field_.get("value", "")
    return PrepResult(extra_params=params)


async def torch_omega_token(fetcher: Fetcher, engine: str, base: str, timeout: float) -> PrepResult:
    resp = await fetcher.get(f"{base}/cgi-bin/omega/omega", engine=engine, kind="prep", timeout=timeout)
    soup = soup_of(resp.text)
    tkn = soup.select_one("input#tkn, input[name=tkn]")
    return PrepResult(extra_params={"tkn": tkn.get("value", "")} if tkn else {})


async def oss_iframe(fetcher: Fetcher, engine: str, base: str, timeout: float) -> PrepResult:
    """OSS serves its real search UI from a hidden iframe whose src ends in `...query=`."""
    resp = await fetcher.get(f"{base}/oss/", engine=engine, kind="prep", timeout=timeout)
    soup = soup_of(resp.text)
    frame = soup.select_one('iframe[style*="display:none"], iframe[style*="display: none"]')
    frame = frame or soup.find("iframe", src=True)
    if frame is None or not frame.get("src"):
        return PrepResult()
    src = urljoin(f"{base}/oss/", frame["src"])
    return PrepResult(search_url=src + "{q}&page={page}")


PREP_HOOKS: dict[str, PrepHook] = {
    "ahmia_token": ahmia_token,
    "torch_omega_token": torch_omega_token,
    "oss_iframe": oss_iframe,
}
