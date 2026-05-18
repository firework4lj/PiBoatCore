from __future__ import annotations

import asyncio
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pi_boat_core.config import Sim7600Config
from pi_boat_core.sensors.base import SensorAdapter


class Sim7600Error(RuntimeError):
    pass


@dataclass(frozen=True)
class GnssPosition:
    latitude: float
    longitude: float
    utc_time: str | None
    altitude_meters: float | None
    speed_knots: float | None
    course_degrees: float | None


class Sim7600Sensor(SensorAdapter):
    name = "sim7600"
    TRACK_START_SPEED_KNOTS = 1.0
    TRACK_START_SUSTAINED_SECONDS = 5
    TRACK_START_DISTANCE_METERS = 30.48
    TRACK_STOP_SPEED_KNOTS = 0.5
    TRACK_STOP_AFTER_SECONDS = 10

    def __init__(self, config: Sim7600Config) -> None:
        self.config = config
        self._gnss_started = False
        self._consecutive_failures = 0
        self._consecutive_no_fix = 0
        self._last_success_monotonic: float | None = None
        self._last_success_payload: dict[str, Any] | None = None
        self._serial_lock = threading.Lock()
        self._track_lock = threading.Lock()
        self._track_points: list[list[Any]] = []
        self._track_sampler_started = False
        self._track_sampling_active = False
        self._track_last_moving_monotonic: float | None = None
        self._track_anchor: dict[str, Any] | None = None
        self._track_speed_start_monotonic: float | None = None

    async def read(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._read_sync)

    def _read_sync(self) -> dict[str, Any]:
        attempts = max(1, self.config.max_attempts)
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            try:
                payload = self._read_once()
                self._consecutive_failures = 0
                self._last_success_monotonic = time.monotonic()
                self._update_track_sampling(payload.get("gnss", {}))
                payload["track_points"] = self._pop_track_points()
                self._last_success_payload = payload
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.config.retry_delay_seconds)

        self._consecutive_failures += 1
        if self._should_reset_modem():
            self._reset_modem()
        return self._error_payload(last_error)

    def _read_once(self) -> dict[str, Any]:
        try:
            import serial
        except ImportError as exc:
            raise Sim7600Error("pyserial is required: install with `pip install .[modem]`") from exc

        with self._serial_lock:
            with serial.Serial(
                self.config.port,
                self.config.baudrate,
                timeout=self.config.timeout_seconds,
                write_timeout=self.config.timeout_seconds,
            ) as modem:
                self._command(modem, "AT")

                if self.config.enable_gnss and not self._gnss_started:
                    self._command(modem, "AT+CGPS=1", allow_error=True)
                    self._gnss_started = True

                csq = parse_csq(self._command(modem, "AT+CSQ"))
                registration = parse_registration(self._command(modem, "AT+CREG?"))
                packet_registration = parse_registration(self._command(modem, "AT+CGREG?"))
                operator = parse_operator(self._command(modem, "AT+COPS?", allow_error=True))
                network = parse_cpsi(self._command(modem, "AT+CPSI?", allow_error=True))
                gnss = parse_cgpsinfo(self._command(modem, "AT+CGPSINFO", allow_error=True))
                self._update_gnss_recovery(modem, gnss)

        status = "ok" if registration.get("registered") or packet_registration.get("registered") else "warning"

        return {
            "status": status,
            "port": self.config.port,
            "consecutive_failures": 0,
            "consecutive_no_fix": self._consecutive_no_fix,
            "last_success_age_seconds": 0,
            "signal": csq,
            "registration": registration,
            "packet_registration": packet_registration,
            "operator": operator,
            "network": network,
            "gnss": gnss,
        }

    def _ensure_track_sampler(self) -> None:
        if self._track_sampler_started or not self.config.enable_gnss:
            return

        self._track_sampler_started = True
        thread = threading.Thread(target=self._track_sampler_loop, name="piboat-track-sampler", daemon=True)
        thread.start()

    def _update_track_sampling(self, gnss: dict[str, Any]) -> None:
        self._ensure_track_sampler()
        if not gnss.get("fix"):
            return

        now = time.monotonic()
        with self._track_lock:
            if self._track_sampling_active:
                return

            if self._track_anchor is None:
                self._track_anchor = gnss
                return

            if should_start_track_sampling(
                anchor=self._track_anchor,
                current=gnss,
                speed_start_monotonic=self._track_speed_start_monotonic,
                now=now,
                start_speed_knots=self.TRACK_START_SPEED_KNOTS,
                sustained_seconds=self.TRACK_START_SUSTAINED_SECONDS,
                start_distance_meters=self.TRACK_START_DISTANCE_METERS,
            ):
                self._track_sampling_active = True
                self._track_last_moving_monotonic = now
                self._track_speed_start_monotonic = None
            elif _speed_at_least(gnss, self.TRACK_START_SPEED_KNOTS):
                self._track_speed_start_monotonic = self._track_speed_start_monotonic or now
                return
            else:
                self._track_speed_start_monotonic = None
                self._track_anchor = average_gnss_position(self._track_anchor, gnss)
                return

        self._append_track_point(self._track_anchor)
        self._append_track_point(gnss)

    def _track_sampler_loop(self) -> None:
        while True:
            time.sleep(1)
            with self._track_lock:
                active = self._track_sampling_active
            if not active:
                continue

            try:
                gnss = self._sample_gnss_position()
                if not gnss.get("fix"):
                    continue

                speed = gnss.get("speed_knots")
                moving = isinstance(speed, int | float) and speed >= self.TRACK_STOP_SPEED_KNOTS
                now = time.monotonic()
                with self._track_lock:
                    if moving:
                        self._track_last_moving_monotonic = now
                    elif (
                        self._track_last_moving_monotonic is not None
                        and now - self._track_last_moving_monotonic >= self.TRACK_STOP_AFTER_SECONDS
                    ):
                        self._track_sampling_active = False
                        self._track_anchor = gnss
                        self._track_speed_start_monotonic = None
                        continue

                self._append_track_point(gnss)
            except Exception:
                continue

    def _sample_gnss_position(self) -> dict[str, Any]:
        try:
            import serial
        except ImportError as exc:
            raise Sim7600Error("pyserial is required: install with `pip install .[modem]`") from exc

        with self._serial_lock:
            with serial.Serial(
                self.config.port,
                self.config.baudrate,
                timeout=self.config.timeout_seconds,
                write_timeout=self.config.timeout_seconds,
            ) as modem:
                return parse_cgpsinfo(self._command(modem, "AT+CGPSINFO", allow_error=True))

    def _append_track_point(self, gnss: dict[str, Any]) -> None:
        point = track_point_from_gnss(gnss)
        if point is None:
            return
        with self._track_lock:
            if self._track_points and self._track_points[-1][1] == point[1] and self._track_points[-1][2] == point[2]:
                return
            self._track_points.append(point)
            if len(self._track_points) > 300:
                self._track_points = self._track_points[-300:]

    def _pop_track_points(self) -> list[list[Any]]:
        with self._track_lock:
            points = self._track_points
            self._track_points = []
            return points

    def _error_payload(self, error: Exception | None) -> dict[str, Any]:
        last_success_age = None
        if self._last_success_monotonic is not None:
            last_success_age = round(time.monotonic() - self._last_success_monotonic, 1)

        payload: dict[str, Any] = {
            "status": "error",
            "port": self.config.port,
            "consecutive_failures": self._consecutive_failures,
            "last_success_age_seconds": last_success_age,
            "error": str(error) if error else "unknown modem error",
        }

        if self._last_success_payload is not None:
            payload["last_known"] = {
                "signal": self._last_success_payload.get("signal"),
                "registration": self._last_success_payload.get("registration"),
                "packet_registration": self._last_success_payload.get("packet_registration"),
                "operator": self._last_success_payload.get("operator"),
                "network": self._last_success_payload.get("network"),
                "gnss": self._last_success_payload.get("gnss"),
            }

        return payload

    def _update_gnss_recovery(self, modem: Any, gnss: dict[str, Any]) -> None:
        if not self.config.enable_gnss:
            return

        if gnss.get("fix") is True:
            self._consecutive_no_fix = 0
            return

        self._consecutive_no_fix += 1
        if self._should_restart_gnss():
            self._command(modem, "AT+CGPS=0", allow_error=True)
            time.sleep(1)
            self._command(modem, "AT+CGPS=1", allow_error=True)
            self._consecutive_no_fix = 0

    def _should_restart_gnss(self) -> bool:
        threshold = self.config.restart_gnss_after_no_fix
        return threshold > 0 and self._consecutive_no_fix >= threshold

    def _should_reset_modem(self) -> bool:
        threshold = self.config.reset_after_failures
        return threshold > 0 and self._consecutive_failures >= threshold

    def _reset_modem(self) -> None:
        try:
            import serial
        except ImportError:
            return

        try:
            with serial.Serial(
                self.config.port,
                self.config.baudrate,
                timeout=self.config.timeout_seconds,
                write_timeout=self.config.timeout_seconds,
            ) as modem:
                self._command(modem, "AT+CRESET", allow_error=True)
                self._gnss_started = False
        except Exception:
            return

    def _command(self, modem: Any, command: str, *, allow_error: bool = False) -> list[str]:
        modem.reset_input_buffer()
        modem.write(f"{command}\r".encode("ascii"))
        modem.flush()

        lines: list[str] = []
        while True:
            raw = modem.readline()
            if not raw:
                if allow_error:
                    return lines
                raise Sim7600Error(f"timeout waiting for {command}")

            line = raw.decode("ascii", errors="replace").strip()
            if not line or line == command:
                continue
            if line == "OK":
                return lines
            if line == "ERROR":
                if allow_error:
                    return lines
                raise Sim7600Error(f"{command} returned ERROR")
            lines.append(line)


