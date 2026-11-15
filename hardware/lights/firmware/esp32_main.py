# ESP32 lighting remote: USB serial in, one-wire UART out to the QT Py RP2040.
#
# Wiring (already in place):
#   ESP32 GPIO25 -----------> QT Py A0   (command line, 57600 8N1)
#   ESP32 GND    ------------ QT Py GND  (needed once the QT Py runs on battery)
#
# Plug the ESP32 into a computer and send text lines at 115200 baud
# (lightctl.py does this for you). Type "help" for the commands.
# The link is one-way, so this board checks each command itself and keeps
# track of what it last told the QT Py; that is what "status" reports.

import sys
import select
import time
from machine import Pin, UART

LINK_TX_PIN = 25
LINK_BAUD = 57600

MODES = (
    "aura", "mega", "spurts", "comet", "wave",
    "red", "orange", "yellow", "green", "cyan", "blue", "purple", "pink", "white",
    "rainbow", "rainbow_cycle", "ocean", "forest", "desert",
)
PRESETS = ("mega", "lucario", "charizard", "charizard_x", "mewtwo", "rayquaza", "gengar")

HELP = """commands:
  mode NAME      %s
                 custom off
  next / prev    step through the animations
  color R G B    solid custom color, each 0-255
  bright N       brightness 0-100 (percent)
  preset NAME    %s
  off / on       blank the strip / restore the last mode
  grid normal    power-grid demo: every LED green
  grid event A B   green, LEDs A to B red (the trouble spot)
  grid cut A B     green, LEDs A to B dark (power cut there)
  status         what this remote last set
  list           animation names
  help           this text""" % (" ".join(MODES), " ".join(PRESETS))

# UART2's RX is parked on GPIO26, which is not wired to anything.
link = UART(2, baudrate=LINK_BAUD, tx=LINK_TX_PIN, rx=26)
led = None
try:
    led = Pin(2, Pin.OUT, value=0)  # onboard blue LED on most dev boards
except Exception:
    pass

state = {"mode": "aura", "preset": "mega", "bright": 30, "color": (255, 255, 255), "grid": "normal"}
before_off = ["aura"]


def blink():
    if led:
        led.value(1)
        time.sleep_ms(15)
        led.value(0)


def status():
    return "mode=%s preset=%s bright=%d color=%d,%d,%d grid=%s" % (
        (state["mode"], state["preset"], state["bright"]) + state["color"] + (state["grid"],)
    )


def check(words):
    """Return (line to send to the QT Py, None) or (None, error)."""
    cmd, args = words[0], words[1:]
    if cmd == "mode" and len(args) == 1:
        name = args[0]
        if name not in MODES and name not in ("custom", "off"):
            return None, "unknown mode '%s' (try 'list')" % name
        if name == "off" and state["mode"] != "off":
            before_off[0] = state["mode"]
        state["mode"] = name
    elif cmd in ("next", "prev"):
        cur = state["mode"]
        i = MODES.index(cur) if cur in MODES else 0
        state["mode"] = MODES[(i + (1 if cmd == "next" else -1)) % len(MODES)]
    elif cmd == "color" and len(args) == 3:
        try:
            rgb = tuple(int(a) for a in args)
        except ValueError:
            return None, "color needs three numbers 0-255"
        if min(rgb) < 0 or max(rgb) > 255:
            return None, "color values are 0-255"
        state["color"] = rgb
        state["mode"] = "custom"
    elif cmd in ("bright", "brightness") and len(args) == 1:
        try:
            pct = int(args[0])
        except ValueError:
            return None, "bright needs a number 0-100"
        if not 0 <= pct <= 100:
            return None, "brightness is 0-100"
        state["bright"] = pct
        cmd = "bright"
    elif cmd == "preset" and len(args) == 1:
        if args[0] not in PRESETS:
            return None, "unknown preset '%s'" % args[0]
        state["preset"] = args[0]
    elif cmd == "grid" and args:
        kind = args[0]
        if kind not in ("normal", "event", "cut"):
            return None, "grid is: normal, event A B, cut A B"
        if kind == "normal":
            args = args[:1]
            state["grid"] = "normal"
        else:
            if len(args) != 3:
                return None, "grid %s needs the first and last LED" % kind
            try:
                a, b = int(args[1]), int(args[2])
            except ValueError:
                return None, "grid needs two LED numbers"
            if min(a, b) < 0 or max(a, b) > 999:
                return None, "LED numbers are 0-999"
            state["grid"] = "%s:%d-%d" % (kind, min(a, b), max(a, b))
        state["mode"] = "grid"
    elif cmd == "off":
        if state["mode"] != "off":
            before_off[0] = state["mode"]
        state["mode"] = "off"
    elif cmd == "on":
        state["mode"] = before_off[0]
    else:
        return None, "unknown command (try 'help')"
    return " ".join([cmd] + args), None


def handle(line):
    words = line.strip().lower().replace(",", " ").split()
    if not words:
        return
    if words[0] == "help":
        print(HELP)
        return
    if words[0] == "list":
        print(" ".join(MODES) + " custom off")
        return
    if words[0] == "status":
        print("OK " + status())
        return
    out, err = check(words)
    if err:
        print("ERR " + err)
        return
    # Leading newline flushes any noise the QT Py may have picked up.
    link.write("\n" + out + "\n")
    blink()
    print("OK " + status())


def main():
    print("ESP32 lighting remote ready. Type 'help'.")
    poll = select.poll()
    poll.register(sys.stdin, select.POLLIN)
    buf = ""
    while True:
        if not poll.poll(200):
            continue
        ch = sys.stdin.read(1)
        if ch in ("\r", "\n"):
            if buf:
                handle(buf)
            buf = ""
        elif ch in ("\x08", "\x7f"):
            buf = buf[:-1]
        elif len(buf) < 80:
            buf += ch


main()
