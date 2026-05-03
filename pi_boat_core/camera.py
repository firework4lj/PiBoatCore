from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from pi_boat_core.config import CameraConfig


class CameraCaptureError(RuntimeError):
    pass


def capture_snapshot(config: CameraConfig) -> bytes:
    with tempfile.TemporaryDirectory() as temp_dir:
        output_path = Path(temp_dir) / "snapshot.jpg"
        command = [
            config.capture_command,
            "--device",
            config.device,
            "--resolution",
            f"{config.width}x{config.height}",
            "--jpeg",
            str(config.jpeg_quality),
            "--quiet",
            str(output_path),
        ]

        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or f"{config.capture_command} failed"
            raise CameraCaptureError(error)

        return output_path.read_bytes()
