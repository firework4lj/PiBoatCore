import unittest

from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageError, heartbeat_voltage_fields, parse_voltage_line


class ArduinoVoltageParserTests(unittest.TestCase):
    def test_parse_voltage_line_reads_uno_payload(self) -> None:
        reading = parse_voltage_line(
            '{"type":"battery_voltage","pin":"A0","voltage":12.647,"charging":false,"soc_estimate_percent":90,'
            '"map_pin":"A1","map_raw":412,"map_voltage":2.014,"map_kpa":45.9,'
            '"tach_pin":"D2","tach_pulses":8,"rpm":960.0}'
        )

        self.assertEqual(reading["pin"], "A0")
        self.assertEqual(reading["voltage"], 12.647)
        self.assertFalse(reading["charging"])
        self.assertEqual(reading["soc_estimate_percent"], 90)
        self.assertEqual(reading["map_pin"], "A1")
        self.assertEqual(reading["map_raw"], 412)
        self.assertEqual(reading["map_voltage"], 2.014)
        self.assertEqual(reading["map_kpa"], 45.9)
        self.assertEqual(reading["tach_pin"], "D2")
        self.assertEqual(reading["tach_pulses"], 8)
        self.assertEqual(reading["rpm"], 960.0)

    def test_parse_voltage_line_infers_charging_when_missing(self) -> None:
        reading = parse_voltage_line('{"type":"battery_voltage","voltage":13.8}')

        self.assertTrue(reading["charging"])

    def test_heartbeat_voltage_fields_omits_engine_metrics(self) -> None:
        reading = parse_voltage_line(
            '{"type":"battery_voltage","pin":"A0","voltage":12.4,"map_kpa":48.2,"rpm":850}'
        )

        self.assertEqual(
            heartbeat_voltage_fields(reading),
            {
                "pin": "A0",
                "voltage": 12.4,
                "charging": False,
                "soc_estimate_percent": None,
            },
        )

    def test_parse_voltage_line_rejects_invalid_payload(self) -> None:
        with self.assertRaises(ArduinoVoltageError):
            parse_voltage_line('{"type":"other","voltage":12.0}')
