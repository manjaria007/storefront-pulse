import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from storefront_pulse.config import Settings
from storefront_pulse.ingest.pipeline import accept_webhook, process_pending
from storefront_pulse.insights import REGISTRY, InsightContext
from storefront_pulse.insights.base import resolve_date
from storefront_pulse.profile import StoreProfile
from storefront_pulse.ring import webhook
from storefront_pulse.simulator.traffic import build_world
from storefront_pulse.store.sqlite import Store
from storefront_pulse.vision.counters import MotionCounter, SimulatedCounter, parse_model_json

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "profiles" / "demo_cafe.yml"
KEY = "test-key"
TODAY = date(2026, 10, 5)  # Monday after the demo week


# ------------------------------------------------------------------ ring webhook
def test_signature_roundtrip_and_tamper():
    body = webhook.encode(webhook.build_event(event_type="motion_detected", device_id="d1", ts_ms=1))
    sig = webhook.sign(KEY, body)
    assert sig.startswith("sha256=") and webhook.verify(KEY, body, sig)
    assert not webhook.verify(KEY, body + b" ", sig)
    assert not webhook.verify(KEY, body, None)
    with pytest.raises(webhook.SignatureError):
        webhook.parse(body, signing_key=KEY, signature="sha256=00")


def test_parse_v11_payload():
    body = webhook.encode(webhook.build_event(event_type="motion_detected", device_id="dev", ts_ms=1234, sub_type="human"))
    ev = webhook.parse(body)
    assert (ev.type, ev.device_id, ev.ts_ms, ev.sub_type, ev.is_traffic) == ("motion_detected", "dev", 1234, "human", True)


def test_dedupe(tmp_path):
    store = Store(tmp_path / "t.db")
    body = webhook.encode(webhook.build_event(event_type="button_press", device_id="d", ts_ms=5))
    sig = webhook.sign(KEY, body)
    assert accept_webhook(store, body, sig, KEY)[0] == "accepted"
    assert accept_webhook(store, body, sig, KEY)[0] == "duplicate"


# ------------------------------------------------------------------ counters
def test_motion_counter_rules():
    c = MotionCounter()
    assert c.count("e", "motion_detected", "d", 0, None).people == 1
    assert c.count("e", "motion_detected", "d", 0, "vehicle").people == 0
    assert c.count("e", "button_press", "d", 0, None).people == 0


def test_parse_model_json_tolerates_prose():
    assert parse_model_json('Sure! {"people_entering": 2, "confidence": 0.8}')["people_entering"] == 2


# ------------------------------------------------------------------ dates
@pytest.mark.parametrize("text,prefer,expected", [
    ("yesterday", "past", date(2026, 10, 4)),
    ("saturday", "past", date(2026, 10, 3)),
    ("saturday", "future", date(2026, 10, 10)),
    ("next saturday", "past", date(2026, 10, 10)),
    ("monday", "past", date(2026, 9, 28)),
    ("2026-09-30", "past", date(2026, 9, 30)),
])
def test_resolve_date(text, prefer, expected):
    assert resolve_date(text, TODAY, prefer) == expected


# ------------------------------------------------------------------ end to end on the demo world
@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("world")
    profile = StoreProfile.load(PROFILE)
    world = build_world(profile, date(2026, 10, 4), weeks=6, seed=42)
    (tmp / "truth.json").write_text(json.dumps(world.truth))
    store = Store(tmp / "pulse.db")
    for p in world.payloads:
        body = webhook.encode(p)
        assert accept_webhook(store, body, webhook.sign(KEY, body), KEY)[0] == "accepted"
    store.put_context(world.context)
    assert process_pending(store, SimulatedCounter(tmp / "truth.json"), limit=10**7) == len(world.payloads)
    return InsightContext(store, profile, today_override=TODAY)


def run(ctx, name, **kw):
    return REGISTRY[name](ctx).tool(**kw)


def test_recap_flags_slow_saturday(ctx):
    r = run(ctx, "get_daily_recap", day="saturday")
    assert r.card["status"] == "below" and r.confidence == "high"


def test_explain_finds_rain_and_late_opening(ctx):
    r = run(ctx, "explain_traffic_change", day="saturday")
    names = {f["factor"] for f in r.card["factors"]}
    assert {"rain", "late opening"} <= names
    assert set(r.card["gap_window"]) <= set(range(8, 13))
    assert any("not proof" in c for c in r.caveats)


def test_explain_normal_day_does_not_invent_causes(ctx):
    r = run(ctx, "explain_traffic_change", day="sunday")
    assert "normal range" in r.speech and not r.card


def test_forecast_flags_saturday_shortage(ctx):
    r = run(ctx, "forecast_staffing", day="saturday")
    assert r.window["date"] == "2026-10-10"
    assert max(r.card["staff_needed"]) > r.card["staff_scheduled"]


def test_forecast_lowers_rainy_hours(ctx):
    r = run(ctx, "forecast_staffing", day="wednesday")
    assert r.card["rain_hours"] and "Rain is forecast" in r.speech


def test_after_hours_finds_wednesday_night(ctx):
    r = run(ctx, "get_after_hours_activity")
    times = [i["time"] for i in r.card["items"] if i["night_of"] == "Wednesday"]
    assert {"23:42", "23:47"} <= set(times)


def test_all_insights_are_fast(ctx):
    import time
    for name in REGISTRY:
        t = time.perf_counter()
        run(ctx, name)
        assert (time.perf_counter() - t) < 0.3, name


# ------------------------------------------------------------------ MCP tool surface
def test_mcp_tools_registered_and_callable(ctx, tmp_path, monkeypatch):
    from storefront_pulse import mcp_server

    monkeypatch.setenv("PULSE_TODAY", TODAY.isoformat())
    s = Settings.from_env()
    s = Settings(**{**s.__dict__, "db_path": Path(ctx.store._conn.execute("PRAGMA database_list").fetchone()[2]),
                    "profile_path": PROFILE})
    server = mcp_server.build_server(s)
    tools = {t.name for t in asyncio.run(server.list_tools())}
    assert set(REGISTRY) | {"get_store_profile", "update_store_profile"} <= tools
    out = asyncio.run(server.call_tool("explain_traffic_change", {"day": "saturday"}))
    payload = out[1] if isinstance(out, tuple) else out
    text = json.dumps(payload, default=str)
    assert "late opening" in text
