from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from typing import Any

from pi_boat_core.config import ArduinoVoltageConfig
from pi_boat_core.sensors.base import SensorAdapter

ADC_REFERENCE_VOLTS = 5.0
ADC_MAX_RAW = 1023.0
VOLTAGE_DIVIDER_RATIO = 5.0
VOLTAGE_CALIBRATION_MULTIPLIER = 1.0
MAP_MIN_VOLTS = 0.50
MAP_MAX_VOLTS = 4.50
MAP_MIN_KPA = 10.0
MAP_MAX_KPA = 105.0
SPARKS_PER_REVOLUTION = 0.5
RPM_WINDOW_SECONDS = 1.0


class ArduinoVoltageError(RuntimeError):
    pass


class ArduinoVoltageSensor(SensorAdapter):
    name = "arduino_voltage"

    def __init__(self, config: ArduinoVoltageConfig) -> None:
        self.config = config
        self._consecutive_failures = 0
        self._last_success_monotonic: float | None = None
        self._last_success_payload: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._streaming = False
        self._stream_stop = threading.Event()
        self._tach_samples: deque[tuple[float, int, float]] = deque()

    async def read(self) -> dict[str, Any]:
        if self._streaming:
            return self._heartbeat_payload()
        return await asyncio.to_thread(self._read_sync)

    async def run_until_stopped(self, stop: asyncio.Event) -> None:
        self._streaming = True
        self._stream_stop.clear()
        stop_task = asyncio.create_task(_set_thread_event_on_stop(stop, self._stream_stop))
        try:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(self._stream_once)
                except Exception as exc:
                    self._consecutive_failures += 1
                    self._last_error = str(exc)
                    await _sleep_or_stop(stop, self.config.retry_delay_seconds)
        finally:
            self._stream_stop.set()
            stop_task.cancel()

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
            raise ArduinoVoltageError("pyserial is required: install with `pip install .[modem]`") from exc

        with serial.Serial(
            self.config.port,
            self.config.baudrate,
            timeout=self.config.timeout_seconds,
            write_timeout=self.config.timeout_seconds,
        ) as arduino:
            arduino.reset_input_buffer()
            deadline = time.monotonic() + max(self.config.timeout_seconds, 4)
            last_parse_error: Exception | None = None
            while time.monotonic() < deadline:
                raw = arduino.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    reading = parse_voltage_line(line)
                    return {
                        "status": "ok",
                        "port": self.config.port,
                        "consecutive_failures": 0,
                        "last_success_age_seconds": 0,
                        **reading,
                    }
                except ArduinoVoltageError as exc:
                    last_parse_error = exc

        if last_parse_error is not None:
            raise ArduinoVoltageError(f"timeout waiting for voltage reading after invalid lines: {last_parse_error}")
        raise ArduinoVoltageError("timeout waiting for voltage reading")

    def _error_payload(self, error: Exception | None) -> dict[str, Any]:
        last_success_age = None
        if self._last_success_monotonic is not None:
            last_success_age = round(time.monotonic() - self._last_success_monotonic, 1)

        payload: dict[str, Any] = {
            "status": "error",
            "port": self.config.port,
            "consecutive_failures": self._consecutive_failures,
            "last_success_age_seconds": last_success_age,
            "error": str(error) if error else "unknown arduino voltage error",
        }
        if self._last_success_payload is not None:
            payload["last_known"] = {
                "voltage": self._last_success_payload.get("voltage"),
                "charging": self._last_success_payload.get("charging"),
                "soc_estimate_percent": self._last_success_payload.get("soc_estimate_percent"),
            }

        return payload

    def _stream_once(self) -> None:
        try:
            import serial
        except ImportError as exc:
            raise ArduinoVoltageError("pyserial is required: install with `pip install .[modem]`") from exc

        with serial.Serial(
            self.config.port,
            self.config.baudrate,
            timeout=1,
            write_timeout=self.config.timeout_seconds,
        ) as arduino:
            arduino.reset_input_buffer()
            while not self._stream_stop.is_set():
                raw = arduino.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                try:
                    payload = parse_voltage_line(line)
                except ArduinoVoltageError:
                    continue

                sampled_at = time.monotonic()
                self._apply_rolling_rpm(payload, sampled_at)
                self._consecutive_failures = 0
                self._last_error = None
                self._last_success_monotonic = sampled_at
                self._last_success_payload = payload

    def _apply_rolling_rpm(self, payload: dict[str, Any], sampled_at: float) -> None:
        tach_pulses = payload.get("tach_pulses")
        interval_ms = payload.get("tach_interval_ms")
        if not isinstance(tach_pulses, int | float) or not isinstance(interval_ms, int | float):
            return

        self._tach_samples.append((sampled_at, int(tach_pulses), float(interval_ms)))
        cutoff = sampled_at - RPM_WINDOW_SECONDS
        while self._tach_samples and self._tach_samples[0][0] < cutoff:
            self._tach_samples.popleft()

        total_pulses = sum(sample[1] for sample in self._tach_samples)
        total_interval_ms = sum(sample[2] for sample in self._tach_samples)
        payload["rpm_instant"] = payload.get("rpm")
        payload["rpm"] = estimate_rpm(total_pulses, total_interval_ms)
        payload["rpm_window_seconds"] = round(total_interval_ms / 1000.0, 3)

    def _heartbeat_payload(self) -> dict[str, Any]:
        if self._last_success_payload is None:
            return self._error_payload(ArduinoVoltageError(self._last_error or "waiting for arduino voltage stream"))

        last_success_age = None
        if self._last_success_monotonic is not None:
            last_success_age = round(time.monotonic() - self._last_success_monotonic, 1)

        return {
            "status": "ok",
            "port": self.config.port,
            "consecutive_failures": 0,
            "last_success_age_seconds": last_success_age,
            **heartbeat_voltage_fields(self._last_success_payload),
        }

    def latest_engine_payload(self) -> dict[str, Any]:
        if self._last_success_payload is None:
            return {
                "status": "error",
                "port": self.config.port,
                "consecutive_failures": self._consecutive_failures,
                "error": self._last_error or "waiting for arduino stream",
            }

        last_success_age = None
        if self._last_success_monotonic is not None:
            last_success_age = round(time.monotonic() - self._last_success_monotonic, 1)

        return {
            "status": "ok",
            "port": self.config.port,
            "consecutive_failures": self._consecutive_failures,
            "last_success_age_seconds": last_success_age,
            **self._last_success_payload,
        }


