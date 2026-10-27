#!/usr/bin/env bash
# Install the desktop dependencies and the official SAM source packages.
# PyTorch is intentionally installed separately: CUDA wheels must match the
# target machine's NVIDIA driver and CUDA runtime.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_DIR="${ENV_DIR:-$ROOT/.venv-jotmark}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENDOR_DIR="${JOTMARK_VENDOR_DIR:-$ROOT/.vendor}"
INSTALL_SAM2="${INSTALL_SAM2:-1}"
INSTALL_SAM3="${INSTALL_SAM3:-1}"
SAM2_SOURCE="${SAM2_SOURCE:-https://github.com/facebookresearch/sam2.git}"
SAM3_SOURCE="${SAM3_SOURCE:-https://github.com/facebookresearch/sam3.git}"

if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    "$PYTHON_BIN" -m venv "$ENV_DIR"
fi

PYTHON="$ENV_DIR/bin/python"
PIP=("$PYTHON" -m pip)

"${PIP[@]}" install --upgrade pip setuptools wheel

if ! "$PYTHON" -c 'import torch, torchvision' >/dev/null 2>&1; then
    cat >&2 <<'EOF'
PyTorch and torchvision must be installed before JotMark.

Install a CUDA build that matches this computer from https://pytorch.org/get-started/locally/,
then re-run this script. For a CPU-only installation, run for example:

  ENV_DIR=.venv-jotmark
  "$ENV_DIR/bin/python" -m pip install torch torchvision

SAM3 itself still requires CUDA at runtime.
EOF
    exit 2
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
