from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from pi_boat_core.client import TelemetryClient, TelemetryPostError
from pi_boat_core.config import Config
from pi_boat_core.models import build_heartbeat
from pi_boat_core.sensors import MockBatterySocSensor, MockBilgeSensor, MockGpsSensor, SensorAdapter, SystemSensor
from pi_boat_core.spool import TelemetrySpool

LOGGER = logging.getLogger("piboatcore")


class BoatTelemetryService:
    def __init__(
        self,
        *,
        config: Config,
        client: TelemetryClient,
        spool: TelemetrySpool,
        sensors: list[SensorAdapter],
    ) -> None:
        self.config = config
        self.client = client
        self.spool = spool
        self.sensors = sensors
        self.sequence = 0
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        LOGGER.info("starting telemetry service for boat_id=%s", self.config.boat_id)
        while not self._stop.is_set():
            await self.tick()

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.config.heartbeat_interval_seconds,
                )
            except TimeoutError:
                pass

    async def tick(self) -> None:
        self.sequence += 1
        sensors = await self.collect_sensors()
        heartbeat = build_heartbeat(
            boat_id=self.config.boat_id,
            device_id=self.config.device_id,
            sequence=self.sequence,
            sensors=sensors,
        )

        await self.flush_spool()
        await self.post_or_spool(heartbeat)

    async def collect_sensors(self) -> dict[str, dict[str, Any]]:
        readings = await asyncio.gather(
            *(self._read_sensor(sensor) for sensor in self.sensors),
        )
        return {name: reading for name, reading in readings}

    async def _read_sensor(self, sensor: SensorAdapter) -> tuple[str, dict[str, Any]]:
        try:
            return sensor.name, await sensor.read()
        except Exception as exc:
            LOGGER.exception("sensor read failed: %s", sensor.name)
            return sensor.name, {"status": "error", "error": str(exc)}

    async def flush_spool(self) -> None:
        for item in self.spool.pending():
            try:
                await asyncio.to_thread(self.client.post_heartbeat, item.payload)
                self.spool.delete(item.id)
                LOGGER.info("flushed queued heartbeat id=%s", item.id)
            except TelemetryPostError:
                LOGGER.warning("server unavailable; keeping queued heartbeats")
                return

    async def post_or_spool(self, payload: dict[str, Any]) -> None:
        try:
            await asyncio.to_thread(self.client.post_heartbeat, payload)
            LOGGER.info("sent heartbeat sequence=%s", payload["sequence"])
        except TelemetryPostError as exc:
            self.spool.enqueue(payload)
            LOGGER.warning("queued heartbeat sequence=%s error=%s", payload["sequence"], exc)


def build_default_service(config: Config) -> BoatTelemetryService:
    sensors: list[SensorAdapter] = [SystemSensor()]
    if config.mock_sensors:
        sensors.extend([MockGpsSensor(), MockBilgeSensor(), MockBatterySocSensor()])

    return BoatTelemetryService(
        config=config,
        client=TelemetryClient(
            server_url=config.server_url,
            timeout_seconds=config.request_timeout_seconds,
        ),
        spool=TelemetrySpool(config.spool_db_path),
        sensors=sensors,
    )


async def async_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = build_default_service(Config.from_env())

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.stop)

    await service.run()


def main() -> None:
    asyncio.run(async_main())
