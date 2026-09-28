"""People counters: turn one Ring traffic event into an estimated number of visitors.

Three interchangeable strategies (select with PULSE_COUNTER):

- ``bedrock``   fetch the Ring snapshot at the event time and ask a Bedrock vision model
                how many people are *entering* (passers-by on the sidewalk count as 0).
- ``simulated`` for the demo world: reads the simulator's ground truth and adds realistic
                counting error, so the analytics see the same kind of noise as production.
- ``motion``    no vision at all: one motion event = one visit, low confidence. Fallback.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..ring.client import RingClient


@dataclass(frozen=True)
class CountResult:
    people: int
    confidence: float
    method: str
    note: str | None = None


class PeopleCounter(Protocol):
    def count(self, event_id: str, event_type: str, device_id: str | None, ts_ms: int,
              sub_type: str | None) -> CountResult: ...


class MotionCounter:
    def count(self, event_id, event_type, device_id, ts_ms, sub_type) -> CountResult:
        if event_type == "button_press":
            return CountResult(0, 0.5, "motion", "doorbell press, not counted as extra visit")
        if sub_type and sub_type != "human":
            return CountResult(0, 0.6, "motion", f"non-human motion ({sub_type})")
        return CountResult(1, 0.5, "motion")


class SimulatedCounter:
    """Ground truth + vision-like error (mostly right, sometimes off by one)."""

    def __init__(self, truth_path: Path, error_rate: float = 0.12, false_positive_rate: float = 0.03,
                 seed: int = 7):
        self._truth: dict[str, int] = json.loads(Path(truth_path).read_text())
        self._rng = random.Random(seed)
        self._err = error_rate
        self._fp = false_positive_rate

    def count(self, event_id, event_type, device_id, ts_ms, sub_type) -> CountResult:
        true_people = self._truth.get(event_id)
        if true_people is None:
            return MotionCounter().count(event_id, event_type, device_id, ts_ms, sub_type)
        if true_people == 0:  # passer-by: occasionally mistaken for a visitor, with low confidence
            if self._rng.random() < self._fp:
                return CountResult(1, 0.55, "simulated-vision", "possible passer-by")
            return CountResult(0, 0.9, "simulated-vision")
        people = true_people
        if self._rng.random() < self._err:
            people = max(1, people + self._rng.choice([-1, 1]))
        return CountResult(people, round(self._rng.uniform(0.75, 0.95), 2), "simulated-vision")


PROMPT = (
    "This is a still frame from a camera at a small shop's entrance. "
    "Count only people who appear to be ENTERING or standing at the entrance. "
    "Ignore people clearly walking past on the sidewalk, reflections, and staff behind a counter. "
    'Reply with JSON only: {"people_entering": int, "passersby": int, "confidence": 0-1, "note": short string}'
)


def parse_model_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in model output: {text[:120]!r}")
    return json.loads(m.group(0))


class BedrockCounter:
    def __init__(self, ring: RingClient, model_id: str, region: str):
        import boto3  # optional dependency

        self._ring = ring
        self._model = model_id
        self._brt = boto3.client("bedrock-runtime", region_name=region)

    def count(self, event_id, event_type, device_id, ts_ms, sub_type) -> CountResult:
        if device_id is None:
            return CountResult(0, 0.0, "bedrock", "no device")
        snap = self._ring.snapshot_at(device_id, ts_ms)
        fmt = "png" if "png" in snap.content_type else "jpeg"
        resp = self._brt.converse(
            modelId=self._model,
            messages=[{"role": "user", "content": [
                {"image": {"format": fmt, "source": {"bytes": snap.content}}},
                {"text": PROMPT},
            ]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        text = resp["output"]["message"]["content"][0]["text"]
        try:
            d = parse_model_json(text)
            return CountResult(int(d.get("people_entering", 0)), float(d.get("confidence", 0.5)),
                               "bedrock", d.get("note"))
        except (ValueError, TypeError):
            # Never invent a count: fall back to the motion rule and say so.
            r = MotionCounter().count(event_id, event_type, device_id, ts_ms, sub_type)
            return CountResult(r.people, 0.3, "bedrock-fallback", "model output unreadable")


def make_counter(settings) -> PeopleCounter:
    if settings.counter == "simulated":
        if not settings.sim_truth_path:
            raise ValueError("PULSE_COUNTER=simulated needs PULSE_SIM_TRUTH")
        return SimulatedCounter(settings.sim_truth_path)
    if settings.counter == "bedrock":
        if not settings.ring_access_token:
            raise ValueError("PULSE_COUNTER=bedrock needs RING_ACCESS_TOKEN")
        return BedrockCounter(RingClient(settings.ring_api_base, settings.ring_access_token),
                              settings.bedrock_model_id, settings.aws_region)
    return MotionCounter()
