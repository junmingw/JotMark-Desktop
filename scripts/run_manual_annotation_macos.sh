#!/usr/bin/env bash
# Start JotMark without importing or enabling AI-assisted functionality.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${JOTMARK_ENV_DIR:-$HOME/Library/Application Support/JotMark/manual-venv}"

environment_is_healthy() {
    [[ -x "$ENV_DIR/bin/python" ]] \
        && (cd "$ROOT" && JOTMARK_MANUAL_ONLY=1 "$ENV_DIR/bin/python" -c \
            "import sys, cv2, numpy; from PIL import Image; from PySide6.QtWidgets import QApplication; import main; assert sys.version_info >= (3, 10); assert main.MANUAL_ONLY" \
            >/dev/null 2>&1)
}

if ! environment_is_healthy; then
    echo "未检测到可用的 JotMark 手工标注环境，正在自动配置..."
    echo "首次运行可能需要安装 Homebrew 和 Python，并下载图形组件。"
    if ! "$ROOT/scripts/install_manual_annotation_macos.sh"; then
        echo "自动安装未完成，请根据上方报错处理后重新运行本启动脚本。" >&2
        exit 1
    fi
fi

export JOTMARK_MANUAL_ONLY=1
export ENV_DIR
exec "$ROOT/scripts/run_desktop.sh" "$@"
