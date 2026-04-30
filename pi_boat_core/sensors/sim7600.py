from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
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

    def __init__(self, config: Sim7600Config) -> None:
        self.config = config
        self._gnss_started = False
        self._consecutive_failures = 0
        self._last_success_monotonic: float | None = None
        self._last_success_payload: dict[str, Any] | None = None

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
                self._last_success_payload = payload
                return payload
            except Exception as exc:
                last_error = exc
                if attempt < attempts:
                    time.sleep(self.config.retry_delay_seconds)

        self._consecutive_failures += 1
        return self._error_payload(last_error)

    def _read_once(self) -> dict[str, Any]:
        try:
            import serial
        except ImportError as exc:
            raise Sim7600Error("pyserial is required: install with `pip install .[modem]`") from exc

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

        status = "ok" if registration.get("registered") or packet_registration.get("registered") else "warning"

        return {
            "status": status,
            "port": self.config.port,
            "consecutive_failures": 0,
            "last_success_age_seconds": 0,
            "signal": csq,
            "registration": registration,
            "packet_registration": packet_registration,
            "operator": operator,
            "network": network,
            "gnss": gnss,
        }

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


def _find_prefix(lines: list[str], prefix: str) -> str | None:
    return next((line for line in lines if line.startswith(prefix)), None)
