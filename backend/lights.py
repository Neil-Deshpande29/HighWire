"""The LED strip that stands for the grid (optional hardware; nothing in the app depends on it).

The strip is the grid laid out west to east, and it follows the map on screen:

    normal   every LED green              no line is in trouble
    event    green with a red stretch     lines there are at high fire risk or overloaded
    cut      green with a dark stretch    a line has been switched off: "Turn this line off", or an approved plan

The server works out the scene and changes the strip by itself, the moment the system changes: when a plan is approved
(before the approval's own slower work is done), when the session is reset, when the agents start on an hour. The
screen only says which hour it shows and which lines were switched by hand (POST /lights). See `_strip_scene` in
backend/routes_la.py; `scene_of` below is the rule. This module turns a scene into one text line and a background
thread sends it over USB serial to an ESP32, which passes it down a wire to the QT Py that drives the strip
(hardware/lights/). Nothing waits on the strip. With no strip plugged in, or without pyserial, every call is a quiet
no-op.

Try it without the app (the hard-coded four-step demonstration, or one scene):

    python -m backend.lights demo
    python -m backend.lights event 0.2

When the app's backend is running, these go through it, because it holds the strip's port and repeats its scene.
Close the app's browser tab first: an open tab puts its own scene back whenever it is used. With no backend running
the port is opened directly.

Settings (environment): LIGHTS_PORT = the ESP32's serial port, or "off" to never touch one. Without it the first
USB serial adapter that looks like an ESP32 is used. LIGHTS_STRIP_PORT = the strip board's own USB port, used as a
second path and to watch the wire when that board is plugged in too ("off" for never). REWIRE_API = the backend's
address (default http://localhost:8000).
"""
from __future__ import annotations

import os
import sys
import threading
import time

N_LEDS = 30  # LEDs on the strip (NUM_LEDS in hardware/lights/firmware/qtpy_main.py)
PATCH = 6  # LEDs in the red or dark stretch
STAGES = ("normal", "event", "cut")
ESP_USB_VIDS = (0x10C4, 0x1A86, 0x0403)  # CP210x, CH340, FTDI: the USB-serial chips on ESP32 boards
BOOT_S = 2.6  # the ESP32 restarts when its port is opened; lines sent before it is up are lost
RESEND_S = 5.0  # the wire to the strip is one-way, so the scene is repeated: a lost line or a restarted strip heals itself
LOOK_S = 30.0  # with no strip connected, how often to look for one being plugged in
STRIP_USB_VIDS = (0x2E8A,)  # Raspberry Pi: the QT Py RP2040 that drives the LEDs, when it is plugged in by USB too
ECHO_S = 0.3  # how long the strip's board gets to report a line it heard from the ESP32
SILENT_SENDS = 3  # scenes in a row the strip's board did not hear over the wire before the ESP32 is restarted
RESTART_GAP_S = 60.0  # and no more often than this
DEMO = (("normal", "No climate event: every line is live, the whole strip is green."),
        ("event", "A wind storm reaches the lines: the stretch in trouble turns red."),
        ("cut", "Power off: that stretch goes dark, the rest of the grid stays green."),
        ("normal", "Power restored: the whole strip is green again."))


def scene_command(stage: str, at: float = 0.5) -> str:
    """The text line for one scene. `at` is where the trouble is along the strip, 0 (first LED) to 1 (last LED)."""
    if stage not in STAGES:
        raise ValueError(f"unknown scene '{stage}'")
    if stage == "normal":
        return "grid normal"
    first = round(min(1.0, max(0.0, float(at))) * (N_LEDS - PATCH))
    return f"grid {stage} {first} {first + PATCH - 1}"


