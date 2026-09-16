"""State machine orchestrator for the recording lifecycle.

Mirrors TranscriptionManager.swift.

Key design decisions:
- GLib.idle_add() marshals callbacks from the asyncio thread to GTK main thread
- GLib.timeout_add() replaces DispatchQueue.main.asyncAfter for delayed execution
- State machine exactly mirrors macOS: idle -> starting -> recording -> stopping -> idle
"""

from __future__ import annotations

import logging
import time

from gi.repository import GLib

from doubao_input.doubao.app_state import AppState, LoginStatus, RecordingState
from doubao_input.doubao.asr_client import ASRClient
from doubao_input.doubao.audio_capture import AudioCapture
from doubao_input.doubao.config import STOP_SAFETY_TIMEOUT
from doubao_input.doubao.params_store import ASRParams, ParamsStore
from doubao_input.i18n import tr

# Minimum press duration to be treated as a real PTT (vs accidental tap).
MIN_PRESS_DURATION = 0.15  # seconds
# ponytail: the observed service doesn't always send finish. Use a bounded quiet
# fallback after audio drains; prefer a documented final-result marker when available.
FINAL_RESULT_QUIET_PERIOD = 0.5
FINAL_AUDIO_SETTLE_PERIOD = 1.0

logger = logging.getLogger(__name__)


