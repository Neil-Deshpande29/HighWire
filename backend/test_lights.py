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
        self.reply = b""

    reply = b""  # what the board has printed since the last write

    @property
    def in_waiting(self):
        return len(self.reply)

    def read(self, n):
        out, self.reply = self.reply[:n], self.reply[n:]
        return out

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
    assert not strip.step() and strip.status() == {"connected": False, "port": None, "scene": "grid event 12 17", "error": "no strip found",
                                                   "direct_port": None, "wire": "unknown"}


def test_a_fixed_scene_can_still_be_asked_for(monkeypatch):
    seen = []
    monkeypatch.setattr(L, "show", lambda stage, at=0.5: seen.append((stage, at)) or {"connected": False})
    assert R.lights_show(R.LightsIn(stage="cut", at=0.25)) == {"connected": False} and seen == [("cut", 0.25)]
    with pytest.raises(HTTPException) as bad:
        R.lights_show(R.LightsIn(stage="party"))
    assert bad.value.status_code == 400 and seen == [("cut", 0.25)]
    with pytest.raises(HTTPException):
        R.lights_show(R.LightsIn())  # neither an hour nor a scene


def test_connecting_ahead_of_time_means_the_first_scene_goes_straight_out():
    port, opened = FakePort(), []
    strip = L.Strip(open_port=lambda name: opened.append(name) or port, find=lambda: "/dev/fake", boot_s=0)
    assert not strip.step() and opened == ["/dev/fake"] and port.lines == []  # connected at server start, nothing to show yet
    strip._want = L.scene_command("cut", 0.5)
    assert strip.step() and opened == ["/dev/fake"] and port.lines == ["grid cut 12 17\r"]  # no second start-up wait


def test_a_second_path_keeps_the_strip_right_and_a_silent_wire_restarts_the_esp32():
    """With the strip's own board plugged in too: the scene also goes straight to it, its replies show whether the
    ESP32's wire is carrying anything, and a wire that stays silent gets the ESP32 restarted."""
    opened = []

    class Board(FakePort):  # the strip's board: prints each line it gets, and the ESP32's copy only if the wire works
        wire_ok = False

        def write(self, data):
            super().write(data)
            line = data.decode().strip()
            self.reply = f"[usb] {line} -> OK\r\n".encode() + (f"[esp32] {line} -> OK\r\n".encode() if self.wire_ok else b"")

    board = Board()

    def open_port(name):
        opened.append(name)
        return board if name == "/dev/strip" else FakePort()

    strip = L.Strip(open_port=open_port, find=lambda: "/dev/esp32", boot_s=0, find_direct=lambda: "/dev/strip", echo_s=0)
    strip._want = L.scene_command("cut", 0.5)
    for _ in range(L.SILENT_SENDS - 1):
        assert strip.step() and strip.status()["wire"] == "unknown"
    assert board.lines[-1] == "grid cut 12 17\r"  # the strip has the scene all along, by its own USB
    assert strip.step() and strip.status()["wire"] == "silent" and opened.count("/dev/esp32") == 1
    board.wire_ok = True  # reopening the ESP32's port restarted it, and its wire is back
    assert strip.step() and opened.count("/dev/esp32") == 2 and strip.status()["wire"] == "carrying commands"
    board.wire_ok = False
    for _ in range(L.SILENT_SENDS + 2):
        strip.step()
    assert strip.status()["wire"] == "silent" and opened.count("/dev/esp32") == 2  # not restarted again within the minute


# ---------------------------------------------------------------- the rule, and the server driving the strip by itself
BRANCHES = [{"id": f"L{k}", "cable": k == 3, "geometry": [[34.0, -119.0 + k], [34.0, -118.9 + k], [34.0, -118.8 + k]]} for k in range(5)]
BBOX = {"lon_min": -119.0, "lon_max": -114.8}


