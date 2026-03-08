#!/usr/bin/env python3
"""
Integration tests for kiro-gateway installer (user-level).

These tests run INSIDE a systemd-enabled Docker container as a non-root user.
They exercise the full install/uninstall flow non-interactively
by feeding scripted input to the installer.

Run locally:  python3 tests/run_tests.py
Run in CI:    see .github/workflows/test.yml
"""

import subprocess
import time
import unittest
from pathlib import Path

HOME = Path.home()
INSTALL_DIR = HOME / ".local" / "share" / "kiro-gateway"
SERVICE_NAME = "kiro-gateway"
SERVICE_FILE = HOME / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"
ENV_FILE = INSTALL_DIR / ".env"
VENV_DIR = INSTALL_DIR / "venv"


def run(
    cmd: str, input_text: str = "", check: bool = True, timeout: int = 180
) -> subprocess.CompletedProcess:
    """Run a shell command with optional stdin."""
    return subprocess.run(
        cmd,
        shell=True,
        text=True,
        input=input_text,
        capture_output=True,
        check=check,
        timeout=timeout,
    )


def wait_for_systemd_user(timeout: int = 30):
    """Wait until systemd --user is ready."""
    state = ""
    for _ in range(timeout):
        r = run("systemctl --user is-system-running", check=False)
        state = r.stdout.strip()
        if state in ("running", "degraded"):
            return
        time.sleep(1)
    raise RuntimeError(f"systemd --user did not reach running state, got: {state}")


def assert_clean_state(tc: unittest.TestCase):
    """Assert that kiro-gateway is fully uninstalled with zero residue."""
    tc.assertFalse(SERVICE_FILE.exists(), "Service file should be removed")
    tc.assertFalse(INSTALL_DIR.exists(), "Install directory should be removed")

    r = run(f"systemctl --user is-enabled {SERVICE_NAME}", check=False)
    tc.assertNotEqual(r.stdout.strip(), "enabled", "Service should not be enabled")

    r = run(f"systemctl --user is-active {SERVICE_NAME}", check=False)
    tc.assertNotEqual(r.stdout.strip(), "active", "Service should not be active")

    r = run(f"systemctl --user cat {SERVICE_NAME}", check=False)
    tc.assertNotEqual(r.returncode, 0, "systemctl cat should fail (unit not found)")

    tc.assertFalse(
        Path("/tmp/kiro-gateway-env-backup").exists(),
        "Temp .env backup should be cleaned up",
    )


class TestInstallRefreshToken(unittest.TestCase):
    """Test full install → verify → uninstall cycle with option 2 (refresh token)."""

    @classmethod
    def setUpClass(cls):
        wait_for_systemd_user()

    def test_01_install(self):
        """Run installer with scripted input (refresh token)."""
        # Input: auto key=Y, source=2, token=fake-test-token, advanced=n
        stdin = "y\n2\nfake-test-token-12345\nn\n"
        result = run("python3 /workspace/install.py", input_text=stdin, check=False)
        print("=== INSTALL STDOUT ===")
        print(result.stdout)
        if result.stderr:
            print("=== INSTALL STDERR ===")
            print(result.stderr)

    def test_02_files_exist(self):
        """Verify all expected files were created."""
        self.assertTrue(INSTALL_DIR.is_dir(), f"{INSTALL_DIR} should exist")
        self.assertTrue(
            (INSTALL_DIR / "kiro-gateway" / "main.py").is_file(), "main.py should exist"
        )
        self.assertTrue(VENV_DIR.is_dir(), "venv should exist")
        self.assertTrue((VENV_DIR / "bin" / "python").is_file(), "venv python should exist")
        self.assertTrue(ENV_FILE.is_file(), ".env should exist")
        self.assertTrue(SERVICE_FILE.is_file(), "systemd service file should exist")

    def test_03_env_permissions(self):
        """.env should be readable only by owner (600)."""
        stat = ENV_FILE.stat()
        mode = oct(stat.st_mode)[-3:]
        self.assertEqual(mode, "600", f".env permissions should be 600, got {mode}")

    def test_04_env_content(self):
        """.env should contain required fields."""
        content = ENV_FILE.read_text()
        self.assertIn("PROXY_API_KEY=", content)
        self.assertIn("REFRESH_TOKEN=", content)
        self.assertIn("SERVER_HOST=", content)

    def test_05_venv_fastapi(self):
        """Virtual environment should have fastapi installed."""
        py = str(VENV_DIR / "bin" / "python")
        result = run(f"{py} -c 'import fastapi; print(fastapi.__version__)'")
        self.assertTrue(len(result.stdout.strip()) > 0, "fastapi version should be printed")

    def test_06_service_enabled(self):
        """Service should be enabled for boot auto-start."""
        result = run(f"systemctl --user is-enabled {SERVICE_NAME}", check=False)
        self.assertEqual(
            result.stdout.strip(), "enabled", "Service should be enabled for auto-start on boot"
        )

    def test_07_service_file_content(self):
        """Service file should contain expected directives."""
        content = SERVICE_FILE.read_text()
        for directive in [
            "WantedBy=default.target",
            "Restart=on-failure",
            "EnvironmentFile=",
        ]:
            self.assertIn(directive, content, f"Service file should contain {directive}")

    def test_08_uninstall(self):
        """Run uninstaller and verify zero residue."""
        # Input: sure=y, keep .env=n
        stdin = "y\nn\n"
        result = run("python3 /workspace/install.py --uninstall", input_text=stdin, check=False)
        print("=== UNINSTALL STDOUT ===")
        print(result.stdout)

    def test_09_cleaned_up(self):
        """Verify everything was removed — zero residue."""
        assert_clean_state(self)


class TestCredsPathExpansion(unittest.TestCase):
    """Test that ~ in credential paths gets expanded to absolute paths."""

    @classmethod
    def setUpClass(cls):
        wait_for_systemd_user()

    def test_10_install_with_tilde_path(self):
        """Install using option 1 with a ~ path and verify expansion."""
        # Create a fake credential file
        fake_creds = HOME / "fake-kiro-creds.json"
        fake_creds.write_text('{"accessToken":"x","refreshToken":"y"}')

        # Input: auto key=Y, source=1, path=~/fake-kiro-creds.json, advanced=n
        stdin = "y\n1\n~/fake-kiro-creds.json\nn\n"
        result = run("python3 /workspace/install.py", input_text=stdin, check=False)
        print("=== CREDS PATH INSTALL STDOUT ===")
        print(result.stdout)

    def test_11_env_has_absolute_path(self):
        """.env should contain an absolute path, not ~."""
        self.assertTrue(ENV_FILE.is_file(), ".env should exist")
        content = ENV_FILE.read_text()
        self.assertIn("KIRO_CREDS_FILE=", content)

        for line in content.splitlines():
            if line.startswith("KIRO_CREDS_FILE="):
                value = line.split("=", 1)[1].strip().strip('"')
                self.assertFalse(
                    value.startswith("~"),
                    f"KIRO_CREDS_FILE should be absolute, got: {value}",
                )
                self.assertTrue(
                    value.startswith("/"),
                    f"KIRO_CREDS_FILE should start with /, got: {value}",
                )
                break

    def test_12_uninstall_and_verify(self):
        """Uninstall and verify zero residue."""
        # Input: sure=y, keep .env=n
        stdin = "y\nn\n"
        result = run("python3 /workspace/install.py --uninstall", input_text=stdin, check=False)
        print("=== CREDS PATH UNINSTALL STDOUT ===")
        print(result.stdout)

        assert_clean_state(self)


if __name__ == "__main__":
    unittest.main(verbosity=2)
