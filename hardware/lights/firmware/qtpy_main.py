# Mega-evolution lighting for a costume LED strip on an RP2040.
#
# Wire this only after reading README.md. Short version:
# Adafruit QT Py RP2040 pads, by the label printed on the board:
#   MO  -- 330 ohm -- white wire (DIN / data) on the strip
#   GND ------------ both black wires, and the battery negative
#   5V  ------------ battery +5V through a series diode, same source as the strip
#   3V  ------------ leave empty
#   BOOT button (middle of the board) changes the animation
#   A0  ------------ command link from the ESP32 (ESP32 GPIO25 -> A0),
#                    plus a GND wire between the two boards
#
# Commands arrive as text lines over that wire (UART, 57600 8N1) or over
# this board's own USB serial. Send "help" over USB for the list.
#
# Flash MicroPython, then copy this file onto the board as main.py.

import math
import random
import time

try:
    from machine import Pin
    import neopixel
except ImportError:
    print("This program runs on the RP2040 with MicroPython installed.")
    raise

# MicroPython's time module has ticks_ms. Desktop Python does not.
if hasattr(time, "ticks_ms"):
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
    sleep_ms = time.sleep_ms
else:
    def ticks_ms():
        return int(time.time() * 1000)

    def ticks_diff(a, b):
        return a - b

    def sleep_ms(ms):
        time.sleep(ms / 1000.0)


# ---------------------------------------------------------------------------
# Costume settings. These are the knobs you will actually change.
# ---------------------------------------------------------------------------

DATA_PIN = 3  # QT Py pad labeled MO
# Count the LEDs. The first one is the end the white data wire enters.
NUM_LEDS = 30
# 0.15 is dim and easy on AA cells. 0.35 is a strong indoor costume.
# 1.0 is full blast and will brown out most battery packs on the white flash.
BRIGHTNESS = 0.30
# "mega", "lucario", "charizard", "charizard_x", "mewtwo", "rayquaza", "gengar"
PRESET = "mega"
BOOT_BUTTON_PIN = 21  # onboard BOOT button, active low
LINK_RX_PIN = 29  # pad labeled A0, wired to ESP32 GPIO25 (UART0 RX)
LINK_BAUD = 57600
COLOR_ORDER = "GRB"  # change to "RGB" if red and green are swapped
FRAME_MS = 20

BREATHE_MS = 8000
CHARGE_MS = 3200
FLASH_MS = 80
GAP_MS = 40
SHOCK_MS = 1000
MEGA_MS = 14000
COOLDOWN_MS = 1800

PALETTES = {
    # Anime-style evolution: violet body, cyan energy, gold sparks, white core.
    "mega": {
        "base": (48, 0, 90),
        "mid": (0, 190, 255),
        "hot": (255, 196, 40),
        "flash": (255, 255, 255),
    },
    "lucario": {
        "base": (0, 30, 90),
        "mid": (0, 130, 255),
        "hot": (140, 220, 255),
        "flash": (255, 255, 255),
    },
    "charizard": {
        "base": (70, 8, 0),
        "mid": (255, 48, 0),
        "hot": (255, 170, 16),
        "flash": (255, 236, 190),
    },
    "charizard_x": {
        "base": (0, 6, 28),
        "mid": (0, 80, 255),
        "hot": (70, 190, 255),
        "flash": (210, 235, 255),
    },
    "mewtwo": {
        "base": (46, 0, 64),
        "mid": (176, 30, 255),
        "hot": (255, 160, 240),
        "flash": (255, 255, 255),
    },
    "rayquaza": {
        "base": (0, 36, 16),
        "mid": (0, 210, 70),
        "hot": (230, 255, 40),
        "flash": (245, 255, 210),
    },
    "gengar": {
        "base": (28, 0, 48),
        "mid": (150, 0, 190),
        "hot": (255, 30, 110),
        "flash": (230, 180, 255),
    },
}

# Each press of BOOT advances to the next name.
# aura through wave are the costume show. Color names are solid fills.
# rainbow sits still along the strip; rainbow_cycle slides that band.
# ocean, forest, and desert are slow theme animations.
MODES = (
    "aura",
    "mega",
    "spurts",
    "comet",
    "wave",
    "red",
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
    "pink",
    "white",
    "rainbow",
    "rainbow_cycle",
    "ocean",
    "forest",
    "desert",
)

# Solid fills. BRIGHTNESS above still scales these.
# Green is pulled down on yellow and orange so they stay distinct on WS2812s,
# where the green die is much brighter than red or blue.
SOLID_COLORS = {
    "red": (255, 0, 0),
    "orange": (255, 60, 0),
    "yellow": (255, 160, 0),
    "green": (0, 255, 0),
    "cyan": (0, 180, 255),
    "blue": (0, 40, 255),
    "purple": (130, 0, 255),
    "pink": (255, 16, 96),
    "white": (255, 255, 255),
    # Set by the "color R G B" command.
    "custom": (255, 255, 255),
}

