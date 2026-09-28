"""Traffic analytics primitives shared by all insights.

Everything is computed in the store's local time zone. "Visits" = estimated people
entering (sum of per-event counts), not raw motion events.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ..profile import StoreProfile
from ..store.sqlite import Store

US_HOLIDAYS = {  # extend or swap for the `holidays` package later
    "2026-01-01": "New Year's Day", "2026-01-19": "MLK Day", "2026-02-16": "Presidents' Day",
    "2026-05-25": "Memorial Day", "2026-06-19": "Juneteenth", "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day", "2026-10-12": "Indigenous Peoples' / Columbus Day",
    "2026-11-11": "Veterans Day", "2026-11-26": "Thanksgiving", "2026-12-25": "Christmas",
}


@dataclass
class DayTraffic:
    day: date
    hourly: list[int]                 # people per local hour (0..23)
    open_hours: list[int]
    total_open: int                   # people during opening hours
    after_hours: list[dict]           # visits outside opening hours (people > 0)
    first_visit: datetime | None      # first visitor after 05:00 local
    has_data: bool
    device_offline_minutes: int


@dataclass
class Baseline:
    weekday: int
    n_days: int
    total_median: float
    total_low: float
    total_high: float
    hourly_median: list[float]
    hourly_low: list[float]
    hourly_high: list[float]
    first_visit_delay_min: float | None   # typical minutes from opening to first visitor
    days: list[date]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def day_bounds(profile: StoreProfile, d: date) -> tuple[int, int]:
    tz = profile.tz
    return _ms(datetime.combine(d, time(0), tz)), _ms(datetime.combine(d + timedelta(days=1), time(0), tz))


def open_hours(profile: StoreProfile, d: date) -> list[int]:
    return [h for h in range(24) if profile.is_open(d.weekday(), h)]


def day_traffic(store: Store, profile: StoreProfile, d: date) -> DayTraffic:
    tz = profile.tz
    s, e = day_bounds(profile, d)
    hourly = [0] * 24
    after: list[dict] = []
    first: datetime | None = None
    rows = store.visits_between(s, e)
    oh = open_hours(profile, d)
    for r in rows:
        if r["people"] <= 0:
            continue
        lt = datetime.fromtimestamp(r["ts_ms"] / 1000, tz)
        hourly[lt.hour] += r["people"]
        if lt.hour not in oh:
            after.append({"time": lt.strftime("%H:%M"), "people": r["people"], "event_id": r["event_id"],
                          "device_id": r["device_id"], "confidence": r["confidence"]})
        elif first is None and lt.hour >= 5:
            first = lt
    # early hours of the next morning belong to "tonight"
    ns, ne = _ms(datetime.combine(d + timedelta(days=1), time(0), tz)), _ms(datetime.combine(d + timedelta(days=1), time(5), tz))
    for r in store.visits_between(ns, ne):
        if r["people"] > 0:
            lt = datetime.fromtimestamp(r["ts_ms"] / 1000, tz)
            after.append({"time": lt.strftime("%H:%M") + " (after midnight)", "people": r["people"],
                          "event_id": r["event_id"], "device_id": r["device_id"], "confidence": r["confidence"]})
    offline = _offline_minutes(store, s, e)
    has_data = bool(rows) or bool(store.events_between(s, e))
    return DayTraffic(d, hourly, oh, sum(hourly[h] for h in oh), after, first, has_data, offline)


def _offline_minutes(store: Store, s: int, e: int) -> int:
    evs = store.events_between(s, e, ["device_offline", "device_online"])
    total, down_since = 0, None
    for ev in evs:
        if ev["type"] == "device_offline" and down_since is None:
            down_since = ev["ts_ms"]
        elif ev["type"] == "device_online" and down_since is not None:
            total += (ev["ts_ms"] - down_since) // 60000
            down_since = None
    if down_since is not None:
        total += (e - down_since) // 60000
    return int(total)


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    if len(v) == 1:
        return float(v[0])
    k = (len(v) - 1) * q
    f = int(k)
    c = min(f + 1, len(v) - 1)
    return float(v[f] + (v[c] - v[f]) * (k - f))


def baseline(store: Store, profile: StoreProfile, d: date, lookback_weeks: int = 6,
             exclude_holidays: bool = True) -> Baseline:
    """Typical traffic for d's weekday from the previous `lookback_weeks` same weekdays."""
    days: list[DayTraffic] = []
    for w in range(1, lookback_weeks + 1):
        prev = d - timedelta(weeks=w)
        if exclude_holidays and prev.isoformat() in US_HOLIDAYS:
            continue
        dt = day_traffic(store, profile, prev)
        if dt.has_data:
            days.append(dt)
    totals = [x.total_open for x in days]
    hourly = [[x.hourly[h] for x in days] for h in range(24)]
    delays = []
    for x in days:
        oc = profile.open_close(x.day.weekday())
        if x.first_visit and oc:
            opened = datetime.combine(x.day, oc[0], profile.tz)
            delays.append((x.first_visit - opened).total_seconds() / 60)
    return Baseline(
        weekday=d.weekday(), n_days=len(days),
        total_median=statistics.median(totals) if totals else 0.0,
        total_low=_pct(totals, 0.1), total_high=_pct(totals, 0.9),
        hourly_median=[statistics.median(h) if h else 0.0 for h in hourly],
        hourly_low=[_pct(h, 0.1) for h in hourly], hourly_high=[_pct(h, 0.9) for h in hourly],
        first_visit_delay_min=statistics.median(delays) if delays else None,
        days=[x.day for x in days],
    )


def rainy_hours(store: Store, d: date, min_mm: float = 0.5) -> list[int]:
    return [r["hour"] for r in store.context_for(d.isoformat()) if (r["precip_mm"] or 0) >= min_mm]


def rain_factor(store: Store, profile: StoreProfile, end: date, weeks: int = 8,
                prior: float = 0.75, prior_weight: float = 5.0) -> tuple[float, int]:
    """How much rain suppresses traffic at this store, learned from its own history.

    For every open (weekday, hour) cell we compare rainy hours with the dry-hour median of
    the same cell. The mean ratio is shrunk toward a prior so a handful of rainy hours
    cannot produce an extreme estimate. Returns (factor, n_rainy_hours_used).
    """
    cells: dict[tuple[int, int], dict[str, list[int]]] = {}
    for i in range(1, weeks * 7 + 1):
        d = end - timedelta(days=i)
        dt = day_traffic(store, profile, d)
        if not dt.has_data:
            continue
        wet = set(rainy_hours(store, d))
        for h in dt.open_hours:
            c = cells.setdefault((d.weekday(), h), {"dry": [], "wet": []})
            c["wet" if h in wet else "dry"].append(dt.hourly[h])
    ratios = []
    for c in cells.values():
        if len(c["dry"]) >= 3 and c["wet"]:
            m = statistics.median(c["dry"])
            if m > 0:
                ratios += [w / m for w in c["wet"]]
    n = len(ratios)
    obs = statistics.fmean(ratios) if ratios else prior
    return (n * obs + prior_weight * prior) / (n + prior_weight), n


def fmt_hour(h: int) -> str:
    return datetime(2000, 1, 1, h % 24).strftime("%-I %p").lower()


def fmt_window(hours: list[int]) -> str:
    return f"{fmt_hour(hours[0])}-{fmt_hour(hours[-1] + 1)}" if hours else ""
