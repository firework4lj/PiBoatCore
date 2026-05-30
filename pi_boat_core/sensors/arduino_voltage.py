from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from statistics import median
from typing import Any

from pi_boat_core.config import ArduinoVoltageConfig
from pi_boat_core.sensors.base import SensorAdapter

ADC_REFERENCE_VOLTS = 5.0
ADC_MAX_RAW = 1023.0
VOLTAGE_DIVIDER_RATIO = 5.0
VOLTAGE_CALIBRATION_MULTIPLIER = 0.75
MAP_MIN_VOLTS = 0.50
MAP_MAX_VOLTS = 4.50
MAP_MIN_KPA = 10.0
MAP_MAX_KPA = 105.0
MAP_LOAD_IDLE_KPA = 35.0
MAP_LOAD_WOT_KPA = 100.0
MAP_SMOOTHING_ALPHA = 0.18
# Calibrated to the inductive plug-wire pickup. A single cylinder on a 4-stroke
# would ideally be 0.5, but this pickup sees multiple edges per spark event.
SPARKS_PER_REVOLUTION = 2.0
RPM_WINDOW_SECONDS = 2.0
RPM_MIN_WINDOW_SECONDS = 1.0
RPM_MEDIAN_WINDOW_SECONDS = 2.5
RPM_SMOOTHING_ALPHA = 0.18
RPM_MAX_VALID = 5500.0
RPM_MAX_CHANGE_PER_SECOND = 2500.0
ENGINE_ANALYSIS_WINDOW_SECONDS = 10.0
ENGINE_RUNNING_RPM = 350.0
ENGINE_IDLE_RPM = 1100.0


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
        self._rpm_windows: deque[tuple[float, float]] = deque()
        self._smoothed_rpm: float | None = None
        self._last_rpm_sampled_at: float | None = None
        self._smoothed_map_kpa: float | None = None
        self._analysis_samples: deque[dict[str, float]] = deque()

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
                self._apply_map_smoothing(payload)
                self._apply_engine_analysis(payload, sampled_at)
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
        window_rpm = estimate_rpm(total_pulses, total_interval_ms)
        window_ready = total_interval_ms >= RPM_MIN_WINDOW_SECONDS * 1000.0
        rpm_rejected = window_ready and window_rpm > RPM_MAX_VALID

        if window_ready and not rpm_rejected:
            self._rpm_windows.append((sampled_at, window_rpm))

        rpm_cutoff = sampled_at - RPM_MEDIAN_WINDOW_SECONDS
        while self._rpm_windows and self._rpm_windows[0][0] < rpm_cutoff:
            self._rpm_windows.popleft()

        filtered_rpm = 0.0
        if self._rpm_windows:
            filtered_rpm = float(median(sample[1] for sample in self._rpm_windows))
        if rpm_rejected and self._smoothed_rpm is not None:
            filtered_rpm = self._smoothed_rpm

        if self._smoothed_rpm is None:
            self._smoothed_rpm = filtered_rpm
        elif self._smoothed_rpm == 0 and filtered_rpm > 0:
            self._smoothed_rpm = filtered_rpm
        elif filtered_rpm == 0:
            self._smoothed_rpm = 0.0
        else:
            elapsed = 0.05
            if self._last_rpm_sampled_at is not None:
                elapsed = max(0.01, sampled_at - self._last_rpm_sampled_at)

            max_step = RPM_MAX_CHANGE_PER_SECOND * elapsed
            delta = filtered_rpm - self._smoothed_rpm
            if abs(delta) > max_step:
                filtered_rpm = self._smoothed_rpm + (max_step if delta > 0 else -max_step)

            self._smoothed_rpm = (RPM_SMOOTHING_ALPHA * filtered_rpm) + ((1 - RPM_SMOOTHING_ALPHA) * self._smoothed_rpm)

        self._last_rpm_sampled_at = sampled_at

        payload["rpm_instant"] = payload.get("rpm")
        payload["rpm_window"] = window_rpm
        payload["rpm_filtered"] = filtered_rpm
        payload["rpm_rejected"] = rpm_rejected
        payload["rpm"] = self._smoothed_rpm
        payload["rpm_window_seconds"] = round(total_interval_ms / 1000.0, 3)

    def _apply_map_smoothing(self, payload: dict[str, Any]) -> None:
        map_kpa = payload.get("map_kpa")
        if not isinstance(map_kpa, int | float):
            return

        if self._smoothed_map_kpa is None:
            self._smoothed_map_kpa = float(map_kpa)
        else:
            self._smoothed_map_kpa = (MAP_SMOOTHING_ALPHA * float(map_kpa)) + (
                (1 - MAP_SMOOTHING_ALPHA) * self._smoothed_map_kpa
            )

        payload["map_kpa_avg"] = self._smoothed_map_kpa
        payload["map_load_raw_percent"] = payload.get("map_load_percent")
        payload["map_load_percent"] = estimate_map_load_percent(self._smoothed_map_kpa)

    def _apply_engine_analysis(self, payload: dict[str, Any], sampled_at: float) -> None:
        sample = _analysis_sample(payload, sampled_at)
        if sample is not None:
            self._analysis_samples.append(sample)

        cutoff = sampled_at - ENGINE_ANALYSIS_WINDOW_SECONDS
        while self._analysis_samples and self._analysis_samples[0]["timestamp"] < cutoff:
            self._analysis_samples.popleft()

        payload.update(analyze_engine_window(list(self._analysis_samples)))

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
    tach_rejected = payload.get("tach_rejected", 0)
    interval_ms = _required_number(payload, "interval_ms")
    if isinstance(tach_rejected, bool) or not isinstance(tach_rejected, int | float):
        tach_rejected = 0

    voltage = payload.get("voltage")
    if isinstance(voltage, bool) or not isinstance(voltage, int | float):
        voltage_sensor_volts = adc_to_volts(voltage_raw)
        voltage = voltage_sensor_volts * VOLTAGE_DIVIDER_RATIO * VOLTAGE_CALIBRATION_MULTIPLIER
    voltage = float(voltage)
    charging = payload.get("charging")
    if not isinstance(charging, bool):
        charging = voltage >= 13.2
    soc_estimate = payload.get("soc_estimate_percent")
    if isinstance(soc_estimate, bool) or not isinstance(soc_estimate, int | float):
        soc_estimate = estimate_lead_acid_soc(voltage)

    map_voltage = adc_to_volts(map_raw)
    map_kpa = estimate_map_kpa(map_voltage)
    rpm = estimate_rpm(tach_pulses, interval_ms)

    return {
        "pin": payload.get("voltage_pin"),
        "voltage_raw": int(voltage_raw),
        "voltage": voltage,
        "charging": charging,
        "soc_estimate_percent": int(soc_estimate),
        "map_pin": payload.get("map_pin"),
        "map_raw": int(map_raw),
        "map_voltage": map_voltage,
        "map_kpa": map_kpa,
        "map_load_percent": estimate_map_load_percent(map_kpa),
        "tach_pin": payload.get("tach_pin"),
        "tach_pulses": int(tach_pulses),
        "tach_rejected": int(tach_rejected),
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
    ratio = (map_kpa - MAP_LOAD_IDLE_KPA) / (MAP_LOAD_WOT_KPA - MAP_LOAD_IDLE_KPA)
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


def analyze_engine_window(samples: list[dict[str, float]]) -> dict[str, Any]:
    if not samples:
        return {
            "engine_state": "unknown",
            "idle_quality": "unknown",
            "map_stability": "unknown",
            "efficiency_hint": "unknown",
            "bog_detected": False,
            "stall_risk": False,
        }

    latest = samples[-1]
    rpm = latest["rpm"]
    map_kpa = latest["map_kpa"]
    load_percent = latest["load_percent"]
    running_samples = [sample for sample in samples if sample["rpm"] >= ENGINE_RUNNING_RPM]
    rpm_values = [sample["rpm"] for sample in running_samples]
    map_values = [sample["map_kpa"] for sample in running_samples]
    load_values = [sample["load_percent"] for sample in running_samples]
    rpm_stddev = stddev(rpm_values)
    map_stddev = stddev(map_values)
    avg_load = average(load_values)

    engine_state = classify_engine_state(rpm, load_percent)
    idle_quality_score = score_inverse(rpm_stddev, excellent=35, poor=180) if engine_state == "idle" else None
    map_stability_score = score_inverse(map_stddev, excellent=1.5, poor=8.0) if running_samples else None
    efficiency_score = estimate_efficiency_score(samples, rpm_stddev, map_stddev, avg_load)
    bog_detected = detect_bog(samples)
    stall_risk = engine_state in {"idle", "running"} and rpm < 500

    return {
        "engine_state": engine_state,
        "idle_quality": label_score(idle_quality_score),
        "idle_quality_score": idle_quality_score,
        "rpm_stddev": rpm_stddev,
        "map_stability": label_score(map_stability_score),
        "map_stability_score": map_stability_score,
        "map_stddev": map_stddev,
        "efficiency_hint": label_score(efficiency_score),
        "efficiency_score": efficiency_score,
        "average_load_percent": avg_load,
        "bog_detected": bog_detected,
        "stall_risk": stall_risk,
        "analysis_window_seconds": round(samples[-1]["timestamp"] - samples[0]["timestamp"], 1),
    }


def classify_engine_state(rpm: float, load_percent: float) -> str:
    if rpm < 80:
        return "off"
    if rpm < ENGINE_RUNNING_RPM:
        return "cranking"
    if rpm < ENGINE_IDLE_RPM and load_percent <= 20:
        return "idle"
    if load_percent <= 35:
        return "light_load"
    if load_percent <= 75:
        return "moderate_load"
    return "heavy_load"


def estimate_efficiency_score(
    samples: list[dict[str, float]],
    rpm_stddev: float | None,
    map_stddev: float | None,
    avg_load: float | None,
) -> float | None:
    running_samples = [sample for sample in samples if sample["rpm"] >= ENGINE_RUNNING_RPM]
    if len(running_samples) < 5 or rpm_stddev is None or map_stddev is None or avg_load is None:
        return None

    load_score = 100.0 - abs(avg_load - 25.0) * 2.0
    rpm_score = score_inverse(rpm_stddev, excellent=40, poor=220) or 0.0
    map_score = score_inverse(map_stddev, excellent=1.5, poor=8.0) or 0.0
    return min(100.0, max(0.0, (load_score * 0.45) + (rpm_score * 0.35) + (map_score * 0.20)))


def detect_bog(samples: list[dict[str, float]]) -> bool:
    if len(samples) < 5:
        return False

    recent = samples[-min(len(samples), 40) :]
    first = recent[0]
    last = recent[-1]
    map_delta = last["map_kpa"] - first["map_kpa"]
    rpm_delta = last["rpm"] - first["rpm"]
    return map_delta >= 12 and rpm_delta <= -120


def _analysis_sample(payload: dict[str, Any], sampled_at: float) -> dict[str, float] | None:
    rpm = payload.get("rpm")
    map_kpa = payload.get("map_kpa_avg", payload.get("map_kpa"))
    load_percent = payload.get("map_load_percent")
    voltage = payload.get("voltage")
    if not all(isinstance(value, int | float) for value in (rpm, map_kpa, load_percent, voltage)):
        return None
    return {
        "timestamp": sampled_at,
        "rpm": float(rpm),
        "map_kpa": float(map_kpa),
        "load_percent": float(load_percent),
        "voltage": float(voltage),
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = average(values)
    if mean is None:
        return None
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return variance ** 0.5


def score_inverse(value: float | None, *, excellent: float, poor: float) -> float | None:
    if value is None:
        return None
    if value <= excellent:
        return 100.0
    if value >= poor:
        return 0.0
    return 100.0 * (1.0 - ((value - excellent) / (poor - excellent)))


def label_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 80:
        return "good"
    if score >= 55:
        return "fair"
    return "poor"


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
