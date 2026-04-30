import unittest

from pi_boat_core.sensors.sim7600 import parse_cgpsinfo, parse_cpsi, parse_csq, parse_operator, parse_registration


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
