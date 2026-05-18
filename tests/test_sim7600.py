import unittest
from unittest.mock import patch

from pi_boat_core.sensors.sim7600 import (
    distance_meters,
    parse_cgpsinfo,
    parse_cpsi,
    parse_csq,
    parse_operator,
    parse_registration,
    should_start_track_sampling,
    track_point_from_gnss,
)


class Sim7600ParserTests(unittest.TestCase):
    def test_parse_csq_converts_rssi_to_dbm(self) -> None:
        signal = parse_csq(["+CSQ: 20,99"])

        self.assertEqual(signal["status"], "ok")
        self.assertEqual(signal["rssi_raw"], 20)
        self.assertEqual(signal["rssi_dbm"], -73)

    def test_parse_registration_marks_home_registered(self) -> None:
        registration = parse_registration(["+CREG: 0,1"])

        self.assertEqual(registration["status"], "home")
        self.assertTrue(registration["registered"])

    def test_parse_operator_reads_name(self) -> None:
        operator = parse_operator(['+COPS: 0,0,"T-Mobile",7'])

        self.assertEqual(operator["name"], "T-Mobile")
        self.assertEqual(operator["access_technology"], "7")

    def test_parse_cpsi_reads_network_mode(self) -> None:
        network = parse_cpsi(["+CPSI: LTE,Online,310-260,0x1234,123456,3,EUTRAN-BAND2,900,5,5,-73,-950,-650,12"])

        self.assertEqual(network["status"], "ok")
        self.assertEqual(network["system_mode"], "LTE")

    def test_parse_cgpsinfo_reads_gnss_fix(self) -> None:
        gnss = parse_cgpsinfo(["+CGPSINFO: 3723.2475,N,12202.3416,W,123519.0,300424,12.5,0.4,181.2"])

        self.assertEqual(gnss["status"], "ok")
        self.assertTrue(gnss["fix"])
        self.assertAlmostEqual(gnss["latitude"], 37.387458333333335)
        self.assertAlmostEqual(gnss["longitude"], -122.03902666666666)

    def test_parse_cgpsinfo_handles_no_fix(self) -> None:
        gnss = parse_cgpsinfo(["+CGPSINFO: ,,,,,,,,"])

        self.assertEqual(gnss["status"], "searching")
        self.assertFalse(gnss["fix"])

    def test_track_point_from_gnss_uses_compact_shape(self) -> None:
        with patch("pi_boat_core.sensors.sim7600.datetime") as datetime:
            datetime.now.return_value.isoformat.return_value = "2026-05-18T12:00:00+00:00"
            point = track_point_from_gnss(
                {
                    "fix": True,
                    "latitude": 45.123456789,
                    "longitude": -122.987654321,
                    "speed_knots": 2.34,
                    "course_degrees": 181.24,
                }
            )

        self.assertEqual(point, ["2026-05-18T12:00:00Z", 45.1234568, -122.9876543, 2.3, 181.2])

    def test_distance_meters_estimates_gps_distance(self) -> None:
        distance = distance_meters(
            {"latitude": 45.0, "longitude": -122.0},
            {"latitude": 45.0, "longitude": -122.000387},
        )

        self.assertAlmostEqual(distance, 30.4, delta=1.0)

    def test_track_sampling_requires_significant_movement_or_sustained_speed(self) -> None:
        anchor = {"fix": True, "latitude": 45.0, "longitude": -122.0, "speed_knots": 0}

        self.assertFalse(
            should_start_track_sampling(
                anchor=anchor,
                current={"fix": True, "latitude": 45.00001, "longitude": -122.00001, "speed_knots": 1.2},
                speed_start_monotonic=None,
                now=100,
                start_speed_knots=1.0,
                sustained_seconds=5,
                start_distance_meters=30.48,
            )
        )
        self.assertTrue(
            should_start_track_sampling(
                anchor=anchor,
                current={"fix": True, "latitude": 45.00001, "longitude": -122.00001, "speed_knots": 1.2},
                speed_start_monotonic=100,
                now=106,
                start_speed_knots=1.0,
                sustained_seconds=5,
                start_distance_meters=30.48,
            )
        )
        self.assertTrue(
            should_start_track_sampling(
                anchor=anchor,
                current={"fix": True, "latitude": 45.0, "longitude": -122.0005, "speed_knots": 0.1},
                speed_start_monotonic=None,
                now=100,
                start_speed_knots=1.0,
                sustained_seconds=5,
                start_distance_meters=30.48,
            )
        )
