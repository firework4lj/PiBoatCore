from __future__ import annotations

import asyncio
import argparse
import json
import subprocess
import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pi_boat_core.camera import capture_snapshot
from pi_boat_core.client import TelemetryClient, TelemetryPostError
from pi_boat_core.config import Config
from pi_boat_core.local_web import LocalWebServer
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
UNDERWAY_SPEED_KNOTS = 1.0
NETWORK_RECOVERY_FAILURE_THRESHOLD = 5
NETWORK_RECOVERY_COOLDOWN_SECONDS = 300
CELLULAR_CONNECTION_NAME = "sim7600-usb0"
ENGINE_SETTINGS_PATH = Path("./engine_settings.json")


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
        self._consecutive_upload_failures = 0
        self._last_network_recovery_monotonic = 0.0
        self.load_engine_settings()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        LOGGER.info("starting telemetry service for boat_id=%s", self.config.boat_id)
        tasks = [asyncio.create_task(self.run_heartbeats())]
        tasks.extend(
            asyncio.create_task(runner(self._stop))
            for sensor in self.sensors
            if callable(runner := getattr(sensor, "run_until_stopped", None))
        )
        if self.config.camera.enabled:
            tasks.append(asyncio.create_task(self.run_camera()))
        if self.config.local_web.enabled:
            LOGGER.info("starting local web interface on http://%s:%s", self.config.local_web.host, self.config.local_web.port)
            tasks.append(asyncio.create_task(self.run_local_web()))

        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()

    async def run_local_web(self) -> None:
        server = LocalWebServer(
            self.config.local_web,
            self.latest_engine_payload,
            self.engine_settings,
            self.update_engine_settings,
        )
        await server.run_until_stopped(self._stop)

    async def run_heartbeats(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:
                LOGGER.exception("telemetry tick failed; will retry")

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
        self.apply_audio_clip_suppression(sensors)
        heartbeat = build_heartbeat(
            boat_id=self.config.boat_id,
            device_id=self.config.device_id,
            sequence=self.sequence,
            sensors=sensors,
        )
        payload = self.format_payload(heartbeat, sensors)

        await self.flush_spool()
        await self.post_or_spool(payload)
        await self.post_audio_events()

    def apply_audio_clip_suppression(self, sensors: dict[str, dict[str, Any]]) -> None:
        speed_knots = current_speed_knots(sensors)
        suppressed = isinstance(speed_knots, int | float) and speed_knots > UNDERWAY_SPEED_KNOTS
        reason = "underway" if suppressed else None

        for sensor in self.sensors:
            set_clip_suppressed = getattr(sensor, "set_clip_suppressed", None)
            if callable(set_clip_suppressed):
                set_clip_suppressed(suppressed, reason)

        audio = sensors.get("audio_activity")
        if audio is not None:
            audio["clip_suppressed"] = suppressed
            audio["clip_suppressed_reason"] = reason

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

    def latest_engine_payload(self) -> dict[str, Any]:
        for sensor in self.sensors:
            latest_engine_payload = getattr(sensor, "latest_engine_payload", None)
            if callable(latest_engine_payload):
                return latest_engine_payload()
        return {"status": "disabled", "error": "arduino voltage sensor is not enabled"}

    def engine_settings(self) -> dict[str, Any]:
        for sensor in self.sensors:
            engine_settings = getattr(sensor, "engine_settings", None)
            if callable(engine_settings):
                return engine_settings()
        return {"status": "disabled", "error": "arduino voltage sensor is not enabled"}

    def update_engine_settings(self, settings: dict[str, Any]) -> dict[str, Any]:
        updated: dict[str, Any] | None = None
        for sensor in self.sensors:
            update_engine_settings = getattr(sensor, "update_engine_settings", None)
            if callable(update_engine_settings):
                updated = update_engine_settings(settings)
                break

        if updated is None:
            updated = self.engine_settings()
        else:
            self.save_engine_settings(updated)
        return updated

    def load_engine_settings(self) -> None:
        try:
            settings = json.loads(ENGINE_SETTINGS_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except json.JSONDecodeError:
            LOGGER.warning("engine settings file is invalid; ignoring %s", ENGINE_SETTINGS_PATH)
            return

        if isinstance(settings, dict):
            self.update_engine_settings(settings)

    def save_engine_settings(self, settings: dict[str, Any]) -> None:
        stored = {
            key: settings[key]
            for key in ("rpm_tuning_preset",)
            if key in settings
        }
        try:
            ENGINE_SETTINGS_PATH.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("failed to save engine settings: %s", exc)

    async def flush_spool(self) -> None:
        for item in self.spool.pending():
            try:
                await asyncio.to_thread(self.client.post_heartbeat, item.payload)
                self.spool.delete(item.id)
                self._record_upload_success()
                LOGGER.info("flushed queued heartbeat id=%s", item.id)
            except TelemetryPostError:
                await self._record_upload_failure()
                LOGGER.warning("server unavailable; keeping queued heartbeats")
                return

    async def post_or_spool(self, payload: dict[str, Any]) -> None:
        try:
            response = await asyncio.to_thread(self.client.post_heartbeat, payload)
            self._record_upload_success()
            self.apply_server_commands(response)
            LOGGER.info("sent heartbeat sequence=%s", _payload_sequence(payload))
        except TelemetryPostError as exc:
            self.spool.enqueue(payload)
            await self._record_upload_failure()
            LOGGER.warning("queued heartbeat sequence=%s error=%s", _payload_sequence(payload), exc)

    def _record_upload_success(self) -> None:
        if self._consecutive_upload_failures:
            LOGGER.info("server upload recovered after %s failures", self._consecutive_upload_failures)
        self._consecutive_upload_failures = 0

    async def _record_upload_failure(self) -> None:
        self._consecutive_upload_failures += 1
        await self._maybe_recover_network_connectivity()

    async def _maybe_recover_network_connectivity(self) -> None:
        if self._consecutive_upload_failures < NETWORK_RECOVERY_FAILURE_THRESHOLD:
            return

        now = time.monotonic()
        if now - self._last_network_recovery_monotonic < NETWORK_RECOVERY_COOLDOWN_SECONDS:
            return

        self._last_network_recovery_monotonic = now
        LOGGER.warning(
            "server uploads failed %s times; attempting cellular recovery",
            self._consecutive_upload_failures,
        )

        await self._recover_modem_sensors()
        messages = await asyncio.to_thread(restart_usb_cellular_connection)
        for message in messages:
            LOGGER.warning("cellular recovery: %s", message)

    async def _recover_modem_sensors(self) -> None:
        for sensor in self.sensors:
            recover = getattr(sensor, "recover_connectivity", None)
            if not callable(recover):
                continue
            try:
                await recover()
            except Exception:
                LOGGER.exception("sensor connectivity recovery failed: %s", getattr(sensor, "name", "unknown"))

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

    async def post_audio_events(self) -> None:
        for sensor in self.sensors:
            pop_events = getattr(sensor, "pop_audio_events", None)
            if not callable(pop_events):
                continue
            for event in pop_events():
                try:
                    response = await asyncio.to_thread(
                        self.client.post_audio_event,
                        boat_id=self.config.boat_id,
                        device_id=self.config.device_id,
                        sent_at=utc_now_iso(),
                        trigger=event.get("trigger", "audio_event"),
                        rms_db=event.get("rms_db"),
                        peak_db=event.get("peak_db"),
                        peak_over_rms_db=event.get("peak_over_rms_db"),
                        duration_seconds=event.get("duration_seconds"),
                        audio=event["wav"],
                    )
                    await self.capture_and_post_audio_event_snapshot(response)
                    LOGGER.info("sent audio event trigger=%s bytes=%s", event.get("trigger"), len(event["wav"]))
                except Exception as exc:
                    requeue_event = getattr(sensor, "requeue_audio_event", None)
                    if callable(requeue_event):
                        requeue_event(event)
                    LOGGER.warning("audio event upload failed: %s", exc)
                    return

    async def capture_and_post_audio_event_snapshot(self, response: dict[str, Any]) -> None:
        if not self.config.camera.enabled:
            return

        event_id = response.get("audio_event", {}).get("event_id")
        if not isinstance(event_id, str) or not event_id:
            return

        sent_at = utc_now_iso()
        try:
            image = await asyncio.to_thread(capture_snapshot, self.config.camera)
            await asyncio.to_thread(
                self.client.post_audio_event_snapshot,
                boat_id=self.config.boat_id,
                device_id=self.config.device_id,
                event_id=event_id,
                sent_at=sent_at,
                image=image,
            )
            LOGGER.info("sent audio event snapshot event_id=%s bytes=%s", event_id, len(image))
        except Exception as exc:
            LOGGER.warning("audio event snapshot failed: %s", exc)

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


def restart_usb_cellular_connection() -> list[str]:
    commands = [
        ["nmcli", "connection", "down", CELLULAR_CONNECTION_NAME],
        ["nmcli", "connection", "up", CELLULAR_CONNECTION_NAME],
    ]
    messages: list[str] = []

    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except FileNotFoundError:
            return ["nmcli not found; cannot restart NetworkManager cellular connection"]
        except subprocess.TimeoutExpired:
            messages.append(f"{' '.join(command)} timed out")
            continue

        output = (completed.stdout or completed.stderr).strip()
        if completed.returncode == 0:
            messages.append(f"{' '.join(command)} ok" + (f": {output}" if output else ""))
        else:
            messages.append(f"{' '.join(command)} failed rc={completed.returncode}" + (f": {output}" if output else ""))

    return messages


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


def current_speed_knots(sensors: dict[str, dict[str, Any]]) -> float | None:
    sim_gnss = sensors.get("sim7600", {}).get("gnss", {})
    gps = sensors.get("gps", {})
    for speed in (sim_gnss.get("speed_knots"), gps.get("speed_knots")):
        if isinstance(speed, int | float):
            return float(speed)
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
