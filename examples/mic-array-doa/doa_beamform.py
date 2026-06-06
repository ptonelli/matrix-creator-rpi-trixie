#!/usr/bin/env python3
"""SRP-PHAT direction-of-arrival + delay-and-sum beamforming for the MATRIX Creator
8-mic array. Operates on a captured 8-channel WAV. No deps beyond numpy.

Usage:
  python3 doa_beamform.py capture.wav [--band 300 3500] [--beam out_mono.wav]
"""
import sys, wave, argparse
import numpy as np

C = 343.0  # speed of sound m/s

# MATRIX Creator mic positions (meters), ALSA channel order 0..7 == HAL M1..M8
MICS_MM = np.array([
    [ 20.0908795, -48.5036755],  # 0 M1
    [-20.0908795, -48.5036755],  # 1 M2
    [-48.5036755, -20.0908795],  # 2 M3
    [-48.5036755,  20.0908795],  # 3 M4
    [-20.0908795,  48.5036755],  # 4 M5
    [ 20.0908795,  48.5036755],  # 5 M6
    [ 48.5036755,  20.0908795],  # 6 M7
    [ 48.5036755, -20.0908795],  # 7 M8
])
MICS = MICS_MM / 1000.0

def read_wav(path):
    w = wave.open(path, 'rb')
    ch, sw, fr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
    x = np.frombuffer(w.readframes(n), dtype='<i2').reshape(-1, ch).astype(np.float64)
    w.close()
    return x, fr

def srp_phat(x, fr, band=(300, 3500), naz=360):
    """Steered-response-power with PHAT weighting over azimuth grid. Returns
    (best_az_deg, power_curve[naz])."""
    nfft = 1
    while nfft < x.shape[0]:
        nfft <<= 1
    X = np.fft.rfft(x, n=nfft, axis=0)                     # (F, M)
    freqs = np.fft.rfftfreq(nfft, 1.0/fr)                  # (F,)
    fmask = (freqs >= band[0]) & (freqs <= band[1])
    X = X[fmask]; freqs = freqs[fmask]
    Xn = X / (np.abs(X) + 1e-12)                           # PHAT: whiten magnitude
    M = x.shape[1]
    pairs = [(i, j) for i in range(M) for j in range(i+1, M)]
    az = np.deg2rad(np.arange(naz))
    # plane-wave unit vectors for each azimuth (source FROM direction az, 2D horizontal)
    u = np.stack([np.cos(az), np.sin(az)], axis=1)         # (naz, 2)
    power = np.zeros(naz)
    w = 2j * np.pi * freqs                                 # (F,)
    for (i, j) in pairs:
        d = MICS[i] - MICS[j]                              # (2,)
        tau = (u @ d) / C                                  # (naz,) expected TDOA per azimuth
        Gij = Xn[:, i] * np.conj(Xn[:, j])                # (F,) cross-spectrum (PHAT)
        # steered correlation = sum_f Gij * exp(+j2pi f tau)
        steer = np.exp(np.outer(tau, w))                  # (naz, F)
        power += np.real(steer @ Gij)
    best = int(np.argmax(power))
    return best, power

def beamform_delaysum(x, fr, az_deg):
    """Delay-and-sum toward az_deg, integer-sample (frac via FFT phase shift)."""
    az = np.deg2rad(az_deg)
    u = np.array([np.cos(az), np.sin(az)])
    tau = (MICS @ u) / C                                  # (M,) per-mic delay
    nfft = x.shape[0]
    X = np.fft.rfft(x, axis=0)
    freqs = np.fft.rfftfreq(nfft, 1.0/fr)
    # align: advance each channel by its tau so wavefronts add coherently
    shift = np.exp(2j*np.pi*np.outer(freqs, tau))         # (F, M)
    Y = (X * shift).sum(axis=1) / x.shape[1]
    y = np.fft.irfft(Y, n=nfft)
    return y

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('wav')
    ap.add_argument('--band', type=float, nargs=2, default=[300, 3500])
    ap.add_argument('--beam', help='write delay-and-sum mono WAV toward DOA')
    ap.add_argument('--frames', type=int, default=0, help='analyze only first N frames')
    args = ap.parse_args()
    x, fr = read_wav(args.wav)
    if args.frames:
        x = x[:args.frames]
    best, power = srp_phat(x, fr, band=tuple(args.band))
    p = power - power.min(); p /= (p.max() + 1e-12)
    print(f"{args.wav}: SRP-PHAT DOA = {best} deg (band {args.band[0]:.0f}-{args.band[1]:.0f} Hz)")
    # second peak / sharpness
    order = np.argsort(power)[::-1]
    print(f"  top-3 azimuths: {[int(order[k]) for k in range(3)]} deg")
    # ascii polar-ish bar every 15 deg
    print("  power by azimuth (every 15deg):")
    for a in range(0, 360, 15):
        bar = '#' * int(p[a]*40)
        mark = ' <= peak' if a == (best//15)*15 else ''
        print(f"   {a:3d}deg |{bar}")
    if args.beam:
        y = beamform_delaysum(x, fr, best)
        y16 = np.clip(y, -32768, 32767).astype('<i2')
        w = wave.open(args.beam, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(fr)
        w.writeframes(y16.tobytes()); w.close()
        # crude gain check vs single mic
        g = 20*np.log10((np.sqrt((y**2).mean())+1e-9)/(np.sqrt((x[:,0]**2).mean())+1e-9))
        print(f"  wrote {args.beam}; beam RMS vs mic0: {g:+.1f} dB")

if __name__ == '__main__':
    main()
