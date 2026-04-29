import unittest

from pi_boat_core.models import build_heartbeat


class HeartbeatModelTests(unittest.TestCase):
    def test_build_heartbeat_marks_all_ok_payload_ok(self) -> None:
        payload = build_heartbeat(
            boat_id="boat",
            device_id="pi",
            sequence=1,
            sensors={"gps": {"status": "ok"}},
        )

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["boat_id"], "boat")
        self.assertEqual(payload["device_id"], "pi")
        self.assertEqual(payload["sequence"], 1)
        self.assertTrue(payload["sent_at"].endswith("Z"))

    def test_build_heartbeat_marks_sensor_error_degraded(self) -> None:
        payload = build_heartbeat(
            boat_id="boat",
            device_id="pi",
            sequence=1,
            sensors={"gps": {"status": "error"}},
        )

        self.assertEqual(payload["status"], "degraded")


if __name__ == "__main__":
    unittest.main()
