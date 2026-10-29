#!/usr/bin/env bash
# Install the AI-assisted desktop dependencies and official SAM sources.
# When SAM3's CUDA requirements are not met, this installer safely switches
# to the lightweight manual-annotation edition instead of leaving a partial,
# unusable AI environment behind.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENDOR_DIR="${JOTMARK_VENDOR_DIR:-$ROOT/.vendor}"
INSTALL_SAM2="${INSTALL_SAM2:-1}"
INSTALL_SAM3="${INSTALL_SAM3:-1}"
SAM2_SOURCE="${SAM2_SOURCE:-https://github.com/facebookresearch/sam2.git}"
SAM3_SOURCE="${SAM3_SOURCE:-https://github.com/facebookresearch/sam3.git}"
MANUAL_ENV_DIR="${JOTMARK_MANUAL_ENV_DIR:-$ROOT/.venv-jotmark-manual}"
TORCH_INDEX_URL="${JOTMARK_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu118}"
SAM3_MIN_VRAM_GB="${JOTMARK_SAM3_MIN_VRAM_GB:-8}"
NVIDIA_SMI_BIN="${JOTMARK_NVIDIA_SMI_BIN:-nvidia-smi}"

has_command() {
    command -v "$1" >/dev/null 2>&1
}

python_is_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

fallback_to_manual() {
    local reason="$1"
    cat <<EOF

===============================================================
 SAM3 智能标注环境不满足要求，已切换到手工标注版
===============================================================
$reason

手工标注版不需要 NVIDIA 显卡、CUDA、PyTorch、SAM 或模型权重，
仍可使用矩形、多边形、折线、点、旋转框、类别与属性标注。
EOF
    if [[ "${JOTMARK_DISABLE_MANUAL_FALLBACK:-0}" == "1" ]]; then
        echo "已按 JOTMARK_DISABLE_MANUAL_FALLBACK=1 停止；未安装手工版。" >&2
        exit 2
    fi
    if [[ "${JOTMARK_FALLBACK_DRY_RUN:-0}" == "1" ]]; then
        echo "JOTMARK_FALLBACK_DRY_RUN=1：已验证手工版回退路径，未执行安装。"
        exit 0
    fi
    echo "正在配置手工标注版环境..."
    ENV_DIR="$MANUAL_ENV_DIR" "$ROOT/scripts/install_manual_annotation.sh"
    echo
    echo "手工标注版已安装。启动命令："
    echo "  ./Start-JotMark-Manual-Linux.sh"
    exit 0
}

version_at_least() {
    # Usage: version_at_least ACTUAL REQUIRED
    [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]
}

check_sam3_host_gpu() {
    [[ "$INSTALL_SAM3" == "1" ]] || return 0
    if ! has_command "$NVIDIA_SMI_BIN"; then
        fallback_to_manual "未检测到 nvidia-smi 或可用的 NVIDIA 驱动，因此无法运行仅支持 CUDA 的 SAM3。"
    fi

    local driver gpu_count
    driver="$($NVIDIA_SMI_BIN --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n 1 | tr -d '[:space:]')"
    gpu_count="$($NVIDIA_SMI_BIN --query-gpu=name --format=csv,noheader 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l | tr -d '[:space:]')"
    if [[ -z "$driver" || "$gpu_count" == "0" ]]; then
        fallback_to_manual "检测不到可用 NVIDIA GPU；SAM3 不能在 CPU 上运行。"
    fi
    # cu118 wheels need a modern driver. The threshold is intentionally
    # conservative; operators can override the wheel index if they maintain
    # a compatible custom PyTorch/CUDA stack.
    if ! version_at_least "$driver" "520.00"; then
        fallback_to_manual "NVIDIA 驱动版本为 $driver，低于默认 CUDA 11.8 PyTorch 环境建议的 520.00。"
    fi
    echo "检测到 $gpu_count 块 NVIDIA GPU，驱动版本：$driver"
}

ensure_python_venv() {
    if has_command "$PYTHON_BIN" && "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
        if ! python_is_supported "$PYTHON_BIN"; then
            echo "检测到的 Python 版本低于 3.10：$($PYTHON_BIN --version 2>&1)。请安装 Python 3.10 或更高版本后重试。" >&2
            exit 2
        fi
        return 0
    fi
    if ! has_command apt-get; then
        echo "未找到可用的 Python 3 + venv，且当前系统不是受支持的 Ubuntu / Debian 自动安装环境。" >&2
        exit 2
    fi
    local sudo_cmd=()
    if [[ "${EUID}" -ne 0 ]]; then
        has_command sudo || { echo "需要 sudo 才能安装 Python 3 与 venv。" >&2; exit 2; }
        sudo_cmd=(sudo)
    fi
    echo "正在安装 Python 3、venv、pip、git 和 Qt 图形运行库（可能需要输入密码）..."
    "${sudo_cmd[@]}" apt-get update
    DEBIAN_FRONTEND=noninteractive "${sudo_cmd[@]}" apt-get install -y \
        python3 python3-venv python3-pip git libgl1 libegl1 libxcb-cursor0
    hash -r
    if ! has_command "$PYTHON_BIN" || ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1 || ! python_is_supported "$PYTHON_BIN"; then
        echo "Python 3 / venv 安装后仍不可用。请重新打开终端后重试。" >&2
        exit 2
    fi
}

