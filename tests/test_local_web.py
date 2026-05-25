import tempfile
import unittest
from pathlib import Path

from pi_boat_core.local_web import EngineRunStore


class EngineRunStoreTests(unittest.TestCase):
    def test_saves_lists_and_deletes_engine_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = EngineRunStore(Path(directory) / "engine_runs.json")

            run = store.save(
                {
                    "id": "dock-test",
                    "name": "Dock test",
                    "samples": [
                        {"timestamp": 1000, "rpm": 700, "mapKpaAvg": 35.0, "loadPercent": 0.0, "voltage": 13.7},
                        {"timestamp": 2000, "rpm": 760, "mapKpaAvg": 37.0, "loadPercent": 3.1, "voltage": 13.8},
                    ],
                }
            )

            runs = store.list_runs()
            result = store.delete(run["id"])

            self.assertEqual(run["name"], "Dock test")
            self.assertEqual(run["stats"]["duration_seconds"], 1.0)
            self.assertEqual(run["stats"]["average_rpm"], 730.0)
            self.assertEqual(len(runs), 1)
            self.assertEqual(result["deleted"], 1)
            self.assertEqual(store.list_runs(), [])

