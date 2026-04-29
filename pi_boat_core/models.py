from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_heartbeat(
    *,
    boat_id: str,
    device_id: str,
    sequence: int,
    sensors: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    sensor_statuses = [reading.get("status", "unknown") for reading in sensors.values()]
    status = "ok" if all(item == "ok" for item in sensor_statuses) else "degraded"

    return {
        "boat_id": boat_id,
        "device_id": device_id,
        "sequence": sequence,
        "sent_at": utc_now_iso(),
        "status": status,
        "sensors": sensors,
    }
