#!/usr/bin/env python3
"""Same agent-visible tool entry for VERO and official Codex deployments."""
from vero.isabelle.tool import main

if __name__ == "__main__":
    main(minimal=True)
