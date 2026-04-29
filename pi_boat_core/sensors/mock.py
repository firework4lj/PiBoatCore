from __future__ import annotations

import random
from typing import Any

from pi_boat_core.sensors.base import SensorAdapter


class MockGpsSensor(SensorAdapter):
    name = "gps"

    async def read(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "latitude": 37.7749 + random.uniform(-0.0005, 0.0005),
            "longitude": -122.4194 + random.uniform(-0.0005, 0.0005),
            "speed_knots": round(random.uniform(0.0, 1.2), 2),
            "fix_quality": "mock",
        }


class MockBilgeSensor(SensorAdapter):
    name = "bilge"

    async def read(self) -> dict[str, Any]:
        active = random.random() < 0.02
        return {
            "status": "ok",
            "active": active,
            "water_detected": active,
            "cycles_last_hour": random.randint(0, 3),
        }


class MockBatterySocSensor(SensorAdapter):
    name = "battery_soc"

    async def read(self) -> dict[str, Any]:
        percent = round(random.uniform(72.0, 98.0), 1)
        return {
            "status": "ok" if percent >= 50 else "warning",
            "percent": percent,
            "voltage": round(random.uniform(12.4, 13.4), 2),
            "source": "mock",
        }
