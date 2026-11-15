"""The LED strip that stands for the grid (optional hardware; nothing in the app depends on it).

The strip is the grid laid out west to east, and it follows the map on screen:

    normal   every LED green              no line is in trouble
    event    green with a red stretch     lines there are at high fire risk or overloaded
    cut      green with a dark stretch    a line has been switched off: "Turn this line off", or an approved plan

The screen works out the scene (frontend/src/la/derive.ts, lightScene) and posts it to POST /lights; this module turns
it into one text line and sends it over USB serial to an ESP32, which passes it down a wire to the QT Py that drives the
strip (hardware/lights/). With no strip plugged in, or without pyserial, every call is a quiet no-op.

Try it without the app (the hard-coded four-step demonstration, or one scene):

    python -m backend.lights demo
    python -m backend.lights event 0.2

When the app's backend is running, these go through it, because it holds the strip's port and repeats its scene.
Close the app's browser tab first: an open tab puts its own scene back whenever it is used. With no backend running
the port is opened directly.

Settings (environment): LIGHTS_PORT = the ESP32's serial port, or "off" to never touch one. Without it the first
USB serial adapter that looks like an ESP32 is used. REWIRE_API = the backend's address (default http://localhost:8000).
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


class Strip:
    """Keeps the strip on the latest scene from a background thread, so a web request never waits on a serial port."""

    def __init__(self, open_port=None, find=find_port, boot_s: float = BOOT_S):
        self._open_port, self._find, self._boot_s = open_port or _open_serial, find, boot_s
        self._ser = None
        self._want: str | None = None
        self._sent: str | None = None
        self._port: str | None = None
        self._error: str | None = None
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def show(self, stage: str, at: float = 0.5) -> dict:
        self._want = scene_command(stage, at)
        if self._thread is None:
            self._thread = threading.Thread(target=self._work, daemon=True)
            self._thread.start()
        self._wake.set()
        return self.status()

    def status(self) -> dict:
        return {"connected": self._ser is not None, "port": self._port, "scene": self._want, "error": self._error}

    def step(self) -> bool:
        """One pass of the worker: connect if needed, send the scene. True when the strip has the scene."""
        want = self._want
        if want is None:
            return False
        try:
            if self._ser is None:
                port = self._find()
                if not port:
                    self._error, self._port = "no strip found", None
                    return False
                self._ser, self._port = self._open_port(port), port
                time.sleep(self._boot_s)
            self._ser.write(want.encode() + b"\r")
            self._sent, self._error = want, None
            try:
                self._ser.reset_input_buffer()  # the ESP32's replies are not needed; do not let them pile up
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as e:  # noqa: BLE001  unplugged, busy, or pyserial missing: try again later
            self._error = str(e)[:120]
            self.close()
            return False

    def close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
        self._ser, self._sent = None, None

    def _work(self):
        while True:
            self._wake.wait(RESEND_S)
            self._wake.clear()
            self.step()


def _open_serial(port: str):
    import serial

    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, 0.2, 1.0
    s.dtr = s.rts = False
    s.open()
    return s


STRIP = Strip()


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
