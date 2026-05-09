import unittest

from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageError, parse_voltage_line


class ArduinoVoltageParserTests(unittest.TestCase):
    def test_parse_voltage_line_reads_uno_payload(self) -> None:
        reading = parse_voltage_line(
            '{"type":"battery_voltage","pin":"A1","voltage":12.647,"charging":false,"soc_estimate_percent":90}'
        )

        self.assertEqual(reading["pin"], "A1")
        self.assertEqual(reading["voltage"], 12.647)
        self.assertFalse(reading["charging"])
        self.assertEqual(reading["soc_estimate_percent"], 90)

    def test_parse_voltage_line_infers_charging_when_missing(self) -> None:
        reading = parse_voltage_line('{"type":"battery_voltage","voltage":13.8}')

        self.assertTrue(reading["charging"])

    def test_parse_voltage_line_rejects_invalid_payload(self) -> None:
        with self.assertRaises(ArduinoVoltageError):
            parse_voltage_line('{"type":"other","voltage":12.0}')
