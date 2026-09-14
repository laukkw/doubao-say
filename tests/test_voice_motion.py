import unittest
from doubao_input.ui.voice_motion import VoiceMotion


class MotionTest(unittest.TestCase):
    def run_level(self, rms):
        motion = VoiceMotion()
        for _ in range(60):
            motion.advance(rms, 0, 1 / 60)
        return motion

    def test_silence_does_not_fake_speech(self):
        self.assertEqual(self.run_level(0).bars(48), [0] * 48)

    def test_quiet_speech_is_visible(self):
        self.assertGreater(self.run_level(0.01).level, 0.35)

    def test_loudness_is_monotonic(self):
        levels = [self.run_level(rms).level for rms in (0.003, 0.01, 0.04, 0.2)]
        self.assertEqual(levels, sorted(levels))

    def test_stale_audio_fades(self):
        motion = self.run_level(0.1)
        for i in range(180):
            motion.advance(0.1, i / 60, 1 / 60)
        self.assertLess(motion.level, 0.001)

    def test_bounds(self):
        for rms in (0, 0.02, 1, float('nan')):
            values = self.run_level(rms).bars(48)
            self.assertTrue(all(0 <= x <= 1 for x in values))
