"""Safe filming mode: real keyboard gestures, synthetic visuals, no ASR or Enter."""
import math
import time
from gi.repository import Gtk, GLib
from doubao_input.doubao.app_state import AppState, RecordingState
from doubao_input.trigger.gesture import KeyGesture
from doubao_input.ui.overlay import Overlay
from doubao_input.i18n import tr
from doubao_input.settings import trigger_key_name


class TriggerDebug:
    def __init__(self, app, finish):
        self.state = AppState()
        self.overlay = Overlay(self.state)
        self.active = False
        self.reset_timer = None
        self.started = time.monotonic()
        self.window = Gtk.ApplicationWindow(application=app, title="Doubao · Trigger Debug")
        self.window.set_default_size(620, 420)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for side in ("start", "end", "top", "bottom"):
            getattr(box, "set_margin_" + side)(32)
        self.window.set_child(box)
        title = Gtk.Label(label="DOUBAO  /  TRIGGER DEBUG")
        title.add_css_class("title-2")
        box.append(title)
        self.key_label = Gtk.Label()
        box.append(self.key_label)
        self.event_label = Gtk.Label(wrap=True)
        self.event_label.add_css_class("title-1")
        box.append(self.event_label)
        box.append(Gtk.Label(wrap=True, label=tr(
            "Tap: start / stop · Hold: release to finish · Double-tap: Enter preview",
            "短按开始/停止 · 长按说话、松开结束 · 双击预览回车")))
        box.append(Gtk.Label(wrap=True, label=tr(
            "DEMO ONLY · Synthetic waveform · No microphone, cloud, paste or Enter",
            "仅调试演示 · 模拟波形 · 不录音、不联网、不粘贴、不发送回车")))
        button = Gtk.Button(label=tr("Exit debug & resume voice input", "退出调试，恢复语音输入"))
        button.connect("clicked", lambda *_: finish())
        box.append(button)
        self.window.connect("close-request", lambda *_: finish() or True)
        self.gesture = KeyGesture(self.start, self.stop, self.toggle, self.enter,
                                  GLib.timeout_add, GLib.source_remove,
                                  hold_ms=app.settings.hold_ms,
                                  double_ms=app.settings.double_ms)
        self.key_name = trigger_key_name(app.settings.doubao_key)
        self.idle()
        self.ticker = GLib.timeout_add(33, self.tick)
        self.window.present()

    def display(self, title, state):
        self.state.recording_state = state
        self.event_label.set_text(title)
        self.overlay.show("DEMO")
        self.overlay.set_text("DEMO · " + title)

    def cancel_reset(self):
        if self.reset_timer is not None:
            GLib.source_remove(self.reset_timer)
            self.reset_timer = None

    def edge(self, pressed):
        self.cancel_reset()
        self.key_label.set_markup(f'<span size="42000" weight="bold">{self.key_name} {"↓" if pressed else "↑"}</span>')
        if pressed:
            if not self.active:
                self.display(tr("Pressed", "已按下"), RecordingState.STARTING)
            self.gesture.press()
        else:
            self.gesture.release()

    def start(self):
        self.active = True
        self.display(tr("Listening · hold to talk", "聆听中 · 长按模式"), RecordingState.RECORDING)

    def toggle(self):
        if self.active:
            self.stop()
        else:
            self.active = True
            self.display(tr("Listening · tap to stop", "聆听中 · 再按停止"), RecordingState.RECORDING)

    def stop(self):
        self.active = False
        self.display(tr("Finished · release detected", "已结束 · 检测到松开/停止"), RecordingState.STOPPING)
        self.reset_timer = GLib.timeout_add(1100, self.idle)

    def enter(self):
        self.active = False
        self.display(tr("Double-tap · Enter ↵ (preview)", "双击 · 回车 ↵（仅预览）"), RecordingState.STOPPING)
        self.reset_timer = GLib.timeout_add(1600, self.idle)

    def idle(self):
        self.reset_timer = None
        self.key_label.set_markup(f'<span size="42000" weight="bold">{self.key_name}</span>')
        self.display(tr("Ready · press your trigger key", "准备就绪 · 请按触发键"), RecordingState.IDLE)
        return False

    def tick(self):
        t = time.monotonic() - self.started
        self.overlay.push_rms((0.08 + 0.24 * (0.5 + 0.5 * math.sin(t * 9)) ** 2) if self.active else 0)
        return True

    def close(self):
        self.gesture.close()
        self.cancel_reset()
        GLib.source_remove(self.ticker)
        self.overlay.hide()
        self.window.destroy()
