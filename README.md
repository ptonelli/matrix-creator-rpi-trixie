# MATRIX Creator on Raspberry Pi — Debian 13 (trixie) / kernel 6.12

Get the **[MATRIX Creator](https://www.matrix.one/products/creator)** HAT (8-mic array,
Spartan‑6 FPGA, 35‑LED RGBW ring, environmental + IMU sensors, ZigBee EM358 radio)
working on a **modern** Raspberry Pi OS — Debian 13 *trixie*, kernel **6.12** — even though
the official MATRIX software is abandoned and the community fork only builds on Bookworm/6.1.

> **Validated 2026-06-06** on a **Raspberry Pi 3 Model B+**, Debian 13 (trixie),
> kernel `6.12.75+rpt-rpi-v8` (arm64). The porting patch applies cleanly to a fresh
> upstream checkout and builds all 10 modules.

This repo contains a **kernel‑6.12 porting patch**, an **install script**, the **systemd
unit** that keeps the FPGA alive, and a full write‑up of how the board actually works.

---

## Why this is not trivial — the two big gotchas

1. **The module code is from 2019–2021.** Five kernel‑API breakages between ~5.4 and 6.12
   stop it from compiling. That is what the patch fixes.
2. **The FPGA does NOT load itself.** The Creator's Spartan‑6 bitstream is **not** in flash —
   it lives in volatile SRAM and must be pushed back **on every boot** over bit‑banged JTAG.
   Without it, every FPGA register reads `0x0000`, the mic IRQ never fires, and capture fails
   with `Input/output error`. A systemd service handles this.

---

## Status — what works (9/9 module devices bind)

| Feature | State | How to access |
|---|---|---|
| 🎤 Microphones (8‑MEMS array) | ✅ Works | `arecord -D plughw:CARD=MATRIXIOSOUND -c 8 -r 16000 -f S16_LE` |
| 💡 LED ring (35× RGBW "Everloop") | ✅ Works | write 35×4 RGBW bytes to `/dev/matrixio_everloop` |
| 🔌 GPIO (16 FPGA lines) | ✅ Works | `gpiochip` (parent `spi0.0`) via libgpiod |
| 🌡️ Env sensors (temp/hum/pressure/UV) | ✅ Live data | `/sys/bus/iio/devices/iio:device0` |
| 📐 IMU (accel/gyro/magnetometer) | ✅ Live data | `/sys/bus/iio/devices/iio:device1` |
| 🔊 Audio output (playback) | ✅ Device present | card `MATRIXIOSOUND` device 1 (test = plug a speaker in) |
| 📡 UART (radio serial link) | ✅ Works | `/dev/ttyMATRIX0` |
| 📶 ZigBee EM358 | ⚠️ Link OK, radio needs flashing | NCP firmware not loaded (see Roadmap) |

**Honest caveats:**
- **Sensors:** the pipeline is alive (values fluctuate = real sensors read by the on‑board
  SAM3 MCU), but the old driver's **unit decoding is not calibrated** — the MCU stores IEEE‑754
  floats and the driver reads them as fixed‑point. Real, changing data; physical units still TODO.
- **ZigBee:** this hardware has a **ZigBee/Thread (EM358)** radio, *not* Z‑Wave. The kernel UART
  link works, but the radio has no NCP firmware (flashing it via OpenOCD is a separate project).

---

## Quick start

```bash
git clone https://github.com/<you>/matrix-creator-rpi-trixie.git
cd matrix-creator-rpi-trixie
sudo ./install.sh
sudo reboot
```

After reboot:

```bash
systemctl is-active matrixio-fpga.service                 # -> active
arecord -l | grep MATRIXIO                                # card present
arecord -D plughw:CARD=MATRIXIOSOUND -c 8 -r 16000 -f S16_LE -d 3 /tmp/t.wav
```

---

## Manual, step by step

> All commands as root. Paths are for trixie (`/boot/firmware/...`).

### 1. Enable SPI + I2C
```bash
sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/; s/^#dtparam=spi=on/dtparam=spi=on/' /boot/firmware/config.txt
echo i2c-dev > /etc/modules-load.d/i2c-dev.conf
```

