"""Opt-in cloud check with bundled ALSA spoken test sounds, never a microphone.

Run: timeout 40s env PYTHONPATH=src python tests/manual_asr.py --run
Requires saved Doubao sign-in, ffmpeg and /usr/share/sounds/alsa. Sends only these
public test samples. Reports timings/counts; no transcripts, URLs or credentials.
"""
import argparse
import subprocess
import time
from unittest.mock import Mock

from gi.repository import GLib

from doubao_input.doubao.app_state import AppState, LoginStatus, RecordingState
from doubao_input.doubao.transcription import TranscriptionManager


def pump(seconds):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    if not parser.parse_args().run:
        parser.error("pass --run to connect to Doubao with saved sign-in")
    sounds = [subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", f"/usr/share/sounds/alsa/{name}.wav",
        "-f", "s16le", "-ar", "16000", "-ac", "1", "pipe:1"], timeout=5)
        for name in ("Front_Left", "Front_Center", "Front_Right")]
    for index, pcm in enumerate([sounds[1]] * 3 + [b"".join(sounds)] * 2, 1):
        state = AppState()
        state.login_status = LoginStatus.LOGGED_IN
        manager = TranscriptionManager(state)
        manager.audio_capture = Mock()
        received, completed = [], []
        state.connect("transcription-text-changed", lambda s, text: received.append(time.monotonic()) if text else None)
        expected = ("center",) if index <= 3 else ("left", "center", "right")
        manager.on_paste = lambda text: completed.append((
            time.monotonic(), len(text), all(word in text.casefold() for word in expected)))
        manager.handle_press()
        started = time.monotonic()
        session = manager.asr_client._session
        try:
            for offset in range(0, len(pcm), 8192):
                block = pcm[offset:offset + 8192]
                manager.asr_client.send_audio(block)
                pump(len(block) / 32000)
            stopped = time.monotonic()
            manager.handle_release()
            deadline = time.monotonic() + 7
            while state.recording_state != RecordingState.IDLE and time.monotonic() < deadline:
                pump(0.02)
            assert len(completed) == 1 and received and not state.error_message, f"Trial {index} did not complete"
            when, characters, complete_words = completed[0]
            assert complete_words, f"Trial {index} lost an expected word"
            print(f"PASS sample {index}: first_result_ms={round((received[0]-started)*1000)} "
                  f"stop_to_result_ms={round((when-stopped)*1000)} characters={characters}", flush=True)
        finally:
            manager.handle_cancel()
            if session and session.thread:
                session.thread.join(3)


if __name__ == "__main__":
    main()
