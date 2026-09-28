"""Ring Partner API webhook handling (payload v1.1).

Ring signs each webhook with HMAC-SHA256 over the raw request body and sends
``X-Signature: sha256=<hex>``. Always verify against raw bytes, never re-serialized JSON.

Payload shape (v1.1, JSON:API style)::

    {"meta": {"version": "1.1", "time": "...", "request_id": "...", "account_id": "..."},
     "data": {"id": "...", "type": "motion_detected",
              "attributes": {"source": "<device id>", "source_type": "devices",
                             "timestamp": <epoch ms>, "sub_type": "human"?,
                             "component_ids": [...]?},
              "relationships": {"devices": {"links": {"self": "/v1/devices/<id>"}}}}}

Note: the Ring Playground currently omits ``sub_type`` / ``cv_detections``, which is why
Storefront Pulse counts people from snapshots instead of trusting motion labels.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

SIGNATURE_HEADER = "X-Signature"
_PREFIX = "sha256="

# Events that carry foot-traffic signal. Others (lifecycle, subscription) are logged only.
TRAFFIC_EVENTS = {"motion_detected", "button_press"}
DEVICE_HEALTH_EVENTS = {"device_online", "device_offline"}


class SignatureError(ValueError):
    pass


def sign(signing_key: str, raw_body: bytes) -> str:
    return _PREFIX + hmac.new(signing_key.encode(), raw_body, hashlib.sha256).hexdigest()


def verify(signing_key: str, raw_body: bytes, received: str | None) -> bool:
    if not received:
        return False
    try:
        expected = sign(signing_key, raw_body).encode("ascii")
        return hmac.compare_digest(expected, received.strip().encode("ascii"))
    except (UnicodeEncodeError, AttributeError):
        return False


@dataclass(frozen=True)
class RingEvent:
    event_id: str
    type: str
    device_id: str | None
    ts_ms: int
    sub_type: str | None
    account_id: str | None
    raw: dict[str, Any]

    @property
    def is_traffic(self) -> bool:
        return self.type in TRAFFIC_EVENTS


def parse(raw_body: bytes, *, signing_key: str | None = None, signature: str | None = None) -> RingEvent:
    """Verify (when a key is configured) and parse a webhook body."""
    if signing_key is not None and not verify(signing_key, raw_body, signature):
        raise SignatureError("Ring webhook signature mismatch")
    payload = json.loads(raw_body)
    data = payload.get("data") or {}
    attrs = data.get("attributes") or {}
    meta = payload.get("meta") or {}
    source_type = attrs.get("source_type", "devices")
    ts = attrs.get("timestamp")
    if not data.get("id") or not data.get("type") or ts is None:
        raise ValueError("webhook payload missing data.id, data.type or attributes.timestamp")
    return RingEvent(
        event_id=str(data["id"]),
        type=str(data["type"]),
        device_id=attrs.get("source") if source_type == "devices" else None,
        ts_ms=int(ts),
        sub_type=attrs.get("sub_type"),
        account_id=meta.get("account_id"),
        raw=payload,
    )


def build_event(
    *, event_type: str, device_id: str, ts_ms: int, account_id: str = "ava1.ring.account.SIM",
    sub_type: str | None = None, request_id: str = "sim",
) -> dict[str, Any]:
    """Build a v1.1 payload identical in shape to Ring's (used by the simulator and tests)."""
    attributes: dict[str, Any] = {"source": device_id, "source_type": "devices", "timestamp": ts_ms}
    if sub_type:
        attributes["sub_type"] = sub_type
    return {
        "meta": {"version": "1.1", "time": "", "request_id": request_id, "account_id": account_id},
        "data": {
            "id": f"{device_id}_{event_type}_{ts_ms}",
            "type": event_type,
            "attributes": attributes,
            "relationships": {"devices": {"links": {"self": f"/v1/devices/{device_id}"}}},
        },
    }


def encode(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
