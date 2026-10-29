"""Shared runtime device policy for desktop inference and batch annotation.

The default is GPU whenever PyTorch reports CUDA availability.  Operators can
set JOTMARK_DEVICE=cpu before launching the application to free VRAM for a
training task without changing source code.
"""

from __future__ import annotations

import os
from contextlib import nullcontext

import torch


def resolve_device() -> str:
    requested = os.environ.get("JOTMARK_DEVICE", os.environ.get("LABELPAW_DEVICE", "auto")).strip().lower()
    if requested not in {"auto", "cuda", "cpu"}:
        raise RuntimeError("JOTMARK_DEVICE 仅支持 auto、cuda 或 cpu。")
    if requested == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    # Explicit cuda remains safe for unattended deployments: it falls back to
    # CPU rather than making the desktop application impossible to start.
    return "cpu"


def inference_context(device: str):
    """Use the lowest broadly supported CUDA autocast precision.

    Ampere (compute capability 8.0+) supports BF16 efficiently.  Turing GPUs
    such as the RTX 2080 Ti do not; requesting BF16 there can keep larger
    temporary buffers alive during SAM3 image encoding.  FP16 is natively
    supported on those cards and substantially reduces peak activation memory.
    """
    if device == "cuda":
        try:
            capability_major, _ = torch.cuda.get_device_capability(0)
            dtype = torch.bfloat16 if capability_major >= 8 else torch.float16
        except Exception:
            # CUDA was selected but querying properties unexpectedly failed.
            # Keep the historical BF16 default rather than preventing startup.
            dtype = torch.bfloat16
        return torch.autocast(device_type="cuda", dtype=dtype)
    return nullcontext()


def device_label(device: str) -> str:
    if device == "cuda":
        try:
            return f"GPU: {torch.cuda.get_device_name(0)}"
        except Exception:
            return "GPU"
    return "CPU"
