#!/usr/bin/env bash
# Beginner-friendly entry point. Missing or broken dependencies are repaired
# automatically by scripts/run_manual_annotation.sh before the UI starts.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/scripts/run_manual_annotation.sh" "$@"
