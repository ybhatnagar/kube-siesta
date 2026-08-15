"""Client for the collector's on-demand trigger service (POST /ingest).

Used by `POST /api/v1/collections` and `POST /runs {collectData:true}`. The collector
writes to the same state DB, so after triggering we poll the `collection_runs` row for
terminal status. Configured via KUBESIESTA_COLLECTOR_URL (default http://localhost:8081).
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Any, Optional

TERMINAL = {"success", "failed", "partial"}


class CollectorUnavailable(RuntimeError):
    """The collector trigger service couldn't be reached."""


def collector_base(base: Optional[str] = None) -> str:
    return (base or os.environ.get("KUBESIESTA_COLLECTOR_URL", "http://localhost:8081")).rstrip("/")


def trigger_collection(
    cluster_id: int, scope: Any, resources: list, window: str,
    *, base: Optional[str] = None, timeout: float = 15.0,
) -> dict:
    """Ask the collector to ingest now. Returns {collection_id, status}."""
    body: dict[str, Any] = {"cluster_id": cluster_id, "resources": resources, "since": window, "step": "1h"}
    if isinstance(scope, dict) and scope.get("namespaces"):
        body["namespaces"] = scope["namespaces"]
    req = urllib.request.Request(
        collector_base(base) + "/ingest", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (configured endpoint)
            return json.loads(resp.read())
    except Exception as exc:  # URLError, timeout, HTTPError, JSON error
        raise CollectorUnavailable(str(exc))


def wait_for_collection(store, collection_id: int, *, timeout: float = 120.0, interval: float = 0.5) -> Optional[dict]:
    """Poll the collection_runs row until it reaches a terminal status (or times out)."""
    deadline = time.monotonic() + timeout
    row = store.get_collection_run(collection_id)
    while time.monotonic() < deadline:
        if row and row.get("status") in TERMINAL:
            return row
        time.sleep(interval)
        row = store.get_collection_run(collection_id)
    return row
