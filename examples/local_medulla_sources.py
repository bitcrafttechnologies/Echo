"""Publish clock, Phoenix weather, and Hacker News through local Medulla."""

from __future__ import annotations

import argparse
import asyncio

from echo import Entity, LocalQueueTransport, Runtime, Signal
from echo.medulla.sources import (
    ClockSignalSource,
    HackerNewsSignalSource,
    OpenMeteoWeatherSource,
)


async def run(latitude: float, longitude: float, location: str, news_limit: int) -> None:
    bit = Entity("bit")
    runtime = Runtime([bit])
    medulla = LocalQueueTransport(
        transport_id="local-world-sources",
        inbound_capacity=10,
    )

    @bit.on("time.observed")
    async def show_time(signal: Signal) -> None:
        print(f"Time: {signal.payload['iso8601']}")

    @bit.on("weather.observed")
    async def show_weather(signal: Signal) -> None:
        print(
            f"Weather in {signal.payload['location']}: "
            f"{signal.payload['temperature_c']} C, "
            f"{signal.payload['condition'].replace('_', ' ')}"
        )

    @bit.on("news.headlines_observed")
    async def show_news(signal: Signal) -> None:
        print("Top Hacker News stories:")
        for story in signal.payload["headlines"]:
            print(f"- {story['title']} ({story['url']})")

    sources = (
        ClockSignalSource(),
        OpenMeteoWeatherSource(
            latitude=latitude,
            longitude=longitude,
            location=location,
        ),
        HackerNewsSignalSource(limit=news_limit),
    )

    await medulla.start()
    outcomes = await asyncio.gather(
        *(source.publish(medulla) for source in sources),
        return_exceptions=True,
    )
    published = 0
    for source, outcome in zip(sources, outcomes, strict=True):
        if isinstance(outcome, Exception):
            print(f"{type(source).__name__} unavailable: {outcome}")
        else:
            published += 1

    for _ in range(published):
        await runtime.emit(await medulla.receive())
    await medulla.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send clock, weather, and news Signals through LocalQueueTransport."
    )
    parser.add_argument("--latitude", type=float, default=33.4484)
    parser.add_argument("--longitude", type=float, default=-112.0740)
    parser.add_argument("--location", default="Phoenix, AZ")
    parser.add_argument("--news-limit", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(
        run(args.latitude, args.longitude, args.location, args.news_limit)
    )


if __name__ == "__main__":
    main()
