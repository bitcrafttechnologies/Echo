"""Open-Meteo current conditions normalized as an Echo Signal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
from typing import Any
from urllib.parse import urlencode

from echo.core.signal import Signal
from echo.medulla.local import LocalQueueTransport, QueueOfferResult
from echo.medulla.sources._http import SignalSourceError, fetch_json


JsonFetcher = Callable[[str, float], Any]

_WEATHER_CONDITIONS = {
    0: "clear_sky",
    1: "mainly_clear",
    2: "partly_cloudy",
    3: "overcast",
    45: "fog",
    48: "depositing_rime_fog",
    51: "light_drizzle",
    53: "moderate_drizzle",
    55: "dense_drizzle",
    56: "light_freezing_drizzle",
    57: "dense_freezing_drizzle",
    61: "slight_rain",
    63: "moderate_rain",
    65: "heavy_rain",
    66: "light_freezing_rain",
    67: "heavy_freezing_rain",
    71: "slight_snow",
    73: "moderate_snow",
    75: "heavy_snow",
    77: "snow_grains",
    80: "slight_rain_showers",
    81: "moderate_rain_showers",
    82: "violent_rain_showers",
    85: "slight_snow_showers",
    86: "heavy_snow_showers",
    95: "thunderstorm",
    96: "thunderstorm_with_slight_hail",
    99: "thunderstorm_with_heavy_hail",
}


class OpenMeteoWeatherSource:
    API_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        *,
        latitude: float,
        longitude: float,
        location: str,
        timeout: float = 10.0,
        fetcher: JsonFetcher = fetch_json,
    ) -> None:
        self._latitude = self._coordinate(latitude, "latitude", -90.0, 90.0)
        self._longitude = self._coordinate(longitude, "longitude", -180.0, 180.0)
        if not isinstance(location, str) or not location:
            raise ValueError("location must be a non-empty string")
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(float(timeout))
            or timeout <= 0
        ):
            raise ValueError("timeout must be finite and positive")
        self._location = location
        self._timeout = float(timeout)
        self._fetcher = fetcher

    @staticmethod
    def _coordinate(value: Any, name: str, minimum: float, maximum: float) -> float:
        if type(value) not in (int, float) or not minimum <= float(value) <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return float(value)

    def request_url(self) -> str:
        query = urlencode(
            {
                "latitude": self._latitude,
                "longitude": self._longitude,
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "weather_code,wind_speed_10m,is_day"
                ),
                "temperature_unit": "celsius",
                "wind_speed_unit": "kmh",
                "timezone": "UTC",
            }
        )
        return f"{self.API_URL}?{query}"

    async def observe(self) -> Signal:
        try:
            data = await asyncio.to_thread(
                self._fetcher, self.request_url(), self._timeout
            )
        except SignalSourceError:
            raise
        except Exception as error:
            raise SignalSourceError(f"weather source failed: {error}") from error
        if type(data) is not dict or type(data.get("current")) is not dict:
            raise SignalSourceError("weather response must contain a current object")
        current = data["current"]
        weather_code = self._integer(current, "weather_code")
        if weather_code not in _WEATHER_CONDITIONS:
            raise SignalSourceError("weather current.weather_code is unsupported")
        return Signal(
            type="weather.observed",
            source="open-meteo",
            payload={
                "location": self._location,
                "latitude": self._latitude,
                "longitude": self._longitude,
                "observed_at": self._string(current, "time"),
                "temperature_c": self._number(current, "temperature_2m"),
                "apparent_temperature_c": self._number(
                    current, "apparent_temperature"
                ),
                "relative_humidity_percent": self._number(
                    current, "relative_humidity_2m"
                ),
                "weather_code": weather_code,
                "condition": _WEATHER_CONDITIONS[weather_code],
                "wind_speed_kmh": self._number(current, "wind_speed_10m"),
                "is_day": bool(self._integer(current, "is_day")),
            },
        )

    async def publish(self, transport: LocalQueueTransport) -> QueueOfferResult:
        return await transport.publish_signal(await self.observe())

    @staticmethod
    def _string(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value:
            raise SignalSourceError(f"weather current.{key} must be a string")
        return value

    @staticmethod
    def _number(data: dict[str, Any], key: str) -> float:
        value = data.get(key)
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise SignalSourceError(f"weather current.{key} must be numeric")
        return float(value)

    @staticmethod
    def _integer(data: dict[str, Any], key: str) -> int:
        value = data.get(key)
        if type(value) is not int:
            raise SignalSourceError(f"weather current.{key} must be an integer")
        return value
