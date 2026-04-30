import unittest

from pi_boat_core.config import Sim7600Config
from pi_boat_core.sensors.sim7600 import Sim7600Sensor


class FlakySim7600Sensor(Sim7600Sensor):
    def __init__(self) -> None:
        super().__init__(
            Sim7600Config(
                enabled=True,
                port="/dev/ttyUSB2",
                baudrate=115200,
                timeout_seconds=2,
                enable_gnss=True,
                max_attempts=2,
                retry_delay_seconds=0,
            )
        )
        self.calls = 0

    def _read_once(self) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise OSError("temporary serial failure")
        return {
            "status": "ok",
            "port": self.config.port,
            "signal": {"status": "ok"},
            "registration": {"registered": True},
            "packet_registration": {"registered": True},
            "operator": {"status": "ok"},
            "network": {"status": "ok"},
            "gnss": {"fix": True},
        }


class FailedSim7600Sensor(Sim7600Sensor):
    def __init__(self) -> None:
        super().__init__(
            Sim7600Config(
                enabled=True,
                port="/dev/ttyUSB2",
                baudrate=115200,
                timeout_seconds=2,
                enable_gnss=True,
                max_attempts=2,
                retry_delay_seconds=0,
            )
        )

    def _read_once(self) -> dict:
        raise OSError("modem disappeared")


class Sim7600ResilienceTests(unittest.TestCase):
    def test_read_retries_transient_failures(self) -> None:
        sensor = FlakySim7600Sensor()

        payload = sensor._read_sync()

        self.assertEqual(sensor.calls, 2)
        self.assertEqual(payload["status"], "ok")

    def test_read_returns_error_payload_after_retries_exhausted(self) -> None:
        sensor = FailedSim7600Sensor()

        payload = sensor._read_sync()

        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["consecutive_failures"], 1)
        self.assertIn("modem disappeared", payload["error"])

    def test_error_payload_includes_last_known_after_prior_success(self) -> None:
        sensor = FlakySim7600Sensor()
        sensor._read_sync()
        sensor._read_once = lambda: (_ for _ in ()).throw(OSError("gone"))

        payload = sensor._read_sync()

        self.assertEqual(payload["status"], "error")
        self.assertIn("last_known", payload)
        self.assertEqual(payload["last_known"]["gnss"], {"fix": True})