def parse_csq(lines: list[str]) -> dict[str, Any]:
    line = _find_prefix(lines, "+CSQ:")
    if line is None:
        return {"status": "unknown"}

    match = re.search(r"\+CSQ:\s*(\d+),(\d+)", line)
    if not match:
        return {"status": "unknown", "raw": line}

    rssi_raw = int(match.group(1))
    ber_raw = int(match.group(2))
    rssi_dbm = None if rssi_raw == 99 else -113 + (2 * rssi_raw)

    return {
        "status": "ok" if rssi_dbm is not None else "unknown",
        "rssi_raw": rssi_raw,
        "rssi_dbm": rssi_dbm,
        "ber_raw": ber_raw,
    }


def parse_registration(lines: list[str]) -> dict[str, Any]:
    line = next((item for item in lines if item.startswith(("+CREG:", "+CGREG:", "+CEREG:"))), None)
    if line is None:
        return {"status": "unknown", "registered": False}

    values = [part.strip() for part in line.split(":", 1)[1].split(",")]
    stat = int(values[1] if len(values) > 1 else values[0])
    return {
        "status": _registration_status(stat),
        "registered": stat in {1, 5},
        "raw_status": stat,
    }


def parse_operator(lines: list[str]) -> dict[str, Any]:
    line = _find_prefix(lines, "+COPS:")
    if line is None:
        return {"status": "unknown"}

    match = re.search(r'\+COPS:\s*\d+,\d+,"([^"]+)"(?:,(\d+))?', line)
    if not match:
        return {"status": "unknown", "raw": line}

    return {
        "status": "ok",
        "name": match.group(1),
        "access_technology": match.group(2),
    }