# Seconds for rainbow_cycle to slide one full length of the strip.
RAINBOW_SHIFT_S = 8.0

# The power-grid demo (the "grid" command). The strip stands for the grid:
#   grid normal       every LED green: all lines live, nothing wrong
#   grid event A B    green, LEDs A..B red: trouble on that stretch
#   grid cut A B      green, LEDs A..B dark: power to that stretch is off
GRID = {"kind": "normal", "a": 0, "b": -1}
GRID_GREEN = (0, 255, 0)
GRID_RED = (255, 0, 0)
GRID_EASE = 0.2  # share of the way each LED moves toward its target every frame
grid_now = None  # what the LEDs show right now, eased toward the scene
last_frame = None  # the last frame sent to the strip, for the "pixels" command

# The mega animation is the only one with a timed sequence.
MEGA_PHASES = (
    ("charge", CHARGE_MS),
    ("flash", FLASH_MS),
    ("gap", GAP_MS),
    ("shockwave", SHOCK_MS),
    ("mega", MEGA_MS),
    ("cooldown", COOLDOWN_MS),
)


def clamp_channel(v):
    if v < 0:
        return 0
    if v > 255:
        return 255
    return int(v)


def scale_color(color, amount):
    if amount < 0:
        amount = 0
    return (
        clamp_channel(color[0] * amount),
        clamp_channel(color[1] * amount),
        clamp_channel(color[2] * amount),
    )


def mix(c1, c2, t):
    if t < 0:
        t = 0
    elif t > 1:
        t = 1
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def add_color(c1, c2):
    return (
        clamp_channel(c1[0] + c2[0]),
        clamp_channel(c1[1] + c2[1]),
        clamp_channel(c1[2] + c2[2]),
    )


def blank():
    return [(0, 0, 0)] * NUM_LEDS


def paint_blob(colors, pos, color, radius, amount=1.0):
    if radius <= 0 or amount <= 0:
        return
    start = int(pos - radius)
    end = int(pos + radius) + 1
    if start < 0:
        start = 0
    if end > NUM_LEDS:
        end = NUM_LEDS
    for i in range(start, end):
        dist = abs(i - pos)
        if dist >= radius:
            continue
        falloff = 1.0 - dist / radius
        colors[i] = add_color(colors[i], scale_color(color, falloff * falloff * amount))


def spawn_spurt(sparks, palette, big=False, origin=None, vel=None):
    if len(sparks) >= 18 or NUM_LEDS <= 0:
        return
    if origin is None:
        origin = random.random() * (NUM_LEDS - 1 if NUM_LEDS > 1 else 1)
    if vel is None:
        speed = 12 + random.random() * 16
        if big:
            speed += 10
        vel = speed if random.random() < 0.5 else -speed
    life = 0.26 + random.random() * 0.28
    radius = 1.2 + random.random() * 0.9
    if big:
        life += 0.18
        radius += 1.1
    roll = random.random()
    if big and roll < 0.34:
        color = palette["flash"]
    elif roll < 0.62:
        color = palette["hot"]
    else:
        color = palette["mid"]
    sparks.append([origin, vel, 0.0, life, radius, color])


def update_sparks(sparks, dt_s):
    i = 0
    while i < len(sparks):
        spark = sparks[i]
        spark[0] += spark[1] * dt_s
        spark[2] += dt_s
        if spark[2] >= spark[3] or spark[0] < -4 or spark[0] > NUM_LEDS + 4:
            sparks.pop(i)
        else:
            i += 1


def overlay_sparks(colors, sparks):
    for pos, _vel, age, life, radius, color in sparks:
        if life <= 0:
            continue
        fade = 1.0 - age / life
        if fade <= 0:
            continue
        paint_blob(colors, pos, color, radius, amount=fade * fade)


def draw_breathe(t, progress, colors, palette):
    fade = 1.0 if progress > 0.18 else progress / 0.18
    sway = 0.5 + 0.5 * math.sin(t * 1.7)
    for i in range(NUM_LEDS):
        along = 0.0 if NUM_LEDS <= 1 else i / (NUM_LEDS - 1)
        wave = 0.5 + 0.5 * math.sin(t * 1.25 + along * 4.2)
        color = mix(palette["base"], palette["mid"], 0.22 + 0.6 * wave)
        body = 1.0 if NUM_LEDS <= 1 else math.sin(along * math.pi)
        level = (0.16 + 0.20 * sway) * (0.35 + 0.65 * body) * fade
        colors[i] = scale_color(color, level)


