"""Allowlisted support data. No logs, paths, devices, transcripts or account IDs."""
import json
import os
import platform
import shutil
from importlib.metadata import version, PackageNotFoundError
from doubao_input.product import VERSION


def report(settings, *, recording=False):
    packages = {}
    for name in ("websockets", "sounddevice", "evdev", "PyGObject"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "unavailable"
    return json.dumps({"format": 1, "product_version": VERSION,
        "platform": platform.system(), "architecture": platform.machine(),
        "python": platform.python_version(), "wayland": bool(os.getenv("WAYLAND_DISPLAY")),
        "hyprland": bool(os.getenv("HYPRLAND_INSTANCE_SIGNATURE")),
        "uinput_writable": os.access("/dev/uinput", os.W_OK),
        "commands": {n: bool(shutil.which(n)) for n in ("pw-record", "wl-copy", "hyprctl")},
        "packages": packages, "language": settings.language,
        "trigger_key": settings.doubao_key,
        "trigger_modifiers": list(settings.doubao_modifiers),
        "polishing_enabled": settings.polish_enabled,
        "recording": recording}, indent=2)
