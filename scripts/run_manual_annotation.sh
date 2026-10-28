#!/usr/bin/env bash
# Start JotMark without importing or enabling any AI-assisted functionality.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark-manual}"

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    echo "找不到手工标注环境：$ENV_DIR" >&2
    echo "请先运行：./scripts/install_manual_annotation.sh" >&2
    exit 1
fi

export JOTMARK_MANUAL_ONLY=1
export ENV_DIR
exec "$ROOT/scripts/run_desktop.sh" "$@"
