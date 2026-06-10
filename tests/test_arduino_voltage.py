import unittest

from pi_boat_core.config import ArduinoVoltageConfig
from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageSensor, analyze_engine_window
from pi_boat_core.sensors.arduino_voltage import ArduinoVoltageError, heartbeat_voltage_fields, parse_voltage_line


class ArduinoVoltageParserTests(unittest.TestCase):
    def test_parse_voltage_line_reads_uno_payload(self) -> None:
        reading = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":678,'
            '"voltage":12.420,"charging":false,"soc_estimate_percent":80,'
            '"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":8,"tach_rejected":3,"interval_ms":50}'
        )

        self.assertEqual(reading["pin"], "A0")
        self.assertEqual(reading["voltage_raw"], 678)
        self.assertAlmostEqual(reading["voltage"], 12.42, places=5)
        self.assertEqual(reading["voltage_source"], "arduino")
        self.assertFalse(reading["charging"])
        self.assertEqual(reading["soc_estimate_percent"], 80)
        self.assertEqual(reading["map_pin"], "A1")
        self.assertEqual(reading["map_raw"], 412)
        self.assertAlmostEqual(reading["map_voltage"], 2.014, places=3)
        self.assertAlmostEqual(reading["map_kpa"], 45.95, places=2)
        self.assertAlmostEqual(reading["map_load_percent"], 16.85, places=2)
        self.assertEqual(reading["tach_pin"], "D2")
        self.assertEqual(reading["tach_pulses"], 8)
        self.assertEqual(reading["tach_rejected"], 3)
        self.assertEqual(reading["tach_interval_ms"], 50)
        self.assertEqual(reading["rpm"], 4800.0)

    def test_parse_voltage_line_keeps_raw_voltage_fallback_for_old_uno_payload(self) -> None:
        reading = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
            '"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":8,"tach_rejected":3,"interval_ms":50}'
        )

        self.assertAlmostEqual(reading["voltage"], 9.494135, places=5)
        self.assertEqual(reading["soc_estimate_percent"], 0)
        self.assertEqual(reading["voltage_source"], "raw_calculated")

    def test_parse_voltage_line_uses_calibrated_raw_when_arduino_voltage_disagrees(self) -> None:
        reading = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":690,'
            '"voltage":16.862,"charging":true,"soc_estimate_percent":100,'
            '"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":0,"tach_rejected":0,"interval_ms":50}'
        )

        self.assertAlmostEqual(reading["voltage"], 12.646628, places=5)
        self.assertEqual(reading["voltage_source"], "raw_calculated")

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

    def test_rolling_rpm_uses_stable_window(self) -> None:
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
        self.assertEqual(payload["rpm_window"], 150.0)
        self.assertEqual(payload["rpm_filtered"], 150.0)
        self.assertLess(abs(payload["rpm"] - payload["rpm_window"]), 60)
        self.assertAlmostEqual(payload["rpm_window_seconds"], 1.0)

    def test_rolling_rpm_rejects_impossible_spike(self) -> None:
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

        payload = {}
        for index in range(40):
            pulses = 1 if index % 4 == 0 else 0
            payload = parse_voltage_line(
                '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
                f'"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":{pulses},"interval_ms":50}}'
            )
            sensor._apply_rolling_rpm(payload, 100.0 + (index * 0.05))

        stable_rpm = payload["rpm"]

        spike = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
            '"map_pin":"A1","map_raw":412,"tach_pin":"D2","tach_pulses":400,"interval_ms":50}'
        )
        sensor._apply_rolling_rpm(spike, 102.1)

        self.assertTrue(spike["rpm_rejected"])
        self.assertEqual(spike["rpm"], stable_rpm)

    def test_rolling_rpm_suppresses_engine_off_tach_noise(self) -> None:
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
        payload = parse_voltage_line(
            '{"type":"engine_raw","voltage_pin":"A0","voltage_raw":518,'
            '"map_pin":"A1","map_raw":1010,"tach_pin":"D2","tach_pulses":4,'
            '"tach_rejected":671,"interval_ms":50}'
        )

        sensor._apply_rolling_rpm(payload, 100.0)

        self.assertTrue(payload["tach_noise"])
        self.assertGreater(payload["rpm_instant"], 0)
        self.assertEqual(payload["rpm"], 0.0)

    def test_map_smoothing_publishes_average_and_offset_load(self) -> None:
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
        payload = {"map_kpa": 35.0, "map_load_percent": 0.0}
        sensor._apply_map_smoothing(payload)
        payload = {"map_kpa": 85.0, "map_load_percent": 76.92}
        sensor._apply_map_smoothing(payload)

        self.assertGreater(payload["map_kpa_avg"], 35.0)
        self.assertLess(payload["map_kpa_avg"], 85.0)
        self.assertEqual(payload["map_load_raw_percent"], 76.92)
        self.assertLess(payload["map_load_percent"], payload["map_load_raw_percent"])

    def test_analyze_engine_window_scores_stable_idle(self) -> None:
        samples = [
            {"timestamp": index * 0.5, "rpm": 720 + (index % 2) * 8, "map_kpa": 36 + (index % 2), "load_percent": 2, "voltage": 13.8}
            for index in range(20)
        ]

        analysis = analyze_engine_window(samples)

        self.assertEqual(analysis["engine_state"], "idle")
        self.assertEqual(analysis["idle_quality"], "good")
        self.assertEqual(analysis["map_stability"], "good")
        self.assertFalse(analysis["bog_detected"])
        self.assertFalse(analysis["stall_risk"])

    def test_analyze_engine_window_detects_bog(self) -> None:
        samples = [
            {"timestamp": 0.0, "rpm": 1600, "map_kpa": 45, "load_percent": 15, "voltage": 13.8},
            {"timestamp": 0.5, "rpm": 1500, "map_kpa": 52, "load_percent": 26, "voltage": 13.8},
            {"timestamp": 1.0, "rpm": 1410, "map_kpa": 59, "load_percent": 37, "voltage": 13.8},
            {"timestamp": 1.5, "rpm": 1360, "map_kpa": 62, "load_percent": 42, "voltage": 13.8},
            {"timestamp": 2.0, "rpm": 1320, "map_kpa": 64, "load_percent": 45, "voltage": 13.8},
        ]

        analysis = analyze_engine_window(samples)

        self.assertTrue(analysis["bog_detected"])
