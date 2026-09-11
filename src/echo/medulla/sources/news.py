"""Hacker News headlines normalized as one bounded Echo Signal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from html import unescape
import math
from typing import Any

from echo.core.signal import Signal
from echo.medulla.local import LocalQueueTransport, QueueOfferResult
from echo.medulla.sources._http import SignalSourceError, fetch_json


JsonFetcher = Callable[[str, float], Any]


class HackerNewsSignalSource:
    API_ROOT = "https://hacker-news.firebaseio.com/v0"

    def __init__(
        self,
        *,
        limit: int = 5,
        timeout: float = 10.0,
        fetcher: JsonFetcher = fetch_json,
    ) -> None:
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be finite and positive")
        self._limit = limit
        self._timeout = float(timeout)
        self._fetcher = fetcher

    async def observe(self) -> Signal:
        try:
            story_ids = await asyncio.to_thread(
                self._fetcher,
                f"{self.API_ROOT}/topstories.json",
                self._timeout,
            )
            if type(story_ids) is not list or any(
                type(story_id) is not int for story_id in story_ids[: self._limit]
            ):
                raise SignalSourceError("news top stories must be an integer array")
            stories = []
            for story_id in story_ids[: self._limit]:
                item = await asyncio.to_thread(
                    self._fetcher,
                    f"{self.API_ROOT}/item/{story_id}.json",
                    self._timeout,
                )
                stories.append(self._normalize_story(item, story_id))
        except SignalSourceError:
            raise
        except Exception as error:
            raise SignalSourceError(f"news source failed: {error}") from error

        return Signal(
            type="news.headlines_observed",
            source="hacker-news",
            payload={"headlines": stories, "count": len(stories)},
        )

    async def publish(self, transport: LocalQueueTransport) -> QueueOfferResult:
        return await transport.publish_signal(await self.observe())

    @staticmethod
    def _normalize_story(item: Any, expected_id: int) -> dict[str, Any]:
        if type(item) is not dict or item.get("id") != expected_id:
            raise SignalSourceError("news item does not match its requested ID")
        title = item.get("title")
        author = item.get("by")
        published = item.get("time")
        score = item.get("score", 0)
        url = item.get("url") or f"https://news.ycombinator.com/item?id={expected_id}"
        if not isinstance(title, str) or not title:
            raise SignalSourceError("news item title must be a non-empty string")
        if not isinstance(author, str) or not author:
            raise SignalSourceError("news item author must be a non-empty string")
        if type(published) is not int or published < 0:
            raise SignalSourceError("news item time must be a Unix timestamp")
        if type(score) is not int or score < 0:
            raise SignalSourceError("news item score must be a non-negative integer")
        if not isinstance(url, str) or not url:
            raise SignalSourceError("news item URL must be a non-empty string")
        return {
            "id": expected_id,
            "title": unescape(title),
            "author": author,
            "published_at": datetime.fromtimestamp(
                published, tz=timezone.utc
            ).isoformat(),
            "score": score,
            "url": url,
        }
