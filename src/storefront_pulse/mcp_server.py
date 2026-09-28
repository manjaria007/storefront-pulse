"""Storefront Pulse MCP server (spec 2025-11-25, Streamable HTTP).

Every registered insight becomes an MCP tool automatically (see insights/base.py), plus two
small tools to read and update the store profile. Tools only read precomputed/stored data,
so each call stays far below Alexa+'s 500 ms round-trip budget.

Run:  PULSE_DB=pulse.db pulse-mcp            -> http://127.0.0.1:8000/mcp
"""

from __future__ import annotations

import inspect
import os
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .config import Settings
from .insights import REGISTRY, InsightContext
from .profile import DAYS, StoreProfile
from .store.sqlite import Store

INSTRUCTIONS = (
    "Storefront Pulse turns the shop's Ring entrance camera into foot-traffic insight for the owner. "
    "Visit numbers are estimates from camera events and snapshots. Each tool returns `speech` (say this), "
    "`facts` and a `card` (show these on screen), plus `confidence` and `caveats`. Never present factors "
    "from explain_traffic_change as proven causes; say they 'line up with' the change."
)


def _as_tool(insight) -> Any:
    sig = inspect.signature(insight.tool)

    def fn(**kwargs):
        return insight.tool(**kwargs).to_dict()

    fn.__signature__ = sig.replace(return_annotation=dict[str, Any])
    fn.__name__ = insight.name
    fn.__doc__ = insight.description
    return fn


def build_server(settings: Settings | None = None, host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    ctx = InsightContext(store, StoreProfile.load(settings.profile_path))

    allowed = os.getenv("PULSE_MCP_ALLOWED_HOSTS")  # e.g. "your-tunnel.trycloudflare.com"
    security = (TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                          allowed_hosts=[h.strip() for h in allowed.split(",")] + ["127.0.0.1:*", "localhost:*"])
                if allowed else None)
    mcp = FastMCP("storefront-pulse", instructions=INSTRUCTIONS, host=host, port=port,
                  stateless_http=True, transport_security=security)

    for cls in REGISTRY.values():
        ins = cls(ctx)
        mcp.add_tool(_as_tool(ins), name=cls.name, title=cls.title, description=cls.description,
                     annotations=ToolAnnotations(title=cls.title, readOnlyHint=True, openWorldHint=False))

    @mcp.tool(title="Store profile", annotations=ToolAnnotations(readOnlyHint=True))
    def get_store_profile() -> dict[str, Any]:
        """Get the shop's name, opening hours per day, scheduled staff per day and staffing assumptions."""
        p = ctx.profile
        return {"name": p.name, "timezone": p.timezone, "hours": p.hours, "scheduled_staff": p.scheduled_staff,
                "visits_per_staff_hour": p.visits_per_staff_hour, "min_staff": p.min_staff}

    @mcp.tool(title="Update store profile", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                                                        idempotentHint=True))
    def update_store_profile(day: str | None = None, open_time: str | None = None, close_time: str | None = None,
                             closed: bool = False, scheduled_staff: int | None = None,
                             visits_per_staff_hour: int | None = None) -> dict[str, Any]:
        """Change opening hours or staffing for a weekday ('monday'..'sunday'; times as HH:MM, 24h),
        mark a day closed, or change how many visits one staff member can handle per hour."""
        p = ctx.profile
        changes = []
        if day:
            key = day.strip().lower()[:3]
            if key not in DAYS:
                raise ValueError("day must be a weekday name like 'saturday'")
            if closed:
                p.hours[key] = None
                changes.append(f"{key} closed")
            elif open_time or close_time:
                cur = p.hours.get(key) or ["09:00", "17:00"]
                p.hours[key] = [open_time or cur[0], close_time or cur[1]]
                changes.append(f"{key} hours {p.hours[key][0]}-{p.hours[key][1]}")
            if scheduled_staff is not None:
                p.scheduled_staff[key] = int(scheduled_staff)
                changes.append(f"{key} staff {scheduled_staff}")
        if visits_per_staff_hour:
            p.visits_per_staff_hour = int(visits_per_staff_hour)
            changes.append(f"{visits_per_staff_hour} visits per staff-hour")
        if changes:
            p.save(settings.profile_path)
        return {"speech": ("Updated: " + ", ".join(changes) + ".") if changes else "Nothing to change.",
                "changes": changes}

    return mcp


def main() -> None:
    port = int(os.getenv("PULSE_MCP_PORT", "8000"))
    host = os.getenv("PULSE_MCP_HOST", "127.0.0.1")
    build_server(host=host, port=port).run(transport="streamable-http")


if __name__ == "__main__":
    main()
