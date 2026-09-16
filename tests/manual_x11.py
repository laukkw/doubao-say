"""Opt-in live desktop check. Uses disposable windows, no microphone or network.

Run in an idle X11 session: timeout 45s env PYTHONPATH=src python tests/manual_x11.py --run
Requires CopyQ, python-xlib, xdotool and xfce4-terminal. Doesn't press Enter in a shell.
"""
import argparse
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import zlib

from doubao_input.inject.clipboard import PreservedClipboard, RESTORE, SNAPSHOT, TOKEN_MIME, _copyq
from doubao_input.inject.injector import Injector, active_window_needs_shift
from doubao_input.inject.target import focused_target

EDITOR = '''
import sys, time
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('GdkX11', '4.0')
from gi.repository import Gtk, GLib, Gdk
GLib.set_prgname('doubao-x11-test-editor')
root=Path(sys.argv[1])
win=Gtk.Window(title='Doubao X11 disposable input check')
view=Gtk.TextView()
keys=Gtk.EventControllerKey()
keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
keys.connect('key-pressed', lambda c,k,code,state: not (
 k in (Gdk.KEY_v,Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK))
view.add_controller(keys)
buf=view.get_buffer()
buf.set_text('前文后文')
win.set_child(view)
win.set_default_size(500, 200)
def save(*args):
 root.joinpath('content').write_text(buf.get_text(buf.get_start_iter(),buf.get_end_iter(),True))
buf.connect('changed', save)
save()
win.present()
view.grab_focus()
buf.place_cursor(buf.get_iter_at_offset(2))
root.joinpath('window').write_text(str(win.get_surface().get_xid()))
def slow():
 if root.joinpath('slow').exists():
  root.joinpath('slow').unlink()
  root.joinpath('blocked').touch()
  time.sleep(1.5)
 return True
GLib.timeout_add(20, slow)
loop=GLib.MainLoop()
GLib.timeout_add_seconds(40, lambda: (loop.quit(), False)[1])
loop.run()
'''
CAPTURE = '''
import os, sys, tty
from pathlib import Path
root=Path(sys.argv[1])
tty.setraw(0)
root.joinpath('ready').touch()
while True:
 data=os.read(0,4096)
 if not data: break
 with root.joinpath('terminal-content').open('ab') as output: output.write(data)
'''


