from __future__ import annotations

import csv
import io
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


def build_compact_heartbeat(
    *,
    boat_id: str,
    device_id: str,
    sequence: int,
    sensors: dict[str, dict[str, Any]],
) -> dict[str, str]:
    sent_at = utc_now_iso()
    sensor_statuses = [reading.get("status", "unknown") for reading in sensors.values()]
    status = "ok" if all(item == "ok" for item in sensor_statuses) else "degraded"

    system = sensors.get("system", {})
    modem = sensors.get("sim7600", {})
    signal = modem.get("signal", {})
    registration = modem.get("registration", {})
    operator = modem.get("operator", {})
    network = modem.get("network", {})
    gnss = modem.get("gnss", {})

    fields = [
        "1",
        boat_id,
        device_id,
        sequence,
        sent_at,
        status,
        system.get("status"),
        system.get("uptime_seconds"),
        modem.get("status"),
        modem.get("consecutive_failures"),
        signal.get("rssi_dbm"),
        _bool_to_int(registration.get("registered")),
        operator.get("name"),
        network.get("system_mode"),
        _bool_to_int(gnss.get("fix")),
        gnss.get("latitude"),
        gnss.get("longitude"),
        gnss.get("speed_knots"),
        gnss.get("course_degrees"),
        gnss.get("altitude_meters"),
    ]

    return {"t": _to_csv_line(fields)}


def _to_csv_line(fields: list[Any]) -> str:
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="")
    writer.writerow(["" if value is None else value for value in fields])
    return output.getvalue()


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return 1 if bool(value) else 0
