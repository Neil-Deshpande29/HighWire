"""The LED strip: the scene the screen asks for -> the one text line the boards take -> what the LEDs show.
Offline: a fake serial port and fake board hardware; no strip is touched."""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import lights as L  # noqa: E402
from backend import routes_la as R  # noqa: E402

FIRMWARE = Path(__file__).resolve().parents[1] / "hardware" / "lights" / "firmware"


class FakePort:
    def __init__(self, fail_after: int | None = None):
        self.lines, self.closed, self.fail_after = [], False, fail_after

    def write(self, data: bytes):
        if self.fail_after is not None and len(self.lines) >= self.fail_after:
            raise OSError("device not configured")  # what pyserial raises when the cable is pulled
        self.lines.append(data.decode())

    def reset_input_buffer(self):
        pass

    def close(self):
        self.closed = True


def test_each_scene_is_one_line_and_the_stretch_stays_on_the_strip():
    assert L.scene_command("normal", 0.9) == "grid normal"
    assert L.scene_command("event", 0.5) == "grid event 12 17"  # the middle six of thirty
    assert L.scene_command("cut", 0.5) == "grid cut 12 17"  # the same stretch goes dark
    assert L.scene_command("event", -3) == "grid event 0 5" and L.scene_command("cut", 7) == "grid cut 24 29"
    with pytest.raises(ValueError):
        L.scene_command("party")


def test_the_strip_gets_the_latest_scene_and_recovers_from_a_pulled_cable():
    ports = [FakePort(fail_after=1), FakePort()]
    opened = []

    def open_port(name):
        opened.append(name)
        return ports[len(opened) - 1]

    strip = L.Strip(open_port=open_port, find=lambda: "/dev/fake", boot_s=0)
    strip._want = L.scene_command("event", 0.2)
    assert strip.step() and ports[0].lines == ["grid event 5 10\r"] and strip.status()["connected"]
    strip._want = L.scene_command("cut", 0.2)
    assert not strip.step() and ports[0].closed and not strip.status()["connected"]  # the cable was pulled
    assert strip.step() and ports[1].lines == ["grid cut 5 10\r"]  # plugged back in: the scene is sent again
    assert strip.step() and ports[1].lines == ["grid cut 5 10\r"] * 2  # and repeated, because the wire is one-way


def test_no_strip_is_a_quiet_no_op():
    strip = L.Strip(open_port=lambda name: pytest.fail("no port may be opened"), find=lambda: None, boot_s=0)
    strip._want = L.scene_command("event")
    assert not strip.step() and strip.status() == {"connected": False, "port": None, "scene": "grid event 12 17", "error": "no strip found"}


def test_lights_route_passes_the_scene_on(monkeypatch):
    seen = []
    monkeypatch.setattr(L, "show", lambda stage, at=0.5: seen.append((stage, at)) or {"connected": False})
    assert R.lights_show(R.LightsIn(stage="cut", at=0.25)) == {"connected": False} and seen == [("cut", 0.25)]
    with pytest.raises(HTTPException) as bad:
        R.lights_show(R.LightsIn(stage="party"))
    assert bad.value.status_code == 400 and seen == [("cut", 0.25)]


def test_the_stand_alone_demo_goes_through_a_running_backend(capsys):
    """The backend holds the strip's port and repeats its scene, so the demo asks it instead of fighting it."""
    asked = []
    assert L.main(["demo", "0"], via=lambda stage, at: asked.append((stage, at)) or {"connected": True}) == 0
    assert asked == [("normal", 0.5), ("event", 0.5), ("cut", 0.5), ("normal", 0.5)]
    assert [line[:2] for line in capsys.readouterr().out.splitlines()] == ["1.", "2.", "3.", "4."]
    assert L.main(["cut", "0.25"], via=lambda stage, at: asked.append((stage, at)) or {"connected": True}) == 0 and asked[-1] == ("cut", 0.25)
    assert L.main(["party"]) == 2
    assert L.main(["event"], via=lambda stage, at: None) == 1  # no backend, and tests never open a port: no strip found


# ---------------------------------------------------------------- the boards' own programs, run here on fake hardware
def _load(name: str, monkeypatch, strip_main_call: bool = False):
    machine = types.ModuleType("machine")
    machine.Pin = type("Pin", (), {"OUT": 1, "IN": 0, "PULL_UP": 2, "__init__": lambda self, *a, **k: None, "value": lambda self, *a: 1})
    machine.UART = lambda *a, **k: types.SimpleNamespace(write=lambda data: None)
    monkeypatch.setitem(sys.modules, "machine", machine)
    monkeypatch.setitem(sys.modules, "neopixel", types.ModuleType("neopixel"))
    src = (FIRMWARE / name).read_text()
    if strip_main_call:  # the ESP32 program ends by starting its endless loop
        assert src.rstrip().endswith("\nmain()")
        src = src.rstrip()[: -len("main()")]
    mod = types.ModuleType(name.removesuffix(".py"))
    exec(compile(src, name, "exec"), mod.__dict__)  # noqa: S102  our own firmware file
    return mod


def test_the_boards_understand_every_line_the_app_sends(monkeypatch, capsys):
    esp = _load("esp32_main.py", monkeypatch, strip_main_call=True)
    qtpy = _load("qtpy_main.py", monkeypatch)
    show = qtpy.Show(qtpy.PALETTES["mega"])
    cmds = qtpy.Commands(show)

    def strip_after(stage, at=0.5):
        line = L.scene_command(stage, at)
        passed_on, err = esp.check(line.split())  # the ESP32 checks each line before it goes down the wire
        assert err is None and passed_on == line
        assert cmds.run(passed_on).startswith("OK mode=grid")
        for _ in range(60):  # a little over a second of frames: the change is eased in
            qtpy.write_frame(show.step(20, False))
        return qtpy.pixels_text()

    assert qtpy.NUM_LEDS == L.N_LEDS
    assert strip_after("normal") == "G" * 30  # 1. no climate event
    assert strip_after("event") == "G" * 12 + "R" * 6 + "G" * 12  # 2. the event: a red stretch
    assert strip_after("cut") == "G" * 12 + "." * 6 + "G" * 12  # 3. power off: that stretch is dark, the rest green
    assert strip_after("normal") == "G" * 30  # 4. power restored
    assert strip_after("event", 0.0) == "R" * 6 + "G" * 24 and strip_after("cut", 1.0) == "G" * 24 + "." * 6
    assert [stage for stage, _ in L.DEMO] == ["normal", "event", "cut", "normal"]  # the hard-coded demonstration
    assert esp.check(["grid", "event", "5"])[1] and esp.check(["grid", "party"])[1]  # a broken line never reaches the strip
    assert esp.check(["mode", "green"]) == ("mode green", None)  # the commands the boards had before still work