def parse_cpsi(lines: list[str]) -> dict[str, Any]:
    line = _find_prefix(lines, "+CPSI:")
    if line is None:
        return {"status": "unknown"}

    parts = [part.strip() for part in line.split(":", 1)[1].split(",")]
    return {
        "status": "ok" if parts and parts[0] != "NO SERVICE" else "warning",
        "system_mode": parts[0] if parts else None,
        "operation_mode": parts[1] if len(parts) > 1 else None,
        "raw": line,
    }


def parse_cgpsinfo(lines: list[str]) -> dict[str, Any]:
    line = _find_prefix(lines, "+CGPSINFO:")
    if line is None:
        return {"status": "unknown", "fix": False}

    fields = [field.strip() for field in line.split(":", 1)[1].split(",")]
    if len(fields) < 4 or not fields[0] or not fields[2]:
        return {"status": "searching", "fix": False}

    position = GnssPosition(
        latitude=_nmea_coordinate_to_decimal(fields[0], fields[1]),
        longitude=_nmea_coordinate_to_decimal(fields[2], fields[3]),
        utc_time=fields[4] if len(fields) > 4 and fields[4] else None,
        altitude_meters=_float_or_none(fields[6]) if len(fields) > 6 else None,
        speed_knots=_float_or_none(fields[7]) if len(fields) > 7 else None,
        course_degrees=_float_or_none(fields[8]) if len(fields) > 8 else None,
    )

    return {
        "status": "ok",
        "fix": True,
        "latitude": position.latitude,
        "longitude": position.longitude,
        "utc_time": position.utc_time,
        "altitude_meters": position.altitude_meters,
        "speed_knots": position.speed_knots,
        "course_degrees": position.course_degrees,
    }


