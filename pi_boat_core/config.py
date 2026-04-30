from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import tomllib


DEFAULT_CONFIG_PATHS = (
    Path("./config.toml"),
    Path("/etc/piboatcore/config.toml"),
)


@dataclass(frozen=True)
class Sim7600Config:
    enabled: bool
    port: str
    baudrate: int
    timeout_seconds: float
    enable_gnss: bool
    max_attempts: int
    retry_delay_seconds: float


@dataclass(frozen=True)
class Config:
    boat_id: str
    device_id: str
    server_url: str
    heartbeat_interval_seconds: float
    spool_db_path: str
    request_timeout_seconds: float
    mock_sensors: bool
    sim7600: Sim7600Config

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "Config":
        config_path = _resolve_config_path(path)
        data = _read_toml(config_path)

        return cls(
            boat_id=_get(data, "boat", "boat_id", default="my-boat"),
            device_id=_get(data, "boat", "device_id", default="raspberry-pi-bridge"),
            server_url=_get(data, "server", "url", default="http://localhost:3000/api/heartbeat"),
            heartbeat_interval_seconds=float(_get(data, "heartbeat", "interval_seconds", default=30)),
            spool_db_path=_get(data, "storage", "spool_db_path", default="./spool.db"),
            request_timeout_seconds=float(_get(data, "server", "request_timeout_seconds", default=8)),
            mock_sensors=bool(_get(data, "sensors", "mock_sensors", default=True)),
            sim7600=Sim7600Config(
                enabled=bool(_get(data, "sim7600", "enabled", default=False)),
                port=_get(data, "sim7600", "port", default="/dev/ttyUSB2"),
                baudrate=int(_get(data, "sim7600", "baudrate", default=115200)),
                timeout_seconds=float(_get(data, "sim7600", "timeout_seconds", default=2)),
                enable_gnss=bool(_get(data, "sim7600", "enable_gnss", default=True)),
                max_attempts=int(_get(data, "sim7600", "max_attempts", default=2)),
                retry_delay_seconds=float(_get(data, "sim7600", "retry_delay_seconds", default=1)),
            ),
        )


def _resolve_config_path(path: str | Path | None) -> Path | None:
    if path is not None:
        return Path(path)

    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate

    return None


def _read_toml(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}

    with path.open("rb") as file:
        return tomllib.load(file)


def _get(data: dict[str, Any], section: str, key: str, *, default: Any) -> Any:
    value = data.get(section, {}).get(key, default)
    if value is None:
        return default
    return value