def draw_red_breathe(t, colors, fade=1.0):
    # Default show. One breath about every 4 seconds. exp(sin) eases
    # out of a dim ember and back in, so it reads as breathing.
    raw = (math.exp(math.sin(t * 1.5)) - 0.36787944) * 0.425
    if raw < 0.0:
        raw = 0.0
    elif raw > 1.0:
        raw = 1.0
    if fade < 0.0:
        fade = 0.0
    elif fade > 1.0:
        fade = 1.0
    level = (0.05 + 0.95 * raw) * fade
    lit = scale_color((255, 0, 0), level)
    for i in range(NUM_LEDS):
        colors[i] = lit


def draw_charge(t, progress, colors, palette):
    # Smooth swells only. The speed-up is the two heads racing inward,
    # which reads as a charge without a seizure-range strobe.
    pulse_hz = 0.7 + progress * 2.6
    pulse = 0.5 + 0.5 * math.sin(t * pulse_hz * 2 * math.pi)
    level = (0.04 + 0.28 * progress) * (0.4 + 0.6 * pulse)
    for i in range(NUM_LEDS):
        colors[i] = scale_color(palette["base"], level)
    if NUM_LEDS <= 1:
        paint_blob(colors, 0, palette["flash"], 1.0, amount=progress)
        return
    center = (NUM_LEDS - 1) / 2.0
    left = center * progress
    right = (NUM_LEDS - 1) - center * progress
    paint_blob(colors, left, palette["hot"], 1.8, amount=0.85)
    paint_blob(colors, right, palette["mid"], 1.8, amount=0.85)
    paint_blob(colors, center, palette["flash"], 0.8 + progress * 2.4, amount=0.25 + progress)


def draw_flash(colors, palette):
    lit = palette["flash"]
    for i in range(NUM_LEDS):
        colors[i] = lit


def draw_shockwave(progress, colors, palette):
    center = 0.0 if NUM_LEDS <= 1 else (NUM_LEDS - 1) / 2.0
    reach = progress * (center + 3.0)
    for i in range(NUM_LEDS):
        dist = abs(i - center)
        if dist > reach:
            colors[i] = scale_color(palette["base"], 0.04)
            continue
        color = mix(palette["mid"], palette["base"], 0.3)
        edge = reach - dist
        if edge < 2.0:
            color = mix(palette["hot"], palette["flash"], 1.0 - edge / 2.0)
        colors[i] = color
    reach2 = reach * 0.62
    for i in range(NUM_LEDS):
        if abs(abs(i - center) - reach2) < 1.15:
            colors[i] = add_color(colors[i], scale_color(palette["hot"], 0.55))


def draw_mega(t, colors, palette):
    breathe = 0.78 + 0.22 * math.sin(t * 2.1)
    for i in range(NUM_LEDS):
        along = 0.0 if NUM_LEDS <= 1 else i / (NUM_LEDS - 1)
        wave = 0.5 + 0.5 * math.sin(t * 1.6 + i * 0.55)
        drift = 0.5 + 0.5 * math.sin(t * 0.55 - along * 3.0)
        color = mix(palette["base"], palette["mid"], 0.30 + 0.70 * wave)
        color = mix(color, palette["hot"], 0.10 + 0.18 * drift)
        colors[i] = scale_color(color, breathe)
    # A living core the spurts appear to shoot out of.
    center = 0.0 if NUM_LEDS <= 1 else (NUM_LEDS - 1) / 2.0
    throb = 0.65 + 0.35 * math.sin(t * 4.6)
    paint_blob(colors, center, palette["hot"], 2.2 + throb, amount=0.28 * throb)
    paint_blob(colors, center, palette["flash"], 1.15, amount=0.45 * throb)
    # Soft ring a few times per mega phase. One traveling edge, not a strobe.
    period = 3.4
    local = t % period
    if local < 0.85 and NUM_LEDS > 1:
        ring_p = local / 0.85
        radius = ring_p * NUM_LEDS * 0.7
        for i in range(NUM_LEDS):
            edge = abs(abs(i - center) - radius)
            if edge < 1.4:
                amount = (1.0 - edge / 1.4) * (1.0 - ring_p) * 0.85
                colors[i] = add_color(colors[i], scale_color(palette["flash"], amount))


def draw_cooldown(t, progress, colors, palette):
    draw_mega(t, colors, palette)
    fade = 1.0 - progress
    fade = fade * fade
    for i in range(NUM_LEDS):
        colors[i] = scale_color(colors[i], fade)


