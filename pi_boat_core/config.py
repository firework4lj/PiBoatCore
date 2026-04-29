from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    boat_id: str
    device_id: str
    server_url: str
    heartbeat_interval_seconds: float
    spool_db_path: str
    request_timeout_seconds: float
    mock_sensors: bool

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            boat_id=os.getenv("BOAT_ID", "my-boat"),
            device_id=os.getenv("DEVICE_ID", "raspberry-pi-bridge"),
            server_url=os.getenv("SERVER_URL", "http://localhost:3000/api/heartbeat"),
            heartbeat_interval_seconds=float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "30")),
            spool_db_path=os.getenv("SPOOL_DB_PATH", "./spool.db"),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "8")),
            mock_sensors=_bool_from_env("MOCK_SENSORS", True),
        )
