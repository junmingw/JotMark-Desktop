#!/usr/bin/env bash
# Install the lightweight macOS manual-annotation edition.  It intentionally
# excludes PyTorch, SAM, YOLO, CUDA, model weights and the training platform.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
ENV_DIR="${JOTMARK_ENV_DIR:-$HOME/Library/Application Support/JotMark/manual-venv}"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10

has_command() {
    command -v "$1" >/dev/null 2>&1
}

python_is_supported() {
    "$1" -c "import sys; raise SystemExit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)" \
        >/dev/null 2>&1
}

configure_homebrew_path() {
    local brew_path=""
    for brew_path in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -x "$brew_path" ]]; then
            eval "$("$brew_path" shellenv)"
            return 0
        fi
    done
    return 1
}

find_supported_python() {
    local candidate resolved
    local candidates=()
    if [[ -n "${PYTHON_BIN:-}" ]]; then
        candidates+=("$PYTHON_BIN")
    fi
    candidates+=(python3.13 python3.12 python3.11 python3.10 python3)
    candidates+=(
        /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
        /opt/homebrew/opt/python@3.12/bin/python3.12
        /usr/local/opt/python@3.12/bin/python3.12
    )

    for candidate in "${candidates[@]}"; do
        if ! has_command "$candidate"; then
            continue
        fi
        resolved="$(command -v "$candidate")"
        if python_is_supported "$resolved"; then
            PYTHON_BIN="$resolved"
            return 0
        fi
    done
    return 1
}

ask_to_install_homebrew() {
    local answer=""
    if [[ "${JOTMARK_INSTALL_HOMEBREW:-}" == "1" ]]; then
        return 0
    fi
    if [[ ! -t 0 ]]; then
        echo "未找到 Homebrew，且当前不是可交互终端；将尝试 Python 签名安装包。" >&2
        return 1
    fi
    read -r -p "未检测到 Python 3.10+。是否安装 Homebrew 并继续安装 Python 3.12？[Y/n] " answer
    [[ -z "$answer" || "$answer" =~ ^[Yy]$ ]]
}

ensure_homebrew() {
    if has_command brew; then
        return 0
    fi
    configure_homebrew_path && return 0

    if ! ask_to_install_homebrew; then
        cat >&2 <<'EOF'
已跳过 Homebrew；接下来尝试 Python 官方签名的 .pkg 安装包。
EOF
        return 1
    fi
    if ! has_command curl; then
        echo "找不到 curl，无法自动安装 Homebrew。请先安装 Homebrew 后重试。" >&2
        return 1
    fi

    echo "正在安装 Homebrew。该过程可能要求输入 macOS 登录密码，请按终端提示操作..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    configure_homebrew_path || has_command brew
}

ensure_supported_python() {
    if find_supported_python; then
        return 0
    fi

    echo "未检测到 Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+，正在准备安装 Python 3.12..."
    if ensure_homebrew && brew install python@3.12; then
        configure_homebrew_path || true
        if find_supported_python; then
            return 0
        fi
    fi
    echo "Homebrew 未能提供 Python；将尝试签名的 Python .pkg 安装包。" >&2
    install_signed_python_pkg && find_supported_python
}

install_signed_python_pkg() {
    local version=3.12.10 filename="python-3.12.10-macos11.pkg"
    local official="https://www.python.org/ftp/python/$version/$filename"
    local -a candidates=(
        "https://mirrors.tuna.tsinghua.edu.cn/python/$version/$filename"
        "https://mirrors.aliyun.com/python-release/macos/$filename"
        "$official"
    )
    local best_url="" best_time=999999 url seconds size
    local workdir pkg
    if ! has_command curl; then
        echo "缺少 curl，无法下载 macOS Python 安装包。" >&2
        return 1
    fi
    echo "正在测速国内 Python .pkg 镜像（不下载文件）..."
    for url in "${candidates[@]}"; do
        size="$(curl -fsSLI --connect-timeout 3 --max-time 5 "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:" {n=$2} END {print n+0}' )" || continue
        [[ "$size" -ge 40000000 ]] || continue
        seconds="$(curl -sSL -o /dev/null --head --connect-timeout 3 --max-time 5 -w '%{time_total}' "$url")" || continue
        if awk -v a="$seconds" -v b="$best_time" 'BEGIN{exit !(a<b)}'; then
            best_time="$seconds"
            best_url="$url"
        fi
    done
    [[ -n "$best_url" ]] || best_url="$official"
    workdir="$(mktemp -d "${TMPDIR:-/tmp}/jotmark-python-pkg.XXXXXXXX")"
    pkg="$workdir/$filename"
    for url in "$best_url" "$official"; do
        [[ "$url" == "$official" && "$best_url" == "$official" && -e "$pkg" ]] && continue
        echo "下载 Python 安装包：$url"
        if curl -fL --retry 2 --connect-timeout 15 --max-time 600 -o "$pkg" "$url" \
            && [[ "$(wc -c < "$pkg")" -ge 40000000 ]] \
            && pkgutil --check-signature "$pkg" | grep -q 'Developer ID Installer: Python Software Foundation'; then
            echo "已验证 Python Software Foundation 安装包签名；macOS 安装需要管理员密码。"
            if sudo /usr/sbin/installer -pkg "$pkg" -target /; then
                rm -f "$pkg"
                rmdir "$workdir"
                return 0
            fi
            echo "安装器执行失败；请检查 macOS 管理员权限。" >&2
            break
        fi
        echo "下载失败、文件不完整或签名不正确；尝试下一个来源。" >&2
        rm -f "$pkg"
    done
    rmdir "$workdir"
    return 1
}

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

ensure_supported_python

if [[ ! -x "$ENV_DIR/bin/python" ]] || ! python_is_supported "$ENV_DIR/bin/python"; then
    echo "正在创建独立 Python 环境：$ENV_DIR"
    mkdir -p "$(dirname "$ENV_DIR")"
    "$PYTHON_BIN" -m venv --upgrade "$ENV_DIR"
fi

PYTHON="$ENV_DIR/bin/python"
if ! "$PYTHON" -m pip --version >/dev/null 2>&1; then
    "$PYTHON" -m ensurepip --upgrade
fi
PIP=("$PYTHON" -m pip)

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
echo "安装完成。启动请双击 Start-JotMark-Manual-macOS.command，或执行："
echo "  cd \"$ROOT\""
echo "  ./Start-JotMark-Manual-macOS.command"
