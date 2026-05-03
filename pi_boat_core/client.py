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

    def post_heartbeat(self, payload: dict[str, Any]) -> None:
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
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            raise TelemetryPostError(str(exc)) from exc
