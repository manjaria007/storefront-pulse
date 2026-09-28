from __future__ import annotations

from datetime import datetime, timedelta

from ..analytics.traffic import (US_HOLIDAYS, baseline, day_traffic, fmt_window, rain_factor,
                                 rainy_hours)
from .base import Fact, Insight, InsightResult, pct, register, resolve_date
from .daily_recap import confidence_for

LATE_THRESHOLD_MIN = 20


def _worst_window(gaps: dict[int, float], sign: int) -> list[int]:
    """Contiguous run of hours with the largest total gap in the given direction (Kadane)."""
    best, best_run, cur, run = 0.0, [], 0.0, []
    for h in sorted(gaps):
        v = sign * gaps[h]
        if cur + v > 0 and (not run or run[-1] == h - 1):
            cur, run = cur + v, run + [h]
        else:
            cur, run = (v, [h]) if v > 0 else (0.0, [])
        if cur > best:
            best, best_run = cur, list(run)
    return best_run


@register
class ExplainTrafficChange(Insight):
    name = "explain_traffic_change"
    title = "Why was traffic different?"
    description = (
        "Explain why foot traffic on a given day was lower or higher than a typical day of the same "
        "weekday. Finds when the gap happened and checks what lines up with it: late opening, rain, "
        "holidays, and camera outages, with an estimate of how many visits each accounts for. "
        "Use for 'why was Saturday slow?', 'why were we so busy yesterday?'. "
        "`day` accepts 'yesterday', a weekday name, or YYYY-MM-DD."
    )

    def tool(self, day: str = "yesterday") -> InsightResult:
        ctx = self.ctx
        d = resolve_date(day, ctx.today, "past")
        label = d.strftime("%A")
        dt = day_traffic(ctx.store, ctx.profile, d)
        bl = baseline(ctx.store, ctx.profile, d)
        if not dt.has_data or not dt.open_hours:
            return InsightResult(self.name, f"I don't have traffic data for {d:%A, %b %-d} to explain.",
                                 "Nothing to explain", confidence="low", window={"date": d.isoformat()})
        conf, why = confidence_for(bl.n_days, dt.device_offline_minutes)
        shortfall = bl.total_median - dt.total_open
        diff = pct(dt.total_open, bl.total_median)
        normal = bl.total_low <= dt.total_open <= bl.total_high
        if normal:
            return InsightResult(
                self.name,
                f"{label} was within the normal range: about {dt.total_open} visits against a typical "
                f"{bl.total_low:.0f} to {bl.total_high:.0f}, so there's no real change to explain.",
                f"{label}: normal", [Fact("Visits", f"~{dt.total_open}", f"typical {bl.total_low:.0f}-{bl.total_high:.0f}")],
                conf, why, window={"date": d.isoformat()})

        sign = 1 if shortfall > 0 else -1
        gaps = {h: dt.hourly[h] - bl.hourly_median[h] for h in dt.open_hours}
        # hours clearly outside their typical band drive the window; fall back to the biggest run
        outside = {h: g for h, g in gaps.items()
                   if (sign > 0 and dt.hourly[h] < bl.hourly_low[h]) or (sign < 0 and dt.hourly[h] > bl.hourly_high[h])}
        window = _worst_window(outside, -sign) if outside else []
        window = window or _worst_window(gaps, -sign)
        window_gap = sum(gaps[h] for h in window)

        # expected hourly visits, then subtract each factor in turn so effects don't double count
        expected = {h: bl.hourly_median[h] for h in dt.open_hours}
        factors: list[dict] = []

        if dt.device_offline_minutes >= 15:
            factors.append({"factor": "camera offline", "visits": None,
                            "detail": f"camera offline ~{dt.device_offline_minutes} min; some visits were not seen"})

        oc = ctx.profile.open_close(d.weekday())
        if sign > 0 and oc and dt.first_visit:
            opened = datetime.combine(d, oc[0], ctx.profile.tz)
            typical_delay = bl.first_visit_delay_min if bl.first_visit_delay_min is not None else 10.0
            actual_delay = (dt.first_visit - opened).total_seconds() / 60
            late_by = actual_delay - typical_delay
            if late_by >= LATE_THRESHOLD_MIN:
                lost, t = 0.0, opened + timedelta(minutes=typical_delay)
                while t < dt.first_visit:
                    h = t.hour
                    step_end = min(dt.first_visit, t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
                    frac = (step_end - t).total_seconds() / 3600
                    if h in expected:
                        take = expected[h] * frac
                        lost += take
                        expected[h] -= take
                    t = step_end
                factors.append({"factor": "late opening", "visits": round(lost),
                                "detail": f"first visitor at {dt.first_visit:%-I:%M %p}, about {late_by:.0f} min later than usual"})

        wet = [h for h in rainy_hours(ctx.store, d) if h in dt.open_hours]
        if wet and sign > 0:
            rf, n = rain_factor(ctx.store, ctx.profile, d)
            lost = sum(expected[h] * (1 - rf) for h in wet)
            for h in wet:
                expected[h] *= rf
            factors.append({"factor": "rain", "visits": round(lost),
                            "detail": f"rain {fmt_window(wet)}; at this shop rain usually cuts traffic ~{(1 - rf) * 100:.0f}% "
                                      f"(learned from {n} rainy hours)"})

        if d.isoformat() in US_HOLIDAYS:
            factors.append({"factor": "holiday", "visits": None, "detail": US_HOLIDAYS[d.isoformat()]})

        explained = sum(f["visits"] or 0 for f in factors)
        gap_abs = abs(shortfall)
        if explained > gap_abs and explained > 0:
            scale = gap_abs / explained
            for f in factors:
                if f["visits"]:
                    f["visits"] = round(f["visits"] * scale)
            explained = gap_abs
        residual = gap_abs - explained

        direction = "below" if sign > 0 else "above"
        speech = (f"{label} had about {dt.total_open} visits, {abs(diff):.0f}% {direction} a typical {label}, "
                  f"mostly between {fmt_window(window)}.")
        named = [f for f in factors if f["visits"]]
        if named:
            parts = [f"{f['factor']} (about {f['visits']} visits)" for f in named]
            speech += f" What lines up with it: {' and '.join(parts)}."
            speech += (" That covers most of the gap." if residual <= 0.25 * gap_abs
                       else f" About {residual:.0f} visits aren't explained by anything I can see.")
        elif sign < 0:
            speech += " Nothing I track explains the extra traffic. Did you run a promotion or was there an event nearby?"
        else:
            speech += " I couldn't match it to rain, a late opening, a holiday or a camera outage."

        facts = [Fact("Visits", f"~{dt.total_open}", f"typical {bl.total_median:.0f} ({bl.total_low:.0f}-{bl.total_high:.0f})"),
                 Fact("Change", f"{diff:+.0f}%", f"{window_gap:+.0f} visits during {fmt_window(window)}")]
        facts += [Fact(f["factor"].capitalize(), f"~{f['visits']} visits" if f["visits"] else "noted", f["detail"]) for f in factors]
        if factors:
            facts.append(Fact("Unexplained", f"~{residual:.0f} visits"))
        caveats = [
            "These are factors that line up with the change, not proof of cause.",
            "When factors overlap in time (e.g. rain during a late opening), their split is an estimate.",
        ]
        if named:
            caveats.append("To confirm the opening-time effect, compare with a day that opened on time in similar weather.")
        card = {"type": "explain", "hours": dt.open_hours,
                "actual": [dt.hourly[h] for h in dt.open_hours],
                "typical": [round(bl.hourly_median[h], 1) for h in dt.open_hours],
                "gap_window": window, "factors": factors, "residual": round(residual)}
        return InsightResult(self.name, speech, f"{label}: {abs(diff):.0f}% {direction} typical", facts, conf, why,
                             caveats, card, {"date": d.isoformat()})
