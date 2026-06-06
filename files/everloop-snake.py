#!/usr/bin/env python3
# ---------------------------------------------------------------------------
#  Rainbow "snake" running around the MATRIX Creator LED ring (Everloop).
# ---------------------------------------------------------------------------
#  The ring has 35 RGBW LEDs. Each LED is 4 bytes: Red, Green, Blue, White.
#  Lighting the ring is just writing those bytes to /dev/matrixio_everloop.
# ---------------------------------------------------------------------------
import time

NB_LEDS    = 35      # number of LEDs on the ring
LENGTH     = 14      # snake length (head + fading tail)
BRIGHTNESS = 60      # max brightness 0-255 -- keep it low, it's dazzling!
SPEED      = 0.05    # seconds between frames (smaller = faster)
DEVICE     = "/dev/matrixio_everloop"


def rainbow(hue):
    # hue goes 0..1 around the whole rainbow -> (red, green, blue) in 0..1
    i = int(hue * 6) % 6
    f = hue * 6 - int(hue * 6)
    p, q, t = 0.0, 1 - f, f
    return [(1, t, p), (q, 1, p), (p, 1, t),
            (p, q, 1), (t, p, 1), (1, p, q)][i]


head = 0.0   # snake head position
hue  = 0.0   # head colour (drifts through the rainbow)

while True:
    frame = bytearray(NB_LEDS * 4)              # all LEDs off to start
    for k in range(LENGTH):                     # draw head, then the tail
        pos = int(head - k) % NB_LEDS
        glow = (1 - k / LENGTH) ** 2            # tail fades towards the end
        r, g, b = rainbow((hue - k * 0.03) % 1.0)
        o = pos * 4
        frame[o]     = int(r * BRIGHTNESS * glow)   # red
        frame[o + 1] = int(g * BRIGHTNESS * glow)   # green
        frame[o + 2] = int(b * BRIGHTNESS * glow)   # blue
        frame[o + 3] = 0                            # white (unused)
    with open(DEVICE, "wb") as f:
        f.write(bytes(frame))
    head = (head + 1) % NB_LEDS                 # snake advances one LED
    hue  = (hue + 0.008) % 1.0                  # colour drifts slowly
    time.sleep(SPEED)
