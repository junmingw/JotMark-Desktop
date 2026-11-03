#!/usr/bin/env bash
# Finder-friendly macOS installer entry point.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/install_manual_annotation_macos.sh" "$@"
