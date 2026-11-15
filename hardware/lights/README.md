# The LED strip

A 30-LED strip that stands for the grid, laid out west to east. It follows the map on screen.

| What the app shows | What the strip shows |
| --- | --- |
| No line in trouble | Every LED green |
| Lines at high fire risk, or overloaded | Green, with a red stretch where the trouble is |
| A line is switched off: **Turn this line off**, or **Approve** on a plan | That stretch goes dark, the rest stays green |
| The line is back on and the trouble has passed | Every LED green again |

The strip is optional. With nothing plugged in the app runs exactly the same.

## Wiring

```
computer --USB--> ESP32 --GPIO25 to A0 (plus GND)--> QT Py RP2040 --MO--> strip (own 5 V supply)
```

The app sends one text line to the ESP32 at 115200 baud. The ESP32 checks it and passes it down the wire to the
QT Py, which drives the LEDs.

## Use it

Plug the ESP32 into the computer that runs the backend, then start the app as usual. The backend finds the port by
itself. To name it, set `LIGHTS_PORT` (for example `/dev/cu.usbserial-10`); `LIGHTS_PORT=off` never opens a port.

Without the app, from the repo folder:

```bash
.venv/bin/python -m backend.lights demo
```

That plays the four steps in the table, one per press of Enter. `demo 4` moves on by itself every 4 seconds.
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
