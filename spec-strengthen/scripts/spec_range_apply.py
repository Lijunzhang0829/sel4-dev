#!/usr/bin/env python3
"""spec_range_apply.py — apply a check-theory.sh range-replace patch to a
source file, writing the result elsewhere (non-destructive).

Used by strengthen.sh to render a `patch.diff` in DRY-RUN mode and to build the
PATCHED PREVIEW the delivery gate inspects for named-realized consumer
evidence (a compound L'+consumer patch must be visible to the gate).

Patch format — IDENTICAL to check-theory.sh --patch/--apply, including
MULTIPLE hunks separated by lines containing only `---`:
  <START> <END>          (1-based, inclusive line range to replace)
  <replacement text...>
  ---
  <START2> <END2>
  <replacement text...>
Hunks are applied in reverse start-line order so earlier edits don't shift the
line numbers of later ones.

Usage:  spec_range_apply.py <patch> <source_in> <out>
"""
import sys


def parse_hunks(patch_text):
    hunks = []
    for block in (b.strip("\n") for b in patch_text.strip().split("---")):
        if not block.strip():
            continue
        lines = block.split("\n")
        header = lines[0].split()
        start, end = int(header[0]), int(header[1])
        replacement = "\n".join(lines[1:])
        hunks.append((start, end, replacement))
    return hunks


def main():
    if len(sys.argv) != 4:
        print("usage: spec_range_apply.py <patch> <source_in> <out>", file=sys.stderr)
        sys.exit(2)
    patch_path, src_path, out_path = sys.argv[1:4]

    with open(patch_path, encoding="utf-8") as f:
        patch_text = f.read()
    try:
        hunks = parse_hunks(patch_text)
    except (IndexError, ValueError) as e:
        print(f"bad patch: {e}", file=sys.stderr)
        sys.exit(2)
    if not hunks:
        print("empty patch", file=sys.stderr)
        sys.exit(2)

    with open(src_path, encoding="utf-8") as f:
        src = f.readlines()

    # apply in reverse start-line order (mirrors check-theory.sh)
    for start, end, replacement in sorted(hunks, key=lambda h: h[0], reverse=True):
        if start < 1 or end > len(src) or start > end:
            print(f"hunk {start}..{end} out of bounds (file has {len(src)} lines)",
                  file=sys.stderr)
            sys.exit(2)
        if replacement and not replacement.endswith("\n"):
            replacement += "\n"
        src[start - 1:end] = [replacement]

    with open(out_path, "w", encoding="utf-8") as f:
        f.writelines(src)


if __name__ == "__main__":
    main()