def parse_voltage_line(line: str) -> dict[str, Any]:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ArduinoVoltageError(f"invalid voltage JSON: {line}") from exc

    if payload.get("type") == "engine_raw":
        return parse_engine_raw_payload(payload)

    if payload.get("type") != "battery_voltage":
        raise ArduinoVoltageError(f"unexpected voltage payload type: {payload.get('type')}")

    voltage = payload.get("voltage")
    if not isinstance(voltage, int | float):
        raise ArduinoVoltageError("voltage must be numeric")

    charging = payload.get("charging")
    if not isinstance(charging, bool):
        charging = voltage >= 13.2

    soc_estimate = payload.get("soc_estimate_percent")
    if not isinstance(soc_estimate, int | float):
        soc_estimate = None

    return {
        "pin": payload.get("pin"),
        "voltage": float(voltage),
        "charging": charging,
        "soc_estimate_percent": None if soc_estimate is None else int(soc_estimate),
        **_optional_string_fields(payload, ["map_pin", "tach_pin"]),
        **_optional_numeric_fields(payload, ["map_raw", "map_voltage", "map_kpa", "tach_pulses", "rpm"]),
    }


def parse_engine_raw_payload(payload: dict[str, Any]) -> dict[str, Any]:
    voltage_raw = _required_number(payload, "voltage_raw")
    map_raw = _required_number(payload, "map_raw")
    tach_pulses = _required_number(payload, "tach_pulses")
    interval_ms = _required_number(payload, "interval_ms")

    voltage_sensor_volts = adc_to_volts(voltage_raw)
    voltage = voltage_sensor_volts * VOLTAGE_DIVIDER_RATIO * VOLTAGE_CALIBRATION_MULTIPLIER
    map_voltage = adc_to_volts(map_raw)
    map_kpa = estimate_map_kpa(map_voltage)
    rpm = estimate_rpm(tach_pulses, interval_ms)

    return {
        "pin": payload.get("voltage_pin"),
        "voltage_raw": int(voltage_raw),
        "voltage": voltage,
        "charging": voltage >= 13.2,
        "soc_estimate_percent": estimate_lead_acid_soc(voltage),
        "map_pin": payload.get("map_pin"),
        "map_raw": int(map_raw),
        "map_voltage": map_voltage,
        "map_kpa": map_kpa,
        "map_load_percent": estimate_map_load_percent(map_kpa),
        "tach_pin": payload.get("tach_pin"),
        "tach_pulses": int(tach_pulses),
        "tach_interval_ms": float(interval_ms),
        "rpm": rpm,
    }


