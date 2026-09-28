"""The Insight protocol: how every storefront scenario plugs in.

An insight is one question a shop owner can ask ("how was yesterday?", "why was Saturday
slow?"). Each insight:

1. subclasses `Insight`, sets `name` / `title` / `description`,
2. implements `tool(...)` with *typed keyword arguments only* (these become the MCP tool's
   input schema that Alexa+ sees), returning `InsightResult`,
3. is decorated with `@register`.

The MCP server exposes every registered insight as its own tool automatically, so adding a
new storefront scenario is one new file in this package. Every result has the same shape:
a short `speech` line for voice, structured `facts`, an honest `confidence`, `caveats`,
and a `card` payload for the on-screen view.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

from ..profile import StoreProfile
from ..store.sqlite import Store

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class Fact:
    label: str
    value: str
    detail: str | None = None


@dataclass
class InsightResult:
    insight: str
    speech: str                      # 1-2 sentences Alexa can say out loud
    headline: str                    # card title
    facts: list[Fact] = field(default_factory=list)
    confidence: str = "medium"       # high | medium | low
    confidence_reason: str = ""
    caveats: list[str] = field(default_factory=list)
    card: dict[str, Any] = field(default_factory=dict)
    window: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InsightContext:
    store: Store
    profile: StoreProfile
    today_override: date | None = None

    @property
    def today(self) -> date:
        if self.today_override:
            return self.today_override
        env = os.getenv("PULSE_TODAY")
        if env:
            return date.fromisoformat(env)
        return datetime.now(self.profile.tz).date()


class Insight:
    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]

    def __init__(self, ctx: InsightContext):
        self.ctx = ctx

    def tool(self, **kwargs) -> InsightResult:  # pragma: no cover - overridden
        raise NotImplementedError


REGISTRY: dict[str, type[Insight]] = {}


def register(cls: type[Insight]) -> type[Insight]:
    if cls.name in REGISTRY:
        raise ValueError(f"duplicate insight name {cls.name}")
    REGISTRY[cls.name] = cls
    return cls


def resolve_date(text: str | None, today: date, prefer: str = "past") -> date:
    """Turn 'yesterday', 'saturday', 'last saturday', 'next friday' or ISO dates into a date.

    prefer='past' resolves bare weekday names to the most recent one (before today);
    prefer='future' resolves them to the next one (after today).
    """
    if not text:
        return today - timedelta(days=1) if prefer == "past" else today + timedelta(days=1)
    t = text.strip().lower()
    if t == "today":
        return today
    if t in ("yesterday", "last night"):
        return today - timedelta(days=1)
    if t == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(t)
    except ValueError:
        pass
    m = re.fullmatch(r"(last|this|next)?\s*(" + "|".join(WEEKDAYS) + r")", t)
    if m:
        qual, wd = m.group(1), WEEKDAYS.index(m.group(2))
        direction = "future" if qual == "next" else "past" if qual == "last" else prefer
        if direction == "past":
            delta = (today.weekday() - wd) % 7 or 7
            return today - timedelta(days=delta)
        delta = (wd - today.weekday()) % 7 or 7
        return today + timedelta(days=delta)
    raise ValueError(f"Could not understand the date '{text}'. Use a weekday, 'yesterday', or YYYY-MM-DD.")


def pct(a: float, b: float) -> float:
    return 0.0 if b == 0 else (a - b) / b * 100