def draw_spurt_bed(t, colors, palette):
    flicker = 0.08 + 0.05 * math.sin(t * 2.2)
    for i in range(NUM_LEDS):
        colors[i] = scale_color(palette["base"], flicker)
    if NUM_LEDS <= 1:
        return
    center = (NUM_LEDS - 1) / 2.0
    throb = 0.55 + 0.45 * math.sin(t * 3.4)
    paint_blob(colors, center, palette["mid"], 1.8, amount=0.28 * throb)


def draw_comet(t, colors, palette):
    span = float(NUM_LEDS - 1) if NUM_LEDS > 1 else 1.0
    cycle = 2.0
    wrapped = t % cycle
    p = wrapped / cycle
    if p < 0.5:
        tri = p * 2.0
        direction = 1.0
    else:
        tri = 2.0 - p * 2.0
        direction = -1.0
    pos = tri * span
    bed = scale_color(palette["base"], 0.07)
    for i in range(NUM_LEDS):
        colors[i] = bed
    tail = 7 if NUM_LEDS > 8 else max(2, NUM_LEDS - 1)
    for k in range(tail, 0, -1):
        amount = 0.6 * (1.0 - k / float(tail + 1))
        paint_blob(colors, pos - direction * k * 0.9, palette["mid"], 1.5, amount=amount)
    paint_blob(colors, pos, palette["hot"], 2.6, amount=0.9)
    paint_blob(colors, pos, palette["flash"], 1.2, amount=1.0)


def draw_wave(t, colors, palette):
    for i in range(NUM_LEDS):
        roll = 0.5 + 0.5 * math.sin(t * 1.8 - i * 0.48)
        shimmer = 0.5 + 0.5 * math.sin(t * 0.7 + i * 0.22)
        color = mix(palette["base"], palette["mid"], 0.15 + 0.85 * roll)
        color = mix(color, palette["hot"], 0.18 * shimmer)
        crest = 0.5 + 0.5 * math.sin(t * 2.6 - i * 0.62)
        crest = crest * crest * crest
        colors[i] = mix(color, palette["flash"], crest * 0.9)


def hsv_to_rgb(h, s, v):
    h = h % 1.0
    sector = int(h * 6.0) % 6
    frac = h * 6.0 - int(h * 6.0)
    p = v * (1.0 - s)
    q = v * (1.0 - frac * s)
    tch = v * (1.0 - (1.0 - frac) * s)
    if sector == 0:
        r, g, b = v, tch, p
    elif sector == 1:
        r, g, b = q, v, p
    elif sector == 2:
        r, g, b = p, v, tch
    elif sector == 3:
        r, g, b = p, q, v
    elif sector == 4:
        r, g, b = tch, p, v
    else:
        r, g, b = v, p, q
    return (
        clamp_channel(r * 255.0),
        clamp_channel(g * 255.0),
        clamp_channel(b * 255.0),
    )


def draw_solid(colors, color, fade=1.0):
    lit = scale_color(color, fade)
    for i in range(NUM_LEDS):
        colors[i] = lit


def draw_grid(t, colors):
    global grid_now
    if grid_now is None or len(grid_now) != NUM_LEDS:
        grid_now = [(0, 0, 0)] * NUM_LEDS
    kind, a, b = GRID["kind"], GRID["a"], GRID["b"]
    red = scale_color(GRID_RED, 0.72 + 0.28 * math.sin(t * 3.2))  # a slow pulse, so the trouble spot reads as live
    for i in range(NUM_LEDS):
        if kind != "normal" and a <= i <= b:
            target = red if kind == "event" else (0, 0, 0)
        else:
            target = GRID_GREEN
        now = grid_now[i]
        step = [0, 0, 0]
        for k in range(3):
            gap = target[k] - now[k]
            step[k] = target[k] if -8 <= gap <= 8 else int(now[k] + gap * GRID_EASE)
        grid_now[i] = colors[i] = (step[0], step[1], step[2])


def grid_text():
    if GRID["kind"] == "normal":
        return "normal"
    return "%s:%d-%d" % (GRID["kind"], GRID["a"], GRID["b"])


def pixels_text():
    # One letter per LED, first LED first: G green, R red, . dark, ? anything else.
    if last_frame is None:
        return ""
    text = ""
    for r, g, b in last_frame:
        if r < 12 and g < 12 and b < 12:
            text += "."
        elif g > 2 * r and g > 2 * b:
            text += "G"
        elif r > 2 * g and r > 2 * b:
            text += "R"
        else:
            text += "?"
    return text


def draw_rainbow(colors, offset, fade=1.0):
    # One full hue wheel along the strip. offset slides it.
    n = NUM_LEDS if NUM_LEDS > 0 else 1
    for i in range(NUM_LEDS):
        hue = (i / float(n) + offset) % 1.0
        colors[i] = scale_color(hsv_to_rgb(hue, 1.0, 1.0), fade)


