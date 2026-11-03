#!/usr/bin/env bash
# Finder-friendly macOS launcher. Missing dependencies are repaired first.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/scripts/run_manual_annotation_macos.sh" "$@"
