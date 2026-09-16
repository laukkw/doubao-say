"""Key-down audio survives gesture arbitration without uploading accidental input."""
from unittest import TestCase
from unittest.mock import Mock, patch

from doubao_input.doubao.app_state import AppState, LoginStatus, RecordingState
from doubao_input.doubao.transcription import TranscriptionManager
from doubao_input.trigger.gesture import KeyGesture


class RecordingStartTest(TestCase):
    def setUp(self):
        self.manager = TranscriptionManager(AppState())
        self.manager.app_state.login_status = LoginStatus.LOGGED_IN
        self.manager.audio_capture = Mock()
        self.network = self.manager.asr_client.connect = Mock()
        self.pending = {}
        self.serial = 0
        def schedule(ms, callback):
            self.serial += 1
            self.pending[self.serial] = callback
            return self.serial
        self.enter = Mock()
        self.gesture = KeyGesture(
            self.manager.handle_toggle, self.manager.handle_toggle,
            self.manager.handle_toggle, self.enter, schedule, self.pending.pop,
            prepare=self.manager.prepare_recording, discard=self.manager.discard_prepared)
        self.params = patch("doubao_input.doubao.transcription.ParamsStore.load", return_value=Mock())
        self.params.start()
        self.addCleanup(self.params.stop)
        self.addCleanup(self.manager.handle_cancel)
        self.addCleanup(self.gesture.close)

    def fire(self):
        self.pending.pop(next(iter(self.pending)))()

    def audio(self, data):
        self.manager.audio_capture.start.call_args.kwargs["on_audio_data"](data)

    def test_audio_before_hold_or_tap_decision_is_kept_once(self):
        prefix, suffix = b"\x01\x00" * 1600, b"\x02\x00" * 1600
        for hold in (False, True):
            with self.subTest(hold=hold):
                self.gesture.press()
                self.audio(prefix)
                session = self.manager.asr_client._session
                self.network.assert_not_called()
                if not hold:
                    self.gesture.release()
                self.fire()
                self.audio(suffix)
                self.network.assert_called_once()
                self.manager.audio_capture.start.assert_called_once()
                self.assertIs(self.manager.asr_client._session, session)
                self.assertEqual(list(session.audio), [prefix, suffix])
                # Closing gesture state after confirmation must not abort dictation.
                self.gesture.close()
                self.assertIs(self.manager.asr_client._session, session)
                self.manager.handle_cancel()
                self.manager.audio_capture.reset_mock()
                self.network.reset_mock()

    def test_double_tap_discards_all_audio_without_connecting(self):
        self.gesture.press()
        self.audio(b"\x01\x00" * 1600)
        session = self.manager.asr_client._session
        self.gesture.release()
        self.gesture.press()
        self.gesture.release()
        self.enter.assert_called_once()
        self.network.assert_not_called()
        self.assertTrue(session.cancelled)
        self.assertFalse(session.audio)
        self.assertFalse(self.manager.prepared)
        self.assertEqual(self.manager.app_state.recording_state, RecordingState.IDLE)

    def test_cancelled_gesture_cannot_restart_or_upload_on_release(self):
        self.gesture.press()
        self.audio(b"\x01\x00" * 1600)
        self.gesture.close()
        self.gesture.release()
        self.network.assert_not_called()
        self.assertFalse(self.pending)
        self.assertIsNone(self.manager.asr_client._session)
        self.assertEqual(self.manager.app_state.recording_state, RecordingState.IDLE)

    def test_late_audio_from_cancelled_gesture_cannot_enter_next_recording(self):
        self.gesture.press()
        old_audio = self.manager.audio_capture.start.call_args.kwargs["on_audio_data"]
        self.gesture.close()
        self.gesture.press()
        old_audio(b"\x01\x00" * 1600)
        self.assertEqual(self.manager.asr_client.diagnostics()["audio_bytes"], 0)
        self.audio(b"\x02\x00" * 1600)
        self.assertEqual(list(self.manager.asr_client._session.audio), [b"\x02\x00" * 1600])

    def test_preparation_during_recording_does_not_replace_active_session(self):
        self.manager.handle_toggle()
        original = self.manager.asr_client._session
        self.manager.prepare_recording()
        self.manager.discard_prepared()
        self.assertIs(self.manager.asr_client._session, original)
        self.manager.audio_capture.start.assert_called_once()
        self.network.assert_called_once()

    def test_key_down_when_signed_out_does_not_start_audio_or_open_login(self):
        self.manager.app_state.login_status = LoginStatus.NOT_LOGGED_IN
        self.manager.on_show_login = Mock()
        self.gesture.press()
        self.manager.audio_capture.start.assert_not_called()
        self.manager.on_show_login.assert_not_called()
        self.network.assert_not_called()