def track_point_from_gnss(gnss: dict[str, Any]) -> list[Any] | None:
    if not gnss.get("fix"):
        return None
    latitude = gnss.get("latitude")
    longitude = gnss.get("longitude")
    if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
        return None

    return [
        datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        round(float(latitude), 7),
        round(float(longitude), 7),
        _round_or_none(gnss.get("speed_knots"), 1),
        _round_or_none(gnss.get("course_degrees"), 1),
    ]


def should_start_track_sampling(
    *,
    anchor: dict[str, Any],
    current: dict[str, Any],
    speed_start_monotonic: float | None,
    now: float,
    start_speed_knots: float,
    sustained_seconds: float,
    start_distance_meters: float,
) -> bool:
    if distance_meters(anchor, current) >= start_distance_meters:
        return True
    return (
        _speed_at_least(current, start_speed_knots)
        and speed_start_monotonic is not None
        and now - speed_start_monotonic >= sustained_seconds
    )


def average_gnss_position(anchor: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    if not _has_position(anchor) or not _has_position(current):
        return current
    updated = dict(current)
    updated["latitude"] = (float(anchor["latitude"]) + float(current["latitude"])) / 2
    updated["longitude"] = (float(anchor["longitude"]) + float(current["longitude"])) / 2
    return updated


def distance_meters(a: dict[str, Any], b: dict[str, Any]) -> float:
    if not _has_position(a) or not _has_position(b):
        return 0.0
    earth_radius_meters = 6_371_000
    lat1 = math.radians(float(a["latitude"]))
    lat2 = math.radians(float(b["latitude"]))
    delta_lat = math.radians(float(b["latitude"]) - float(a["latitude"]))
    delta_lon = math.radians(float(b["longitude"]) - float(a["longitude"]))
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return earth_radius_meters * 2 * math.atan2(math.sqrt(haversine), math.sqrt(1 - haversine))


def _has_position(gnss: dict[str, Any]) -> bool:
    return isinstance(gnss.get("latitude"), int | float) and isinstance(gnss.get("longitude"), int | float)


def _speed_at_least(gnss: dict[str, Any], threshold: float) -> bool:
    speed = gnss.get("speed_knots")
    return isinstance(speed, int | float) and speed >= threshold


def _nmea_coordinate_to_decimal(value: str, hemisphere: str) -> float:
    split_at = 2 if hemisphere in {"N", "S"} else 3
    degrees = int(value[:split_at])
    minutes = float(value[split_at:])
    decimal = degrees + (minutes / 60)
    return -decimal if hemisphere in {"S", "W"} else decimal


def _registration_status(stat: int) -> str:
    return {
        0: "not_registered",
        1: "home",
        2: "searching",
        3: "denied",
        4: "unknown",
        5: "roaming",
    }.get(stat, "unknown")


def _float_or_none(value: str) -> float | None:
    if not value:
        return None
    return float(value)


def _round_or_none(value: Any, digits: int) -> float | None:
    if not isinstance(value, int | float):
        return None
    return round(float(value), digits)


def _find_prefix(lines: list[str], prefix: str) -> str | None:
    return next((line for line in lines if line.startswith(prefix)), None)
