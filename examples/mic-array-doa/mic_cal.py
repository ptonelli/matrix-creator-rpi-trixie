#!/usr/bin/env python3
"""MATRIX Creator mic-array DOA calibration capture tool.

Run this ON banshee in an interactive SSH session:
    python3 /root/mic_cal.py            # default: every 3rd LED (12 positions)
    python3 /root/mic_cal.py step=2     # every 2nd LED (18 positions)
    python3 /root/mic_cal.py 0,9,17,26  # only these LED indices

For each position it lights ONE LED on the ring; you place the phone (playing
music) right next to that lit LED, then press ENTER to record. Captures are
8-channel WAVs saved in /root/caldata/ with a manifest. Pull them afterwards
for DOA analysis. No numpy needed here (recording is done via arecord).
"""
import os, sys, subprocess, json

DEV       = "plughw:CARD=MATRIXIOSOUND"
EVERLOOP  = "/dev/matrixio_everloop"
NLED      = 35
DUR       = 5                      # seconds per capture
OUTDIR    = "/root/caldata"
COLOR     = (0, 14, 0, 0)         # R,G,B,W  -> dim green

def parse_leds(argv):
    if len(argv) > 1:
        a = argv[1]
        if a.startswith("step="):
            return list(range(0, NLED, int(a.split("=")[1])))
        return [int(x) for x in a.split(",")]
    return list(range(0, NLED, 3))  # ~30.9 deg apart, 12 points

def set_led(idx):
    d = bytearray(NLED * 4)
    if idx is not None:
        r, g, b, w = COLOR
        d[idx*4:idx*4+4] = bytes((r, g, b, w))
    with open(EVERLOOP, "wb") as f:
        f.write(bytes(d))

def record(path, dur):
    subprocess.run(
        ["arecord", "-D", DEV, "-c", "8", "-r", "16000", "-f", "S16_LE",
         "-d", str(dur), path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def save_manifest(manifest):
    with open(os.path.join(OUTDIR, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

def main():
    leds = parse_leds(sys.argv)
    os.makedirs(OUTDIR, exist_ok=True)
    manifest = []
    mpath = os.path.join(OUTDIR, "manifest.json")
    if os.path.exists(mpath):
        try:
            manifest = json.load(open(mpath))   # append to a previous run
            print(f"(resuming: {len(manifest)} existing captures kept)")
        except Exception:
            manifest = []
    print(f"=== Mic-array DOA calibration : {len(leds)} positions x {DUR}s ===")
    print("Tip: keep the music broadband (normal song), volume moderate, ~30-50 cm")
    print("from the lit LED, roughly in the board's plane.\n")
    i = 0
    while i < len(leds):
        led = leds[i]
        ang = led * 360.0 / NLED
        set_led(led)
        c = input(f"[{i+1}/{len(leds)}] LED {led} lit (ring {ang:.0f} deg). "
                  f"Place music there, ENTER=record  s=skip  q=quit > ").strip().lower()
        if c == "q":
            break
        if c == "s":
            i += 1
            continue
        path = os.path.join(OUTDIR, f"cal_led{led:02d}.wav")
        print(f"    recording {DUR}s ... (music playing at the LED)")
        try:
            record(path, DUR)
        except Exception as e:
            print("    !! arecord failed:", e, "-> retry this position")
            continue
        sz = os.path.getsize(path)
        again = input(f"    saved {os.path.basename(path)} ({sz} B). "
                      f"ENTER=keep&next  r=redo  q=quit > ").strip().lower()
        if again == "r":
            continue            # same i, re-record
        if again == "q":
            break
        manifest = [m for m in manifest if m["led"] != led]
        manifest.append({"led": led, "ring_deg": round(ang, 1),
                         "file": os.path.basename(path)})
        save_manifest(manifest)
        i += 1
    set_led(None)               # all LEDs off
    print(f"\nDone: {len(manifest)} captures in {OUTDIR} (+ manifest.json).")
    print("Now tell Claude: captures are ready to pull.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        set_led(None)
        print("\ninterrupted -> LEDs off")
