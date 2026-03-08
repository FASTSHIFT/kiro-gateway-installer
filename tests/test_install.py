#!/usr/bin/env python3
"""
Integration tests for kiro-gateway installer.

These tests run INSIDE a systemd-enabled Docker container.
They exercise the full install/uninstall flow non-interactively
by feeding scripted input to the installer.

Run locally:  python3 tests/run_tests.py
Run in CI:    see .github/workflows/test.yml
"""

import subprocess
import time
import unittest
from pathlib import Path

INSTALL_DIR = Path("/opt/kiro-gateway")
SERVICE_NAME = "kiro-gateway"
SERVICE_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
ENV_FILE = INSTALL_DIR / ".env"
VENV_DIR = INSTALL_DIR / "venv"


def run(
    cmd: str, input_text: str = "", check: bool = True, timeout: int = 120
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


def wait_for_systemd(timeout: int = 30):
    """Wait until systemd is fully booted inside the container."""
    for _ in range(timeout):
        r = run("systemctl is-system-running", check=False)
        state = r.stdout.strip()
        if state in ("running", "degraded"):
            return
        time.sleep(1)
    raise RuntimeError(f"systemd did not reach running state, got: {state}")


class TestInstall(unittest.TestCase):
    """Test the full installation flow."""

    @classmethod
    def setUpClass(cls):
        wait_for_systemd()

    def test_01_install(self):
        """Run installer with scripted input (non-interactive)."""
        # Input sequence:
        #   line 1: Use auto-generated API key? -> Y
        #   line 2: Choose credential source -> 2 (refresh token)
        #   line 3: Enter REFRESH_TOKEN -> fake-test-token-12345
        #   line 4: Configure advanced options? -> n
        stdin = "y\n2\nfake-test-token-12345\nn\n"

        result = run("python3 /workspace/install.py", input_text=stdin, check=False)
        print("=== INSTALL STDOUT ===")
        print(result.stdout)
        if result.stderr:
            print("=== INSTALL STDERR ===")
            print(result.stderr)

        # The service will fail to actually serve (no real token),
        # but the installation steps up to service creation should succeed.
        # We check structural correctness, not runtime connectivity.

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

    def test_06_service_user(self):
        """Service user should exist with nologin shell."""
        result = run("id kiro-gateway")
        self.assertIn("kiro-gateway", result.stdout)

        result = run("getent passwd kiro-gateway")
        self.assertIn("nologin", result.stdout)

    def test_07_service_enabled(self):
        """Service should be enabled for boot auto-start."""
        result = run(f"systemctl is-enabled {SERVICE_NAME}", check=False)
        self.assertEqual(
            result.stdout.strip(), "enabled", "Service should be enabled for auto-start on boot"
        )

    def test_08_service_file_content(self):
        """Service file should contain security hardening directives."""
        content = SERVICE_FILE.read_text()
        for directive in [
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "PrivateTmp=true",
            "WantedBy=multi-user.target",
        ]:
            self.assertIn(directive, content, f"Service file should contain {directive}")

    def test_09_directory_permissions(self):
        """Install directory should have restricted permissions."""
        stat = INSTALL_DIR.stat()
        mode = oct(stat.st_mode)[-3:]
        self.assertIn(mode, ("750", "755"), f"Install dir permissions should be 750, got {mode}")


class TestUninstall(unittest.TestCase):
    """Test the uninstall flow."""

    def test_10_uninstall(self):
        """Run uninstaller with scripted input."""
        # Input sequence:
        #   line 1: Are you sure? -> y
        #   line 2: Keep .env? -> n
        #   line 3: Remove user? -> y
        stdin = "y\nn\ny\n"

        result = run("python3 /workspace/install.py --uninstall", input_text=stdin, check=False)
        print("=== UNINSTALL STDOUT ===")
        print(result.stdout)

    def test_11_cleaned_up(self):
        """Verify everything was removed."""
        self.assertFalse(SERVICE_FILE.exists(), "Service file should be removed")
        self.assertFalse(INSTALL_DIR.exists(), "Install directory should be removed")

        result = run("id kiro-gateway", check=False)
        self.assertNotEqual(result.returncode, 0, "User should be removed")

        result = run(f"systemctl is-enabled {SERVICE_NAME}", check=False)
        self.assertNotEqual(result.stdout.strip(), "enabled", "Service should not be enabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
