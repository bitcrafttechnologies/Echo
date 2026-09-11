"""Optional world-data producers for the local Medulla transport."""

from echo.medulla.sources._http import SignalSourceError
from echo.medulla.sources.clock import ClockSignalSource
from echo.medulla.sources.news import HackerNewsSignalSource
from echo.medulla.sources.weather import OpenMeteoWeatherSource

__all__ = [
    "ClockSignalSource",
    "HackerNewsSignalSource",
    "OpenMeteoWeatherSource",
    "SignalSourceError",
]
