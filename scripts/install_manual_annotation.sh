#!/usr/bin/env bash
# Install the lightweight, manual-annotation edition.  No model code, model
# weight, CUDA toolkit or NVIDIA driver is downloaded or required.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark-manual}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

has_command() {
    command -v "$1" >/dev/null 2>&1
}

python_is_supported() {
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)" \
        >/dev/null 2>&1
}

install_ubuntu_prerequisites() {
    local sudo_cmd=()
    if [[ "${EUID}" -ne 0 ]]; then
        if ! has_command sudo; then
            echo "当前账户没有 sudo，无法自动安装系统组件。请让管理员安装 Python 3、python3-venv、python3-pip、libgl1、libegl1 和 libxcb-cursor0。" >&2
            return 1
        fi
        sudo_cmd=(sudo)
    fi

    echo "正在安装 JotMark 手工版所需的系统组件（可能需要输入登录密码）..."
    "${sudo_cmd[@]}" apt-get update
    DEBIAN_FRONTEND=noninteractive "${sudo_cmd[@]}" apt-get install -y \
        python3 python3-venv python3-pip libgl1 libegl1 libxcb-cursor0
}

ensure_system_prerequisites() {
    local needs_setup=0
    if ! has_command "$PYTHON_BIN"; then
        echo "未检测到 Python 3。"
        needs_setup=1
    elif ! python_is_supported "$PYTHON_BIN"; then
        echo "检测到的 Python 版本低于 ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}：$($PYTHON_BIN --version 2>&1)" >&2
        echo "JotMark 手工版需要 Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} 或更高版本。" >&2
        return 1
    elif ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
        echo "检测到 Python，但缺少 python3-venv。"
        needs_setup=1
    fi

    if [[ "$needs_setup" -eq 0 ]]; then
        return 0
    fi

    if has_command apt-get; then
        install_ubuntu_prerequisites
        hash -r
        return 0
    fi

    cat >&2 <<'EOF'
无法识别可自动安装的软件包管理器。本安装器目前可自动配置 Ubuntu / Debian。
请先通过系统的软件管理器安装 Python 3.10+、venv、pip，以及 Qt 所需的
OpenGL / EGL / XCB Cursor 图形库，再重新运行本脚本。
EOF
    return 1
}

if ! ensure_system_prerequisites; then
    exit 2
fi

if ! has_command "$PYTHON_BIN"; then
    echo "系统组件安装后仍未找到 $PYTHON_BIN。请关闭并重新打开终端后再运行本脚本。" >&2
    exit 2
fi

if ! python_is_supported "$PYTHON_BIN"; then
    echo "当前 Python 不满足 Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ 要求：$($PYTHON_BIN --version 2>&1)" >&2
    exit 2
fi

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    echo "python3-venv 仍不可用。请重新运行本脚本，或联系系统管理员。" >&2
    exit 2
fi

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ENV_DIR"
fi

PYTHON="$ENV_DIR/bin/python"
PIP=("$PYTHON" -m pip)

select_fastest_pypi_index() {
    "$PYTHON" - <<'PY'
import time
import urllib.request

candidates = [
    ("Tsinghua TUNA", "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"),
    ("USTC", "https://mirrors.ustc.edu.cn/pypi/simple/"),
    ("Aliyun", "https://mirrors.aliyun.com/pypi/simple/"),
    ("Official PyPI", "https://pypi.org/simple/"),
]
best = None
for name, url in candidates:
    try:
        request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "JotMark installer"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=3.5) as response:
            response.read(0)
        elapsed = time.monotonic() - started
        if best is None or elapsed < best[0]:
            best = (elapsed, name, url)
    except Exception:
        pass

if best:
    print(f"{best[1]}|{best[2]}")
else:
    print("Official PyPI (fallback)|https://pypi.org/simple/")
PY
}

if [[ -n "${JOTMARK_PIP_INDEX_URL:-}" ]]; then
    PIP_INDEX_NAME="custom source"
    PIP_INDEX_URL="$JOTMARK_PIP_INDEX_URL"
else
    echo "正在检测软件包镜像响应速度（最多约 15 秒）..."
    mirror_choice="$(select_fastest_pypi_index)"
    PIP_INDEX_NAME="${mirror_choice%%|*}"
    PIP_INDEX_URL="${mirror_choice#*|}"
fi

echo "本次安装使用软件包源：$PIP_INDEX_NAME"
echo "$PIP_INDEX_URL"

pip_install() {
    if "${PIP[@]}" install "$@" --index-url "$PIP_INDEX_URL" --timeout 30 --retries 2; then
        return 0
    fi
    if [[ "$PIP_INDEX_URL" == "https://pypi.org/simple/" ]]; then
        return 1
    fi
    echo "所选软件包源下载失败，正在使用官方 PyPI 重试一次..." >&2
    "${PIP[@]}" install "$@" --index-url "https://pypi.org/simple/" --timeout 30 --retries 2
}

pip_install --upgrade pip setuptools wheel
pip_install -r "$ROOT/requirements-manual.txt"

JOTMARK_MANUAL_ONLY=1 "$PYTHON" - <<'PY'
import cv2
import numpy
from PIL import Image
from PySide6.QtWidgets import QApplication
import main
from jotmark.data.exporter import Exporter
from ui.annotation_tree_widget import AnnotationTreeWidget
from ui.attribute_widgets import AttributeScopeDialog, BooleanValuePicker

print("手工标注环境检查通过")
print("PySide6、OpenCV、NumPy、Pillow 已安装")
assert main.MANUAL_ONLY, "手工版必须以 JOTMARK_MANUAL_ONLY=1 启动"
assert hasattr(Exporter, "save_curvelanes"), "缺少 CurveLanes 折线导出器"
assert hasattr(AnnotationTreeWidget, "show_class_context_menu_for_item"), "缺少类别右键菜单"
assert hasattr(AnnotationTreeWidget, "show_annotation_context_menu_for_item"), "缺少标注右键改类菜单"
assert BooleanValuePicker is not None, "缺少是/否属性选择控件"
assert hasattr(AttributeScopeDialog, "_current_visibility_signature"), "缺少单选属性重复修改修复"
print("手工模式、CurveLanes、右侧标注改类、属性选择检查通过")
PY

echo
echo "安装完成。启动请执行："
echo "  cd \"$ROOT\""
echo "  ./scripts/run_manual_annotation.sh"
