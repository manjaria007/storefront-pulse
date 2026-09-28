"""Quick local check: run insights directly (no MCP) and print what Alexa would say.

  PULSE_TODAY=2026-10-05 python scripts/ask.py
  PULSE_TODAY=2026-10-05 python scripts/ask.py explain_traffic_change day=saturday
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from storefront_pulse.config import Settings  # noqa: E402
from storefront_pulse.insights import REGISTRY, InsightContext  # noqa: E402
from storefront_pulse.profile import StoreProfile  # noqa: E402
from storefront_pulse.store.sqlite import Store  # noqa: E402

DEFAULT_RUN = [
    ("get_daily_recap", {}),
    ("get_daily_recap", {"day": "saturday"}),
    ("explain_traffic_change", {"day": "saturday"}),
    ("forecast_staffing", {"day": "saturday"}),
    ("get_after_hours_activity", {}),
]

s = Settings.from_env()
ctx = InsightContext(Store(s.db_path), StoreProfile.load(s.profile_path))
runs = DEFAULT_RUN
if len(sys.argv) > 1:
    runs = [(sys.argv[1], dict(a.split("=", 1) for a in sys.argv[2:]))]
for name, args in runs:
    t = time.perf_counter()
    r = REGISTRY[name](ctx).tool(**args)
    print(f"\n--- {name} {args}  [{(time.perf_counter() - t) * 1000:.0f} ms, confidence {r.confidence}]")
    print(r.speech)
    for f in r.facts:
        print(f"   {f.label}: {f.value}" + (f"  ({f.detail})" if f.detail else ""))
    for c in r.caveats:
        print(f"   ! {c}")