def heartbeat_voltage_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "pin": payload.get("pin"),
        "voltage": payload.get("voltage"),
        "charging": payload.get("charging"),
        "soc_estimate_percent": payload.get("soc_estimate_percent"),
    }


def adc_to_volts(raw: float) -> float:
    return raw * (ADC_REFERENCE_VOLTS / ADC_MAX_RAW)


def estimate_map_kpa(volts: float) -> float:
    constrained_volts = min(MAP_MAX_VOLTS, max(MAP_MIN_VOLTS, volts))
    ratio = (constrained_volts - MAP_MIN_VOLTS) / (MAP_MAX_VOLTS - MAP_MIN_VOLTS)
    return MAP_MIN_KPA + (ratio * (MAP_MAX_KPA - MAP_MIN_KPA))


def estimate_map_load_percent(map_kpa: float) -> float:
    ratio = (map_kpa - MAP_MIN_KPA) / (MAP_MAX_KPA - MAP_MIN_KPA)
    return min(100.0, max(0.0, ratio * 100.0))


def estimate_rpm(pulse_count: float, interval_ms: float) -> float:
    if interval_ms <= 0 or SPARKS_PER_REVOLUTION <= 0:
        return 0.0
    pulses_per_second = pulse_count * (1000.0 / interval_ms)
    return (pulses_per_second * 60.0) / SPARKS_PER_REVOLUTION


def estimate_lead_acid_soc(voltage: float) -> int:
    if voltage >= 12.70:
        return 100
    if voltage >= 12.50:
        return 90
    if voltage >= 12.42:
        return 80
    if voltage >= 12.32:
        return 70
    if voltage >= 12.20:
        return 60
    if voltage >= 12.06:
        return 50
    if voltage >= 11.90:
        return 40
    if voltage >= 11.75:
        return 30
    if voltage >= 11.58:
        return 20
    if voltage >= 11.31:
        return 10
    return 0


def _required_number(payload: dict[str, Any], field: str) -> float:
    value = payload.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ArduinoVoltageError(f"{field} must be numeric")
    return float(value)


def _optional_string_fields(payload: dict[str, Any], fields: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in fields:
        value = payload.get(field)
        if isinstance(value, str) and value:
            values[field] = value
    return values


def _optional_numeric_fields(payload: dict[str, Any], fields: list[str]) -> dict[str, float | int]:
    values: dict[str, float | int] = {}
    for field in fields:
        value = payload.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            values[field] = value
        elif isinstance(value, float):
            values[field] = value
    return values


async def _sleep_or_stop(stop: asyncio.Event, timeout: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=max(0.1, timeout))
    except TimeoutError:
        pass


async def _set_thread_event_on_stop(stop: asyncio.Event, thread_event: threading.Event) -> None:
    await stop.wait()
    thread_event.set()
