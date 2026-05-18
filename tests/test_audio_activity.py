import unittest

from pi_boat_core.config import AudioActivityConfig
from pi_boat_core.sensors.audio_activity import (
    AudioActivitySensor,
    CLIP_COOLDOWN_SECONDS,
    analyze_pcm16,
    audio_event_trigger,
    amplitude_to_db,
    classify_audio_activity,
    is_impact_sample,
    pcm16_to_wav,
)


class AudioActivityTests(unittest.TestCase):
    def test_audio_sensor_enters_cooldown_after_burst(self) -> None:
        sensor = AudioActivitySensor(test_audio_config())

        sensor._record_event_time(100)
        sensor._record_event_time(140)
        sensor._record_event_time(180)

        self.assertEqual(sensor._cooldown_until_monotonic, 180 + CLIP_COOLDOWN_SECONDS)
        self.assertEqual(sensor._clips_recent_locked(180), 3)

    def test_audio_sensor_suppression_clears_active_event(self) -> None:
        sensor = AudioActivitySensor(test_audio_config())
        sensor._active_event = {"trigger": "impact"}

        sensor.set_clip_suppressed(True, "underway")

        self.assertTrue(sensor._clip_suppressed)
        self.assertEqual(sensor._clip_suppressed_reason, "underway")
        self.assertIsNone(sensor._active_event)

    def test_amplitude_to_db_handles_silence_and_full_scale(self) -> None:
        self.assertEqual(amplitude_to_db(0), -120.0)
        self.assertAlmostEqual(amplitude_to_db(32767), 0.0, places=3)

    def test_analyze_pcm16_returns_rms_and_peak(self) -> None:
        chunk = (
            int(0).to_bytes(2, byteorder="little", signed=True)
            + int(3000).to_bytes(2, byteorder="little", signed=True)
            + int(-4000).to_bytes(2, byteorder="little", signed=True)
        )

        rms, peak = analyze_pcm16(chunk)

        self.assertAlmostEqual(rms, 2886.75, places=2)
        self.assertEqual(peak, 4000)

    def test_pcm16_to_wav_wraps_audio_data(self) -> None:
        pcm = int(1000).to_bytes(2, byteorder="little", signed=True) * 10
        wav = pcm16_to_wav(pcm, sample_rate=16000)

        self.assertTrue(wav.startswith(b"RIFF"))
        self.assertIn(b"WAVE", wav[:16])
        self.assertGreater(len(wav), len(pcm))

    def test_is_impact_sample_requires_loud_sharp_spike(self) -> None:
        self.assertFalse(
            is_impact_sample(
                rms_db=-22.9,
                peak_db=-6.4,
                impact_threshold_db=-4,
                min_peak_delta_db=20,
            ),
        )
        self.assertTrue(
            is_impact_sample(
                rms_db=-31,
                peak_db=-3,
                impact_threshold_db=-4,
                min_peak_delta_db=20,
            ),
        )

    def test_audio_event_trigger_detects_impacts_and_heavy_activity(self) -> None:
        self.assertEqual(
            audio_event_trigger(
                rms_db=-31,
                peak_db=-3,
                impact_threshold_db=-4,
                min_peak_delta_db=20,
                heavy_threshold_db=-18,
            ),
            "impact",
        )
        self.assertEqual(
            audio_event_trigger(
                rms_db=-17,
                peak_db=-12,
                impact_threshold_db=-4,
                min_peak_delta_db=20,
                heavy_threshold_db=-18,
            ),
            "heavy_activity",
        )
        self.assertIsNone(
            audio_event_trigger(
                rms_db=-30,
                peak_db=-10,
                impact_threshold_db=-4,
                min_peak_delta_db=20,
                heavy_threshold_db=-18,
            ),
        )

    def test_classify_audio_activity_uses_rolling_levels(self) -> None:
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-50,
                peak_db=-40,
                impact_count=0,
                moderate_threshold_db=-32,
                heavy_threshold_db=-22,
            ),
            "calm",
        )
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-38,
                peak_db=-30,
                impact_count=0,
                moderate_threshold_db=-32,
                heavy_threshold_db=-22,
            ),
            "mild_activity",
        )
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-28,
                peak_db=-20,
                impact_count=0,
                moderate_threshold_db=-32,
                heavy_threshold_db=-22,
            ),
            "moderate_activity",
        )
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-18,
                peak_db=-12,
                impact_count=0,
                moderate_threshold_db=-32,
                heavy_threshold_db=-18,
            ),
            "heavy_activity",
        )
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-40,
                peak_db=-3,
                impact_count=1,
                moderate_threshold_db=-32,
                heavy_threshold_db=-18,
            ),
            "possible_impact",
        )
        self.assertEqual(
            classify_audio_activity(
                avg_rms_db=-40,
                peak_db=-12,
                impact_count=3,
                moderate_threshold_db=-32,
                heavy_threshold_db=-18,
            ),
            "impact_detected",
        )


def test_audio_config() -> AudioActivityConfig:
    return AudioActivityConfig(
        enabled=True,
        device="default",
        sample_rate=16000,
        chunk_seconds=0.5,
        window_seconds=60,
        impact_threshold_db=-4,
        impact_min_peak_delta_db=20,
        moderate_threshold_db=-32,
        heavy_threshold_db=-18,
    )


if __name__ == "__main__":
    unittest.main()
