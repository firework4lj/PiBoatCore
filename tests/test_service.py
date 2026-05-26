import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pi_boat_core.service import BoatTelemetryService, current_speed_knots


class ServiceTests(unittest.TestCase):
    def test_current_speed_knots_prefers_sim7600_gnss(self) -> None:
        self.assertEqual(
            current_speed_knots(
                {
                    "sim7600": {"gnss": {"speed_knots": 2.4}},
                    "gps": {"speed_knots": 0.2},
                }
            ),
            2.4,
        )

    def test_current_speed_knots_falls_back_to_gps(self) -> None:
        self.assertEqual(current_speed_knots({"gps": {"speed_knots": 1.2}}), 1.2)

    def test_current_speed_knots_handles_missing_speed(self) -> None:
        self.assertIsNone(current_speed_knots({"sim7600": {"gnss": {"fix": True}}}))

    def test_heartbeat_loop_keeps_running_after_tick_exception(self) -> None:
        async def run_test() -> int:
            service = BoatTelemetryService(
                config=SimpleNamespace(heartbeat_interval_seconds=0.01),
                client=None,
                spool=None,
                sensors=[],
            )
            calls = 0

            async def tick() -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("temporary failure")
                service.stop()

            service.tick = tick
            await service.run_heartbeats()
            return calls

        with self.assertLogs("piboatcore", level="ERROR"):
            self.assertEqual(asyncio.run(run_test()), 2)

    def test_upload_failures_trigger_cellular_recovery(self) -> None:
        async def run_test() -> tuple[int, bool]:
            class RecoverableSensor:
                name = "sim7600"

                def __init__(self) -> None:
                    self.recovered = False

                async def read(self) -> dict:
                    return {}

                async def recover_connectivity(self) -> None:
                    self.recovered = True

            sensor = RecoverableSensor()
            service = BoatTelemetryService(
                config=SimpleNamespace(heartbeat_interval_seconds=0.01),
                client=None,
                spool=None,
                sensors=[sensor],
            )

            with patch("pi_boat_core.service.restart_usb_cellular_connection", return_value=["ok"]) as restart:
                for _ in range(5):
                    await service._record_upload_failure()
                return restart.call_count, sensor.recovered

        with self.assertLogs("piboatcore", level="WARNING"):
            self.assertEqual(asyncio.run(run_test()), (1, True))


if __name__ == "__main__":
    unittest.main()
