from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class TelemetryPostError(RuntimeError):
    pass


@dataclass(frozen=True)
class TelemetryClient:
    server_url: str
    timeout_seconds: float
    api_token: str = ""

    def post_heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        request = urllib.request.Request(
            self.server_url,
            data=body,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise TelemetryPostError(f"server returned HTTP {response.status}")
                body = response.read()
                if not body:
                    return {}
                return json.loads(body.decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryPostError(str(exc)) from exc

    def post_snapshot(self, *, boat_id: str, device_id: str, sent_at: str, image: bytes) -> None:
        snapshot_url = self.server_url.rsplit("/", 1)[0] + "/snapshot"
        headers = {
            "Content-Type": "image/jpeg",
            "X-Boat-Id": boat_id,
            "X-Device-Id": device_id,
            "X-Sent-At": sent_at,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        request = urllib.request.Request(
            snapshot_url,
            data=image,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise TelemetryPostError(f"server returned HTTP {response.status}")
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryPostError(str(exc)) from exc

    def post_audio_event(
        self,
        *,
        boat_id: str,
        device_id: str,
        sent_at: str,
        trigger: str,
        rms_db: float | None,
        peak_db: float | None,
        peak_over_rms_db: float | None,
        duration_seconds: float | None,
        audio: bytes,
    ) -> dict[str, Any]:
        audio_url = self.server_url.rsplit("/", 1)[0] + "/audio-event"
        headers = {
            "Content-Type": "audio/wav",
            "X-Boat-Id": boat_id,
            "X-Device-Id": device_id,
            "X-Sent-At": sent_at,
            "X-Trigger": trigger,
        }
        optional_headers = {
            "X-Rms-Db": rms_db,
            "X-Peak-Db": peak_db,
            "X-Peak-Over-Rms-Db": peak_over_rms_db,
            "X-Duration-Seconds": duration_seconds,
        }
        for name, value in optional_headers.items():
            if value is not None:
                headers[name] = str(value)
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        request = urllib.request.Request(
            audio_url,
            data=audio,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise TelemetryPostError(f"server returned HTTP {response.status}")
                body = response.read()
                if not body:
                    return {}
                return json.loads(body.decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryPostError(str(exc)) from exc

    def post_audio_event_snapshot(
        self,
        *,
        boat_id: str,
        device_id: str,
        event_id: str,
        sent_at: str,
        image: bytes,
    ) -> None:
        snapshot_url = self.server_url.rsplit("/", 1)[0] + "/audio-event-snapshot"
        headers = {
            "Content-Type": "image/jpeg",
            "X-Boat-Id": boat_id,
            "X-Device-Id": device_id,
            "X-Event-Id": event_id,
            "X-Sent-At": sent_at,
        }
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        request = urllib.request.Request(
            snapshot_url,
            data=image,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if response.status < 200 or response.status >= 300:
                    raise TelemetryPostError(f"server returned HTTP {response.status}")
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryPostError(str(exc)) from exc
