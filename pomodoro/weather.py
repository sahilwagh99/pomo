"""Weather for the panel's clock card, from Open-Meteo (open-meteo.com): free, no account and no key.

Nothing is asked until you set a city. Then Pomo looks the city up once (its name goes to Open-Meteo's
geocoding service) and asks for that spot's weather about every half hour (its latitude and longitude go to
Open-Meteo's forecast service). Nothing else about you is sent.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REFRESH_S = 30 * 60

# WMO weather codes -> (words, glyph)
CODES = {
    0: ("Clear", "sun"), 1: ("Mostly clear", "sun"), 2: ("Partly cloudy", "cloud_sun"), 3: ("Cloudy", "cloud"),
    45: ("Fog", "fog"), 48: ("Fog", "fog"),
    51: ("Drizzle", "rain"), 53: ("Drizzle", "rain"), 55: ("Drizzle", "rain"), 56: ("Freezing drizzle", "rain"),
    57: ("Freezing drizzle", "rain"), 61: ("Light rain", "rain"), 63: ("Rain", "rain"), 65: ("Heavy rain", "rain"),
    66: ("Freezing rain", "rain"), 67: ("Freezing rain", "rain"), 71: ("Light snow", "snow"), 73: ("Snow", "snow"),
    75: ("Heavy snow", "snow"), 77: ("Snow grains", "snow"), 80: ("Showers", "rain"), 81: ("Showers", "rain"),
    82: ("Heavy showers", "rain"), 85: ("Snow showers", "snow"), 86: ("Snow showers", "snow"),
    95: ("Thunderstorm", "storm"), 96: ("Thunderstorm", "storm"), 99: ("Thunderstorm", "storm"),
}


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float

    @classmethod
    def from_settings(cls, raw) -> "Place | None":
        try:
            return cls(str(raw["name"]), float(raw["lat"]), float(raw["lon"])) if raw else None
        except (KeyError, TypeError, ValueError):
            return None

    def to_settings(self) -> dict:
        return {"name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass(frozen=True)
class Weather:
    temp: float
    high: float | None
    low: float | None
    code: int
    is_day: bool
    fahrenheit: bool = False

    @property
    def words(self) -> str:
        return CODES.get(self.code, ("", ""))[0]

    @property
    def glyph(self) -> str:
        glyph = CODES.get(self.code, ("", "cloud"))[1]
        return "moon" if glyph == "sun" and not self.is_day else glyph


def parse_places(data: dict) -> list[Place]:
    out = []
    for r in data.get("results") or []:
        try:
            bits = [r["name"], r.get("admin1"), r.get("country")]
            name = ", ".join(dict.fromkeys(b for b in bits if b))
            out.append(Place(name, float(r["latitude"]), float(r["longitude"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def parse_weather(data: dict, fahrenheit: bool = False) -> Weather | None:
    try:
        now = data["current"]
        daily = data.get("daily") or {}
        highs, lows = daily.get("temperature_2m_max") or [None], daily.get("temperature_2m_min") or [None]
        return Weather(float(now["temperature_2m"]), highs[0], lows[0], int(now.get("weather_code", 3)),
                       bool(now.get("is_day", 1)), fahrenheit)
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def uses_fahrenheit() -> bool:
    from PySide6.QtCore import QLocale

    return QLocale().measurementSystem() == QLocale.MeasurementSystem.ImperialUSSystem


class _Job(QThread):
    done = Signal(object)  # a list of Places, a Weather, or None when it didn't work

    def __init__(self, kind: str, arg, parent=None):
        super().__init__(parent)
        self.kind, self.arg = kind, arg

    def run(self) -> None:
        try:
            import httpx2

            from . import APP_VERSION

            headers = {"User-Agent": f"Pomo/{APP_VERSION}"}
            with httpx2.Client(timeout=httpx2.Timeout(15.0, connect=10.0), follow_redirects=True) as client:
                if self.kind == "geocode":
                    resp = client.get(GEOCODE_URL, params={"name": self.arg, "count": 6, "language": "en",
                                                           "format": "json"}, headers=headers)
                    self.done.emit(parse_places(resp.json()) if resp.status_code == 200 else None)
                else:
                    place, fahrenheit = self.arg
                    params = {"latitude": place.lat, "longitude": place.lon, "timezone": "auto", "forecast_days": 1,
                              "current": "temperature_2m,weather_code,is_day",
                              "daily": "temperature_2m_max,temperature_2m_min"}
                    if fahrenheit:
                        params["temperature_unit"] = "fahrenheit"
                    resp = client.get(FORECAST_URL, params=params, headers=headers)
                    self.done.emit(parse_weather(resp.json(), fahrenheit) if resp.status_code == 200 else None)
        except Exception:  # offline, or a reply we can't read: no weather this time
            self.done.emit(None)


class WeatherService(QObject):
    """Today's weather for your place, refreshed about every half hour while something shows it."""

    changed = Signal()
    found = Signal(list)  # places matching a search (empty when none, or when the search failed)

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.now: Weather | None = None
        self._fetched = 0.0
        self._jobs: list[_Job] = []

    @property
    def place(self) -> Place | None:
        return Place.from_settings(self.ctrl.settings.weather_place)

    def set_place(self, place: Place | None) -> None:
        self.ctrl.settings.weather_place = place.to_settings() if place else {}
        self.ctrl.save_settings()
        self.now, self._fetched = None, 0.0
        self.changed.emit()
        self.refresh(force=True)

    def search(self, text: str) -> None:
        if text.strip():
            self._start(_Job("geocode", text.strip()), self._searched)

    def refresh(self, force: bool = False) -> None:
        place = self.place
        if place is None or (not force and time.monotonic() - self._fetched < REFRESH_S and self.now is not None):
            return
        self._fetched = time.monotonic()
        self._start(_Job("forecast", (place, uses_fahrenheit())), self._fetched_weather)

    def _start(self, job: _Job, then) -> None:
        job.done.connect(then)
        job.finished.connect(lambda j=job: self._jobs.remove(j) if j in self._jobs else None)
        self._jobs.append(job)
        job.start()

    def _searched(self, places) -> None:
        self.found.emit(places or [])

    def _fetched_weather(self, weather) -> None:
        if weather is not None:
            self.now = weather
        elif self.now is None:
            self._fetched = time.monotonic() - REFRESH_S + 5 * 60  # try again in five minutes
        self.changed.emit()
