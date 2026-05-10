import unittest
from unittest.mock import patch

from pi_boat_core.camera import capture_snapshot
from pi_boat_core.config import CameraConfig


class CameraTests(unittest.TestCase):
    def test_capture_snapshot_passes_rotation_when_configured(self) -> None:
        captured = {}

        def fake_run(command, **_kwargs):
            captured["command"] = command
            output_path = command[-1]
            with open(output_path, "wb") as file:
                file.write(b"jpg")

            class Result:
                returncode = 0
                stderr = ""
                stdout = ""

            return Result()

        config = CameraConfig(
            enabled=True,
            device="/dev/video0",
            interval_seconds=300,
            width=640,
            height=360,
            jpeg_quality=55,
            capture_command="fswebcam",
            rotation_degrees=180,
        )

        with patch("subprocess.run", fake_run):
            image = capture_snapshot(config)

        self.assertEqual(image, b"jpg")
        self.assertIn("--rotate", captured["command"])
        self.assertIn("180", captured["command"])
