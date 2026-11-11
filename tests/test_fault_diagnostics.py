"""Fatal-signal reporting is best effort and must not affect GUI startup."""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from jotmark.diagnostics import enable_fault_diagnostics


class FaultDiagnosticsTests(unittest.TestCase):
    def test_missing_stderr_does_not_prevent_startup(self):
        with patch("jotmark.diagnostics.sys.stderr", None):
            self.assertFalse(enable_fault_diagnostics())

    @unittest.skipUnless(os.name == "posix", "POSIX fatal-signal test")
    def test_abort_prints_python_stack_in_separate_process(self):
        program = (
            "import resource, os\n"
            "resource.setrlimit(resource.RLIMIT_CORE, (0, 0))\n"
            "from jotmark.diagnostics import enable_fault_diagnostics\n"
            "assert enable_fault_diagnostics()\n"
            "os.abort()\n"
        )
        result = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Fatal Python error: Aborted", result.stderr)
        self.assertIn('File "<string>"', result.stderr)


if __name__ == "__main__":
    unittest.main()
