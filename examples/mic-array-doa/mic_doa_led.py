#!/usr/bin/env python3
"""Real-time DOA -> Everloop on the MATRIX Creator, with intensity-scaled beam
width and pitch-mapped color.

  - Direction: SRP-PHAT on the 8-mic array -> LED pointing at the source
    (mapped via the measured calibration DOA = CAL_S*ring + CAL_OFF).
  - Beam width: number of lit LEDs grows 1..7 with sound level.
  - Color: fundamental frequency (HPS pitch) -> hue, low=red ... high=violet.

Run ON banshee (needs numpy):
    python3 /root/mic_doa_led.py [--gate -50] [--loud -12] [--frame 4096]
Ctrl-C stops (LEDs off).
"""
import sys, subprocess, argparse, math, colorsys
import numpy as np

# ---- calibration (from mic_cal.py 9-point fit, RMS 5.3 deg) ----
CAL_S   = -1
CAL_OFF = 350.9
# ---------------------------------------------------------------

DEV   = "plughw:CARD=MATRIXIOSOUND"
EVER  = "/dev/matrixio_everloop"
NLED  = 35
STEP  = 360.0 / NLED
RATE  = 16000
CH    = 8
C     = 343.0
BAND  = (300.0, 3500.0)          # DOA band
NAZ   = 72                       # azimuth grid (5 deg)
NMAX  = 7                        # max lit LEDs
BRIGHT = 60                      # peak brightness 0..255 (center of the beam)
FALLOFF = 1.8                    # gaussian beam sharpness (higher = tighter center)
F0_RANGE = (70.0, 1000.0)        # pitch search range (Hz)
HUE_RANGE = (80.0, 800.0)        # f0 -> hue mapping range (Hz)

MICS = np.array([
    [ 20.0908795, -48.5036755], [-20.0908795, -48.5036755],
    [-48.5036755, -20.0908795], [-48.5036755,  20.0908795],
    [-20.0908795,  48.5036755], [ 20.0908795,  48.5036755],
    [ 48.5036755,  20.0908795], [ 48.5036755, -20.0908795],
]) / 1000.0

def build_steering(nfft):
    freqs = np.fft.rfftfreq(nfft, 1.0 / RATE)
    fmask = (freqs >= BAND[0]) & (freqs <= BAND[1])
    fb = freqs[fmask]
    az = np.deg2rad(np.arange(0, 360, 360 // NAZ))
    u = np.stack([np.cos(az), np.sin(az)], axis=1)
    pairs = [(i, j) for i in range(CH) for j in range(i + 1, CH)]
    w = 2j * np.pi * fb
    steer = [np.exp(np.outer((u @ (MICS[i] - MICS[j])) / C, w)) for (i, j) in pairs]
    return fmask, pairs, steer, np.arange(0, 360, 360 // NAZ)

def doa(x, fmask, pairs, steer):
    nfft = (len(fmask) - 1) * 2
    X = np.fft.rfft(x, n=nfft, axis=0)[fmask]
    Xn = X / (np.abs(X) + 1e-12)
    power = np.zeros(NAZ)
    for k, (i, j) in enumerate(pairs):
        power += np.real(steer[k] @ (Xn[:, i] * np.conj(Xn[:, j])))
    return power

def estimate_f0(x):
    mono = x.mean(axis=1)
    mag = np.abs(np.fft.rfft(mono * np.hanning(len(mono))))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / RATE)
    hps = mag.copy()
    for h in (2, 3, 4):                      # harmonic product spectrum
        dec = mag[::h]
        hps[:len(dec)] *= dec
    lo = int(np.searchsorted(freqs, F0_RANGE[0]))
    hi = int(np.searchsorted(freqs, F0_RANGE[1]))
    if hi <= lo:
        return None
    return float(freqs[lo + int(np.argmax(hps[lo:hi]))])

def f0_to_rgb(f0):
    if not f0 or f0 <= 0:
        return (1.0, 1.0, 1.0)               # white fallback
    lo, hi = HUE_RANGE
    t = math.log2(min(max(f0, lo), hi) / lo) / math.log2(hi / lo)
    r, g, b = colorsys.hsv_to_rgb(t * 0.7, 1.0, 1.0)   # red -> violet
    return (r, g, b)

def count_for(lvl, gate, loud):
    t = (lvl - gate) / max(1e-6, (loud - gate))
    t = min(1.0, max(0.0, t))
    return int(round(1 + (NMAX - 1) * t))

def _b(v):
    return max(0, min(255, int(v)))

def render(center, n, rgb):
    d = bytearray(NLED * 4)
    offs = [k - n // 2 for k in range(n)]
    m = max((abs(o) for o in offs), default=0) or 1     # normalize by real max offset
    r, g, b = rgb
    for off in offs:
        li = (center + off) % NLED
        scale = math.exp(-FALLOFF * (off / m) ** 2)     # gaussian: bright center, soft edges
        d[li*4:li*4+4] = bytes((_b(r*BRIGHT*scale), _b(g*BRIGHT*scale),
                                _b(b*BRIGHT*scale), 0))
    with open(EVER, "wb") as f:
        f.write(bytes(d))

def clear():
    with open(EVER, "wb") as f:
        f.write(bytes(NLED * 4))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=float, default=-50.0, help="dBFS level gate (1 LED)")
    ap.add_argument("--loud", type=float, default=-12.0, help="dBFS for full 7-LED beam")
    ap.add_argument("--frame", type=int, default=4096)
    args = ap.parse_args()
    fmask, pairs, steer, azdeg = build_steering(args.frame)
    nbytes = args.frame * CH * 2
    p = subprocess.Popen(
        ["arecord", "-D", DEV, "-c", str(CH), "-r", str(RATE),
         "-f", "S16_LE", "-t", "raw", "-q"], stdout=subprocess.PIPE)
    print(f"Live DOA->LED | width=level({args.gate}..{args.loud}dBFS->1..{NMAX}) "
          f"color=pitch | Ctrl-C to stop")
    lit = False
    try:
        while True:
            raw = p.stdout.read(nbytes)
            if len(raw) < nbytes:
                break
            x = np.frombuffer(raw, dtype="<i2").reshape(-1, CH).astype(np.float64)
            lvl = 20 * np.log10(np.sqrt((x ** 2).mean()) / 32768 + 1e-12)
            if lvl < args.gate:
                if lit:
                    clear(); lit = False
                print(f"\rlevel {lvl:5.1f} dBFS | (silence)            ", end="", flush=True)
                continue
            try:
                az = azdeg[int(np.argmax(doa(x, fmask, pairs, steer)))]
                led = int(round(((CAL_S * (az - CAL_OFF)) % 360) / STEP)) % NLED
                n = count_for(lvl, args.gate, args.loud)
                f0 = estimate_f0(x)
                render(led, n, f0_to_rgb(f0))
                lit = True
                print(f"\rlevel {lvl:5.1f} dBFS | DOA {az:3d}->LED {led:2d} | "
                      f"{n} LEDs | f0 {f0 or 0:5.0f} Hz     ", end="", flush=True)
            except Exception as e:                       # one bad frame must not kill the demo
                print(f"\r[skip frame: {e}]                         ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        p.terminate()
        clear()
        print("\nstopped, LEDs off")

if __name__ == "__main__":
    main()
