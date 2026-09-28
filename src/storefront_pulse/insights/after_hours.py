from __future__ import annotations

from datetime import timedelta

from ..analytics.traffic import day_traffic
from .base import Fact, Insight, InsightResult, register, resolve_date


@register
class AfterHoursActivity(Insight):
    name = "get_after_hours_activity"
    title = "After-hours activity"
    description = (
        "List people seen at the shop entrance outside opening hours (passers-by excluded), with times and "
        "snapshot references. Use for 'anything happen overnight?', 'was anyone at the door after we closed?'. "
        "Give `day` for one night, or leave it empty to cover the last `days` nights (default 7)."
    )

    MIN_CONFIDENCE = 0.6

    def tool(self, day: str | None = None, days: int = 7) -> InsightResult:
        ctx = self.ctx
        if day:
            dates = [resolve_date(day, ctx.today, "past")]
        else:
            days = max(1, min(int(days), 31))
            dates = [ctx.today - timedelta(days=i) for i in range(days, 0, -1)]
        items, hidden = [], 0
        for d in dates:
            for a in day_traffic(ctx.store, ctx.profile, d).after_hours:
                if a["confidence"] < self.MIN_CONFIDENCE:
                    hidden += 1
                    continue
                items.append({"date": d.isoformat(), "night_of": d.strftime("%A"), **a,
                              "snapshot": f"/snapshots/{a['event_id']}"})
        span = f"{dates[0]:%A} night" if len(dates) == 1 else f"the last {len(dates)} nights"
        if not items:
            speech = f"No one came to the door after hours in {span}."
        else:
            by_night: dict[str, list[str]] = {}
            for i in items:
                by_night.setdefault(i["night_of"], []).append(i["time"].replace(" (after midnight)", ""))
            parts = [f"{n} night at {', '.join(ts)}" for n, ts in by_night.items()]
            speech = (f"{len(items)} after-hours visit{'s' if len(items) != 1 else ''} in {span}: "
                      f"{'; '.join(parts)}. The snapshots are on your screen.")
        facts = [Fact(f"{i['night_of']} {i['time']}", f"{i['people']} person" + ("s" if i["people"] > 1 else ""),
                      f"confidence {i['confidence']:.0%}") for i in items]
        caveats = ["A visit here means someone was detected at the entrance; it is not an alarm or an intrusion alert."]
        if hidden:
            caveats.append(f"{hidden} low-confidence detection{'s' if hidden != 1 else ''} (likely passers-by) not listed.")
        return InsightResult(self.name, speech, f"After hours: {len(items)} visit{'s' if len(items) != 1 else ''}",
                             facts, "high" if items else "medium", "from camera events outside opening hours",
                             caveats,
                             {"type": "after_hours", "items": items},
                             {"from": dates[0].isoformat(), "to": dates[-1].isoformat()})
