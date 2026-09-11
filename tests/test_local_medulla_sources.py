from __future__ import annotations

from datetime import datetime, timezone
import os
import sys
import unittest
from urllib.parse import parse_qs, urlparse


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from echo import LocalQueueTransport
from echo.medulla.sources import (
    ClockSignalSource,
    HackerNewsSignalSource,
    OpenMeteoWeatherSource,
    SignalSourceError,
)


class ClockSignalSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_clock_publishes_a_timezone_aware_local_signal(self) -> None:
        instant = datetime(2026, 9, 10, 12, 30, tzinfo=timezone.utc)
        source = ClockSignalSource(clock=lambda: instant)
        transport = LocalQueueTransport()
        await transport.start()

        await source.publish(transport)
        signal = await transport.receive()

        self.assertEqual(signal.type, "time.observed")
        self.assertEqual(signal.timestamp, instant)
        self.assertEqual(signal.payload["iso8601"], instant.isoformat())


class WeatherSignalSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_meteo_response_is_normalized_and_published(self) -> None:
        requested: list[tuple[str, float]] = []

        def fetch(url: str, timeout: float):
            requested.append((url, timeout))
            return {
                "current": {
                    "time": "2026-09-10T12:30",
                    "temperature_2m": 31.5,
                    "apparent_temperature": 32.1,
                    "relative_humidity_2m": 28,
                    "weather_code": 0,
                    "wind_speed_10m": 7.2,
                    "is_day": 1,
                }
            }

        source = OpenMeteoWeatherSource(
            latitude=33.4484,
            longitude=-112.074,
            location="Phoenix, AZ",
            fetcher=fetch,
        )
        transport = LocalQueueTransport()
        await transport.start()

        await source.publish(transport)
        signal = await transport.receive()

        self.assertEqual(signal.type, "weather.observed")
        self.assertEqual(signal.source, "open-meteo")
        self.assertEqual(signal.payload["temperature_c"], 31.5)
        self.assertEqual(signal.payload["condition"], "clear_sky")
        self.assertTrue(signal.payload["is_day"])
        query = parse_qs(urlparse(requested[0][0]).query)
        self.assertEqual(query["latitude"], ["33.4484"])
        self.assertIn("weather_code", query["current"][0])

    async def test_malformed_weather_isolated_as_source_error(self) -> None:
        source = OpenMeteoWeatherSource(
            latitude=0,
            longitude=0,
            location="Test",
            fetcher=lambda _url, _timeout: {"current": {"temperature_2m": "hot"}},
        )
        with self.assertRaises(SignalSourceError):
            await source.observe()


class NewsSignalSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_hacker_news_stories_are_bounded_normalized_and_published(self) -> None:
        def fetch(url: str, _timeout: float):
            if url.endswith("topstories.json"):
                return [101, 102, 103]
            story_id = int(url.rsplit("/", 1)[1].split(".", 1)[0])
            return {
                "id": story_id,
                "title": f"Story &amp; {story_id}",
                "by": "author",
                "time": 1_789_040_000,
                "score": 42,
                "url": f"https://example.com/{story_id}",
            }

        source = HackerNewsSignalSource(limit=2, fetcher=fetch)
        transport = LocalQueueTransport()
        await transport.start()

        await source.publish(transport)
        signal = await transport.receive()

        self.assertEqual(signal.type, "news.headlines_observed")
        self.assertEqual(signal.payload["count"], 2)
        self.assertEqual(signal.payload["headlines"][0]["title"], "Story & 101")
        self.assertEqual(signal.payload["headlines"][1]["id"], 102)

    async def test_missing_story_url_uses_non_api_browser_url(self) -> None:
        def fetch(url: str, _timeout: float):
            if url.endswith("topstories.json"):
                return [101]
            return {
                "id": 101,
                "title": "Ask HN",
                "by": "author",
                "time": 1_789_040_000,
                "score": 1,
            }

        signal = await HackerNewsSignalSource(limit=1, fetcher=fetch).observe()
        self.assertEqual(
            signal.payload["headlines"][0]["url"],
            "https://news.ycombinator.com/item?id=101",
        )

    def test_news_limit_is_hard_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 10"):
            HackerNewsSignalSource(limit=11)


if __name__ == "__main__":
    unittest.main()
