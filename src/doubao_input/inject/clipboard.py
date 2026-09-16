"""Temporary X11 selection with CopyQ format preservation and read acknowledgement.

Clipboard stays in memory; a failed restore saves a private recovery file.
CopyQ's hidden MIME flag excludes dictation from history. XRes identifies the target process so reads
by clipboard managers don't count as delivery. Requires native X11 and CopyQ.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
import select
import subprocess
import threading
import time
import uuid

from doubao_input.i18n import tr

logger = logging.getLogger(__name__)
TOKEN_MIME = "application/x-doubao-say-transfer"
SNAPSHOT = r"""
var item = {}, total = 0;
var formats = str(clipboard('?')).split(String.fromCharCode(10));
if (formats.length > 128) throw 'Clipboard has too many formats';
// Qt offers many encodings of one QImage. Keep its lossless representation;
// requesting every alias expands an ordinary screenshot beyond the size limit.
var qtImage = formats.indexOf('application/x-qt-image') >= 0 && formats.indexOf('image/png') >= 0;
var imageAliases = ['application/x-qt-image', 'image/bmp', 'image/cur', 'image/icns',
    'image/ico', 'image/jfif', 'image/jp2', 'image/jpeg', 'image/jpg', 'image/pbm',
    'image/pgm', 'image/ppm', 'image/tif', 'image/tiff', 'image/wbmp', 'image/webp',
    'image/xbm', 'image/xpm', 'BITMAP', 'PIXMAP'];
formats.forEach(function(format) {
    if (qtImage && imageAliases.indexOf(format) >= 0) return;
    if (format && ['TARGETS', 'TIMESTAMP', 'MULTIPLE', 'SAVE_TARGETS'].indexOf(format) < 0) {
        var value = clipboard(format);
        if (qtImage && format === 'image/png' && !value.size()) throw 'Clipboard PNG unavailable';
        total += value.size();
        if (total > 32 * 1024 * 1024) throw 'Clipboard exceeds 32 MiB snapshot limit';
        item[format] = value;
    }
});
print(pack(item));
"""
RESTORE = """
if (str(clipboard('application/x-doubao-say-transfer')) === str(arguments[1]))
    copy(unpack(input()));
