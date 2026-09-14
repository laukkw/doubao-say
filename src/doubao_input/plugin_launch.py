"""Plugin entry points wait for the shell-owned daemon instead of racing it."""
from pathlib import Path
import subprocess
import time

PLUGIN_ID = "md.lifeos.doubao-say"
BUS_NAME = "md.lifeos.DoubaoSay"
OBJECT_PATH = "/md/lifeos/DoubaoSay"


def is_plugin(root):
    return root.name == PLUGIN_ID and (root / "omarchy/Service.qml").is_file()


def launch(*, show=True, timeout=15):
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def has_owner():
        return bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (BUS_NAME,)),
            GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]

    wait_for_service(has_owner, timeout=timeout)
    if show:
        bus.call_sync(BUS_NAME, OBJECT_PATH, "org.freedesktop.Application",
            "Activate", GLib.Variant("(a{sv})", ({},)), None,
            Gio.DBusCallFlags.NONE, 5000, None)


def wait_for_service(has_owner, *, timeout=15):
    if has_owner():
        return
    subprocess.run(["omarchy", "plugin", "enable", PLUGIN_ID], check=True, timeout=10)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if has_owner():
            return
        time.sleep(0.1)
    raise RuntimeError("Doubao Say's plugin did not start. Check Omarchy plugin status and try again.")


def installed_root():
    return Path(__file__).resolve().parents[2]
