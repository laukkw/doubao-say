"""Cancellable Unicode input through Wayland's virtual keyboard (no clipboard)."""
import logging
import subprocess
import time

from doubao_input.inject.target import focused_target

logger = logging.getLogger(__name__)


def type_text(text, expected_target, cancelled):
    """Never retry: a failed process may already have delivered some characters."""
    def allowed():
        return (not cancelled() and expected_target
                and focused_target() == expected_target and not cancelled())

    if not text or not allowed():
        return False
    process = None
    try:
        # Keep transcripts out of argv, logs and temporary files. communicate()
        # handles partial pipe writes; subsequent calls resume after a timeout.
        process = subprocess.Popen(["wtype", "-"], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pending = text.encode("utf-8")
        deadline = time.monotonic() + max(5, len(text) * 0.03 + 2)
        while allowed() and time.monotonic() < deadline:
            try:
                process.communicate(input=pending, timeout=0.03)
                return process.returncode == 0 and allowed()
            except subprocess.TimeoutExpired:
                pending = None
        return False
    except (OSError, ValueError):
        # Error details from helpers can contain input; never log them.
        logger.warning("Direct input unavailable or failed; text retained")
        return False
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdin:
                process.stdin.close()
