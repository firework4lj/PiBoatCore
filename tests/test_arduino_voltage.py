import unittest

from pi_boat_core.config import ArduinoVoltageConfig
from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageSensor
from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageError, heartbeat_voltage_fields, parse_voltage_line


class ArduinoVoltageParserTests(unittest.TestCase):
    def test_parse_voltage_line_reads_uno_payload(self) -> None:
        reading = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
            '"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":8,"interval_ms":50}'
        )

        self.assertEqual(reading["pin"], "A0")
        self.assertEqual(reading["voltage_raw"], 518)
        self.assertAlmostEqual(reading["voltage"], 12.658847, places=5)
        self.assertFalse(reading["charging"])
        self.assertEqual(reading["soc_estimate_percent"], 90)
        self.assertEqual(reading["map_pin"], "A1")
        self.assertEqual(reading["map_raw"], 412)
        self.assertAlmostEqual(reading["map_voltage"], 2.014, places=3)
        self.assertAlmostEqual(reading["map_kpa"], 45.95, places=2)
        self.assertEqual(reading["tach_pin"], "D2")
        self.assertEqual(reading["tach_pulses"], 8)
        self.assertEqual(reading["tach_interval_ms"], 50)
        self.assertEqual(reading["rpm"], 19200.0)

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

    def test_rolling_rpm_uses_one_second_window(self) -> None:
        sensor = ArduinoVoltageSensor(
            ArduinoVoltageConfig(
                enabled=True,
                port="/dev/null",
                baudrate=115200,
                timeout_seconds=1,
                max_attempts=1,
                retry_delay_seconds=0.1,
            )
        )
        for index in range(20):
            pulses = 1 if index % 4 == 0 else 0
            payload = parse_voltage_line(
                '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
                f'"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":{pulses},"interval_ms":50}}'
            )
            sensor._apply_rolling_rpm(payload, 100.0 + (index * 0.05))

        self.assertEqual(payload["rpm_instant"], 0.0)
        self.assertEqual(payload["rpm"], 600.0)
        self.assertAlmostEqual(payload["rpm_window_seconds"], 1.0)
