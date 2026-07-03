#!/usr/bin/env bash
# Host wrapper: routes check-theory.sh into the sel4-dev container.
# See scripts-container/check-theory.sh for the real implementation.
source "$(dirname "${BASH_SOURCE[0]}")/_dx.sh" "$@"
