"""pulse-sim: build a simulated storefront world and push it through the real ingest path.

  pulse-sim build --out demo --end 2026-10-04 --weeks 6
  pulse-sim load  --data demo --db pulse.db               # in-process, signed + verified
  pulse-sim load  --data demo --post http://localhost:8080/ring/webhook
  pulse-sim live  --post http://localhost:8080/ring/webhook  # one event "now" (live demo)
"""

from __future__ import annotations

import argparse
import json
import os
import time as _time
from datetime import date
from pathlib import Path

import httpx

from ..ingest.pipeline import accept_webhook, process_pending
from ..profile import StoreProfile
from ..ring.webhook import build_event, encode, sign
from ..store.sqlite import Store
from ..vision.counters import SimulatedCounter
from .traffic import DEFAULT_DEVICE, build_world

SIM_KEY = "sim-signing-key"


def _key() -> str:
    return os.getenv("RING_HMAC_KEY", SIM_KEY)


def cmd_build(a) -> None:
    profile = StoreProfile.load(Path(a.profile))
    w = build_world(profile, date.fromisoformat(a.end), weeks=a.weeks, seed=a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "events.jsonl").open("w") as f:
        for p in w.payloads:
            f.write(json.dumps(p) + "\n")
    (out / "truth.json").write_text(json.dumps(w.truth))
    (out / "context.json").write_text(json.dumps(w.context))
    (out / "storylines.json").write_text(json.dumps(w.storyline_notes, indent=2))
    real = sum(1 for v in w.truth.values() if v > 0)
    print(f"built {len(w.payloads)} events ({real} with visitors) -> {out}/")
    for d, n in w.storyline_notes.items():
        print(f"  {d}: {'; '.join(n)}")


def load_world(data: Path, store: Store, post: str | None = None) -> dict:
    key = _key()
    stats = {"accepted": 0, "duplicate": 0, "ignored": 0}
    client = httpx.Client(timeout=10) if post else None
    for line in (data / "events.jsonl").open():
        body = encode(json.loads(line))
        sig = sign(key, body)
        if client:
            r = client.post(post, content=body, headers={"X-Signature": sig, "Content-Type": "application/json"})
            r.raise_for_status()
            stats[r.json()["status"]] += 1
        else:
            status, _ = accept_webhook(store, body, sig, key)
            stats[status] += 1
    store.put_context([tuple(r) for r in json.loads((data / "context.json").read_text())])
    if not client:
        stats["counted"] = process_pending(store, SimulatedCounter(data / "truth.json"), limit=10**7)
    return stats


def cmd_load(a) -> None:
    store = Store(a.db)
    print(load_world(Path(a.data), store, a.post))


def cmd_live(a) -> None:
    ts = int(_time.time() * 1000)
    body = encode(build_event(event_type=a.type, device_id=a.device, ts_ms=ts))
    r = httpx.post(a.post, content=body, headers={"X-Signature": sign(_key(), body),
                                                  "Content-Type": "application/json"})
    print(r.status_code, r.text)


def main() -> None:
    ap = argparse.ArgumentParser(prog="pulse-sim")
    sub = ap.add_subparsers(required=True)
    b = sub.add_parser("build")
    b.add_argument("--out", default="demo")
    b.add_argument("--end", default="2026-10-04")
    b.add_argument("--weeks", type=int, default=6)
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--profile", default="profiles/demo_cafe.yml")
    b.set_defaults(fn=cmd_build)
    ld = sub.add_parser("load")
    ld.add_argument("--data", default="demo")
    ld.add_argument("--db", default="pulse.db")
    ld.add_argument("--post", default=None)
    ld.set_defaults(fn=cmd_load)
    lv = sub.add_parser("live")
    lv.add_argument("--post", required=True)
    lv.add_argument("--device", default=DEFAULT_DEVICE)
    lv.add_argument("--type", default="motion_detected")
    lv.set_defaults(fn=cmd_live)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
