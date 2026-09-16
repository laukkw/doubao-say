"""Protected delivery failures; no desktop, keyboard, clipboard manager or audio."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from doubao_input.inject.clipboard import PreservedClipboard, RESTORE, SNAPSHOT
from doubao_input.inject.injector import Injector


class ClipboardTest(unittest.TestCase):
    def test_snapshot_failure_reports_actual_reason_without_touching_clipboard(self):
        cases = (
            ("Clipboard exceeds 32 MiB snapshot limit", "exceeds 32 MiB"),
            ("Clipboard has too many formats", "exceeds 128 formats"),
            ("Clipboard PNG unavailable", "preserve the clipboard image"),
            ("Unrelated failure", "Check that CopyQ is running"),
        )
        for reason, message in cases:
            with self.subTest(reason=reason):
                # CopyQ includes the source script in stderr. Its other throw
                # strings must not be mistaken for the exception that occurred.
                failure = subprocess.CalledProcessError(4, ["copyq"],
                    stderr=("ScriptError: " + reason + "\n" + SNAPSHOT).encode())
                display = Mock()
                display.Display.return_value.get_selection_owner.return_value.id = 1
                clipboard = PreservedClipboard("text", "x11:1:2")
                with patch.dict("sys.modules", {"Xlib": SimpleNamespace(display=display)}), \
                     patch("doubao_input.inject.clipboard._copyq", side_effect=failure), \
                     patch("doubao_input.inject.clipboard.tr", side_effect=lambda en, zh: en), \
                     patch("doubao_input.inject.clipboard.threading.Thread") as worker:
                    with self.assertRaisesRegex(RuntimeError, message):
                        clipboard.__enter__()
                    worker.assert_not_called()
                self.assertFalse(clipboard.claimed)
                display.Display.return_value.close.assert_called_once()

    def test_focus_change_or_missing_provider_never_falls_back_to_destructive_copy(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                injector = Injector(preserve_clipboard=True)
                injector._simulate_paste = Mock()
                injector._copy_to_clipboard = Mock()
                with patch("doubao_input.inject.injector.is_x11", return_value=True), \
                     patch("doubao_input.inject.injector.focused_target", side_effect=["x11:1:2", "x11:3:4"]), \
                     patch("doubao_input.inject.clipboard.PreservedClipboard") as provider:
                    if unavailable:
                        provider.return_value.__enter__.side_effect = FileNotFoundError("copyq")
                    self.assertFalse(injector.inject("text", use_shift=False, expected_target="x11:1:2"))
                injector._simulate_paste.assert_not_called()
                injector._copy_to_clipboard.assert_not_called()
                if not unavailable:
                    provider.return_value.__exit__.assert_called_once()

    def test_key_dispatch_without_data_acknowledgement_is_not_success(self):
        injector = Injector(preserve_clipboard=True)
        injector._simulate_paste = Mock(return_value=True)
        with patch("doubao_input.inject.injector.is_x11", return_value=True), \
             patch("doubao_input.inject.injector.focused_target", return_value="x11:1:2"), \
             patch("doubao_input.inject.clipboard.PreservedClipboard") as provider:
            provider.return_value.__enter__.return_value.wait.return_value = False
            self.assertFalse(injector.inject("text", use_shift=False, expected_target="x11:1:2"))
            provider.return_value.__exit__.assert_called_once()

    def test_new_copy_or_cancellation_ends_wait_without_claiming_success(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                clipboard = PreservedClipboard("text", "x11:1:2")
                if lost:
                    clipboard.lost.set()
                self.assertFalse(clipboard.wait(lambda: not lost))

    def test_restore_has_ownership_token_and_snapshot_stays_off_disk_on_success(self):
        clipboard = PreservedClipboard("text", "x11:1:2")
        clipboard.claimed = True
        clipboard.snapshot = b"packed original formats"
        clipboard.lost.set()
        with patch("doubao_input.inject.clipboard._copyq") as copyq, \
             patch("doubao_input.inject.clipboard.tempfile.mkstemp") as disk:
            clipboard.__exit__(None, None, None)
            copyq.assert_called_once_with(RESTORE, data=b"packed original formats", args=(clipboard.token,))
            disk.assert_not_called()
        self.assertIsNone(clipboard.snapshot)
        self.assertTrue(clipboard.stop.is_set())

    def test_restore_failure_saves_private_original_instead_of_discarding_it(self):
        clipboard = PreservedClipboard("text", "x11:1:2")
        clipboard.claimed = True
        clipboard.snapshot = b"packed original formats"
        clipboard.thread = Mock()
        with tempfile.TemporaryDirectory() as folder, \
             patch.dict("os.environ", {"XDG_STATE_HOME": folder}), \
             patch("doubao_input.inject.clipboard._copyq", side_effect=OSError("CopyQ stopped")):
            with self.assertRaises(RuntimeError):
                clipboard.__exit__(None, None, None)
            files = list(Path(folder).rglob("*.copyq"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), b"packed original formats")
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        clipboard.thread.join.assert_called_once()
        self.assertTrue(clipboard.stop.is_set())

    def test_unbounded_payload_or_unknown_target_is_rejected_before_any_clipboard_access(self):
        for text, target in (("中" * 50000, "x11:1:2"), ("text", None), ("text", "wayland:1")):
            with self.subTest(target=target), self.assertRaises(ValueError):
                PreservedClipboard(text, target)
