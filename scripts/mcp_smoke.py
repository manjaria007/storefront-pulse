"""End-to-end MCP check over Streamable HTTP: handshake, list tools, call each, time round trips.

  python scripts/mcp_smoke.py http://127.0.0.1:8000/mcp
"""

import asyncio
import json
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
CALLS = [
    ("get_daily_recap", {"day": "yesterday"}),
    ("explain_traffic_change", {"day": "saturday"}),
    ("forecast_staffing", {"day": "saturday"}),
    ("get_after_hours_activity", {}),
    ("get_store_profile", {}),
]
BUDGET_MS = 500


async def main() -> int:
    async with streamablehttp_client(URL) as (read, write, _):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print(f"server={init.serverInfo.name} protocol={init.protocolVersion}")
            tools = (await s.list_tools()).tools
            print("tools:", ", ".join(t.name for t in tools))
            worst = 0.0
            for name, args in CALLS:
                t = time.perf_counter()
                res = await s.call_tool(name, args)
                ms = (time.perf_counter() - t) * 1000
                worst = max(worst, ms)
                body = res.structuredContent or json.loads(res.content[0].text)
                body = body.get("result", body)
                print(f"\n[{name} {args}] {ms:.0f} ms  error={res.isError}")
                print("  ", body.get("speech", body))
            print(f"\nslowest round trip: {worst:.0f} ms (budget {BUDGET_MS} ms)")
            return 0 if worst < BUDGET_MS and init.protocolVersion == "2025-11-25" else 1


sys.exit(asyncio.run(main()))