def draw_ocean(t, colors, fade=1.0):
    # Slow swells. Foam only on the crests, so the troughs stay deep blue.
    deep = (0, 14, 46)
    mid = (0, 100, 160)
    foam = (186, 236, 255)
    for i in range(NUM_LEDS):
        wave = 0.5 + 0.5 * math.sin(t * 0.85 - i * 0.52)
        swell = 0.5 + 0.5 * math.sin(t * 0.32 + i * 0.18)
        color = mix(deep, mid, 0.18 + 0.82 * wave)
        crest = wave * wave * wave
        color = mix(color, foam, crest * 0.62 * (0.45 + 0.55 * swell))
        level = 0.22 + 0.62 * wave
        colors[i] = scale_color(color, level * fade)


def draw_forest(t, colors, fade=1.0):
    # Dim canopy, with a few warm spots drifting through it like sun.
    deep = (0, 22, 4)
    leaf = (10, 128, 22)
    sun = (255, 186, 42)
    for i in range(NUM_LEDS):
        along = 0.0 if NUM_LEDS <= 1 else i / (NUM_LEDS - 1)
        sway = 0.5 + 0.5 * math.sin(t * 0.5 + i * 0.36)
        shade = 0.5 + 0.5 * math.sin(along * 8.5)
        color = mix(deep, leaf, 0.12 + 0.7 * sway)
        level = (0.18 + 0.36 * sway) * (0.62 + 0.38 * shade)
        colors[i] = scale_color(color, level * fade)
    if NUM_LEDS <= 1:
        colors[0] = add_color(colors[0], scale_color(sun, 0.35 * fade))
        return
    span = float(NUM_LEDS - 1)
    for start, speed, radius in ((0.08, 0.035, 2.0), (0.4, 0.028, 2.4), (0.72, 0.042, 1.7)):
        pos = ((start + t * speed) % 1.0) * span
        paint_blob(colors, pos, sun, radius, amount=0.5 * fade)


def draw_desert(t, colors, fade=1.0):
    # Amber dunes, a little heat shimmer, and one slow sun.
    shadow = (96, 28, 0)
    sand = (255, 132, 16)
    glare = (255, 214, 120)
    for i in range(NUM_LEDS):
        dune = 0.5 + 0.5 * math.sin(t * 0.38 - i * 0.3)
        shimmer = 0.5 + 0.5 * math.sin(t * 2.4 + i * 1.1)
        color = mix(shadow, sand, 0.28 + 0.58 * dune)
        color = mix(color, glare, 0.1 * shimmer)
        level = 0.32 + 0.4 * dune
        colors[i] = scale_color(color, level * fade)
    if NUM_LEDS <= 1:
        colors[0] = add_color(colors[0], scale_color(glare, 0.4 * fade))
        return
    span = float(NUM_LEDS - 1)
    sun_pos = (0.5 + 0.5 * math.sin(t * 0.16)) * span
    paint_blob(colors, sun_pos, glare, 4.2, amount=0.42 * fade)


def render_phase(name, t, progress, colors, palette, sparks):
    if name == "breathe":
        draw_breathe(t, progress, colors, palette)
        overlay_sparks(colors, sparks)
    elif name == "charge":
        draw_charge(t, progress, colors, palette)
        overlay_sparks(colors, sparks)
    elif name == "flash":
        draw_flash(colors, palette)
    elif name == "gap":
        return
    elif name == "shockwave":
        draw_shockwave(progress, colors, palette)
        overlay_sparks(colors, sparks)
    elif name == "mega":
        draw_mega(t, colors, palette)
        overlay_sparks(colors, sparks)
    elif name == "cooldown":
        draw_cooldown(t, progress, colors, palette)
        overlay_sparks(colors, sparks)


