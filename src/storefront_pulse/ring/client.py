"""Minimal Ring Partner API client: just what Storefront Pulse needs (snapshots, devices).

All calls are server-to-server (Ring blocks browser CORS). Snapshot downloads may answer
with a 303 redirect to a pre-signed media URL; the redirect is followed *without* the
bearer token so it never leaks to the media host.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Snapshot:
    content: bytes
    content_type: str
    timestamp_ms: int | None


class RingClient:
    def __init__(self, base_url: str, access_token: str, timeout: float = 10.0):
        self._base = base_url.rstrip("/")
        self._token = access_token
        self._http = httpx.Client(timeout=timeout, follow_redirects=False)

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", **extra}

    def list_devices(self) -> list[dict]:
        r = self._http.get(
            f"{self._base}/v1/devices",
            params={"include": "status,capabilities,location"},
            headers=self._headers(Accept="application/vnd.api+json"),
        )
        r.raise_for_status()
        return r.json().get("data", [])

    def snapshot_at(self, device_id: str, ts_ms: int, fmt: str = "jpeg") -> Snapshot:
        body = {"type": "at_timestamp", "timestamp": ts_ms, "image_options": {"format": fmt}}
        r = self._http.post(
            f"{self._base}/v1/devices/{device_id}/media/image/download",
            json=body,
            headers=self._headers(**{"Content-Type": "application/json", "Accept": "*/*"}),
        )
        if r.status_code in (301, 302, 303, 307, 308):
            r = self._http.get(r.headers["Location"])  # no auth header on purpose
        r.raise_for_status()
        ts = r.headers.get("X-Media-Timestamp")
        return Snapshot(r.content, r.headers.get("Content-Type", f"image/{fmt}"), int(ts) if ts else None)

    def close(self) -> None:
        self._http.close()
