#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark}"
RUNTIME_DIR="${JOTMARK_RUNTIME_DIR:-${LABELPAW_RUNTIME_DIR:-$ROOT/.runtime}}"

# Keep known pre-rename environments usable until the operator creates the
# workspace-local .venv-jotmark environment.  An explicit ENV_DIR always wins.
if [[ ! -x "$ENV_DIR/bin/python" && "$ENV_DIR" == "$ROOT/.venv-jotmark" ]]; then
    for candidate in \
        "$ROOT/.venv-labelpaw" \
        "/home/nex/Documents/JotMark/.venv-labelpaw"; do
        if [[ -x "$candidate/bin/python" ]]; then
            ENV_DIR="$candidate"
            echo "使用兼容环境：$ENV_DIR；建议后续运行 scripts/install_jotmark.sh 创建 .venv-jotmark。" >&2
            break
        fi
    done
fi

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    echo "找不到 JotMark 环境：$ENV_DIR" >&2
    if [[ "${JOTMARK_MANUAL_ONLY:-}" == "1" ]]; then
        echo "请先运行 scripts/install_manual_annotation.sh。" >&2
    else
        echo "请先运行 scripts/install_jotmark.sh。" >&2
    fi
    exit 1
fi

mkdir -p "$RUNTIME_DIR/matplotlib" "$RUNTIME_DIR/config"
export MPLCONFIGDIR="$RUNTIME_DIR/matplotlib"

# Qt's IBus input-context plugin is bundled with PySide6, and class names are
# stored as UTF-8.  On some desktop sessions the IBus environment variables
# are present while this launcher redirects XDG_CONFIG_HOME.  IBus locates its
# session-bus record below XDG_CONFIG_HOME, so capture the active address
# *before* applying the JotMark-specific configuration directory.
if [[ "${QT_IM_MODULE:-}" == "ibus" && -z "${IBUS_ADDRESS:-}" ]] \
    && command -v ibus >/dev/null 2>&1; then
    active_ibus_address="$(ibus address 2>/dev/null || true)"
    if [[ "$active_ibus_address" == unix:* ]]; then
        export IBUS_ADDRESS="$active_ibus_address"
    elif command -v ibus-daemon >/dev/null 2>&1; then
        ibus-daemon -drx >/dev/null 2>&1 || true
        active_ibus_address="$(ibus address 2>/dev/null || true)"
        if [[ "$active_ibus_address" == unix:* ]]; then
            export IBUS_ADDRESS="$active_ibus_address"
        fi
    fi
fi

export XDG_CONFIG_HOME="$RUNTIME_DIR/config"
export JOTMARK_DEVICE="${JOTMARK_DEVICE:-${LABELPAW_DEVICE:-auto}}"

# SAM3 on 11 GB GPUs performs several large, short-lived allocations while
# encoding the 1008x1008 image embedding.  Expandable allocator segments make
# those allocations less sensitive to fragmentation.  Keep an explicit user
# setting unchanged for operators who need a different PyTorch allocator mode.
export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"

# The supplied environment is a Conda clone. Prefer its C++ runtime while
# preserving any caller-provided library search path.
if [[ -d "$ENV_DIR/lib" ]]; then
    export LD_LIBRARY_PATH="$ENV_DIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec "$ENV_DIR/bin/python" "$ROOT/main.py" "$@"
