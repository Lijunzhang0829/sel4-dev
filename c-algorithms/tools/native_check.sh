#!/usr/bin/env bash
set -euo pipefail
# Run inside the pinned compiler container with /project read-only.
cd /project
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
flags=(-std=c99 -g -O1 -Wall -Wextra -Werror -fsanitize=address,undefined -fno-omit-frame-pointer -no-pie -Ivendor/src)
gcc "${flags[@]}" -Dmalloc=audit_malloc -Drealloc=audit_realloc -Dfree=audit_free \
  -c vendor/src/binary-heap.c -o "$work/heap.o"
gcc "${flags[@]}" -c vendor/src/compare-int.c -o "$work/compare.o"
gcc "${flags[@]}" tools/native_check.c "$work/heap.o" "$work/compare.o" -o "$work/check"
# Non-PIE reduces a suspected old-ASan/host address-layout interaction.
# A real segfault must fail once, rather than loop in an old ASan signal handler.
ASAN_OPTIONS=detect_leaks=1:handle_segv=0 timeout --signal=KILL 30s "$work/check"
