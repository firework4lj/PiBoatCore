from __future__ import annotations

import asyncio
import argparse
import logging
import signal
from datetime import UTC, datetime
from typing import Any

from pi_boat_core.camera import capture_snapshot
from pi_boat_core.client import TelemetryClient, TelemetryPostError
from pi_boat_core.config import Config
from pi_boat_core.models import build_compact_heartbeat, build_heartbeat, utc_now_iso
from pi_boat_core.sensors import (
    ArduinoVoltageSensor,
    AudioActivitySensor,
    MockBatterySocSensor,
    MockBilgeSensor,
    MockGpsSensor,
    SensorAdapter,
    Sim7600Sensor,
    SystemSensor,
)
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
        self._camera_wakeup = asyncio.Event()
        self._live_snapshot_until_epoch = 0.0
        self._live_snapshot_interval_seconds = 2.0
        self._snapshot_requested = False
        self._last_snapshot_request_id: str | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        LOGGER.info("starting telemetry service for boat_id=%s", self.config.boat_id)
        tasks = [asyncio.create_task(self.run_heartbeats())]
        if self.config.camera.enabled:
            tasks.append(asyncio.create_task(self.run_camera()))

        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    async def run_heartbeats(self) -> None:
        while not self._stop.is_set():
            await self.tick()

            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=self.config.heartbeat_interval_seconds,
                )
            except TimeoutError:
                pass

    async def run_camera(self) -> None:
        if self.config.camera.interval_seconds > 0:
            LOGGER.info("starting camera snapshots every %ss", self.config.camera.interval_seconds)
        else:
            LOGGER.info("starting camera snapshots on demand")

        while not self._stop.is_set():
            if self.should_capture_snapshot():
                requested_snapshot = self._snapshot_requested
                posted = await self.capture_and_post_snapshot()
                if requested_snapshot and posted:
                    self._snapshot_requested = False
                elif requested_snapshot:
                    self._snapshot_requested = False
                    self._last_snapshot_request_id = None

            try:
                timeout = self.camera_interval_seconds()
                if timeout is None:
                    await self._wait_for_camera_event()
                else:
                    await asyncio.wait_for(self._wait_for_camera_event(), timeout=timeout)
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
        payload = self.format_payload(heartbeat, sensors)

        await self.flush_spool()
        await self.post_or_spool(payload)

    def format_payload(self, heartbeat: dict[str, Any], sensors: dict[str, dict[str, Any]]) -> dict[str, Any]:
        if self.config.payload_format == "compact":
            return build_compact_heartbeat(
                boat_id=self.config.boat_id,
                device_id=self.config.device_id,
                sequence=self.sequence,
                sensors=sensors,
            )
        return heartbeat

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
            response = await asyncio.to_thread(self.client.post_heartbeat, payload)
            self.apply_server_commands(response)
            LOGGER.info("sent heartbeat sequence=%s", _payload_sequence(payload))
        except TelemetryPostError as exc:
            self.spool.enqueue(payload)
            LOGGER.warning("queued heartbeat sequence=%s error=%s", _payload_sequence(payload), exc)

    async def capture_and_post_snapshot(self) -> bool:
        sent_at = utc_now_iso()
        try:
            image = await asyncio.to_thread(capture_snapshot, self.config.camera)
            await asyncio.to_thread(
                self.client.post_snapshot,
                boat_id=self.config.boat_id,
                device_id=self.config.device_id,
                sent_at=sent_at,
                image=image,
            )
            LOGGER.info("sent camera snapshot bytes=%s", len(image))
            return True
        except Exception as exc:
            LOGGER.warning("camera snapshot failed: %s", exc)
            return False

    async def _wait_for_camera_event(self) -> None:
        stop_task = asyncio.create_task(self._stop.wait())
        camera_task = asyncio.create_task(self._camera_wakeup.wait())
        done, pending = await asyncio.wait(
            {stop_task, camera_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if camera_task in done:
            self._camera_wakeup.clear()

    def should_capture_snapshot(self) -> bool:
        return self._snapshot_requested or self.live_camera_active() or self.config.camera.interval_seconds > 0

    def camera_interval_seconds(self) -> float | None:
        if self.live_camera_active():
            return self._live_snapshot_interval_seconds
        if self.config.camera.interval_seconds <= 0:
            return None
        return self.config.camera.interval_seconds

    def live_camera_active(self) -> bool:
        return time_now_epoch() < self._live_snapshot_until_epoch

    def apply_server_commands(self, response: dict[str, Any]) -> None:
        commands = response.get("commands", {})
        snapshot = commands.get("camera_snapshot", {})
        request_id = snapshot.get("request_id")
        if snapshot.get("requested") and isinstance(request_id, str) and request_id != self._last_snapshot_request_id:
            self._last_snapshot_request_id = request_id
            self._snapshot_requested = True
            self._camera_wakeup.set()

        live = commands.get("camera_live", {})
        if not live.get("active"):
            self._live_snapshot_until_epoch = 0.0
            return

        until = _parse_iso_epoch(live.get("until"))
        interval = live.get("interval_seconds")
        if until is None:
            return

        self._live_snapshot_until_epoch = until
        if isinstance(interval, int | float) and interval > 0:
            self._live_snapshot_interval_seconds = float(interval)
        self._camera_wakeup.set()


def build_default_service(config: Config) -> BoatTelemetryService:
    sensors: list[SensorAdapter] = [SystemSensor()]
    if config.sim7600.enabled:
        sensors.append(Sim7600Sensor(config.sim7600))
    if config.arduino_voltage.enabled:
        sensors.append(ArduinoVoltageSensor(config.arduino_voltage))
    if config.audio_activity.enabled:
        LOGGER.info("audio activity sensor enabled device=%s", config.audio_activity.device)
        sensors.append(AudioActivitySensor(config.audio_activity))

    if config.mock_sensors:
        sensors.extend([MockGpsSensor(), MockBilgeSensor(), MockBatterySocSensor()])

    return BoatTelemetryService(
        config=config,
        client=TelemetryClient(
            server_url=config.server_url,
            timeout_seconds=config.request_timeout_seconds,
            api_token=config.server_api_token,
        ),
        spool=TelemetrySpool(config.spool_db_path),
        sensors=sensors,
    )


def _payload_sequence(payload: dict[str, Any]) -> Any:
    if "sequence" in payload:
        return payload["sequence"]
    compact = payload.get("t")
    if isinstance(compact, str):
        parts = compact.split(",", 4)
        if len(parts) >= 4:
            return parts[3]
    return "unknown"


def time_now_epoch() -> float:
    return datetime.now(UTC).timestamp()


def _parse_iso_epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


async def async_main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    service = build_default_service(Config.from_file(args.config))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.stop)

    await service.run()


def main() -> None:
    asyncio.run(async_main())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PiBoatCore telemetry service.")
    parser.add_argument(
        "--config",
        help="Path to config.toml. Defaults to ./config.toml, then /etc/piboatcore/config.toml.",
    )
    return parser.parse_args()
