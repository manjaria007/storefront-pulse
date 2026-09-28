"""Store profile: opening hours, time zone, staffing assumptions.

Kept in a small YAML file so a shop owner (or Alexa, via `update_store_profile`) can
change it without touching code.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class StoreProfile:
    name: str = "Demo Cafe"
    timezone: str = "America/New_York"
    latitude: float = 42.36
    longitude: float = -71.06
    # day -> [open "HH:MM", close "HH:MM"] or None when closed
    hours: dict[str, list[str] | None] = field(
        default_factory=lambda: {d: ["08:00", "18:00"] for d in DAYS}
    )
    visits_per_staff_hour: int = 25
    min_staff: int = 1
    scheduled_staff: dict[str, int] = field(default_factory=lambda: {d: 2 for d in DAYS})
    device_ids: list[str] = field(default_factory=list)  # empty = accept all devices

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def open_close(self, weekday: int) -> tuple[time, time] | None:
        span = self.hours.get(DAYS[weekday])
        if not span:
            return None
        o, c = (time.fromisoformat(s) for s in span)
        return o, c

    def is_open(self, weekday: int, hour: int) -> bool:
        oc = self.open_close(weekday)
        if oc is None:
            return False
        o, c = oc
        return o.hour <= hour < (c.hour if c.minute == 0 else c.hour + 1)

    @classmethod
    def load(cls, path: Path) -> "StoreProfile":
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}
        return cls(**data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(asdict(self), sort_keys=False))