"""


def _copyq(script, *, data=None, args=()):
    return subprocess.run(["copyq", "eval", script, *args], input=data,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=5, check=True).stdout


class PreservedClipboard:
    """Serve one paste, then restore only if our selection is still current."""

    def __init__(self, text, target):
        self.data = text.encode("utf-8")
        # ponytail: inline selection capped at 128 KiB; add ICCCM INCR for larger dictation.
        if len(self.data) > 128 * 1024:
            raise ValueError(tr("Text exceeds the protected clipboard limit; copy it manually.",
                                "文字超过剪贴板保护上限，请手动复制。"))
        if not target or not target.startswith("x11:"):
            raise ValueError(tr("Clipboard protection requires a known X11 target.",
                                "剪贴板保护需要可确认的 X11 目标窗口。"))
        self.pid = int(target.rsplit(":", 1)[1])
        self.token = uuid.uuid4().hex
        self.ready = threading.Event()
        self.consumed = threading.Event()
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.error = None
        self.claimed = False
        self.thread = None
        self.snapshot = None

    def __enter__(self):
        from Xlib import display
        for _ in range(3):
            probe = display.Display()
            try:
                atom = probe.intern_atom("CLIPBOARD")
                owner = probe.get_selection_owner(atom)
                self.previous_owner = owner.id if owner else 0
            finally:
                probe.close()
            try:
                self.snapshot = _copyq(SNAPSHOT)
            except (OSError, subprocess.SubprocessError) as error:
                reason = "copyq_error"
                message = tr("Could not read the original clipboard. Check that CopyQ is running.",
                             "无法读取原剪贴板，请确认 CopyQ 正在运行。")
                lines = (getattr(error, "stderr", None) or b"").splitlines()
                if b"ScriptError: Clipboard exceeds 32 MiB snapshot limit" in lines:
                    reason = "size_limit"
                    message = tr("Original clipboard exceeds 32 MiB; it was left unchanged. Text is kept for retry.",
                                 "原剪贴板数据超过 32 MiB，未改动原内容；识别文字已保留，可稍后重试。")
                elif b"ScriptError: Clipboard has too many formats" in lines:
                    reason = "format_limit"
                    message = tr("Original clipboard exceeds 128 formats; it was left unchanged.",
                                 "原剪贴板超过 128 种格式，未改动原内容。")
                elif b"ScriptError: Clipboard PNG unavailable" in lines:
                    reason = "image_unavailable"
                    message = tr("Could not preserve the clipboard image; it was left unchanged.",
                                 "无法保存剪贴板中的图片，未改动原内容。")
                logger.warning("Clipboard snapshot failed: %s", reason)
                raise RuntimeError(message) from None
            self.ready.clear()
            self.error = None
            self.thread = threading.Thread(target=self._serve, name="clipboard-transfer", daemon=True)
            self.thread.start()
            self.ready.wait(3)
            if self.error != "Clipboard changed during snapshot":
                break
            # CopyQ can hand ownership to its helper after copy() returns. Re-read
            # the latest clipboard; nothing has been overwritten at this point.
            self.thread.join(2)
        if not self.ready.is_set() or self.error:
            self.__exit__(None, None, None)
            raise RuntimeError(tr("Could not prepare protected clipboard. Is CopyQ running?",
                                  "无法准备受保护的剪贴板，请确认 CopyQ 正在运行。"))
        return self

    def wait(self, cancelled):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.consumed.wait(0.02):
                return True
            if cancelled() or self.lost.is_set() or self.error:
                logger.info("Clipboard transfer interrupted: ownership_lost=%s error=%s",
                            self.lost.is_set(), self.error)
                return False
        logger.warning("Target did not acknowledge clipboard data within five seconds")
        return False

    def __exit__(self, *_):
        try:
            if self.claimed:
                # The provider must stay alive while CopyQ requests our ownership token.
                _copyq(RESTORE, data=self.snapshot, args=(self.token,))
                if not self.lost.wait(1):
                    raise OSError("Clipboard restore did not release the temporary selection")
        except (OSError, subprocess.SubprocessError):
            # Never discard the only snapshot if the clipboard manager disappears.
            folder = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "doubao-say"
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, recovery = tempfile.mkstemp(prefix="clipboard-recovery-", suffix=".copyq", dir=folder)
            with os.fdopen(fd, "wb") as stream:
                stream.write(self.snapshot)
            logger.error("Could not restore original clipboard; private recovery snapshot saved")
            raise RuntimeError(tr(f"Clipboard restoration failed. Original data saved to {recovery}",
                                  f"剪贴板恢复失败，原内容已备份至 {recovery}")) from None
        finally:
            self.stop.set()
            if self.thread:
                self.thread.join(2)
            self.snapshot = None
            self.data = b""

    def _serve(self):
        from Xlib import X, Xatom, display, error, protocol
        from Xlib.ext import res
        connection = None
        try:
            connection = display.Display()
            if not connection.has_extension("X-Resource"):
                raise RuntimeError("XRes unavailable")
            clipboard = connection.intern_atom("CLIPBOARD")
            targets = connection.intern_atom("TARGETS")
            utf8 = connection.intern_atom("UTF8_STRING")
            token = connection.intern_atom(TOKEN_MIME)
            hidden = connection.intern_atom("application/x-copyq-hidden")
            text_atoms = {utf8}
            window = connection.screen().root.create_window(
                0, 0, 1, 1, 0, X.CopyFromParent, X.InputOnly, X.CopyFromParent)
            # Don't overwrite a copy made while the original formats were being read.
            connection.grab_server()
            try:
                owner = connection.get_selection_owner(clipboard)
                if (owner.id if owner else 0) != self.previous_owner:
                    raise RuntimeError("Clipboard changed during snapshot")
                window.set_selection_owner(clipboard, X.CurrentTime)
                connection.sync()
                self.claimed = True
            finally:
                connection.ungrab_server()
                connection.flush()
            self.ready.set()
            transfers = set()
            while not self.stop.is_set():
                if not connection.pending_events():
                    select.select([connection], [], [], 0.05)
                    continue
                event = connection.next_event()
                if event.type == X.SelectionClear:
                    self.lost.set()
                elif event.type == X.PropertyNotify:
                    if event.state == X.PropertyDelete and (event.window.id, event.atom) in transfers:
                        self.consumed.set()
                elif event.type == X.SelectionRequest:
                    prop = event.property or event.target
                    kind, bits, data = event.target, 8, None
                    if event.target == targets:
                        kind, bits, data = Xatom.ATOM, 32, list(text_atoms | {targets, token, hidden})
                    elif event.target == token:
                        data = self.token.encode()
                    elif event.target == hidden:
                        data = b"1"
                    elif event.target in text_atoms:
                        data = self.data
                        try:
                            ids = connection.res_query_client_ids([
                                {"client": event.requestor.id, "mask": res.LocalClientPIDMask}]).ids
                        except error.XError:
                            ids = []  # A requestor may close before its PID lookup completes.
                        if any(item.value and item.value[0] == self.pid for item in ids):
                            event.requestor.change_attributes(event_mask=X.PropertyChangeMask,
                                                              onerror=lambda *_: None)
                            transfers.add((event.requestor.id, prop))
                    if data is not None:
                        event.requestor.change_property(prop, kind, bits, data,
                                                        onerror=lambda *_: None)
                    reply = protocol.event.SelectionNotify(
                        time=event.time, requestor=event.requestor, selection=event.selection,
                        target=event.target, property=prop if data is not None else X.NONE)
                    event.requestor.send_event(reply, onerror=lambda *_: None)
                    connection.flush()
        except (OSError, error.XError, error.DisplayError, RuntimeError, ValueError) as exc:
            self.error = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            if self.error != "Clipboard changed during snapshot":
                logger.warning("Protected clipboard failed (%s)", self.error)
        finally:
            self.ready.set()
            if connection:
                connection.close()
