#!/usr/bin/env bash
# Install the lightweight, manual-annotation edition.  No model code, model
# weight, CUDA toolkit or NVIDIA driver is downloaded or required.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark-manual}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "未找到 Python 3。请先安装 Python 3、python3-venv 和 python3-pip。" >&2
    exit 2
fi

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    cat >&2 <<'EOF'
当前系统缺少 Python 虚拟环境组件。请在终端执行：

  sudo apt update
  sudo apt install -y python3-venv python3-pip

完成后，再重新执行本脚本。
EOF
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

"$PYTHON" - <<'PY'
import cv2
import numpy
from PIL import Image
from PySide6.QtWidgets import QApplication

print("手工标注环境检查通过")
print("PySide6、OpenCV、NumPy、Pillow 已安装")
PY

echo
echo "安装完成。启动请执行："
echo "  cd \"$ROOT\""
echo "  ./scripts/run_manual_annotation.sh"
