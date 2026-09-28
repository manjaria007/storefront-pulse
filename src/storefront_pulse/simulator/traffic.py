"""Storefront traffic simulator.

Generates weeks of Ring webhook payloads (v1.1 shape) for a camera at a shop entrance,
plus the ground truth (people per event) and hourly weather context.

The model is deliberately simple but realistic enough to exercise the analytics:
- hourly arrivals ~ Poisson(weekday level x hour-of-day shape x daily mood x weather)
- visitors arrive in groups (1-4 people); each group triggers one motion event
- passers-by trigger motion events that should count as zero visitors
- random rainy spells suppress traffic; occasional after-hours motion at night

Scripted "demo storylines" are layered on top (e.g. rainy Saturday + late opening),
so the demo has something real to find. Add new storylines in STORYLINES.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable

from ..profile import StoreProfile
from ..ring.webhook import build_event

DEFAULT_DEVICE = "ava1.ring.device.SIM-STOREFRONT-ENTRANCE"

# visits per open hour, by weekday (Mon..Sun)
WEEKDAY_LEVEL = [14, 13, 14, 15, 18, 24, 20]
# relative hour-of-day shape for a cafe (weekday / weekend)
WEEKDAY_SHAPE = {7: 1.3, 8: 1.6, 9: 1.1, 10: 0.8, 11: 0.9, 12: 1.4, 13: 1.1, 14: 0.7,
                 15: 0.8, 16: 0.9, 17: 0.8, 18: 0.6}
WEEKEND_SHAPE = {8: 0.7, 9: 1.1, 10: 1.5, 11: 1.6, 12: 1.5, 13: 1.2, 14: 0.9, 15: 0.8,
                 16: 0.7, 17: 0.7, 18: 0.6}
GROUP_SIZES = [(1, 0.70), (2, 0.22), (3, 0.06), (4, 0.02)]
RAIN_FACTOR = 0.6
PASSERBY_PER_HOUR = 3.0


@dataclass
class DayPlan:
    """Per-day knobs the storylines can override."""
    day: date
    rain_hours: set[int] = field(default_factory=set)
    open_at: time | None = None          # None = normal opening time
    mood: float = 1.0                    # day-level multiplier
    after_hours_events: list[time] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class World:
    payloads: list[dict]
    truth: dict[str, int]
    context: list[tuple[str, int, float | None, float | None, str | None, str]]
    storyline_notes: dict[str, list[str]]


def _poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


def _group_size(rng: random.Random) -> int:
    r, acc = rng.random(), 0.0
    for size, p in GROUP_SIZES:
        acc += p
        if r <= acc:
            return size
    return 1


# --------------------------------------------------------------------------- storylines
def storyline_rainy_late_saturday(plans: dict[date, DayPlan], end: date) -> None:
    """Last Saturday in range: rain all morning AND the store opened 45 minutes late."""
    sat = end - timedelta(days=(end.weekday() - 5) % 7)
    if sat in plans:
        p = plans[sat]
        p.rain_hours = {7, 8, 9, 10, 11}
        p.open_at = time(8, 45)
        p.notes.append("rain 7-12; opened 08:45 instead of 08:00")


def storyline_after_hours_wednesday(plans: dict[date, DayPlan], end: date) -> None:
    """Most recent Wednesday: two motion events near midnight."""
    wed = end - timedelta(days=(end.weekday() - 2) % 7)
    if wed in plans:
        plans[wed].after_hours_events += [time(23, 42), time(23, 47)]
        plans[wed].notes.append("after-hours activity 23:42 and 23:47")


STORYLINES: dict[str, Callable[[dict[date, DayPlan], date], None]] = {
    "rainy_late_saturday": storyline_rainy_late_saturday,
    "after_hours_wednesday": storyline_after_hours_wednesday,
}


# --------------------------------------------------------------------------- generator
def build_world(profile: StoreProfile, end: date, weeks: int = 6, seed: int = 42,
                device_id: str = DEFAULT_DEVICE, storylines: list[str] | None = None,
                forecast_days: int = 7) -> World:
    rng = random.Random(seed)
    tz = profile.tz
    start = end - timedelta(days=weeks * 7 - 1)
    plans: dict[date, DayPlan] = {}
    d = start
    while d <= end:
        p = DayPlan(d, mood=rng.gauss(1.0, 0.08))
        if rng.random() < 0.22:  # random rainy spell
            s = rng.randint(7, 15)
            p.rain_hours = set(range(s, s + rng.randint(2, 5)))
        if rng.random() < 0.08:
            p.after_hours_events.append(time(rng.randint(0, 4), rng.randint(0, 59)))
        plans[d] = p
        d += timedelta(days=1)
    for name in storylines if storylines is not None else list(STORYLINES):
        STORYLINES[name](plans, end)

    payloads: list[dict] = []
    truth: dict[str, int] = {}
    context: list = []

    def emit(local_dt: datetime, people: int, event_type: str = "motion_detected") -> None:
        ts_ms = int(local_dt.timestamp() * 1000)
        pl = build_event(event_type=event_type, device_id=device_id, ts_ms=ts_ms)
        eid = pl["data"]["id"]
        if eid in truth:  # same-millisecond collision; nudge
            ts_ms += 1
            pl = build_event(event_type=event_type, device_id=device_id, ts_ms=ts_ms)
            eid = pl["data"]["id"]
        payloads.append(pl)
        truth[eid] = people

    for day, plan in sorted(plans.items()):
        wd = day.weekday()
        oc = profile.open_close(wd)
        shape = WEEKEND_SHAPE if wd >= 5 else WEEKDAY_SHAPE
        for hour in range(24):
            rain = hour in plan.rain_hours
            context.append((day.isoformat(), hour, round(rng.uniform(1.5, 6.0), 1) if rain else 0.0,
                            round(12 + 6 * math.sin((hour - 9) / 24 * 2 * math.pi) + rng.gauss(0, 1), 1),
                            "rain" if rain else "dry", "simulator"))
            h_start = datetime.combine(day, time(hour), tz)
            # passers-by during daylight regardless of opening
            if 7 <= hour <= 20:
                for _ in range(_poisson(rng, PASSERBY_PER_HOUR * (0.7 if rain else 1.0))):
                    emit(h_start + timedelta(seconds=rng.randint(0, 3599)), 0)
            if oc is None:
                continue
            open_t = plan.open_at or oc[0]
            if not (oc[0].hour <= hour < oc[1].hour):
                continue
            lam = WEEKDAY_LEVEL[wd] * shape.get(hour, 0.5) * plan.mood * (RAIN_FACTOR if rain else 1.0)
            n_groups = _poisson(rng, lam / 1.4)
            for _ in range(n_groups):
                t = h_start + timedelta(seconds=rng.randint(0, 3599))
                if t.time() < open_t:
                    continue  # door still locked
                emit(t, _group_size(rng))
            if hour == 10 and rng.random() < 0.5:  # delivery driver rings the bell
                emit(h_start + timedelta(minutes=rng.randint(0, 50)), 0, "button_press")
        for t in plan.after_hours_events:
            local = datetime.combine(day, t, tz)
            if t.hour < 5:
                local += timedelta(days=1)
            emit(local, 1)

    # weather forecast for the coming days (used by the staffing forecast)
    for i in range(1, forecast_days + 1):
        fd = end + timedelta(days=i)
        rainy = set(range(13, 17)) if i == 3 else set()
        for hour in range(24):
            r = hour in rainy
            context.append((fd.isoformat(), hour, 3.0 if r else 0.0, 14.0, "rain" if r else "dry", "forecast"))

    payloads.sort(key=lambda p: p["data"]["attributes"]["timestamp"])
    notes = {d.isoformat(): p.notes for d, p in plans.items() if p.notes}
    return World(payloads, truth, context, notes)
