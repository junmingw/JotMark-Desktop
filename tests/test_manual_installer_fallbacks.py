"""Installer branch tests; no package manager, network or GUI is invoked."""

from pathlib import Path
import subprocess
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def functions_only(filename, main_marker):
    source = (SCRIPTS / filename).read_text(encoding="utf-8")
    return "has_command() {" + source.split("has_command() {", 1)[1].split(main_marker, 1)[0]


def run_bash(script):
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"Bash failed ({result.returncode}): {result.stderr}")
    return result.stdout


class LinuxFallbackTests(unittest.TestCase):
    def test_package_managers_are_tried_in_order(self):
        functions = functions_only("install_manual_annotation.sh", "\nif ! find_supported_python; then")
        for success, expected in (
            ("apt-get", "apt-get update"),
            ("dnf", "dnf install"),
            ("yum", "yum install"),
            ("pacman", "pacman -S --needed"),
            ("zypper", "zypper --non-interactive"),
        ):
            with self.subTest(success=success):
                commands = '''
has_command() { [[ "$1" == sudo || "$1" == apt-get || "$1" == dnf || "$1" == yum || "$1" == pacman || "$1" == zypper ]]; }
sudo() { "$@"; }
apt-get() { echo "apt-get $*"; [[ "$success" == apt-get ]]; }
dnf() { echo "dnf $*"; [[ "$success" == dnf ]]; }
yum() { echo "yum $*"; [[ "$success" == yum ]]; }
pacman() { echo "pacman $*"; [[ "$success" == pacman ]]; }
zypper() { echo "zypper $*"; [[ "$success" == zypper ]]; }
install_system_prerequisites
'''
                output = run_bash('set -euo pipefail\nsuccess=' + success + '\n' + functions + commands)
                self.assertIn(expected, output)

    def test_missing_package_managers_returns_to_user_fallback(self):
        functions = functions_only("install_manual_annotation.sh", "\nif ! find_supported_python; then")
        output = run_bash(functions + '\nhas_command() { return 1; }\nif install_system_prerequisites; then exit 1; fi\necho fallback\n')
        self.assertIn("fallback", output)


class MacFallbackTests(unittest.TestCase):
    def test_brew_failure_calls_signed_package_fallback(self):
        functions = functions_only("install_manual_annotation_macos.sh", "\nensure_supported_python\n")
        commands = '''
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
find_supported_python() { [[ "${pkg_ready:-0}" == 1 ]]; }
ensure_homebrew() { return 0; }
brew() { return 1; }
install_signed_python_pkg() { pkg_ready=1; echo signed-pkg; }
ensure_supported_python
'''
        self.assertIn("signed-pkg", run_bash('set -euo pipefail\n' + functions + commands))

    def test_existing_python_skips_installation(self):
        functions = functions_only("install_manual_annotation_macos.sh", "\nensure_supported_python\n")
        commands = '''
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
find_supported_python() { return 0; }
ensure_homebrew() { echo unexpected; return 1; }
install_signed_python_pkg() { echo unexpected; return 1; }
ensure_supported_python
echo existing
'''
        self.assertEqual("existing", run_bash('set -euo pipefail\n' + functions + commands).strip())

    def test_mirror_failure_uses_signed_official_pkg(self):
        functions = functions_only("install_manual_annotation_macos.sh", "\nensure_supported_python\n")
        commands = '''
curl() {
    local url="${*: -1}" dest="" previous="" word
    if [[ "$1" == -fsSLI ]]; then
        printf 'HTTP/2 200\\r\\ncontent-length: 45720356\\r\\n'
        return 0
    fi
    if [[ "$1" == -sSL ]]; then
        printf '0.10'
        return 0
    fi
    for word in "$@"; do
        if [[ "$previous" == -o ]]; then dest="$word"; fi
        previous="$word"
    done
    if [[ "$url" != https://www.python.org/* ]]; then
        echo mirror-failed >&2
        return 22
    fi
    echo official-downloaded >&2
    truncate -s 45720356 "$dest"
}
pkgutil() { echo 'Developer ID Installer: Python Software Foundation (BMM5U3QVKW)'; }
sudo() { [[ "$1" == /usr/sbin/installer ]]; }
install_signed_python_pkg
'''
        result = subprocess.run(["bash", "-c", 'set -euo pipefail\n' + functions + commands],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mirror-failed", result.stderr)
        self.assertIn("official-downloaded", result.stderr)


if __name__ == "__main__":
    unittest.main()