### 2. Build dependencies
```bash
apt-get update
apt-get install -y dkms git build-essential device-tree-compiler curl
# NB: trixie has NO raspberrypi-kernel-headers package; headers come from
# linux-headers-rpi-v8 (check /lib/modules/$(uname -r)/build exists).
```

### 3. Sources
```bash
mkdir -p /opt/matrix && cd /opt/matrix
git clone --depth 1 https://github.com/qnlbnsl/matrixio-kernel-modules.git
git clone --depth 1 https://github.com/qnlbnsl/matrix-creator-init.git   # FPGA bitstream + scripts
git clone --depth 1 https://github.com/qnlbnsl/Matrix-IO.git             # has the arm64 xc3sprog .deb
```

### 4. Apply the kernel‑6.12 patch
```bash
cd /opt/matrix/matrixio-kernel-modules
git apply /path/to/matrix-creator-rpi-trixie/patches/matrixio-kernel-6.12-trixie.patch
```

### 5. Build + install
```bash
cd /opt/matrix/matrixio-kernel-modules/src
make                                          # builds the .ko files + matrixio.dtbo
make -C /lib/modules/$(uname -r)/build M=$(pwd) modules_install
depmod -a
cp matrixio.dtbo /boot/firmware/overlays/     # trixie path
grep -q '^dtoverlay=matrixio' /boot/firmware/config.txt || echo 'dtoverlay=matrixio' >> /boot/firmware/config.txt
```

### 6. xc3sprog (JTAG programmer) + libraries
```bash
# xc3sprog needs libftdi.so.1 (the OLD libftdi 0.x), libusb-0.1-4 and wiringPi.
apt-get download libftdi1 libusb-0.1-4 && dpkg -i libftdi1_*.deb libusb-0.1-4_*.deb
curl -fsSL -o /tmp/wiringpi.deb https://github.com/WiringPi/WiringPi/releases/download/3.16/wiringpi_3.16_arm64.deb
dpkg -i /tmp/wiringpi.deb
dpkg -i --force-depends /opt/matrix/Matrix-IO/build/xc3sprog/arm64/matrixio-xc3sprog_*_arm64.deb

# Sanity check: this must list the JTAG chain (SAM3 + XC6SLX + EM358):
xc3sprog -c matrix_creator
```

### 7. FPGA programming service (the crucial step ⚠️)
```bash
mkdir -p /usr/local/lib/matrixio
cp /opt/matrix/matrix-creator-init/blob/system_creator.bit /usr/local/lib/matrixio/
cp files/matrixio-fpga-program.sh /usr/local/sbin/ && chmod +x /usr/local/sbin/matrixio-fpga-program.sh
cp files/matrixio-fpga.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable matrixio-fpga.service
```

### 8. Make the secondary modules load at boot
```bash
cp files/matrixio-modules-load.conf /etc/modules-load.d/matrixio.conf
# (core/mic/codec/playback load via the overlay; regmap/everloop/gpio/env/imu/uart via this file.)
```

### 9. Reboot and validate (see *Quick start* above).

---

## The kernel‑6.12 patch, explained

[`patches/matrixio-kernel-6.12-trixie.patch`](patches/matrixio-kernel-6.12-trixie.patch)
fixes five families of API breakage (plus one IIO tweak):

1. **`platform_driver.remove` returns `void`** (kernel 6.11; was `int`).
   → codec, env, everloop, gpio, imu, regmap, uart.
2. **`class_create()` dropped the `THIS_MODULE` argument** (6.4); and the **`.dev_uevent`**
   callback now takes `const struct device *`. → everloop, regmap.
3. **`struct gpio_chip` is now incomplete** without `#include <linux/gpio/driver.h>`. → gpio.
4. **ALSA SoC `.copy_user` → `.copy`** (6.5): the buffer argument changed from
   `void __user *` to `struct iov_iter *` (copy with `copy_from_iter` + `kfifo_in`). → playback.
5. **tty/uart `port->state->xmit` removed** (6.10): the circular buffer became a kfifo;
   `start_tx` is rewritten with the `uart_port_tx()` helper. → uart.