def test_the_rule_green_red_where_the_trouble_is_dark_once_power_is_cut():
    calm, load = [0.1] * 5, [40] * 5
    assert L.scene_of(BRANCHES, BBOX, calm, load, 0.3) == ("normal", 0.5)
    windy = [0.1, 0.35, 0.1, 0.9, 0.1]  # L1 is over the alarm; L3 is too, but it is an underground cable
    assert L.scene_of(BRANCHES, BBOX, windy, load, 0.3) == ("event", 0.26)
    assert L.scene_of(BRANCHES, BBOX, windy, load, 0.3, off={"L1"}) == ("cut", 0.26)  # the same stretch goes dark
    assert L.scene_of(BRANCHES, BBOX, windy, load, 0.3, demand_cut=True) == ("cut", 0.26)  # a plan that sheds demand instead
    assert L.scene_of(BRANCHES, BBOX, calm, [40, 40, 40, 40, 120], 0.3) == ("event", 0.98)  # an overloaded line is trouble too
    assert L.scene_of(BRANCHES, BBOX, calm, load, 0.3, off={"L2"}) == ("cut", 0.5)  # a calm-day switch-off: dark where that line is


@pytest.fixture(scope="module")
def grid():
    from backend.la.engine import LAGrid

    return LAGrid()


def test_the_server_changes_the_strip_by_itself_the_moment_a_plan_is_approved(grid, monkeypatch):
    from backend.agents.mock_planner import run_mock
    from backend.agents.tools import JobLog
    from backend.la import authority as A
    from backend.la import record as REC
    from backend.la.state import LAState

    t = "2018-11-08T19:00:00+00:00"  # Woolsey, 11 AM: 29 lines over the fire alarm
    st = LAState("E1", t, grid=grid)
    out = run_mock(st, JobLog(), pace=0)
    st.plans, st.plan_status = {p["id"]: p for p in out["plans"]}, {p["id"]: "pending" for p in out["plans"]}
    pid = next(p["id"] for p in out["plans"] if p["result"]["is_safe"])
    role = "supervisor" if A.of_result(st.plans[pid]["result"])["level"] == "supervisor" else "operator"
    seen = []
    monkeypatch.setattr(L, "show", lambda stage, at=0.5: seen.append((stage, at)) or {"connected": False})
    monkeypatch.setattr(L.STRIP, "status", lambda: {"connected": False})
    for k, v in {"event": None, "t": None, "actions": []}.items():
        monkeypatch.setitem(R.SCREEN, k, v)
    apply = st.apply_actions
    monkeypatch.setattr(st, "apply_actions", lambda actions: seen.append("switching") or apply(actions))
    R.LA["state"] = st
    REC.clear()
    try:
        R._strip_refresh()
        assert seen == []  # no screen has said which hour it shows: the strip is left alone
        R.lights_show(R.LightsIn(event="E1", t=t))
        (stage, at), = seen
        assert stage == "event" and 0 < at < 0.5  # red, on the western half: Woolsey was in the west valleys
        R.la_approve(pid, R.LARoleIn(role=role))
        # dark at once, before the switching and the bulletin are worked out, and still dark afterwards
        assert seen[1:] == [("cut", at), "switching", ("cut", at)]
        R.lights_show(R.LightsIn(event="E1", t=t))  # the screen repeating its hour changes nothing
        assert seen[-1] == ("cut", at)
        R.la_reset()
        assert seen[-1] == ("event", at)  # nothing is switched off any more, and the wind is still there
    finally:
        R.LA["state"] = None
        REC.clear()


def test_hand_switches_and_the_hour_on_screen_set_the_scene(monkeypatch):
    seen = []
    monkeypatch.setattr(L, "show", lambda stage, at=0.5: seen.append(stage) or {"connected": False})
    monkeypatch.setattr(L.STRIP, "status", lambda: {"connected": False})
    for k, v in {"event": None, "t": None, "actions": []}.items():
        monkeypatch.setitem(R.SCREEN, k, v)
    monkeypatch.setitem(R.LA, "state", None)
    storm, calm = "2018-11-08T19:00:00+00:00", "2018-11-08T08:00:00+00:00"
    off, on = {"type": "open_line", "target": "L5"}, {"type": "close_line", "target": "L5"}
    R.lights_show(R.LightsIn(event="E1", t=calm))  # midnight, before the wind
    R.lights_show(R.LightsIn(event="E1", t=storm))
    R.lights_show(R.LightsIn(event="E1", t=storm, actions=[off]))  # "Turn this line off"
    R.lights_show(R.LightsIn(event="E1", t=storm, actions=[on]))  # "Turn this line on"
    R.lights_show(R.LightsIn(event="E1", t=calm, actions=[off]))
    assert seen == ["normal", "event", "cut", "event", "cut"]
    with pytest.raises(HTTPException):
        R.lights_show(R.LightsIn(event="NOPE", t=storm))


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
