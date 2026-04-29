from __future__ import annotations

import os
import shutil
import time
from typing import Any

from pi_boat_core.sensors.base import SensorAdapter


class SystemSensor(SensorAdapter):
    name = "system"

    def __init__(self) -> None:
        self.started_at = time.monotonic()

    async def read(self) -> dict[str, Any]:
        load_1m, load_5m, load_15m = os.getloadavg()
        disk = shutil.disk_usage("/")

        return {
            "status": "ok",
            "uptime_seconds": round(time.monotonic() - self.started_at, 1),
            "load_average": {
                "1m": load_1m,
                "5m": load_5m,
                "15m": load_15m,
            },
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
            },
        }
