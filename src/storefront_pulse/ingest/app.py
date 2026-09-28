"""Ring webhook receiver (FastAPI).

POST /ring/webhook   Ring Partner API webhook endpoint (register this URL in the Ring
                     Developer Portal). Acknowledges fast; counting runs in the background.
GET  /healthz        liveness.
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from ..config import Settings
from ..profile import StoreProfile
from ..ring.webhook import SignatureError
from ..store.sqlite import Store
from ..vision.counters import make_counter
from .pipeline import accept_webhook, process_pending

log = logging.getLogger("storefront_pulse.ingest")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    store = Store(settings.db_path)
    counter = make_counter(settings)
    profile = StoreProfile.load(settings.profile_path)
    app = FastAPI(title="Storefront Pulse ingest")

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.post("/ring/webhook")
    async def ring_webhook(request: Request, background: BackgroundTasks,
                           x_signature: str | None = Header(default=None)):
        raw = await request.body()
        try:
            status, ev = accept_webhook(store, raw, x_signature, settings.ring_signing_key,
                                        profile.device_ids or None)
        except SignatureError:
            raise HTTPException(status_code=401, detail="bad signature")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if status == "accepted" and ev and ev.is_traffic:
            background.add_task(process_pending, store, counter)
        log.info("ring %s %s -> %s", ev.type if ev else "?", ev.event_id if ev else "?", status)
        return {"status": status}

    app.state.store = store
    return app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host="0.0.0.0", port=8080)


if __name__ == "__main__":
    main()
