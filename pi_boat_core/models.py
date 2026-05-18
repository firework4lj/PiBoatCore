from __future__ import annotations

import csv
import io
import json
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
    voltage = sensors.get("arduino_voltage", {})
    audio = sensors.get("audio_activity", {})

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
        voltage.get("status"),
        voltage.get("voltage"),
        _bool_to_int(voltage.get("charging")),
        voltage.get("soc_estimate_percent"),
        audio.get("status"),
        audio.get("state"),
        audio.get("rms_db"),
        audio.get("peak_db"),
        audio.get("impact_count_1m"),
        audio.get("peak_over_rms_db"),
        _compact_json(modem.get("track_points")),
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


def _compact_json(value: Any) -> str | None:
    if not value:
        return None
    return json.dumps(value, separators=(",", ":"))
