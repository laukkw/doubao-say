"""Audio-driven visual envelope, independent of GTK and capture frequency."""
import math


class VoiceMotion:
    def __init__(self):
        self.level = 0.0
        self.phase = 0.0

    def advance(self, rms, age, elapsed):
        rms = rms if math.isfinite(rms) else 0.0
        # Speech is normally far below full scale: map -54…-16 dB to visuals.
        db = 20 * math.log10(max(1e-9, rms))
        target = max(0.0, min(1.0, (db + 54) / 38)) ** 0.85
        target *= math.exp(-max(0.0, age - 0.30) / 0.18)
        elapsed = max(0.0, min(elapsed, 0.1))
        tau = 0.055 if target > self.level else 0.23
        self.level += (target - self.level) * (1 - math.exp(-elapsed / tau))
        self.phase += elapsed * (3 + 7 * self.level)

    def bars(self, count):
        values = []
        for i in range(count):
            x = 2 * i / max(1, count - 1) - 1
            shape = 0.18 + 0.82 * math.cos(x * math.pi / 2) ** 0.8
            ripple = (0.58 + 0.26 * math.sin(x * 8 - self.phase)
                      + 0.16 * math.sin(x * 17 + self.phase * 1.3))
            values.append(self.level * shape * ripple)
        return values
