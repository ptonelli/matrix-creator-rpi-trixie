#!/usr/bin/env python3
import sys, wave, numpy as np

path = sys.argv[1]
w = wave.open(path, 'rb')
ch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
raw = w.readframes(n)
w.close()
assert sw == 2, "expected S16"
x = np.frombuffer(raw, dtype='<i2').reshape(-1, ch).astype(np.float64)
print(f"file={path}  channels={ch}  rate={fr}  frames={n}  dur={n/fr:.2f}s")
FS = 32768.0

def dbfs(v):
    return -np.inf if v <= 0 else 20*np.log10(v/FS)

print("\nch |   RMS dBFS |  peak dBFS | DC offset |  peak raw | note")
print("---+-----------+-----------+-----------+-----------+------")
rms = np.sqrt((x**2).mean(axis=0))
peak = np.abs(x).max(axis=0)
dc = x.mean(axis=0)
for c in range(ch):
    note = ""
    if peak[c] == 0: note = "DEAD (all zero)"
    elif peak[c] >= FS-1: note = "CLIPPING"
    print(f"{c:2d} | {dbfs(rms[c]):9.2f} | {dbfs(peak[c]):9.2f} | {dc[c]:9.1f} | {int(peak[c]):9d} | {note}")

# inter-channel correlation (are the 8 mics distinct, or duplicated/bridged?)
print("\nInter-channel correlation matrix (Pearson, on AC-coupled signal):")
xc = x - x.mean(axis=0)
std = xc.std(axis=0)
std[std == 0] = 1
corr = (xc.T @ xc) / (len(xc) * np.outer(std, std))
hdr = "    " + " ".join(f"c{c}" for c in range(ch))
print(hdr)
for i in range(ch):
    row = " ".join(f"{corr[i,j]:+.2f}".replace("+1.00"," 1.0").rjust(5) for j in range(ch))
    print(f"c{i}  {row}")

# flag suspiciously identical channel pairs
print("\nHighly correlated pairs (|r|>0.95, would suggest bridged/duplicated mics):")
found = False
for i in range(ch):
    for j in range(i+1, ch):
        if abs(corr[i,j]) > 0.95:
            print(f"  c{i}~c{j}: r={corr[i,j]:+.3f}"); found = True
if not found:
    print("  none -> all 8 channels are distinct signals (good)")

# overall noise floor spread
print(f"\nNoise floor spread: RMS min {dbfs(rms.min()):.1f} dBFS (c{rms.argmin()}), "
      f"max {dbfs(rms.max()):.1f} dBFS (c{rms.argmax()}), "
      f"spread {dbfs(rms.max())-dbfs(rms.min()):.1f} dB")
