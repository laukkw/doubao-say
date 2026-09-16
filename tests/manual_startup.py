"""Opt-in key-down buffer check through an isolated PipeWire source and Doubao.

Run: timeout 30s env PYTHONPATH=src python tests/manual_startup.py --run
Requires PipeWire tools, saved sign-in and the bundled ALSA Front_Center.wav.
Uploads only that public sample. No physical microphone, keyboard input or paste.
"""
import argparse
import json
import subprocess
import time
import uuid

from gi.repository import GLib

from doubao_input.doubao.app_state import AppState, LoginStatus, RecordingState
from doubao_input.doubao.transcription import TranscriptionManager


def graph():
    return json.loads(subprocess.check_output(["pw-dump"], timeout=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    if not parser.parse_args().run:
        parser.error("pass --run to upload the public sample with saved sign-in")
    name = "doubao-startup-test-" + uuid.uuid4().hex[:8]
    sink, source = name + "-sink", name + "-source"
    loopback = subprocess.Popen([
        "pw-loopback", "--channels", "1", "--channel-map", "MONO",
        "--capture-props", f"media.class=Audio/Sink node.name={sink} node.autoconnect=false priority.session=0",
        "--playback-props", f"media.class=Audio/Source node.name={source} node.autoconnect=false priority.session=0",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    state = AppState()
    state.login_status = LoginStatus.LOGGED_IN
    manager = TranscriptionManager(state)
    manager.audio_capture.device = source
    completed = []
    manager.on_paste = lambda text: completed.append({word: word in text.casefold()
                                                       for word in ("front", "center")})
    session = None
    try:
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            nodes = {obj.get("info", {}).get("props", {}).get("node.name"): obj["id"] for obj in graph()}
            if sink in nodes and source in nodes:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Virtual audio nodes did not appear")
        source_id = nodes[source]
        manager.prepare_recording()
        session = manager.asr_client._session
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            objects = graph()
            clients = {obj["id"] for obj in objects if str(obj.get("info", {}).get("props", {}).get(
                "application.process.id")) == str(manager.audio_capture._process.pid)}
            recorders = {obj["id"] for obj in objects if obj["type"] == "PipeWire:Interface:Node"
                         and obj.get("info", {}).get("props", {}).get("client.id") in clients}
            if any(obj["type"] == "PipeWire:Interface:Link"
                   and obj.get("info", {}).get("output-node-id") == source_id
                   and obj.get("info", {}).get("input-node-id") in recorders for obj in objects):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("Recorder was not linked to the isolated source; refusing upload")
        deadline = time.monotonic() + 2
        while not manager.asr_client.diagnostics().get("audio_bytes") and time.monotonic() < deadline:
            time.sleep(0.02)
        assert manager.asr_client.diagnostics().get("audio_bytes"), "Virtual capture did not produce samples"
        subprocess.run(["pw-play", "--target", sink, "/usr/share/sounds/alsa/Front_Center.wav"],
                       check=True, timeout=5)
        time.sleep(0.3)
        stats = manager.asr_client.diagnostics()
        assert stats["audio_bytes"] > 32000 and stats["sent_bytes"] == 0 and session.thread is None
        # Confirm only after the entire phrase was captured. Reopening the microphone
        # or replacing the session here would discard its beginning (or all of it).
        manager.handle_toggle()
        assert manager.asr_client._session is session
        manager.handle_toggle()
        deadline = time.monotonic() + 7
        while state.recording_state != RecordingState.IDLE and time.monotonic() < deadline:
            GLib.MainContext.default().iteration(False)
            time.sleep(0.005)
        assert completed == [{"front": True, "center": True}] and state.error_message is None, (
            f"Buffered phrase incomplete: words={completed}, results={session.results}, "
            f"empty={session.empty_results}, bytes={session.received_bytes}, error={state.error_message}")
        print("PASS: audio captured before confirmation, zero bytes sent beforehand, full public phrase recognized")
    finally:
        manager.handle_cancel()
        manager.audio_capture.stop()
        if session and session.thread:
            session.thread.join(3)
        loopback.terminate()
        try:
            loopback.wait(timeout=3)
        except subprocess.TimeoutExpired:
            loopback.kill()
            loopback.wait(timeout=2)
        assert session is None or session.thread is None or not session.thread.is_alive(), "ASR worker did not exit"


if __name__ == "__main__":
    main()
