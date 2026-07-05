#!/usr/bin/env bash
# One-time: build AARCH64 ASpec+AInvs heaps in a SEPARATE Isabelle home
# (never touches the baked ARM heaps). Reused by all AArch64 adjudications.
set -eu
H=/root/.isabelle/aarch64  # via ISABELLE_IDENTIFIER (dist settings CLOBBERS exported ISABELLE_HOME_USER — see B-ISOLATION.md)
ML=polyml-5.9.1_x86_64_32-linux
if [ ! -d "$H" ]; then
  mkdir -p "$H/heaps/$ML"
  [ -d /root/.isabelle/etc ] && cp -a /root/.isabelle/etc "$H/etc"
  for s in Pure HOL Word_Lib; do
    src="/root/.isabelle/heaps/$ML/$s"
    [ -e "$src" ] && cp -a "$src" "$H/heaps/$ML/$s" && echo "reused $s"
  done
fi
export ISABELLE_IDENTIFIER=aarch64
export L4V_ARCH=AARCH64
echo "=== gen Kernel_Config.thy (AARCH64) $(date) ==="
make -C /sel4-project/verification/l4v/spec/cspec/c config
echo "=== build AInvs (AARCH64) start $(date) ==="
isabelle build -v -b -d /sel4-project/verification/l4v AInvs
echo "=== DONE $(date) ==="
