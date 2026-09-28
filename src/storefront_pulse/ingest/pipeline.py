"""Ingest pipeline shared by the HTTP receiver, the simulator replay and (later) Lambda.

accept_webhook()  fast path: verify + parse + dedupe + persist. Must stay well under
                  Ring's 5-second acknowledgement budget, so no network calls here.
process_pending() slow path: count people for each new traffic event (snapshot + vision).
                  Locally runs as a background task; on AWS it becomes an SQS-driven Lambda.
"""

from __future__ import annotations

import logging

from ..ring import webhook
from ..store.sqlite import Store
from ..vision.counters import PeopleCounter

log = logging.getLogger(__name__)


def accept_webhook(store: Store, raw_body: bytes, signature: str | None,
                   signing_key: str | None, allowed_devices: list[str] | None = None) -> tuple[str, webhook.RingEvent | None]:
    """Returns (status, event). status in {"accepted", "duplicate", "ignored"}."""
    ev = webhook.parse(raw_body, signing_key=signing_key, signature=signature)
    if allowed_devices and ev.device_id and ev.device_id not in allowed_devices:
        return "ignored", ev
    is_new = store.add_event(ev.event_id, ev.type, ev.device_id, ev.ts_ms, ev.sub_type, ev.raw)
    return ("accepted" if is_new else "duplicate"), ev


def process_pending(store: Store, counter: PeopleCounter, limit: int = 500) -> int:
    done = 0
    for row in store.unprocessed_traffic_events(limit):
        try:
            res = counter.count(row["event_id"], row["type"], row["device_id"], row["ts_ms"], row["sub_type"])
            store.add_visit(row["event_id"], row["ts_ms"], row["device_id"], res.people,
                            res.confidence, res.method, res.note)
            store.mark_processed(row["event_id"])
            done += 1
        except Exception:  # keep going; the event stays unprocessed for retry
            log.exception("counting failed for %s", row["event_id"])
    return done
