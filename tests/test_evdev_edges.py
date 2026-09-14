import struct
import unittest
from doubao_input.trigger.evdev_ptt import EvdevPtt


class EdgesTest(unittest.TestCase):
    def test_remapped_duplicate_edges(self):
        events = []
        listener = EvdevPtt(lambda: None, lambda: None,
                            on_key=lambda k, down: events.append((k, down)))
        def emit(value, source):
            listener._dispatch(struct.pack('llHHi', 0, 0, 1, 464, value),
                               lambda fn, *args: fn(*args), source)
        emit(1, 10)
        emit(1, 11)
        emit(2, 10)
        emit(0, 10)
        self.assertEqual(events, [(464, True)])
        emit(0, 11)
        emit(0, 11)
        self.assertEqual(events, [(464, True), (464, False)])
