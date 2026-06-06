# Mic-array DOA + beamforming example

A worked, validated example of using the MATRIX Creator's **8-mic array** for
**direction-of-arrival (DOA)** estimation and beamforming — entirely in software
on the host (the FPGA only hands us 8 clean PCM channels; the spatial layer is
ours). It ends with a live demo that lights the Everloop LED pointing at whoever
is talking, with the beam width scaled by loudness and the color set by pitch.

> Validated 2026-06-06 on a Raspberry Pi 3 B+ (Debian 13 trixie, kernel 6.12)
> with the array working as described in the top-level README.

## What's here

| Script | Where it runs | What it does |
|---|---|---|
| `analyze_mic.py` | any host w/ numpy | Per-channel quality: RMS/peak/DC, noise floor, inter-channel correlation, dead/clipping/bridged-channel detection |
| `doa_beamform.py` | any host w/ numpy | Offline SRP-PHAT DOA + delay-and-sum beamforming on a captured 8-ch WAV (importable library too) |
| `mic_cal.py` | **on the Pi** | Interactive calibration capture: lights one Everloop LED at a time, you put a broadband sound there, it records 8-ch WAVs + a manifest |
| `mic_doa_led.py` | **on the Pi** (needs numpy) | Real-time DOA → Everloop: lights the LED pointing at the source, beam width ∝ level, color ∝ pitch |

`arecord` (alsa-utils) is enough to *capture*; the numpy-based analysis can run
either on the Pi (`apt install python3-numpy`) or on another machine after
copying the WAVs over.

## The array

8 MEMS mics in a regular octagon, radius ≈ 52.5 mm (from `matrix-creator-hal`).
ALSA channel `i` (0..7) maps to HAL mic `M(i+1)` in ring order — confirmed
empirically: each channel correlates most with its two ring neighbours. Max mic
spacing 105 mm → spatial aliasing above ~1.6 kHz, irrelevant for voice DOA
(300–3500 Hz). Coordinates are baked into `doa_beamform.py` / `mic_doa_led.py`.

## Calibration (DOA ↔ Everloop ring)

The DOA angle is in the array's math frame, which is rotated/mirrored relative to
the physical board. To map a measured angle to a *physical LED*, calibrate:

```bash
# on the Pi, in an interactive SSH session:
python3 mic_cal.py 0,9,17,26      # then add diagonals: python3 mic_cal.py 4,13,22,31
```
For each lit LED, hold a **broadband** source there (a song works; avoid a pure
tone — a single frequency wraps in phase and breaks GCC-PHAT). Then fit, off the
Pi, from the captured `caldata/`:

The 9-point fit obtained here:

```
DOA = -1 × (LED ring angle) + 350.9°     RMS 5.3°  (< one LED spacing of 10.3°)
```

So handedness is **mirrored** (`s = -1`) with a **-9°** offset. The inverse used
by the live demo:

```
ring_angle = (350.9 - DOA) mod 360
LED        = round(ring_angle / 10.286) mod 35
```

Re-run the calibration in your room and update `CAL_S` / `CAL_OFF` at the top of
`mic_doa_led.py` if your board orientation differs.

## Run the live demo

```bash
python3 mic_doa_led.py                 # defaults
python3 mic_doa_led.py --gate -45      # less sensitive
python3 mic_doa_led.py --loud -6 --frame 2048
```

Output per frame:

```
level -33.9 dBFS | DOA 138->LED 24 | 4 LEDs | f0 117 Hz
```

- **Direction**: the lit LED points at the dominant source.
- **Width**: 1 LED when faint → up to 7 when loud (`--gate`/`--loud` set the range).
- **Brightness**: gaussian beam, bright centre fading to the edges (`BRIGHT`/`FALLOFF`).
- **Color**: fundamental frequency (HPS pitch), red (~80 Hz) → green (~300 Hz) →
  violet (~800 Hz+), log-scaled.

Ctrl-C stops and clears the ring. The demo controls the Everloop directly; if you
run a breathing/idle effect as a service, stop it first (e.g.
`systemctl stop everloop-pulse.service`) and restart it after.

## Measured characteristics (this board, this room)

- All 8 channels live, distinct, well matched (~3.6 dB RMS spread under signal).
- Silence noise floor ≈ −50 dBFS (an *upper bound* — mostly room tone; true
  electronic self-noise is below, since the residual is partly coherent).
- SNR on a directed source ≈ +22 dB.
- Delay-and-sum array gain ≈ +2 dB here (limited by *coherent* room noise; against
  uncorrelated noise it approaches 10·log₁₀(8) ≈ 9 dB). An MVDR/superdirective
  beamformer would do better — a natural next step.

## Possible next steps

- Temporal smoothing of the DOA (less LED jitter).
- MVDR / superdirective beamformer instead of delay-and-sum.
- Feed the 8 channels (or the beamformed mono) into wyoming-satellite for HA Assist.