class Show:
    def __init__(self, palette):
        self.palette = palette
        self.mode_i = 0
        self.mode_name = MODES[0]
        self.mode_elapsed = 0
        self.phase_i = 0
        self.elapsed = 0
        self.total = 0
        self.sparks = []
        self.until_spurt = 500
        self.btn_level = 1
        self.btn_lockout = 0
        self.phase_name = self.mode_name

    def enter(self, index):
        self.phase_i = index % len(MEGA_PHASES)
        self.elapsed = 0
        name = MEGA_PHASES[self.phase_i][0]
        self.phase_name = name
        print("phase", name)
        if name == "flash":
            self.sparks = []
        elif name == "charge":
            self.until_spurt = 140
        elif name == "shockwave":
            self.burst_from_center(7)
        elif name == "mega":
            for _ in range(6):
                spawn_spurt(self.sparks, self.palette, big=True)
            self.until_spurt = 100

    def burst_from_center(self, count):
        center = 0.0 if NUM_LEDS <= 1 else (NUM_LEDS - 1) / 2.0
        for n in range(count):
            direction = -1 if n % 2 == 0 else 1
            speed = 18 + random.random() * 22
            spawn_spurt(
                self.sparks,
                self.palette,
                big=True,
                origin=center,
                vel=direction * speed,
            )

    def take_press(self, down, dt_ms):
        if self.btn_lockout > 0:
            self.btn_lockout -= dt_ms
        level = 0 if down else 1
        pressed = self.btn_level == 1 and level == 0 and self.btn_lockout <= 0
        if pressed:
            self.btn_lockout = 400
        self.btn_level = level
        return pressed

    def set_mode(self, index):
        self.mode_i = index % len(MODES)
        self.mode_name = MODES[self.mode_i]
        self.mode_elapsed = 0
        self.sparks = []
        self.until_spurt = 80
        self.phase_name = self.mode_name
        print("mode", self.mode_name)
        if self.mode_name == "mega":
            self.enter(0)

    def on_button(self):
        self.set_mode(self.mode_i + 1)

    def spawn_burst(self, dt_ms, lo, hi, big_chance, extra):
        self.until_spurt -= dt_ms
        if self.until_spurt > 0:
            return
        spawn_spurt(self.sparks, self.palette, big=random.random() < big_chance)
        if extra and random.random() < 0.5:
            spawn_spurt(self.sparks, self.palette, big=False)
        span = hi - lo
        self.until_spurt = lo + int(random.random() * span) if span > 0 else lo

    def spawn_for_phase(self, name, dt_ms, progress):
        self.until_spurt -= dt_ms
        if self.until_spurt > 0:
            return
        if name == "breathe":
            spawn_spurt(self.sparks, self.palette, big=False)
            self.until_spurt = 700 + int(random.random() * 1100)
        elif name == "charge":
            center = 0.0 if NUM_LEDS <= 1 else (NUM_LEDS - 1) / 2.0
            origin = random.random() * (NUM_LEDS - 1 if NUM_LEDS > 1 else 1)
            direction = 1 if center >= origin else -1
            spawn_spurt(
                self.sparks,
                self.palette,
                big=progress > 0.65,
                origin=origin,
                vel=direction * (14 + progress * 20),
            )
            self.until_spurt = int(180 - progress * 100)
            if self.until_spurt < 50:
                self.until_spurt = 50
        elif name == "mega":
            spawn_spurt(self.sparks, self.palette, big=random.random() < 0.35)
            if random.random() < 0.45:
                spawn_spurt(self.sparks, self.palette, big=False)
            self.until_spurt = 80 + int(random.random() * 160)

    def render_mega(self, dt_ms):
        name, duration = MEGA_PHASES[self.phase_i]
        if duration <= 0 or self.elapsed >= duration:
            self.enter(self.phase_i + 1)
            name, duration = MEGA_PHASES[self.phase_i]
        progress = 0.0 if duration <= 0 else self.elapsed / float(duration)
        if progress > 1:
            progress = 1
        if name not in ("flash", "gap"):
            update_sparks(self.sparks, dt_ms / 1000.0)
            self.spawn_for_phase(name, dt_ms, progress)
        colors = blank()
        render_phase(name, self.total / 1000.0, progress, colors, self.palette, self.sparks)
        return colors

    def go_to(self, name):
        # Jump straight to a named mode (from a command). "custom" and "off"
        # are reachable only this way; BOOT presses still cycle MODES.
        if name in MODES:
            self.set_mode(MODES.index(name))
            return
        self.mode_name = name
        self.mode_elapsed = 0
        self.sparks = []
        self.phase_name = name
        print("mode", name)

    def render_mode(self, dt_ms):
        t = self.mode_elapsed / 1000.0
        colors = blank()
        name = self.mode_name
        if name == "off":
            return colors
        if name == "comet":
            draw_comet(t, colors, self.palette)
            return colors
        if name == "wave":
            draw_wave(t, colors, self.palette)
            return colors
        if name == "spurts":
            draw_spurt_bed(t, colors, self.palette)
            update_sparks(self.sparks, dt_ms / 1000.0)
            self.spawn_burst(dt_ms, 70, 160, 0.4, True)
            overlay_sparks(colors, self.sparks)
            return colors
        if name == "grid":
            draw_grid(t, colors)
            return colors
        fade = self.mode_elapsed / 700.0
        if fade > 1.0:
            fade = 1.0
        if name in SOLID_COLORS:
            draw_solid(colors, SOLID_COLORS[name], fade)
            return colors
        if name == "rainbow":
            draw_rainbow(colors, 0.0, fade)
            return colors
        if name == "rainbow_cycle":
            shift = 0.0 if RAINBOW_SHIFT_S <= 0 else (t / RAINBOW_SHIFT_S) % 1.0
            draw_rainbow(colors, shift, fade)
            return colors
        if name == "ocean":
            draw_ocean(t, colors, fade)
            return colors
        if name == "forest":
            draw_forest(t, colors, fade)
            return colors
        if name == "desert":
            draw_desert(t, colors, fade)
            return colors
        draw_red_breathe(t, colors, fade)
        return colors

    def step(self, dt_ms, button_down):
        if dt_ms < 0:
            dt_ms = 0
        self.total += dt_ms
        switched = self.take_press(button_down, dt_ms)
        if switched:
            self.on_button()
            dt_ms = 0
        else:
            self.mode_elapsed += dt_ms
            if self.mode_name == "mega":
                self.elapsed += dt_ms
        if self.mode_name == "mega":
            return self.render_mega(dt_ms)
        return self.render_mode(dt_ms)


