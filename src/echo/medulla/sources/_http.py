"""Small non-executable JSON client used by optional Medulla sources."""

from __future__ import annotations

import json
from typing import Any
from urllib.request import Request, urlopen


MAX_JSON_BYTES = 1_000_000


class SignalSourceError(Exception):
    """An external source could not produce a valid Signal."""


def fetch_json(url: str, timeout: float) -> Any:
    """Fetch bounded JSON without object hooks or type reconstruction."""

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Echo-Medulla/0.9.5",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_JSON_BYTES + 1)
    except Exception as error:
        raise SignalSourceError(f"source request failed: {error}") from error
    if len(body) > MAX_JSON_BYTES:
        raise SignalSourceError("source response exceeds the JSON size limit")
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SignalSourceError("source response is not valid JSON") from error
