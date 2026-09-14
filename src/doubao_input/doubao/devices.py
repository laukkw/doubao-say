"""Read-only PipeWire source discovery. No system default changes."""
import json
import subprocess


def microphones():
    try:
        objects = json.loads(subprocess.check_output(["pw-dump"], timeout=2,
                            stderr=subprocess.DEVNULL))
        return [(p["node.name"], p.get("node.description", p["node.name"]))
                for obj in objects if isinstance(obj, dict)
                for p in [obj.get("info", {}).get("props", {})]
                if p.get("media.class") == "Audio/Source" and isinstance(p.get("node.name"), str)]
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return []