np = None


def write_frame(colors):
    global last_frame
    last_frame = colors
    if np is None:
        return
    for i, color in enumerate(colors):
        red = clamp_channel(color[0] * BRIGHTNESS)
        green = clamp_channel(color[1] * BRIGHTNESS)
        blue = clamp_channel(color[2] * BRIGHTNESS)
        if COLOR_ORDER == "RGB":
            np[i] = (green, red, blue)
        else:
            np[i] = (red, green, blue)
    np.write()


def play_ignition(palette):
    # One spark runs the length of the strip so you can see that data,
    # power, and LED count are right before the show starts.
    if np is None or NUM_LEDS <= 0:
        return
    for i in range(NUM_LEDS):
        colors = blank()
        paint_blob(colors, i, palette["hot"], 2.0, amount=1.0)
        if i > 0:
            paint_blob(colors, i - 1, palette["mid"], 2.4, amount=0.45)
        write_frame(colors)
        sleep_ms(16)
    write_frame(blank())


def palette_or_default():
    if PRESET in PALETTES:
        return PALETTES[PRESET]
    print("Unknown PRESET '%s', using mega" % PRESET)
    return PALETTES["mega"]


HELP = """commands (one per line):
  mode NAME      jump to an animation (see 'list'), or 'custom'
  next / prev    step through the animations like the BOOT button
  color R G B    solid custom color, each 0-255
  bright N       brightness 0-100 (percent)
  preset NAME    palette for the show modes: %s
  off / on       blank the strip / come back to the last mode
  grid normal    power-grid demo: every LED green
  grid event A B   green, LEDs A to B red (the trouble spot)
  grid cut A B     green, LEDs A to B dark (power cut there)
  pixels         one letter per LED as lit now: G R . ?
  status         print the current settings
  list           print the animation names""" % " ".join(sorted(PALETTES))


class Commands:
    """Line-based command parser fed by the ESP32 link and by USB stdin."""

    def __init__(self, show):
        self.show = show
        self.before_off = show.mode_name

    def status(self):
        return "mode=%s preset=%s bright=%d color=%d,%d,%d grid=%s" % (
            (self.show.mode_name, PRESET, int(BRIGHTNESS * 100 + 0.5)) + SOLID_COLORS["custom"] + (grid_text(),)
        )

    def run(self, line):
        global BRIGHTNESS, PRESET, grid_now
        words = line.strip().lower().replace(",", " ").split()
        if not words:
            return None
        cmd, args = words[0], words[1:]
        show = self.show
        try:
            if cmd == "mode" and len(args) == 1:
                name = args[0]
                if name not in MODES and name not in ("custom", "off"):
                    return "ERR unknown mode " + name
                if name == "off":
                    self.before_off = show.mode_name
                show.go_to(name)
            elif cmd == "next":
                show.set_mode(show.mode_i + 1)
            elif cmd == "prev":
                show.set_mode(show.mode_i - 1)
            elif cmd == "color" and len(args) == 3:
                rgb = tuple(int(a) for a in args)
                if min(rgb) < 0 or max(rgb) > 255:
                    return "ERR color values are 0-255"
                SOLID_COLORS["custom"] = rgb
                show.go_to("custom")
            elif cmd in ("bright", "brightness") and len(args) == 1:
                pct = int(args[0])
                if pct < 0 or pct > 100:
                    return "ERR brightness is 0-100"
                BRIGHTNESS = pct / 100.0
            elif cmd == "preset" and len(args) == 1:
                if args[0] not in PALETTES:
                    return "ERR unknown preset " + args[0]
                PRESET = args[0]
                show.palette = PALETTES[PRESET]
            elif cmd == "off":
                if show.mode_name != "off":
                    self.before_off = show.mode_name
                show.go_to("off")
            elif cmd == "on":
                show.go_to(self.before_off if self.before_off != "off" else MODES[0])
            elif cmd == "grid" and args:
                kind = args[0]
                if kind not in ("normal", "event", "cut"):
                    return "ERR grid is: normal, event A B, cut A B"
                a, b = 0, -1
                if kind != "normal":
                    if len(args) != 3:
                        return "ERR grid %s needs the first and last LED" % kind
                    a, b = int(args[1]), int(args[2])
                    if a > b:
                        a, b = b, a
                    a = max(a, 0)
                    b = min(b, NUM_LEDS - 1)
                GRID["kind"], GRID["a"], GRID["b"] = kind, a, b
                if show.mode_name != "grid":  # already showing the grid: only the scene changes, with no restart
                    grid_now = None
                    show.go_to("grid")
            elif cmd == "pixels":
                return "OK pixels " + pixels_text()
            elif cmd == "status":
                pass
            elif cmd == "list":
                return " ".join(MODES) + " custom off"
            elif cmd == "help":
                return HELP
            else:
                return "ERR unknown command: " + line.strip()
        except ValueError:
            return "ERR numbers expected: " + line.strip()
        return "OK " + self.status()


