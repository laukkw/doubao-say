"""Read-only dependency/permission checks: no login, recording or input injection."""
import importlib
import os
from pathlib import Path
import shutil


def check_runtime():
    results = {}
    for module in ("gi", "cairo", "evdev", "websockets", "sounddevice"):
        try:
            importlib.import_module(module)
            results[module] = True
        except (ImportError, OSError):
            results[module] = False
    for namespace, version in (("Gtk", "4.0"), ("WebKit", "6.0"), ("Gtk4LayerShell", "1.0")):
        try:
            import gi
            gi.require_version(namespace, version)
            importlib.import_module(f"gi.repository.{namespace}")
            results[namespace] = True
        except (ImportError, OSError, ValueError):
            results[namespace] = False
    for command in ("pw-record", "pw-dump", "wl-copy"):
        results[command] = shutil.which(command) is not None
    return results


def main():
    results = check_runtime()
    for name, passed in results.items():
        print(f"{'OK' if passed else 'MISSING'}: {name}")
    print("INFO: keyboard access " + ("available" if any(
        os.access(p, os.R_OK) for p in Path("/dev/input").glob("event*")) else "not available"))
    print("INFO: virtual keyboard access " + ("available" if os.access("/dev/uinput", os.W_OK) else "not available"))
    print("INFO: device access checks do not prove that your trigger key or paste works")
    if not all(results.values()):
        print("Install the documented system dependencies; see packaging/INSTALL.md or bundled INSTALL.md")
        return 78
    print("PASS: runtime dependencies (no microphone, network, login or keystroke test performed)")
    return 0
