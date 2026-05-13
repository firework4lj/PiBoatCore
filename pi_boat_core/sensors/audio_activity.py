from __future__ import annotations

import math
import subprocess
import threading
import time
from collections import deque
from typing import Any

from pi_boat_core.config import AudioActivityConfig
from pi_boat_core.sensors.base import SensorAdapter


class AudioActivitySensor(SensorAdapter):
    name = "audio_activity"

    def __init__(self, config: AudioActivityConfig) -> None:
        self.config = config
        self._samples: deque[dict[str, float]] = deque()
        self._lock = threading.Lock()
        self._started = False
        self._last_error: str | None = None
        self._last_sample_monotonic: float | None = None

    async def read(self) -> dict[str, Any]:
        self._ensure_started()
        with self._lock:
            samples = list(self._samples)
            last_error = self._last_error
            last_sample_monotonic = self._last_sample_monotonic

        if not samples:
            return {
                "status": "warning" if last_error else "starting",
                "state": "unknown",
                "error": last_error,
            }

        now = time.monotonic()
        age = None if last_sample_monotonic is None else round(now - last_sample_monotonic, 1)
        recent = [sample for sample in samples if now - sample["monotonic"] <= self.config.window_seconds]
        if not recent:
            return {
                "status": "warning",
                "state": "unknown",
                "last_sample_age_seconds": age,
                "error": last_error or "no recent audio samples",
            }

        avg_rms_db = sum(sample["rms_db"] for sample in recent) / len(recent)
        peak_db = max(sample["peak_db"] for sample in recent)
        impact_count = sum(1 for sample in recent if sample["peak_db"] >= self.config.impact_threshold_db)
        state = classify_audio_activity(
            avg_rms_db=avg_rms_db,
            peak_db=peak_db,
            impact_count=impact_count,
            moderate_threshold_db=self.config.moderate_threshold_db,
            heavy_threshold_db=self.config.heavy_threshold_db,
        )

        return {
            "status": "ok",
            "state": state,
            "rms_db": round(avg_rms_db, 1),
            "peak_db": round(peak_db, 1),
            "impact_count_1m": impact_count,
            "samples": len(recent),
            "last_sample_age_seconds": age,
            "error": last_error,
        }

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        thread = threading.Thread(target=self._monitor_audio, name="piboat-audio-activity", daemon=True)
        thread.start()

    def _monitor_audio(self) -> None:
        bytes_per_sample = 2
        chunk_size = max(1, int(self.config.sample_rate * self.config.chunk_seconds)) * bytes_per_sample

        while True:
            process = self._start_arecord()
            try:
                while True:
                    chunk = process.stdout.read(chunk_size) if process.stdout else b""
                    if not chunk:
                        raise RuntimeError("audio stream ended")
                    self._record_chunk(chunk)
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)
                try:
                    process.kill()
                    process.wait(timeout=2)
                except Exception:
                    pass
                time.sleep(5)

    def _start_arecord(self) -> subprocess.Popen[bytes]:
        command = [
            "arecord",
            "-D",
            self.config.device,
            "-f",
            "S16_LE",
            "-r",
            str(self.config.sample_rate),
            "-c",
            "1",
            "-t",
            "raw",
        ]
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def _record_chunk(self, chunk: bytes) -> None:
        rms, peak = analyze_pcm16(chunk)
        sample = {
            "monotonic": time.monotonic(),
            "rms_db": amplitude_to_db(rms),
            "peak_db": amplitude_to_db(peak),
        }

        with self._lock:
            self._last_error = None
            self._last_sample_monotonic = sample["monotonic"]
            self._samples.append(sample)
            cutoff = sample["monotonic"] - max(self.config.window_seconds * 2, 120)
            while self._samples and self._samples[0]["monotonic"] < cutoff:
                self._samples.popleft()


def analyze_pcm16(chunk: bytes) -> tuple[float, int]:
    sample_count = len(chunk) // 2
    if sample_count <= 0:
        return 0.0, 0

    total_squares = 0
    peak = 0
    for index in range(0, sample_count * 2, 2):
        sample = int.from_bytes(chunk[index : index + 2], byteorder="little", signed=True)
        magnitude = abs(sample)
        total_squares += sample * sample
        if magnitude > peak:
            peak = magnitude

    return math.sqrt(total_squares / sample_count), peak


def amplitude_to_db(value: int | float) -> float:
    if value <= 0:
        return -120.0
    return 20 * math.log10(min(value, 32767) / 32767)


def classify_audio_activity(
    *,
    avg_rms_db: float,
    peak_db: float,
    impact_count: int,
    moderate_threshold_db: float,
    heavy_threshold_db: float,
) -> str:
    if impact_count >= 3 or peak_db >= -6:
        return "impact_detected"
    if avg_rms_db >= heavy_threshold_db or impact_count >= 1:
        return "heavy_activity"
    if avg_rms_db >= moderate_threshold_db:
        return "moderate_activity"
    if avg_rms_db >= moderate_threshold_db - 10:
        return "mild_activity"
    return "calm"