class LineReader:
    """Collects bytes into lines without blocking the animation loop."""

    def __init__(self):
        self.buf = bytearray()

    def feed(self, data):
        lines = []
        for b in data:
            if b in (10, 13):
                if self.buf:
                    lines.append(self.buf.decode())
                    self.buf = bytearray()
            elif 32 <= b < 127 and len(self.buf) < 64:
                # Anything else is line noise (e.g. the ESP32 booting) - drop it.
                self.buf.append(b)
        return lines


def run():
    global np
    palette = palette_or_default()
    # timing=1 is the 800 kHz waveform WS2812 / SK6812 strips expect.
    np = neopixel.NeoPixel(Pin(DATA_PIN), NUM_LEDS, timing=1)
    boot_button = Pin(BOOT_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
    # Command link from the ESP32. UART0 RX on GPIO29 = pad A0. The pull-up
    # keeps the line idle (high) while the ESP32 is unplugged or booting.
    from machine import UART

    link = UART(0, baudrate=LINK_BAUD, rx=Pin(LINK_RX_PIN, Pin.IN, Pin.PULL_UP), rxbuf=256)
    link_lines = LineReader()
    # The same commands also work typed into this board's USB serial.
    import sys
    import select

    usb_poll = select.poll()
    usb_poll.register(sys.stdin, select.POLLIN)
    usb_lines = LineReader()
    try:
        import rp2
    except ImportError:
        rp2 = None
    bootsel_ok = True

    def pressed():
        # BOOT is the button near the middle of the QT Py.
        nonlocal bootsel_ok
        if boot_button.value() == 0:
            return True
        if rp2 is not None and bootsel_ok:
            try:
                if rp2.bootsel_button():
                    return True
            except Exception as exc:
                bootsel_ok = False
                print("BOOTSEL unavailable", exc)
        return False

    peak_ma = int(NUM_LEDS * 60 * BRIGHTNESS)
    print("QT Py data pad MO, preset", PRESET, "leds", NUM_LEDS, "brightness", BRIGHTNESS)
    print("full-strip flash is about", peak_ma, "mA")
    print("animations:", " ".join(MODES))
    play_ignition(palette)
    show = Show(palette)
    commands = Commands(show)
    print("mode", show.mode_name)
    print("listening for ESP32 commands on A0 at", LINK_BAUD, "baud; type 'help' here for the list")

    def handle(lines, source):
        for line in lines:
            reply = commands.run(line)
            if reply:
                print("[%s] %s -> %s" % (source, line, reply))

    last = ticks_ms()
    while True:
        n = link.any()
        if n:
            handle(link_lines.feed(link.read(n)), "esp32")
        while usb_poll.poll(0):
            handle(usb_lines.feed(sys.stdin.buffer.read(1)), "usb")
        now = ticks_ms()
        dt = ticks_diff(now, last)
        if dt < FRAME_MS:
            sleep_ms(FRAME_MS - dt)
            continue
        last = now
        if dt > 80:
            dt = 80
        write_frame(show.step(dt, pressed()))


def main():
    try:
        run()
    except Exception as exc:
        import sys

        print("light controller error:", exc)
        if hasattr(sys, "print_exception"):
            sys.print_exception(exc)
        if np is not None:
            try:
                np.fill((0, 0, 0))
                np.write()
            except Exception:
                pass


if __name__ == "__main__":
    main()
