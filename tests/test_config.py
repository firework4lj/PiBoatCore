import tempfile
import unittest
from pathlib import Path

from pi_boat_core.config import Config


class ConfigTests(unittest.TestCase):
    def test_from_file_reads_toml_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.toml"
            path.write_text(
                """
                [boat]
                boat_id = "sea-rose"
                device_id = "forward-pi"

                [server]
                url = "https://boat.example/api/heartbeat"
                request_timeout_seconds = 5

                [heartbeat]
                interval_seconds = 10

                [storage]
                spool_db_path = "/tmp/piboat-spool.db"

                [sensors]
                mock_sensors = false
                """,
                encoding="utf-8",
            )

            config = Config.from_file(path)

        self.assertEqual(config.boat_id, "sea-rose")
        self.assertEqual(config.device_id, "forward-pi")
        self.assertEqual(config.server_url, "https://boat.example/api/heartbeat")
        self.assertEqual(config.request_timeout_seconds, 5)
        self.assertEqual(config.heartbeat_interval_seconds, 10)
        self.assertEqual(config.spool_db_path, "/tmp/piboat-spool.db")
        self.assertFalse(config.mock_sensors)

    def test_from_file_uses_defaults_when_no_file_is_available(self) -> None:
        config = Config.from_file(None)

        self.assertEqual(config.boat_id, "my-boat")
        self.assertEqual(config.device_id, "raspberry-pi-bridge")
        self.assertEqual(config.server_url, "http://localhost:3000/api/heartbeat")
        self.assertEqual(config.heartbeat_interval_seconds, 30)
        self.assertEqual(config.spool_db_path, "./spool.db")
        self.assertEqual(config.request_timeout_seconds, 8)
        self.assertTrue(config.mock_sensors)
