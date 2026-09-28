from __future__ import annotations

import math

from ..analytics.traffic import baseline, fmt_window, open_hours, rain_factor, rainy_hours
from ..profile import DAYS
from .base import Fact, Insight, InsightResult, register, resolve_date


def _runs(hours: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for h in hours:
        if runs and runs[-1][-1] == h - 1:
            runs[-1].append(h)
        else:
            runs.append([h])
    return runs


@register
class ForecastStaffing(Insight):
    name = "forecast_staffing"
    title = "Staffing forecast"
    description = (
        "Forecast foot traffic for an upcoming day, hour by hour, using the same weekday's recent history "
        "and the weather forecast, then compare it with the scheduled staff to flag hours that look "
        "under- or over-staffed. Use for 'how should I staff Saturday?', 'will Friday be busy?'. "
        "`day` accepts 'tomorrow', a weekday name (resolved to the next one), or YYYY-MM-DD."
    )

    def tool(self, day: str = "tomorrow") -> InsightResult:
        ctx = self.ctx
        d = resolve_date(day, ctx.today, "future")
        label = d.strftime("%A, %b %-d")
        hours = open_hours(ctx.profile, d)
        if not hours:
            return InsightResult(self.name, f"The shop is closed on {label}.", f"{label}: closed",
                                 window={"date": d.isoformat()})
        bl = baseline(ctx.store, ctx.profile, d, lookback_weeks=7)
        if bl.n_days < 2:
            return InsightResult(self.name, f"I need at least two past {d:%A}s of data to forecast {label}.",
                                 "Not enough history", confidence="low", window={"date": d.isoformat()})
        wet = [h for h in rainy_hours(ctx.store, d) if h in hours]
        rf, _ = rain_factor(ctx.store, ctx.profile, ctx.today) if wet else (1.0, 0)
        cap = ctx.profile.visits_per_staff_hour
        scheduled = ctx.profile.scheduled_staff.get(DAYS[d.weekday()], 2)

        exp, low, high, need = {}, {}, {}, {}
        for h in hours:
            m = rf if h in wet else 1.0
            exp[h], low[h], high[h] = bl.hourly_median[h] * m, bl.hourly_low[h] * m, bl.hourly_high[h] * m
            need[h] = max(ctx.profile.min_staff, math.ceil(exp[h] / cap))
        total = sum(exp.values())
        peak = max(hours, key=lambda h: exp[h])
        short = _runs([h for h in hours if need[h] > scheduled])
        spare = [r for r in _runs([h for h in hours if need[h] < scheduled]) if len(r) >= 3]

        speech = (f"For {label} I expect about {total:.0f} visits, busiest around {fmt_window([peak])} "
                  f"with roughly {exp[peak]:.0f} an hour.")
        if wet:
            speech += f" Rain is forecast {fmt_window(wet)}, which usually slows things down, so I've lowered those hours."
        if short:
            extra = max(need[h] for r in short for h in r) - scheduled
            speech += (f" With {scheduled} staff scheduled, you'll likely be short {extra} "
                       f"person{'s' if extra > 1 else ''} {' and '.join(fmt_window(r) for r in short)}.")
        else:
            speech += f" {scheduled} staff should cover it."
        if spare:
            speech += f" You could run lighter {' and '.join(fmt_window(r) for r in spare)}."

        facts = [Fact("Expected visits", f"~{total:.0f}", f"range {sum(low.values()):.0f}-{sum(high.values()):.0f}"),
                 Fact("Peak", fmt_window([peak]), f"~{exp[peak]:.0f} visits/hour"),
                 Fact("Scheduled staff", str(scheduled), f"assumes {cap} visits per staff-hour")]
        facts += [Fact("Add staff", fmt_window(r), f"need {max(need[h] for h in r)}") for r in short]
        facts += [Fact("Could run lighter", fmt_window(r), f"need {max(need[h] for h in r)}") for r in spare]
        caveats = [f"Based on the last {bl.n_days} {d:%A}s plus the weather forecast; events, promotions or "
                   "holidays nearby aren't included unless you tell me.",
                   f"Staffing math assumes one person handles about {cap} visits an hour; change it in the store profile."]
        conf = "high" if bl.n_days >= 5 else "medium"
        card = {"type": "forecast", "hours": hours, "expected": [round(exp[h], 1) for h in hours],
                "low": [round(low[h], 1) for h in hours], "high": [round(high[h], 1) for h in hours],
                "staff_needed": [need[h] for h in hours], "staff_scheduled": scheduled, "rain_hours": wet}
        return InsightResult(self.name, speech, f"{label}: staffing plan", facts, conf,
                             f"history from {bl.n_days} past {d:%A}s", caveats, card, {"date": d.isoformat()})