class TranscriptionManager:
    """Orchestrates the recording lifecycle."""

    def __init__(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.asr_client = ASRClient()
        self.audio_capture = AudioCapture()

        self.using_cached_params = False
        self.prepared = False
        self.awaiting_final_result = False
        self.safety_timer_id: int | None = None
        self.final_result_timer_id: int | None = None
        self._press_started_at: float = 0.0
        self._generation = 0
        self._stopped_at = None

        # Callbacks set by app.py
        self.on_auth_expired = None  # () -> None
        self.on_show_login = None  # () -> None
        self.on_params_needed = None  # (callback: (ASRParams|None)->None) -> None
        self.on_overlay_show = None  # () -> None
        self.on_overlay_hide = None  # () -> None
        self.on_overlay_update = None  # (text: str) -> None
        self.on_paste = None  # (text: str) -> None
        self.on_empty_complete = None  # () -> None; successful finish without text
        self.on_recover = None  # (partial_text) -> None, before a failed session resets
        self.on_cancel_enabled_changed = None  # (enabled: bool) -> None

        self._wire_asr_callbacks()

    def _wire_asr_callbacks(self) -> None:
        """Wire ASR client callbacks to marshal from asyncio to GTK thread."""
        generation = self._generation
        def deliver(callback, *args):
            return GLib.idle_add(self._deliver, generation, callback, args)
        self.asr_client.on_open = lambda: deliver(self._on_asr_open)
        self.asr_client.on_result = lambda text: GLib.idle_add(
            self._deliver, generation, self._on_asr_result, (text,)
        )
        self.asr_client.on_finish = lambda: deliver(self._on_asr_finish)
        self.asr_client.on_error = lambda err: deliver(self._on_asr_error, err)
        self.asr_client.on_auth_error = lambda: deliver(self._on_auth_error)

    def _deliver(self, generation, callback, args=()):
        if generation == self._generation:
            callback(*args)
        return GLib.SOURCE_REMOVE

    def _later(self, milliseconds, callback):
        return GLib.timeout_add(milliseconds, self._deliver, self._generation, callback)

    def prepare_recording(self) -> None:
        """Buffer from key-down locally; gesture confirmation alone opens ASR."""
        if (self.app_state.recording_state == RecordingState.IDLE
                and self.app_state.login_status == LoginStatus.LOGGED_IN):
            self._start_recording(defer_connection=True)

    def discard_prepared(self) -> None:
        """A double-tap or cancelled gesture must never upload buffered audio."""
        if self.prepared:
            self.handle_cancel()

    # --- Toggle ---

    def handle_toggle(self) -> None:
        """Called on GTK main thread from hotkey manager.
        Kept for compatibility with the original toggle-style API."""
        state = self.app_state.recording_state
        if self.prepared:
            self.prepared = False
            self._connect_asr()
        elif state == RecordingState.IDLE:
            self._start_recording()
        elif state in (RecordingState.STARTING, RecordingState.RECORDING):
            self._stop_recording()
        # STOPPING: ignore

    # --- Push-to-hold (primary API for this project) ---

    def handle_press(self) -> None:
        """Right-Alt down: start recording (if not already recording)."""
        import time as _t
        self._press_started_at = _t.monotonic()
        if self.prepared or self.app_state.recording_state == RecordingState.IDLE:
            self.handle_toggle()

    def handle_release(self) -> None:
        """Right-Alt up: stop recording and inject (unless a too-brief tap)."""
        import time as _t
        dur = _t.monotonic() - self._press_started_at if self._press_started_at else 0
        self._press_started_at = 0.0
        state = self.app_state.recording_state
        if state not in (RecordingState.STARTING, RecordingState.RECORDING):
            return
        if dur < MIN_PRESS_DURATION:
            logger.info("Press too short (%.3fs), treating as accidental tap", dur)
            self.handle_cancel()
            return
        self._stop_recording()

    def _start_recording(self, *, defer_connection=False) -> None:
        if self.app_state.login_status != LoginStatus.LOGGED_IN:
            logger.warning("Not logged in, showing login window")
            if self.on_show_login:
                self.on_show_login()
            return

        logger.info("Preparing local audio on key-down" if defer_connection else "Starting recording...")
        self.prepared = defer_connection
        self._generation += 1
        self._stopped_at = None
        self._wire_asr_callbacks()
        send_audio = self.asr_client.prepare()
        self._set_state(RecordingState.STARTING)
        self.app_state.transcription_text = ""
        self.app_state.error_message = None
        if self.on_overlay_show:
            self.on_overlay_show()

        # Start audio immediately (buffered in ASR client until WS connects)
        try:
            self.audio_capture.start(on_audio_data=send_audio)
        except Exception as e:
            logger.error("Audio capture failed: %s", e)
            self._reset_to_idle()
            self.app_state.error_message = tr("Microphone failed to start; check your input device and permissions",
                                              "麦克风启动失败，请检查输入设备和权限")
            return

        if not defer_connection:
            self._connect_asr()

    def _connect_asr(self) -> None:
        # Keep the original session and queue, including audio from key-down.
        cached = ParamsStore.load()
        if cached:
            logger.info("Using cached ASR params")
            self.using_cached_params = True
            self.asr_client.connect(cached)
        elif self.on_params_needed:
            self.using_cached_params = False
            generation = self._generation
            self.on_params_needed(lambda params: self._deliver(
                generation, self._on_params_extracted, (params,)))
        else:
            self._reset_to_idle()
            self.app_state.error_message = tr("Could not connect; please sign in again", "无法获取连接参数，请重新登录")

    def _stop_recording(self) -> None:
        logger.info("Stopping recording...")
        self._stopped_at = time.monotonic()
        self._set_state(RecordingState.STOPPING)
        try:
            self.audio_capture.finish()
        except Exception as error:
            self._on_asr_error(error)
            return
        self.asr_client.finish_sending()
        self.awaiting_final_result = True

        # Safety timeout
        self.safety_timer_id = self._later(
            int(STOP_SAFETY_TIMEOUT * 1000), self._safety_timeout
        )
        # A result may already be complete before the key is released. Without
        # this timer, silence after release needlessly takes the full safety timeout.
        if self.app_state.transcription_text.strip():
            self._schedule_final_completion()

    def _safety_timeout(self) -> bool:
        self.safety_timer_id = None
        if self.app_state.recording_state == RecordingState.STOPPING:
            stats = self.asr_client.diagnostics()
            logger.warning("Recognition wait expired; partial_chars=%d stats=%s",
                           len(self.app_state.transcription_text), stats)
            has_text = bool(self.app_state.transcription_text.strip())
            if self.on_recover and has_text:
                self.on_recover(self.app_state.transcription_text)
            self._reset_to_idle()
            if has_text:
                message = tr("Recognition timed out. Partial text is in Recent result; review it before sending.",
                             "识别超时，已有文字保留在“最近识别结果”中，请检查后再发送。")
            elif stats.get("audio_bytes") == 0:
                message = tr("No microphone audio received. Check the selected input device.",
                             "未收到麦克风音频，请检查所选输入设备。")
            elif stats.get("connected") is False:
                message = tr("Recognition connection timed out. Check the network and try again.",
                             "识别连接超时，请检查网络后重试。")
            elif (stats.get("empty_results", 0) > 0 and stats.get("pending_bytes") == 0
                  and stats.get("sending") is False):
                message = tr(
                    "Doubao returned only empty results. No text was produced; check the selected microphone and input level.",
                    "豆包仅返回空结果，本次没有生成文字。请检查所选麦克风和输入音量。")
            else:
                message = tr("Recognition timed out without receiving text. Please try again.",
                             "识别超时，本次没有收到文字，请稍后重试。")
            self.app_state.error_message = message
        self.safety_timer_id = None
        return GLib.SOURCE_REMOVE

    # --- ASR callbacks (on GTK main thread via GLib.idle_add) ---

    def _on_asr_open(self) -> bool:
        if self.app_state.recording_state == RecordingState.STARTING:
            self._set_state(RecordingState.RECORDING)
        return GLib.SOURCE_REMOVE

    def _on_asr_result(self, text: str) -> bool:
        changed = text != self.app_state.transcription_text
        self.app_state.transcription_text = text
        if self.on_overlay_update:
            self.on_overlay_update(text)
        if self.app_state.recording_state == RecordingState.STARTING:
            self._set_state(RecordingState.RECORDING)
        if self.awaiting_final_result and changed:
            self._schedule_final_completion()
        return GLib.SOURCE_REMOVE

    def _on_asr_finish(self) -> bool:
        self._cancel_final_result_timer()
        self.awaiting_final_result = False
        if self.app_state.recording_state in (
            RecordingState.STARTING,
            RecordingState.STOPPING,
            RecordingState.RECORDING,
        ):
            self._complete_transcription()
        return GLib.SOURCE_REMOVE

    def _schedule_final_completion(self) -> None:
        """Debounce trailing partial results until the stream goes quiet."""
        self._cancel_final_result_timer()
        self.final_result_timer_id = self._later(
            int(FINAL_RESULT_QUIET_PERIOD * 1000),
            self._finish_after_quiet_period,
        )

    def _finish_after_quiet_period(self) -> bool:
        self.final_result_timer_id = None
        if self.awaiting_final_result:
            if (self.asr_client.has_pending_audio or not self.asr_client.is_connected
                    or self.asr_client.drained_for < FINAL_AUDIO_SETTLE_PERIOD):
                self._schedule_final_completion()
                return GLib.SOURCE_REMOVE
            logger.info("Result stream quiet, completing transcription")
            self.awaiting_final_result = False
            self._complete_transcription()
        return GLib.SOURCE_REMOVE

    def _cancel_final_result_timer(self) -> None:
        if self.final_result_timer_id is not None:
            GLib.source_remove(self.final_result_timer_id)
            self.final_result_timer_id = None

    def _on_asr_error(self, error) -> bool:
        if self.app_state.recording_state == RecordingState.IDLE:
            return GLib.SOURCE_REMOVE
        logger.error("ASR request failed")
        if self.on_recover and self.app_state.transcription_text.strip():
            self.on_recover(self.app_state.transcription_text)
        # NOTE: genuine auth failures arrive via `on_auth_error` -> `_on_auth_error`,
        # which calls `_handle_auth_failure()` and re-prompts login. A generic ASR
        # error (connection refused, timeout, DNS, proxy down, etc.) must NOT be
        # treated as auth failure — doing so wipes cached cookies and pops the
        # login window every time the network/proxy is unavailable.
        self._reset_to_idle()
        self.app_state.error_message = tr("Connection failed; check your network and retry", "连接出错,请检查网络后重试")
        return GLib.SOURCE_REMOVE

    def _on_auth_error(self) -> bool:
        self._handle_auth_failure()
        return GLib.SOURCE_REMOVE

    # --- Completion & Reset ---

    def _complete_transcription(self) -> None:
        if self._stopped_at is not None:
            logger.info("Release-to-finalization: %.0f ms",
                        (time.monotonic() - self._stopped_at) * 1000)
        text = self.app_state.transcription_text.strip()
        logger.info("Completing transcription (%d characters); stats=%s",
                    len(text), self.asr_client.diagnostics())
        if text and self.on_paste:
            self.on_paste(text)
        elif not text and self.on_empty_complete:
            self.on_empty_complete()
        self._reset_to_idle()

    def _reset_to_idle(self) -> bool:
        self.prepared = False
        self._generation += 1
        self._cancel_final_result_timer()
        if self.safety_timer_id is not None:
            GLib.source_remove(self.safety_timer_id)
            self.safety_timer_id = None
        self.awaiting_final_result = False
        self.audio_capture.stop()
        self.asr_client.disconnect()
        self._set_state(RecordingState.IDLE)
        self.app_state.error_message = None
        if self.on_overlay_hide:
            self.on_overlay_hide()
        self.using_cached_params = False
        self.app_state.transcription_text = ""
        return GLib.SOURCE_REMOVE

    def handle_cancel(self) -> None:
        if self.app_state.recording_state == RecordingState.IDLE:
            return
        logger.info("Cancelling transcription")
        self.awaiting_final_result = False
        self.audio_capture.stop()
        self.asr_client.disconnect()
        self._reset_to_idle()

    def _handle_auth_failure(self) -> None:
        if self.on_recover and self.app_state.transcription_text.strip():
            self.on_recover(self.app_state.transcription_text)
        logger.warning("Auth failure, clearing cached params")
        clear_failed = False
        try:
            ParamsStore.clear()
        except OSError:
            clear_failed = True
            logger.warning("Could not remove expired credentials")
        self.using_cached_params = False
        self.audio_capture.stop()
        self.asr_client.disconnect()
        self._reset_to_idle()
        self.app_state.login_status = LoginStatus.NOT_LOGGED_IN
        if self.on_auth_expired:
            self.on_auth_expired()
        if clear_failed:
            self.app_state.error_message = tr(
                "Sign-in expired, but saved credentials could not be removed. Check folder permissions.",
                "登录已过期，但无法删除保存的凭证，请检查目录权限。")

    def _set_state(self, new_state: RecordingState) -> None:
        self.app_state.recording_state = new_state
        if self.on_cancel_enabled_changed:
            self.on_cancel_enabled_changed(new_state != RecordingState.IDLE)

    def _on_params_extracted(self, params: ASRParams | None) -> None:
        """Called when WebView param extraction completes."""
        if params:
            try:
                ParamsStore.save(params)
            except (OSError, ValueError):
                self.audio_capture.stop()
                self._reset_to_idle()
                self.app_state.error_message = tr(
                    "Could not save sign-in; check configuration permissions and disk space.",
                    "无法保存登录信息，请检查配置目录权限和磁盘空间。")
                return
            self.asr_client.connect(params)
        else:
            self._reset_to_idle()
            self.app_state.error_message = tr("Could not connect; please sign in again", "无法获取连接参数，请重新登录")
