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

    def test_build_heartbeat_embeds_audio_activity_for_temporary_display(self) -> None:
        payload = build_heartbeat(
            boat_id="boat",
            device_id="pi",
            sequence=1,
            sensors={
                "sim7600": {
                    "status": "ok",
                    "operator": {"name": "Dark Star"},
                    "network": {"system_mode": "LTE"},
                },
                "audio_activity": {"status": "ok", "state": "heavy_activity", "impact_count_1m": 4},
            },
        )

        self.assertEqual(payload["device_id"], "pi")
        self.assertEqual(payload["status"], "ok audio:heavy_activity impacts:4")
        self.assertEqual(payload["sensors"]["sim7600"]["operator"]["name"], "Dark Star audio:heavy_activity impacts:4")
        self.assertEqual(payload["sensors"]["sim7600"]["network"]["system_mode"], "LTE audio:heavy_activity impacts:4")
        self.assertEqual(payload["sensors"]["audio_activity"]["state"], "heavy_activity")

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
                "arduino_voltage": {
                    "status": "ok",
                    "voltage": 12.7,
                    "charging": False,
                    "soc_estimate_percent": 100,
                },
                "audio_activity": {
                    "status": "ok",
                    "state": "moderate_activity",
                    "impact_count_1m": 2,
                },
            },
        )

        fields = payload["t"].split(",")
        self.assertEqual(fields[0], "1")
        self.assertEqual(fields[1], "boat")
        self.assertEqual(fields[2], "pi audio:moderate_activity impacts:2")
        self.assertEqual(fields[3], "7")
        self.assertEqual(fields[5], "ok audio:moderate_activity impacts:2")
        self.assertEqual(fields[10], "-83")
        self.assertEqual(fields[12], "Dark Star audio:moderate_activity impacts:2")
        self.assertEqual(fields[13], "LTE audio:moderate_activity impacts:2")
        self.assertEqual(fields[20], "ok")
        self.assertEqual(fields[21], "12.7")


if __name__ == "__main__":
    unittest.main()