def wait(check, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("Desktop check timed out")


def activate(window):
    subprocess.run(["xdotool", "windowactivate", "--sync", str(window)], check=True, timeout=3)
    time.sleep(0.15)


def history_digest():
    data = _copyq('for(var i=0;i<size();++i) {print(read(i)); print(String.fromCharCode(0));}')
    return hashlib.sha256(data).digest()


def large_png():
    # Qt's BMP export alone exceeds 32 MiB; the lossless PNG remains small.
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", 4096, 2160, 8, 6, 0, 0, 0)
    rows = (b"\0" + b"\xff\x80\0\xff" * 4096) * 2160
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="allow disposable desktop and clipboard checks")
    if not parser.parse_args().run:
        parser.error("pass --run to operate the desktop")
    original_window = subprocess.check_output(["xdotool", "getactivewindow"], timeout=3).strip()
    original_clipboard = _copyq(SNAPSHOT)
    original_history = history_digest()
    marker = "qa-" + uuid.uuid4().hex
    owned = []
    injector = Injector(preserve_clipboard=True)
    try:
        _copyq("""var item={}; item['text/plain']='clipboard sentinel';
item['text/html']='<b>clipboard sentinel</b>';
item['image/png']=input();
item['image/svg+xml']='<svg xmlns="http://www.w3.org/2000/svg"><text>vector sentinel</text></svg>';
item['application/x-doubao-test-data']='custom sentinel';
item[str(arguments[1])]=arguments[2]; item[mimeHidden]='1'; copy(item);""",
               data=large_png(), args=(TOKEN_MIME, marker))
        formats = ("text/plain", "text/html", "image/png", "image/svg+xml", "application/x-doubao-test-data")
        saved = {mime: _copyq("print(clipboard(str(arguments[1])));", args=(mime,)) for mime in formats}
        assert all(saved.values()), {mime: len(data) for mime, data in saved.items()}
        snapshot = _copyq(SNAPSHOT)
        stored = _copyq("print(Object.keys(unpack(input())).join(String.fromCharCode(10)));", data=snapshot).decode().splitlines()
        assert all(mime in stored for mime in formats) and "image/bmp" not in stored
        assert len(snapshot) < 1024 * 1024, "Qt raster aliases were redundantly serialized"
        print("PASS: large Qt image uses one lossless PNG; text, HTML, SVG and custom data retained", flush=True)
        with tempfile.TemporaryDirectory(prefix="doubao-x11-check-") as folder:
            root = Path(folder)
            editor = subprocess.Popen([sys.executable, "-c", EDITOR, folder],
                                      env={**os.environ, "GTK_A11Y": "none", "GTK_IM_MODULE": "simple", "XMODIFIERS": "@im=none"})
            owned.append(editor)
            wait(lambda: (root / "window").exists())
            editor_id = int((root / "window").read_text())
            activate(editor_id)
            target = focused_target()
            assert target and target.startswith(f"x11:{editor_id}:")
            assert not active_window_needs_shift()
            from doubao_input.ui.overlay import Overlay
            from gi.repository import GLib
            overlay = Overlay()
            overlay.show("X11 focus check")
            end = time.monotonic() + 0.3
            while time.monotonic() < end:
                GLib.MainContext.default().iteration(False)
                time.sleep(0.005)
            assert focused_target() == target, "Overlay stole focus"
            overlay.hide()
            print("PASS: recording overlay preserves focus", flush=True)
            text = "中文自动输入\n第二行"
            subprocess.run(["xdotool", "key", "Menu"], check=True, timeout=3)
            assert injector.inject(text, expected_target=target), injector.last_error
            try:
                wait(lambda: (root / "content").read_text() == "前文" + text + "后文")
            except AssertionError:
                raise AssertionError(f"Disposable editor received {(root / 'content').read_text()!r}") from None
            for mime, data in saved.items():
                assert _copyq("print(clipboard(str(arguments[1])));", args=(mime,)) == data, mime
            print("PASS: Unicode inserted once; text, HTML, large image, SVG and custom data restored", flush=True)
            (root / "slow").touch()
            wait(lambda: (root / "blocked").exists())
            long_text = "长文本验证。" * 500
            started = time.monotonic()
            assert injector.inject(long_text, expected_target=target), injector.last_error
            assert time.monotonic() - started >= 1.0, "Restored before slow target read"
            wait(lambda: (root / "content").read_text() == "前文" + text + long_text + "后文")
            print("PASS: waits for a slow target reading long Chinese text", flush=True)
            _copyq("var i={};i[str(arguments[1])]=arguments[2];i[mimeHidden]='1';copy(i);",
                   args=(TOKEN_MIME, marker))
            with PreservedClipboard("cancelled", target) as selection:
                assert not selection.wait(lambda: True)
            assert _copyq("print(clipboard());") == b""
            print("PASS: cancellation restores an empty clipboard", flush=True)
            with PreservedClipboard("must not overwrite new copy", target) as selection:
                _copyq("var i={};i['text/plain']='new copy';i[str(arguments[1])]=arguments[2];"
                       "i[mimeHidden]='1';copy(i);", args=(TOKEN_MIME, marker))
                assert not selection.wait(lambda: False)
            assert _copyq("print(clipboard());") == b"new copy"
            print("PASS: concurrent copy is preserved", flush=True)
            capture = root / "capture.py"
            capture.write_text(CAPTURE)
            terminal = subprocess.Popen([
                "xfce4-terminal", "--disable-server", "--title=Doubao X11 disposable terminal check",
                "--command", f"{sys.executable} {capture} {root}"])
            owned.append(terminal)
            wait(lambda: (root / "ready").exists())
            ids = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--pid", str(terminal.pid)], timeout=3)
            activate(int(ids.splitlines()[-1]))
            assert active_window_needs_shift()
            assert not injector.inject("must not paste", expected_target=target)
            assert _copyq("print(clipboard());") == b"new copy"
            terminal_text = "终端自动输入测试"
            assert injector.inject(terminal_text, expected_target=focused_target()), injector.last_error
            wait(lambda: (root / "terminal-content").exists() and
                 (root / "terminal-content").read_bytes() == terminal_text.encode())
            print("PASS: changed focus rejected; terminal paste exact with no Enter", flush=True)
            time.sleep(0.3)
            assert history_digest() == original_history, "Clipboard history changed"
            print("PASS: CopyQ history unchanged", flush=True)
    finally:
        injector.close()
        for process in reversed(owned):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        _copyq(RESTORE, data=original_clipboard, args=(marker,))
        subprocess.run(["xdotool", "windowactivate", original_window.decode()], timeout=3, check=False)


if __name__ == "__main__":
    main()