6. **IIO `indio_dev->mlock` is now private**: replaced by the driver's own `data->lock`
   (already declared and `mutex_init`'d upstream). → env, imu.

---

## Usage

**Microphones (8 channels)** — always address the card by **name** (the numeric index
changes with boot order):
```bash
arecord -D plughw:CARD=MATRIXIOSOUND -c 8 -r 16000 -f S16_LE -d 5 out.wav
```

**LED ring (35 RGBW)** — 4 bytes per LED (R,G,B,W), brightness 0–255 (keep it low, it's bright):
```bash
# all LEDs dim green:
python3 -c 'open("/dev/matrixio_everloop","wb").write(bytes([0,12,0,0]*35))'
# turn the ring off:
python3 -c 'open("/dev/matrixio_everloop","wb").write(bytes(35*4))'
# rainbow snake demo:
python3 files/everloop-snake.py
```

**Sensors (IIO)**:
```bash
cat /sys/bus/iio/devices/iio:device0/name            # matrixio_env
cat /sys/bus/iio/devices/iio:device0/in_*_raw
cat /sys/bus/iio/devices/iio:device1/in_accel_*_raw  # IMU: move the board, values change
```

**Raw FPGA registers** (debugging) via `/dev/matrixio_regmap`
(ioctl `RD=1201` / `WR=1200`, int32 buffer `[addr, len_bytes, data...]`).

---

## Gotchas

- **ALSA card by name**, never by index: `plughw:CARD=MATRIXIOSOUND`.
- The **FPGA is volatile**: without the systemd service, capture breaks after a power cycle
  (EIO) and registers read `0`. Tell-tale sign: no `matrixio-mic` line in `/proc/interrupts`.
- `raspberrypi-kernel-headers` does **not** exist on trixie — use `linux-headers-rpi-v8`.
- `xc3sprog` needs `libftdi.so.1` (old `libftdi1` 0.x, **not** `libftdi1-2`), `libusb-0.1-4`,
  and `wiringPi`.
- On kernel 6.12 **sysfs GPIO** is offset (base 512) — prefer libgpiod. The GPIO18 reset in the
  old scripts is **not** needed to program: xc3sprog reconfigures over JTAG anyway.
- **env/imu** report real, changing data but **uncalibrated units** (MCU floats read as fixed‑point).

---

## How the mic array really works (FPGA vs software)

A common misconception is that the FPGA does the beamforming. It does **not**:

- The **FPGA** does the per‑mic front‑end DSP — PDM capture, **CIC filter + decimation + FIR** —
  in parallel for all 8 mics, and hands the host **8 clean PCM channels** over SPI.
- **Beamforming and direction‑of‑arrival** (combining/steering the 8 mics) were always done
  **in software on the Pi** (the old `matrix-creator-hal`).

Good news for new projects: you get 8 clean PCM channels for free, and you're free to pick your
own spatial layer in software (e.g. [ODAS](https://github.com/introlab/odas)).

---

## Roadmap

- **[wyoming-satellite](https://github.com/rhasspy/wyoming-satellite) → Home Assistant Assist** —
  the 8 channels are standard ALSA inputs; start mono (MVP), add beamforming later.
- **Beamforming/DOA** in software (ODAS) over the 8 channels; drive the LED ring from the DOA.
- **ZigBee EM358**: build `matrix-creator-openocd`, flash the NCP firmware
  (`blob/ncp_xon_xoff.hex` via `em358-program.bash`), then run zigbee2mqtt.
- **Calibrate the sensor decoding** (interpret as float) for proper physical units.
- **Package the patch as DKMS** so it survives kernel upgrades.

---

## Credits

This project stands on the shoulders of:

- [MATRIX Labs](https://github.com/matrix-io) — original hardware, FPGA gateware and drivers.
- [@qnlbnsl](https://github.com/qnlbnsl) — the maintained forks this builds on
  (`matrixio-kernel-modules`, `matrix-creator-init`, `Matrix-IO`).
- [WiringPi](https://github.com/WiringPi/WiringPi) — community‑maintained GPIO library.

Only the *original* work here (the porting patch, scripts, systemd unit and docs) is under the
MIT [LICENSE](LICENSE). Third‑party sources and binaries (kernel modules, FPGA bitstream,
xc3sprog, wiringPi) keep their own licenses and are fetched from upstream at install time —
they are **not** redistributed in this repo.
