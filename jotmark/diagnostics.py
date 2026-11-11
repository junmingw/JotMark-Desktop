"""Best-effort fatal-signal diagnostics for terminal-launched applications."""

import faulthandler
import sys


def enable_fault_diagnostics():
    """Print Python thread stacks on fatal signals when stderr is available.

    This does not recover from native crashes or guarantee a native stack.
    GUI launchers without a usable stderr must continue to start normally.
    """
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
    except (OSError, RuntimeError, ValueError, AttributeError) as exc:
        if sys.stderr is not None:
            try:
                print(f"无法启用崩溃堆栈诊断：{exc}", file=sys.stderr)
            except (OSError, ValueError):
                pass
        return False
    return True
