#!/usr/bin/env bash
# Enable the MATRIX Creator on Debian 13 (trixie) / kernel 6.12, Raspberry Pi arm64.
# Run as root. A reboot is required at the end. Idempotent where practical.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC=/opt/matrix
WIRINGPI_URL="https://github.com/WiringPi/WiringPi/releases/download/3.16/wiringpi_3.16_arm64.deb"

log(){ echo -e "\n\033[1;36m== $* ==\033[0m"; }
[ "$(id -u)" -eq 0 ] || { echo "Please run as root."; exit 1; }

log "1/8  Enable SPI + I2C"
sed -i 's/^#dtparam=i2c_arm=on/dtparam=i2c_arm=on/; s/^#dtparam=spi=on/dtparam=spi=on/' /boot/firmware/config.txt
grep -q '^dtoverlay=matrixio' /boot/firmware/config.txt || echo 'dtoverlay=matrixio' >> /boot/firmware/config.txt
echo i2c-dev > /etc/modules-load.d/i2c-dev.conf

log "2/8  Build dependencies"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# NB: on trixie there is no raspberrypi-kernel-headers package; the headers come
# from linux-headers-rpi-v8 (check /lib/modules/$(uname -r)/build exists).
apt-get install -y -qq dkms git build-essential device-tree-compiler curl

log "3/8  Sources (kernel modules + creator-init blobs + xc3sprog .deb)"
mkdir -p "$SRC" && cd "$SRC"
[ -d matrixio-kernel-modules ] || git clone --depth 1 https://github.com/qnlbnsl/matrixio-kernel-modules.git
[ -d matrix-creator-init ]     || git clone --depth 1 https://github.com/qnlbnsl/matrix-creator-init.git
[ -d Matrix-IO ]               || git clone --depth 1 https://github.com/qnlbnsl/Matrix-IO.git

log "4/8  Apply the kernel-6.12 porting patch"
cd "$SRC/matrixio-kernel-modules"
if git apply --check "$HERE/patches/matrixio-kernel-6.12-trixie.patch" 2>/dev/null; then
  git apply "$HERE/patches/matrixio-kernel-6.12-trixie.patch"
else
  echo "  (patch already applied, or conflicts -- inspect manually)"
fi

log "5/8  Build + install kernel modules and the device-tree overlay"
cd "$SRC/matrixio-kernel-modules/src"
make
make -C "/lib/modules/$(uname -r)/build" M="$(pwd)" modules_install
depmod -a
cp matrixio.dtbo /boot/firmware/overlays/

log "6/8  xc3sprog + JTAG libraries"
cd /tmp
apt-get download libftdi1 libusb-0.1-4         # libftdi1 here is the OLD 0.x (provides libftdi.so.1)
dpkg -i libftdi1_*.deb libusb-0.1-4_*.deb || true
curl -fsSL -o wiringpi.deb "$WIRINGPI_URL"      # wiringPi was removed from Debian; community .deb
dpkg -i wiringpi.deb || true
dpkg -i --force-depends "$SRC"/Matrix-IO/build/xc3sprog/arm64/matrixio-xc3sprog_*_arm64.deb

log "7/8  FPGA programming service (re-flashes volatile SRAM at every boot)"
mkdir -p /usr/local/lib/matrixio
# The bitstream is a MATRIX Labs binary; we use the copy from the cloned repo
# rather than redistributing it here.
cp "$SRC/matrix-creator-init/blob/system_creator.bit" /usr/local/lib/matrixio/
cp "$HERE/files/matrixio-fpga-program.sh" /usr/local/sbin/
chmod +x /usr/local/sbin/matrixio-fpga-program.sh
cp "$HERE/files/matrixio-fpga.service" /etc/systemd/system/
systemctl daemon-reload && systemctl enable matrixio-fpga.service

log "8/8  Make secondary modules load at boot"
# core/mic/codec/playback load via the overlay; the rest via this file.
cp "$HERE/files/matrixio-modules-load.conf" /etc/modules-load.d/matrixio.conf

log "DONE -- reboot, then verify:"
echo "  systemctl is-active matrixio-fpga.service"
echo "  arecord -l | grep MATRIXIO"
echo "  arecord -D plughw:CARD=MATRIXIOSOUND -c 8 -r 16000 -f S16_LE -d 3 /tmp/t.wav"
