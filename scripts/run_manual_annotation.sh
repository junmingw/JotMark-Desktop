#!/usr/bin/env bash
# Start JotMark without importing or enabling any AI-assisted functionality.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark-manual}"

environment_is_healthy() {
    [[ -x "$ENV_DIR/bin/python" ]] \
        && JOTMARK_MANUAL_ONLY=1 "$ENV_DIR/bin/python" -c \
            "import cv2, numpy; from PIL import Image; from PySide6.QtWidgets import QApplication; import main; assert main.MANUAL_ONLY" \
            >/dev/null 2>&1
}

if ! environment_is_healthy; then
    echo "未检测到可用的 JotMark 手工标注环境，正在自动配置..."
    echo "首次运行可能需要下载 Python 图形组件，并在缺少系统组件时请求 sudo 密码。"
    if ! "$ROOT/scripts/install_manual_annotation.sh"; then
        echo "自动安装未完成，请根据上方报错处理后重新运行本启动脚本。" >&2
        exit 1
    fi
fi

export JOTMARK_MANUAL_ONLY=1
export ENV_DIR
exec "$ROOT/scripts/run_desktop.sh" "$@"