check_sam3_host_gpu
ensure_python_venv

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ENV_DIR"
fi

PYTHON="$ENV_DIR/bin/python"
PIP=("$PYTHON" -m pip)

"${PIP[@]}" install --upgrade pip setuptools wheel

if [[ "$INSTALL_SAM3" == "1" ]]; then
    if ! "$PYTHON" -c 'import torch, torchvision; raise SystemExit(0 if torch.cuda.is_available() else 1)' >/dev/null 2>&1; then
        echo "正在安装 CUDA 版 PyTorch（源：$TORCH_INDEX_URL）..."
        if ! "${PIP[@]}" install --upgrade torch torchvision --index-url "$TORCH_INDEX_URL" --timeout 60 --retries 2; then
            echo "CUDA 版 PyTorch 安装失败。请检查网络、驱动兼容性，或设置 JOTMARK_TORCH_INDEX_URL 后重试。" >&2
            exit 2
        fi
    fi

    SAM3_GPU_REPORT="$("$PYTHON" - <<'PY'
import torch
if not torch.cuda.is_available():
    raise SystemExit(1)
props = torch.cuda.get_device_properties(0)
major, minor = torch.cuda.get_device_capability(0)
print(f"{props.name}|{props.total_memory / 1024**3:.2f}|{major}.{minor}")
PY
)" || fallback_to_manual "已安装的 PyTorch 仍无法使用 CUDA；SAM3 不能在 CPU 上运行。"

    IFS='|' read -r SAM3_GPU_NAME SAM3_GPU_VRAM_GB SAM3_GPU_CAPABILITY <<<"$SAM3_GPU_REPORT"
    SAM3_GPU_CAPABILITY_MAJOR="${SAM3_GPU_CAPABILITY%%.*}"
    if [[ "${SAM3_GPU_CAPABILITY_MAJOR:-0}" -lt 7 ]]; then
        fallback_to_manual "检测到 GPU：$SAM3_GPU_NAME（计算能力 $SAM3_GPU_CAPABILITY）。SAM3 安装器要求计算能力至少为 7.0。"
    fi
    if ! version_at_least "$SAM3_GPU_VRAM_GB" "$SAM3_MIN_VRAM_GB"; then
        fallback_to_manual "检测到 GPU：$SAM3_GPU_NAME，显存 ${SAM3_GPU_VRAM_GB} GiB；SAM3 安装器最低要求为 ${SAM3_MIN_VRAM_GB} GiB。"
    fi
    echo "SAM3 GPU 检查通过：$SAM3_GPU_NAME，${SAM3_GPU_VRAM_GB} GiB，计算能力 $SAM3_GPU_CAPABILITY"
    if ! version_at_least "$SAM3_GPU_VRAM_GB" "12"; then
        echo "提示：显存低于 12 GiB，SAM3 可尝试使用 FP16 运行；处理大图时仍可能显存不足。" >&2
    fi
elif ! "$PYTHON" -c 'import torch, torchvision' >/dev/null 2>&1; then
    echo "未启用 SAM3，正在安装通用 PyTorch / torchvision..."
    "${PIP[@]}" install --upgrade torch torchvision --timeout 60 --retries 2
fi

"${PIP[@]}" install -r "$ROOT/requirements-core.txt"

install_source() {
    local package_name="$1"
    local source="$2"
    local checkout="$VENDOR_DIR/$package_name"

    if [[ -d "$source" ]]; then
        echo "Installing $package_name from local source: $source"
        "${PIP[@]}" install -e "$source"
        return
    fi

    if [[ ! -d "$checkout/.git" ]]; then
        command -v git >/dev/null 2>&1 || {
            echo "git is required to fetch $package_name from $source" >&2
            exit 3
        }
        mkdir -p "$VENDOR_DIR"
        git clone --depth 1 "$source" "$checkout"
    else
        echo "Reusing existing $package_name source: $checkout"
    fi

    "${PIP[@]}" install -e "$checkout"
}

if [[ "$INSTALL_SAM2" == "1" ]]; then
    install_source "sam2" "$SAM2_SOURCE"
fi

if [[ "$INSTALL_SAM3" == "1" ]]; then
    install_source "sam3" "$SAM3_SOURCE"
fi

"$PYTHON" - <<'PY'
import torch

print(f"PyTorch: {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
for name in ("sam2", "sam3"):
    try:
        module = __import__(name)
        print(f"{name}: {getattr(module, '__file__', 'installed')}")
    except ImportError:
        print(f"{name}: not installed")
PY

echo
echo "Installation complete. Put model weights under: $ROOT/weights"
