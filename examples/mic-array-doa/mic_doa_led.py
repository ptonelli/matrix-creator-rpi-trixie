#!/usr/bin/env python3
"""Real-time DOA -> Everloop on the MATRIX Creator.

  - Direction: SRP-PHAT on the 8-mic array -> LED pointing at the source
    (mapped via the measured calibration DOA = CAL_S*ring + CAL_OFF).
  - Beam width: number of lit LEDs grows 1..7 with sound level.
  - Color: fundamental frequency (HPS pitch) -> hue, low=red ... high=violet.
  - Brightness: per-LED TEMPORAL envelope (replaces the old spatial gaussian).
    A lit LED fades IN gently (attack) and keeps strengthening the longer the
    source lasts; once the source moves away or goes quiet the LED fades OUT
    smoothly (release). Moving sources leave a soft comet trail.

Two threads decouple the (coarse) audio rate from the (smooth) LED rate:
  - audio worker (~4 Hz at frame=4096): arecord -> SRP-PHAT/f0 -> updates the
    shared target (which LEDs should be lit, and their color).
  - render loop (~fps Hz): advances each LED's brightness toward its target with
    exponential attack/release and writes the Everloop.

Run ON banshee (needs numpy):
    python3 /root/mic_doa_led.py [--gate -50] [--loud -12] [--frame 4096]
                                 [--attack 3.5] [--release 1.5] [--fps 50]
Ctrl-C / systemd stop -> LEDs off.
"""
import subprocess, argparse, math, colorsys, threading, time, signal
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
BRIGHT = 60                      # peak brightness 0..255
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
    return colorsys.hsv_to_rgb(t * 0.7, 1.0, 1.0)   # red -> violet

def count_for(lvl, gate, loud):
    t = (lvl - gate) / max(1e-6, (loud - gate))
    t = min(1.0, max(0.0, t))
    return int(round(1 + (NMAX - 1) * t))


class Target:
    """What the audio worker wants lit; read by the render loop."""
    def __init__(self):
        self.lock = threading.Lock()
        self.leds = frozenset()      # LED indices currently in the beam
        self.rgb  = (1.0, 1.0, 1.0)  # target color for those LEDs


def audio_worker(args, target, stop):
    fmask, pairs, steer, azdeg = build_steering(args.frame)
    nbytes = args.frame * CH * 2
    p = subprocess.Popen(
        ["arecord", "-D", DEV, "-c", str(CH), "-r", str(RATE),
         "-f", "S16_LE", "-t", "raw", "-q"], stdout=subprocess.PIPE)
    try:
        while not stop.is_set():
            raw = p.stdout.read(nbytes)
            if len(raw) < nbytes:
                break
            x = np.frombuffer(raw, dtype="<i2").reshape(-1, CH).astype(np.float64)
            lvl = 20 * np.log10(np.sqrt((x ** 2).mean()) / 32768 + 1e-12)
            if lvl < args.gate:
                with target.lock:
                    target.leds = frozenset()    # -> everything releases / fades out
                continue
            try:
                az  = azdeg[int(np.argmax(doa(x, fmask, pairs, steer)))]
                led = int(round(((CAL_S * (az - CAL_OFF)) % 360) / STEP)) % NLED
                n   = count_for(lvl, args.gate, args.loud)
                offs = [k - n // 2 for k in range(n)]
                leds = frozenset((led + o) % NLED for o in offs)
                rgb  = f0_to_rgb(estimate_f0(x))
                with target.lock:
                    target.leds = leds
                    target.rgb  = rgb
            except Exception:                    # one bad frame must not kill it
                pass
    finally:
        p.terminate()
        stop.set()


def write_everloop(buf):
    with open(EVER, "wb") as f:
        f.write(buf)


def render_loop(args, target, stop):
    b   = np.zeros(NLED)             # current brightness 0..1 per LED
    col = np.zeros((NLED, 3))        # current color (rgb 0..1) per LED
    period = 1.0 / args.fps
    last = time.monotonic()
    try:
        while not stop.is_set():
            now = time.monotonic()
            dt  = now - last
            last = now
            with target.lock:
                leds = target.leds
                rgb  = np.asarray(target.rgb)
            # exponential approach coefficients for this dt
            a_att = 1.0 - math.exp(-dt / max(1e-3, args.attack))
            a_rel = 1.0 - math.exp(-dt / max(1e-3, args.release))
            tgt = np.zeros(NLED)
            idx = list(leds)
            if idx:
                tgt[idx] = 1.0
            rising = tgt > b
            b[rising]  += (tgt[rising]  - b[rising])  * a_att   # fade in
            b[~rising] += (tgt[~rising] - b[~rising]) * a_rel   # fade out
            if idx:                                             # lit LEDs pull color
                col[idx] += (rgb - col[idx]) * a_att            # toward the new hue
            # compose RGBW buffer
            px = np.clip(col * b[:, None] * BRIGHT, 0, 255).astype(np.uint8)
            out = np.zeros((NLED, 4), dtype=np.uint8)
            out[:, :3] = px
            write_everloop(out.tobytes())
            slp = period - (time.monotonic() - now)
            if slp > 0:
                time.sleep(slp)
    finally:
        write_everloop(bytes(NLED * 4))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", type=float, default=-50.0, help="dBFS gate (below = silence)")
    ap.add_argument("--loud", type=float, default=-12.0, help="dBFS for full 7-LED beam")
    ap.add_argument("--frame", type=int, default=4096)
    ap.add_argument("--attack", type=float, default=3.5, help="fade-in time constant (s)")
    ap.add_argument("--release", type=float, default=1.5, help="fade-out time constant (s)")
    ap.add_argument("--fps", type=float, default=50.0, help="LED refresh rate (Hz)")
    args = ap.parse_args()

    target = Target()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    aw = threading.Thread(target=audio_worker, args=(args, target, stop), daemon=True)
    rl = threading.Thread(target=render_loop,  args=(args, target, stop), daemon=True)
    aw.start(); rl.start()
    print(f"Live DOA->LED | width=level({args.gate}..{args.loud}dBFS) color=pitch | "
          f"attack={args.attack}s release={args.release}s @ {args.fps:.0f}fps | "
          f"Ctrl-C to stop", flush=True)
    try:
        while not stop.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        stop.set()
    aw.join(timeout=2); rl.join(timeout=2)
    try:
        write_everloop(bytes(NLED * 4))          # belt-and-braces: LEDs off
    except Exception:
        pass
    print("stopped, LEDs off")


if __name__ == "__main__":
    main()
