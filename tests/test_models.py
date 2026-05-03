import unittest

from pi_boat_core.models import build_compact_heartbeat, build_heartbeat


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

    def test_build_compact_heartbeat_uses_t_csv_payload(self) -> None:
        payload = build_compact_heartbeat(
            boat_id="boat",
            device_id="pi",
            sequence=7,
            sensors={
                "system": {"status": "ok", "uptime_seconds": 12.3},
                "sim7600": {
                    "status": "ok",
                    "consecutive_failures": 0,
                    "signal": {"rssi_dbm": -83},
                    "registration": {"registered": True},
                    "operator": {"name": "Dark Star"},
                    "network": {"system_mode": "LTE"},
                    "gnss": {
                        "fix": True,
                        "latitude": 45.5,
                        "longitude": -122.7,
                        "speed_knots": 0,
                        "course_degrees": None,
                        "altitude_meters": 22.1,
                    },
                },
            },
        )

        fields = payload["t"].split(",")
        self.assertEqual(fields[0], "1")
        self.assertEqual(fields[1], "boat")
        self.assertEqual(fields[2], "pi")
        self.assertEqual(fields[3], "7")
        self.assertEqual(fields[5], "ok")
        self.assertEqual(fields[10], "-83")


if __name__ == "__main__":
    unittest.main()