def scene_of(branches: list[dict], bbox: dict, risk, loading, alarm: float, off=(), demand_cut: bool = False) -> tuple[str, float]:
    """(scene, where along the strip) for one hour of the grid.

    `risk` and `loading` are the hour's per-line fire index and percent of rating, in branch order: the same numbers
    the map is coloured by. `off` are the lines switched off by an approved plan or by hand; `demand_cut` says demand
    was cut some other way (a plan that sheds load). The stretch sits where the trouble is, by the lines' longitude."""
    trouble = [b for k, b in enumerate(branches) if (not b["cable"] and risk[k] >= alarm) or loading[k] > 100]
    off = set(off)
    if not trouble and not off and not demand_cut:
        return "normal", 0.5
    stage = "cut" if off or demand_cut else "event"
    where = trouble or [b for b in branches if b["id"] in off]
    if not where:
        return stage, 0.5
    lons = sorted(b["geometry"][len(b["geometry"]) // 2][1] for b in where)
    at = (lons[len(lons) // 2] - bbox["lon_min"]) / (bbox["lon_max"] - bbox["lon_min"])
    return stage, round(min(1.0, max(0.0, at)), 2)


def find_port() -> str | None:
    """The ESP32's serial port: LIGHTS_PORT if set, else the first USB serial adapter with an ESP32-style chip."""
    named = os.environ.get("LIGHTS_PORT", "").strip()
    if named.lower() in ("off", "none", "0"):
        return None
    if named:
        return named
    try:
        from serial.tools import list_ports
    except ImportError:  # pyserial is only needed when a strip is attached
        return None
    return next((p.device for p in list_ports.comports() if p.vid in ESP_USB_VIDS), None)


def find_strip_port() -> str | None:
    """The strip board's own USB serial port, when that board is plugged into this computer as well as the ESP32:
    LIGHTS_STRIP_PORT if set ("off" for never), else the first Raspberry Pi USB serial device."""
    if os.environ.get("LIGHTS_PORT", "").strip().lower() in ("off", "none", "0"):
        return None
    named = os.environ.get("LIGHTS_STRIP_PORT", "").strip()
    if named.lower() in ("off", "none", "0"):
        return None
    if named:
        return named
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    return next((p.device for p in list_ports.comports() if p.vid in STRIP_USB_VIDS), None)


class Strip:
    """Keeps the strip on the latest scene from a background thread, so a web request never waits on a serial port.

    The scene goes to the ESP32, which passes it down its wire to the strip's board. When the strip's board is also
    plugged into this computer by its own USB, the scene goes there as well, and that board's replies show whether the
    wire is carrying the ESP32's commands. If it falls silent the ESP32 is restarted (by reopening its port), which is
    what brings the wire back."""

    def __init__(self, open_port=None, find=find_port, boot_s: float = BOOT_S, find_direct=None, echo_s: float = ECHO_S):
        self._open_port, self._find, self._boot_s = open_port or _open_serial, find, boot_s
        self._find_direct, self._echo_s = find_direct or (lambda: None), echo_s
        self._ser = None  # the ESP32
        self._direct = None  # the strip board's own USB, when it is plugged in too
        self._want: str | None = None
        self._port: str | None = None
        self._direct_port: str | None = None
        self._error: str | None = None
        self._wire: bool | None = None  # does the strip's board hear the ESP32? None = cannot tell
        self._silent = 0
        self._restarted = 0.0
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def show(self, stage: str, at: float = 0.5) -> dict:
        self._want = scene_command(stage, at)
        self.warm()
        return self.status()

    def warm(self):
        """Connect in the background now, so the first scene does not wait for the ESP32 to start up."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        self._wake.set()

    def status(self) -> dict:
        wire = "unknown" if self._wire is None else "carrying commands" if self._wire else "silent"
        return {"connected": self._ser is not None or self._direct is not None, "port": self._port, "scene": self._want,
                "error": self._error, "direct_port": self._direct_port, "wire": wire}

    def step(self) -> bool:
        """One pass of the worker: connect what is not connected, send the scene. True when the strip has the scene."""
        if self._ser is None:
            self._connect()
        if self._direct is None:
            self._connect_direct()
        want = self._want
        if want is None:  # connected (or not) and waiting for the first scene
            return False
        by_wire = self._write("_ser", want)
        direct = self._write("_direct", want)
        if by_wire and direct:
            self._watch_wire(want)
        if by_wire or direct:
            self._error = None
        elif not self._error:
            self._error = "no strip found"
        return by_wire or direct

    def _connect(self):
        try:
            port = self._find()
            if not port:
                self._error, self._port = "no strip found", None
                return
            self._ser, self._port = self._open_port(port), port
            time.sleep(self._boot_s)
        except Exception as e:  # noqa: BLE001  busy, or pyserial missing: try again later
            self._error = str(e)[:120]
            self.close()

    def _connect_direct(self):
        try:
            port = self._find_direct()
            if port:
                self._direct, self._direct_port = self._open_port(port), port
        except Exception:  # noqa: BLE001  the second path is a bonus: never an error
            self._direct, self._direct_port = None, None

    def _write(self, which: str, want: str) -> bool:
        ser = getattr(self, which)
        if ser is None:
            return False
        try:
            ser.reset_input_buffer()  # old replies are not needed; do not let them pile up
            ser.write(want.encode() + b"\r")
            return True
        except Exception as e:  # noqa: BLE001  unplugged: forget the port and look for it again later
            if which == "_ser":
                self._error = str(e)[:120]
                self.close()
            else:
                self._close_direct()
            return False

    def _watch_wire(self, want: str):
        """The strip's board prints each line it gets and where from. No line from the ESP32 means its wire is silent."""
        if self._wake.wait(self._echo_s):  # a newer scene is waiting: send that first, judge the wire next time
            return
        try:
            n = self._direct.in_waiting
            heard = self._direct.read(n).decode(errors="replace") if n else ""
        except Exception:  # noqa: BLE001
            self._close_direct()
            return
        if f"[esp32] {want} ->" in heard:
            self._wire, self._silent = True, 0
            return
        self._silent += 1
        if self._silent >= SILENT_SENDS:
            self._wire = False
            if time.time() - self._restarted > RESTART_GAP_S:
                self._restarted, self._silent = time.time(), 0
                self.close()  # the next pass reopens the port, which restarts the ESP32

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ser = None

    def _close_direct(self):
        if self._direct is not None:
            try:
                self._direct.close()
            except Exception:  # noqa: BLE001
                pass
        self._direct, self._direct_port, self._wire = None, None, None

    def _work(self):
        while True:
            self._wake.wait(RESEND_S if self._ser is not None or self._direct is not None else LOOK_S)
            self._wake.clear()
            self.step()


def _open_serial(port: str):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, 0.2, 1.0
    s.dtr = s.rts = False
    s.open()
    return s


STRIP = Strip(find_direct=lambda: find_strip_port())


def show(stage: str, at: float = 0.5) -> dict:
    return STRIP.show(stage, at)


def _via_backend(stage: str, at: float) -> dict | None:
    """Ask a running backend to show the scene. None when no backend answers."""
    import json
    import urllib.request

    url = os.environ.get("REWIRE_API", "http://localhost:8000").rstrip("/") + "/lights"
    req = urllib.request.Request(url, data=json.dumps({"stage": stage, "at": at}).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1.5) as r:  # noqa: S310  our own backend
            return json.loads(r.read())
    except Exception:  # noqa: BLE001  not running: the caller opens the port itself
        return None


def main(argv: list[str], via=_via_backend) -> int:
    if not argv or argv[0] not in STAGES + ("demo",):
        print(__doc__)
        return 2
    steps = list(DEMO) if argv[0] == "demo" else [(argv[0], "")]
    at = 0.5 if argv[0] == "demo" or len(argv) < 2 else float(argv[1])
    hold = float(argv[1]) if argv[0] == "demo" and len(argv) > 1 else None  # seconds per step; without it, Enter moves on
    strip = None
    for k, (stage, text) in enumerate(steps, 1):
        answer = via(stage, at) if strip is None else None
        if answer is None:  # no backend running: drive the strip from here
            if strip is None:
                strip = Strip()
                if not find_port():
                    print("No strip found. Plug in the ESP32, or set LIGHTS_PORT to its serial port.")
                    return 1
            strip._want = scene_command(stage, at)
            if not strip.step():
                print(f"not sent: {strip._error}")
                return 1
        print(f"{k}. {text}" if text else f"{scene_command(stage, at)} sent")
        if k < len(steps):
            if hold is None:
                input("   Enter for the next step ")
            else:
                time.sleep(hold)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
