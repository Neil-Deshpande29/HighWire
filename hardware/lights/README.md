# The LED strip

A 30-LED strip that stands for the grid, laid out west to east. It follows the map on screen.

| What the app shows | What the strip shows |
| --- | --- |
| No line in trouble | Every LED green |
| Lines at high fire risk, or overloaded | Green, with a red stretch where the trouble is |
| A line is switched off: **Turn this line off**, or **Approve** on a plan | That stretch goes dark, the rest stays green |
| The line is back on and the trouble has passed | Every LED green again |

The server changes the strip by itself, at the moment the system changes: about 40 ms after **Approve** is pressed,
before the approval's own work is finished. Nothing waits on the strip, and the strip waits on nothing.

The strip is optional. With nothing plugged in the app runs exactly the same.

## Wiring

```
computer --USB--> ESP32 --GPIO25 to A0 (plus GND)--> QT Py RP2040 --MO--> strip (own 5 V supply)
```

The app sends one text line to the ESP32 at 115200 baud. The ESP32 checks it and passes it down the wire to the
QT Py, which drives the LEDs.

## Use it

Plug the ESP32 into the computer that runs the backend, then start the app as usual. The backend finds the port by
itself and opens it when it starts. To name it, set `LIGHTS_PORT` (for example `/dev/cu.usbserial-10`);
`LIGHTS_PORT=off` never opens a port.

Plug the QT Py into the same computer too if you can. The scene then also goes straight to it, so the strip stays
right even if the wire between the two boards stops carrying commands, and the backend restarts the ESP32 when that
happens. `http://localhost:8000/lights` shows what is connected and whether the wire is "carrying commands" or "silent".
Do not open either board's port in another program while the backend runs: the two would take each other's replies.

Without the app, from the repo folder:

```bash
.venv/bin/python -m backend.lights demo
```

That plays the four steps in the table, one per press of Enter. `demo 4` moves on by itself every 4 seconds.
Close the app's browser tab first: an open tab puts its own scene back on the strip whenever it is used.
One scene on its own: `.venv/bin/python -m backend.lights event 0.2` (the number is where along the strip, 0 to 1).

## The command the boards take

Both boards had a set of commands already (`mode green`, `color R G B`, `bright N`, `off`, ...). Those still work.
One was added:

```
grid normal        every LED green
grid event A B     green, LEDs A to B red, with a slow pulse
grid cut A B       green, LEDs A to B dark
```

`pixels`, typed into the QT Py's own USB serial, prints one letter per LED as lit now (`G`, `R`, `.` for dark).

## The board programs

`firmware/qtpy_main.py` and `firmware/esp32_main.py` are the programs on the two boards. Both boards run MicroPython;
each file is copied onto its board as `main.py` (Thonny or `mpremote cp` both work). `backend/test_lights.py` runs both
programs on fake hardware and checks that every line the app sends lights the strip as the table says.

The strip has 30 LEDs. For another length, change `NUM_LEDS` in `firmware/qtpy_main.py` and `N_LEDS` in
`backend/lights.py` together.
