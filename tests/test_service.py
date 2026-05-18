import unittest

from pi_boat_core.service import current_speed_knots


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


if __name__ == "__main__":
    unittest.main()
