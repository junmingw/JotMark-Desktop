#!/usr/bin/env bash
# Beginner-friendly entry point for the Linux manual-annotation installer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/scripts/install_manual_annotation.sh" "$@"
