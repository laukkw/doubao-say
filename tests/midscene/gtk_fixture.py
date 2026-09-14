"""Mapped GTK onboarding fixture for Midscene CI; uses synthetic data only."""

import signal

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from doubao_input.doubao.app_state import AppState, LoginStatus
from doubao_input.i18n import set_language
from doubao_input.settings import Settings
from doubao_input.ui.control_window import ControlWindow
from doubao_input.ui.setup_actions import SetupActions


def main():
    Gtk.init()
    set_language("en")
    state = AppState()
    state.login_status = LoginStatus.LOGGED_IN
    settings = Settings(
        polish_enabled=True,
        polish_base_url="https://example.invalid/v1",
        polish_model="synthetic-model",
    )
    summary = {
        "key": "Fn",
        "key_code": 464,
        "key_modifiers": (),
        "microphone": "Synthetic microphone",
        "microphone_id": "",
        "microphone_ok": True,
        "voice_test_ok": True,
        "onboarding_complete": False,
    }
    holder = {}

    def test_endpoint(_settings, _key, done):
        def finish():
            done("Synthetic endpoint response", None)
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(350, finish)

    def complete_setup():
        holder["control"].set_feedback("Setup completed by the synthetic E2E fixture.")

    actions = SetupActions(
        test_voice=lambda: None,
        cancel_preview=lambda: None,
        open_settings=lambda: None,
        is_preview_testing=lambda: False,
        summary=lambda: summary,
        complete_setup=complete_setup,
        apply_key=lambda _key, _modifiers=(): None,
        polish_settings=lambda: settings,
        polish_has_key=lambda: True,
        save_polish=lambda _settings, _key: None,
        test_polish=test_endpoint,
        apply_microphone=lambda _microphone: None,
    )
    control = ControlWindow(
        state,
        on_login_clicked=lambda: None,
        on_quit_clicked=lambda: None,
        on_check_mic_clicked=lambda: None,
        actions=actions,
    )
    holder["control"] = control
    control.show()

    loop = GLib.MainLoop()

    def stop(*_args):
        loop.quit()
        return GLib.SOURCE_REMOVE

    signal.signal(signal.SIGTERM, lambda *_args: GLib.idle_add(stop))
    signal.signal(signal.SIGINT, lambda *_args: GLib.idle_add(stop))
    print("READY: synthetic Doubao Say GTK fixture", flush=True)
    loop.run()
    control.destroy()


if __name__ == "__main__":
    main()
