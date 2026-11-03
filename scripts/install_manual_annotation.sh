#!/usr/bin/env bash
# Install the lightweight, manual-annotation edition.  No model code, model
# weight, CUDA toolkit or NVIDIA driver is downloaded or required.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
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

find_supported_python() {
    local candidate
    for candidate in "$PYTHON_BIN" python3.13 python3.12 python3.11 python3.10 python3; do
        if has_command "$candidate" && python_is_supported "$candidate" \
            && "$candidate" -m venv --help >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$candidate")"
            return 0
        fi
    done
    return 1
}

install_system_prerequisites() {
    local sudo_cmd=()
    if [[ "$EUID" -ne 0 ]]; then
        if ! has_command sudo; then
            echo "无 sudo 权限；将尝试在用户目录安装独立 Python。" >&2
            return 1
        fi
        sudo_cmd=(sudo)
    fi

    if has_command apt-get; then
        echo "使用 apt 安装 Python 和 Qt 系统库..."
        "${sudo_cmd[@]}" apt-get update && \
            DEBIAN_FRONTEND=noninteractive "${sudo_cmd[@]}" apt-get install -y \
                python3 python3-venv python3-pip libgl1 libegl1 libxcb-cursor0 && return 0
    fi
    if has_command dnf; then
        echo "使用 dnf 安装 Python 和 Qt 系统库..."
        "${sudo_cmd[@]}" dnf install -y python3 python3-pip mesa-libGL mesa-libEGL xcb-util-cursor && return 0
    fi
    if has_command yum; then
        echo "使用 yum 安装 Python 和 Qt 系统库..."
        "${sudo_cmd[@]}" yum install -y python3 python3-pip mesa-libGL mesa-libEGL xcb-util-cursor && return 0
    fi
    if has_command pacman; then
        echo "使用 pacman 安装 Python 和 Qt 系统库..."
        "${sudo_cmd[@]}" pacman -S --needed --noconfirm python python-pip mesa libglvnd xcb-util-cursor && return 0
    fi
    if has_command zypper; then
        echo "使用 zypper 安装 Python 和 Qt 系统库..."
        "${sudo_cmd[@]}" zypper --non-interactive install python3 python3-pip libGL1 libEGL1 libxcb-cursor0 && return 0
    fi
    echo "系统包管理器不可用或安装失败；将尝试用户级 Python。" >&2
    return 1
}

install_user_python() {
    local uv_dir="${JOTMARK_UV_DIR:-$HOME/.local/share/jotmark/bin}"
    local uv_bin="$uv_dir/uv" bootstrap
    if [[ ! -x "$uv_bin" ]]; then
        bootstrap="$(mktemp "${TMPDIR:-/tmp}/jotmark-uv-install.XXXXXXXX")"
        if has_command curl; then
            if ! curl -fLsS --connect-timeout 15 --max-time 120 https://astral.sh/uv/install.sh -o "$bootstrap"; then
                rm -f "$bootstrap"
                return 1
            fi
        elif has_command wget; then
            if ! wget -q --timeout=20 -O "$bootstrap" https://astral.sh/uv/install.sh; then
                rm -f "$bootstrap"
                return 1
            fi
        else
            echo "缺少 curl/wget，无法下载用户级 Python 引导器。" >&2
            rm -f "$bootstrap"
            return 1
        fi
        echo "正在使用 Astral 官方 uv 安装器配置用户级 Python（不需要 sudo）..."
        if ! UV_INSTALL_DIR="$uv_dir" sh "$bootstrap"; then
            rm -f "$bootstrap"
            return 1
        fi
        rm -f "$bootstrap"
    fi
    [[ -x "$uv_bin" ]] || return 1
    local managed_dir="${JOTMARK_PYTHON_INSTALL_DIR:-$HOME/.local/share/jotmark/python}"
    UV_PYTHON_INSTALL_DIR="$managed_dir" "$uv_bin" python install 3.12 || return 1
    PYTHON_BIN="$(UV_PYTHON_INSTALL_DIR="$managed_dir" UV_PYTHON_PREFERENCE=only-managed "$uv_bin" python find 3.12)" || return 1
    python_is_supported "$PYTHON_BIN" && "$PYTHON_BIN" -m venv --help >/dev/null 2>&1
}

if ! find_supported_python; then
    install_system_prerequisites || true
    hash -r
    if ! find_supported_python; then
        if ! install_user_python; then
            echo "无法自动配置 Python 3.10+；请检查网络，或安装 Python 3.10+、venv 与 Qt/OpenGL 系统库后重试。" >&2
            exit 2
        fi
    fi
fi

if [[ ! -x "$ENV_DIR/bin/python" ]] || ! python_is_supported "$ENV_DIR/bin/python"; then
    if ! "$PYTHON_BIN" -m venv --upgrade "$ENV_DIR"; then
        echo "当前 Python 无法创建虚拟环境；尝试安装系统 venv 组件..." >&2
        if install_system_prerequisites && find_supported_python \
            && "$PYTHON_BIN" -m venv --upgrade "$ENV_DIR"; then
            :
        elif ! install_user_python || ! "$PYTHON_BIN" -m venv --upgrade "$ENV_DIR"; then
            echo "无法创建虚拟环境。请检查目录权限并安装 Python venv/Qt 系统库。" >&2
            exit 2
        fi
    fi
fi

PYTHON="$ENV_DIR/bin/python"
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    "$PYTHON" -m ensurepip --upgrade
fi
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
from jotmark.data.attribute_schema import ClassAttributeStore
from jotmark.data.category_conversion import convert_attribute_to_category
from ui.annotation_tree_widget import AnnotationTreeWidget
from ui.attribute_widgets import AttributeScopeDialog, BooleanValuePicker
from ui.category_conversion_dialog import CategoryConversionDialog

print("手工标注环境检查通过")
print("PySide6、OpenCV、NumPy、Pillow 已安装")
assert main.MANUAL_ONLY, "手工版必须以 JOTMARK_MANUAL_ONLY=1 启动"
assert hasattr(Exporter, "save_curvelanes"), "缺少 CurveLanes 折线导出器"
assert hasattr(AnnotationTreeWidget, "show_class_context_menu_for_item"), "缺少类别右键菜单"
assert hasattr(AnnotationTreeWidget, "show_annotation_context_menu_for_item"), "缺少标注右键改类菜单"
assert BooleanValuePicker is not None, "缺少是/否属性选择控件"
assert hasattr(AttributeScopeDialog, "_current_visibility_signature"), "缺少单选属性重复修改修复"
assert hasattr(ClassAttributeStore, "replace_schema"), "缺少属性 JSON 导入支持"
assert CategoryConversionDialog is not None and callable(convert_attribute_to_category), "缺少类别/属性安全转换工具"
print("手工模式、CurveLanes、右侧标注改类、属性导入与安全转换检查通过")
PY

echo
echo "安装完成。启动请执行："
echo "  cd \"$ROOT\""
echo "  ./scripts/run_manual_annotation.sh"
