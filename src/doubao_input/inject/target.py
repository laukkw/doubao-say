"""Best-effort Hyprland window identity, without recording window titles."""
import json
import os
import subprocess


def focused_target():
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return None
    try:
        data = json.loads(subprocess.check_output(
            ["hyprctl", "activewindow", "-j"], timeout=0.5, stderr=subprocess.DEVNULL))
        if not isinstance(data, dict):
            return None
        if data.get("class", "").casefold() == "md.lifeos.doubaosay":
            return None
        address = data.get("address")
        return address if isinstance(address, str) and address not in ("", "0x0") else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
