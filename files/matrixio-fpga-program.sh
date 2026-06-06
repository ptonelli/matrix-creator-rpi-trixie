#!/bin/bash
# Program the MATRIX Creator FPGA bitstream into its (volatile) SRAM.
#
# The Creator's Spartan-6 FPGA does NOT load its gateware from on-board flash:
# the bitstream lives in SRAM and is lost on every power cycle. We push it back
# over bit-banged JTAG (xc3sprog, "matrix_creator" cable) at each boot.
#
# Without this, every FPGA register reads 0x0000, the mic IRQ never fires, and
# audio capture fails with "Input/output error".
set -e

BIT=/usr/local/lib/matrixio/system_creator.bit

for i in 1 2 3 4 5; do
  # "-p 1" targets JTAG chain position 1 (the XC6SLX FPGA; position 0 is the SAM3 MCU).
  if xc3sprog -c matrix_creator -p 1 "$BIT" 2>&1 | grep -q "DNA is"; then
    echo "MATRIX Creator FPGA programmed (attempt $i)"
    exit 0
  fi
  sleep 0.5
done

echo "FAILED to program MATRIX Creator FPGA" >&2
exit 1
