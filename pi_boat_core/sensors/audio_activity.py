from __future__ import annotations

import io
import math
import logging
import subprocess
import threading
import time
import wave
from collections import deque
from typing import Any

from pi_boat_core.config import AudioActivityConfig
from pi_boat_core.sensors.base import SensorAdapter

LOGGER = logging.getLogger("piboatcore.audio")


class AudioActivitySensor(SensorAdapter):
    name = "audio_activity"

    def __init__(self, config: AudioActivityConfig) -> None:
        self.config = config
        self._samples: deque[dict[str, float]] = deque()
        self._audio_chunks: deque[dict[str, Any]] = deque()
        self._audio_events: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()
        self._started = False
        self._last_error: str | None = None
        self._last_sample_monotonic: float | None = None
        self._logged_first_sample = False
        self._active_event: dict[str, Any] | None = None

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
        impact_count = sum(
            1
            for sample in recent
            if is_impact_sample(
                rms_db=sample["rms_db"],
                peak_db=sample["peak_db"],
                impact_threshold_db=self.config.impact_threshold_db,
                min_peak_delta_db=self.config.impact_min_peak_delta_db,
            )
        )
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
            "peak_over_rms_db": round(peak_db - avg_rms_db, 1),
            "impact_count_1m": impact_count,
            "samples": len(recent),
            "last_sample_age_seconds": age,
            "error": last_error,
        }

    def pop_audio_events(self, limit: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            events = []
            while self._audio_events and len(events) < limit:
                events.append(self._audio_events.popleft())
            return events

    def requeue_audio_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._audio_events.appendleft(event)

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        LOGGER.info("starting audio activity monitor device=%s sample_rate=%s", self.config.device, self.config.sample_rate)
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
                LOGGER.warning("audio activity monitor failed: %s", exc)
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
        now = time.monotonic()
        sample = {
            "monotonic": now,
            "rms_db": amplitude_to_db(rms),
            "peak_db": amplitude_to_db(peak),
        }

        with self._lock:
            self._last_error = None
            self._last_sample_monotonic = sample["monotonic"]
            self._samples.append(sample)
            if not self._logged_first_sample:
                self._logged_first_sample = True
                LOGGER.info("audio activity monitor receiving samples")
            self._audio_chunks.append({"monotonic": now, "pcm": chunk})
            self._maybe_record_audio_event(sample)
            cutoff = sample["monotonic"] - max(self.config.window_seconds * 2, 120)
            while self._samples and self._samples[0]["monotonic"] < cutoff:
                self._samples.popleft()
            audio_cutoff = sample["monotonic"] - 20
            while self._audio_chunks and self._audio_chunks[0]["monotonic"] < audio_cutoff:
                self._audio_chunks.popleft()

    def _maybe_record_audio_event(self, sample: dict[str, float]) -> None:
        now = sample["monotonic"]
        if self._active_event and now >= self._active_event["end_monotonic"]:
            self._finish_audio_event(self._active_event)
            self._active_event = None

        if self._active_event:
            return

        trigger = audio_event_trigger(
            rms_db=sample["rms_db"],
            peak_db=sample["peak_db"],
            impact_threshold_db=self.config.impact_threshold_db,
            min_peak_delta_db=self.config.impact_min_peak_delta_db,
            heavy_threshold_db=self.config.heavy_threshold_db,
        )
        if not trigger:
            return

        self._active_event = {
            "trigger": trigger,
            "trigger_monotonic": now,
            "start_monotonic": now - 5,
            "end_monotonic": now + 5,
            "rms_db": sample["rms_db"],
            "peak_db": sample["peak_db"],
            "peak_over_rms_db": sample["peak_db"] - sample["rms_db"],
        }

    def _finish_audio_event(self, event: dict[str, Any]) -> None:
        chunks = [
            item["pcm"]
            for item in self._audio_chunks
            if event["start_monotonic"] <= item["monotonic"] <= event["end_monotonic"]
        ]
        if not chunks:
            return

        wav = pcm16_to_wav(b"".join(chunks), sample_rate=self.config.sample_rate)
        self._audio_events.append(
            {
                "trigger": event["trigger"],
                "rms_db": round(event["rms_db"], 1),
                "peak_db": round(event["peak_db"], 1),
                "peak_over_rms_db": round(event["peak_over_rms_db"], 1),
                "duration_seconds": round(len(b"".join(chunks)) / (self.config.sample_rate * 2), 1),
                "wav": wav,
            }
        )
        while len(self._audio_events) > 20:
            self._audio_events.popleft()


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


def pcm16_to_wav(pcm: bytes, *, sample_rate: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(sample_rate)
        file.writeframes(pcm)
    return output.getvalue()


def audio_event_trigger(
    *,
    rms_db: float,
    peak_db: float,
    impact_threshold_db: float,
    min_peak_delta_db: float,
    heavy_threshold_db: float,
) -> str | None:
    if is_impact_sample(
        rms_db=rms_db,
        peak_db=peak_db,
        impact_threshold_db=impact_threshold_db,
        min_peak_delta_db=min_peak_delta_db,
    ):
        return "impact"
    if rms_db >= heavy_threshold_db:
        return "heavy_activity"
    return None


def is_impact_sample(
    *,
    rms_db: float,
    peak_db: float,
    impact_threshold_db: float,
    min_peak_delta_db: float,
) -> bool:
    return peak_db >= impact_threshold_db and peak_db - rms_db >= min_peak_delta_db


def classify_audio_activity(
    *,
    avg_rms_db: float,
    peak_db: float,
    impact_count: int,
    moderate_threshold_db: float,
    heavy_threshold_db: float,
) -> str:
    if impact_count >= 3:
        return "impact_detected"
    if avg_rms_db >= heavy_threshold_db:
        return "heavy_activity"
    if impact_count >= 1:
        return "possible_impact"
    if avg_rms_db >= moderate_threshold_db:
        return "moderate_activity"
    if avg_rms_db >= moderate_threshold_db - 10:
        return "mild_activity"
    return "calm"
