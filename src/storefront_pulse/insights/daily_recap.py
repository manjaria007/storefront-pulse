from __future__ import annotations

from datetime import datetime

from ..analytics.traffic import US_HOLIDAYS, baseline, day_traffic, fmt_hour
from .base import Fact, Insight, InsightResult, pct, register, resolve_date


def confidence_for(n_days: int, offline_min: int) -> tuple[str, str]:
    if offline_min >= 30:
        return "low", f"camera was offline for about {offline_min} minutes"
    if n_days >= 4:
        return "high", f"compared with {n_days} previous same weekdays"
    if n_days >= 2:
        return "medium", f"only {n_days} previous same weekdays to compare with"
    return "low", "not enough history yet to know what is typical"


@register
class DailyRecap(Insight):
    name = "get_daily_recap"
    title = "Daily recap"
    description = (
        "Summarize the shop's foot traffic for one day: estimated visits, how that compares with a "
        "typical day of the same weekday, the busiest hour, and any after-hours activity. "
        "Use for questions like 'how was yesterday?', 'how busy were we on Saturday?', or a morning briefing. "
        "`day` accepts 'yesterday', 'today', a weekday name like 'saturday', or YYYY-MM-DD."
    )

    def tool(self, day: str = "yesterday") -> InsightResult:
        ctx = self.ctx
        d = resolve_date(day, ctx.today, "past")
        dt = day_traffic(ctx.store, ctx.profile, d)
        label = d.strftime("%A, %b %-d")
        if not dt.has_data:
            return InsightResult(self.name, f"I don't have any camera data for {label}.",
                                 f"No data for {label}", confidence="low",
                                 confidence_reason="no events recorded", window={"date": d.isoformat()})
        bl = baseline(ctx.store, ctx.profile, d)
        if not dt.open_hours:
            return InsightResult(self.name, f"The shop was closed on {label}. "
                                 f"I saw {len(dt.after_hours)} visits at the door.", f"{label}: closed",
                                 window={"date": d.isoformat()})
        status = ("below" if dt.total_open < bl.total_low else
                  "above" if dt.total_open > bl.total_high else "typical") if bl.n_days else "unknown"
        diff = pct(dt.total_open, bl.total_median)
        busiest = max(dt.open_hours, key=lambda h: dt.hourly[h])
        wd = d.strftime("%A")
        conf, why = confidence_for(bl.n_days, dt.device_offline_minutes)

        if status == "typical":
            cmp_txt = f"a normal {wd} (typical is {bl.total_low:.0f} to {bl.total_high:.0f})"
        elif status == "unknown":
            cmp_txt = "and I don't have enough history yet to say what's typical"
        else:
            cmp_txt = f"{abs(diff):.0f}% {status} a typical {wd}"
        speech = (f"{label} had about {dt.total_open} visits, {cmp_txt}. "
                  f"Busiest hour was {fmt_hour(busiest)} with {dt.hourly[busiest]}.")
        if dt.after_hours:
            speech += f" There {'was' if len(dt.after_hours) == 1 else 'were'} {len(dt.after_hours)} after-hours visit{'s' if len(dt.after_hours) != 1 else ''} at the door."

        facts = [
            Fact("Visits", f"~{dt.total_open}", f"typical {wd}: {bl.total_low:.0f}-{bl.total_high:.0f} (median {bl.total_median:.0f})"),
            Fact("Versus typical", f"{diff:+.0f}%", status),
            Fact("Busiest hour", fmt_hour(busiest), f"{dt.hourly[busiest]} visits"),
            Fact("First visitor", dt.first_visit.strftime("%-I:%M %p") if dt.first_visit else "none"),
            Fact("After-hours visits", str(len(dt.after_hours))),
        ]
        caveats = ["Visit counts are estimated from camera events and snapshots, not a door counter."]
        if d.isoformat() in US_HOLIDAYS:
            caveats.append(f"{US_HOLIDAYS[d.isoformat()]} - holidays often differ from normal days.")
        if status == "below":
            caveats.append("Ask me 'why was it slow?' and I'll look for what lines up with the drop.")
        card = {
            "type": "day_traffic",
            "hours": dt.open_hours,
            "actual": [dt.hourly[h] for h in dt.open_hours],
            "typical": [round(bl.hourly_median[h], 1) for h in dt.open_hours],
            "typical_low": [round(bl.hourly_low[h], 1) for h in dt.open_hours],
            "typical_high": [round(bl.hourly_high[h], 1) for h in dt.open_hours],
            "status": status,
        }
        return InsightResult(self.name, speech, f"{label}: {status} day", facts, conf, why, caveats, card,
                             {"date": d.isoformat(), "compared_with": ",".join(x.isoformat() for x in bl.days),
                              "generated_at": datetime.now(ctx.profile.tz).isoformat(timespec="seconds")})
