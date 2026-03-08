#!/usr/bin/env python3
"""
kiro-gateway interactive installer (user-level)
Deploys kiro-gateway as a systemd user service with auto-verification.

No root/sudo required. Installs to ~/.local/share/kiro-gateway.
Uses systemctl --user for service management.

Usage:
    python3 install.py              # Install
    python3 install.py --uninstall  # Uninstall
    python3 install.py --hello      # Test model connectivity
"""

import getpass
import os
import secrets
import shutil
import string
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ── Constants ─────────────────────────────────────────────────────────────────

INSTALL_DIR = Path.home() / ".local" / "share" / "kiro-gateway"
SERVICE_NAME = "kiro-gateway"
SERVICE_DIR = Path.home() / ".config" / "systemd" / "user"
SERVICE_FILE = SERVICE_DIR / f"{SERVICE_NAME}.service"
VENV_DIR = INSTALL_DIR / "venv"
ENV_FILE = INSTALL_DIR / ".env"
SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SCRIPT_DIR / "kiro-gateway"


# ── Colored output ────────────────────────────────────────────────────────────


class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def info(msg: str):
    print(f"  {C.CYAN}ℹ{C.RESET}  {msg}")


def ok(msg: str):
    print(f"  {C.GREEN}✔{C.RESET}  {msg}")


def warn(msg: str):
    print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")


def err(msg: str):
    print(f"  {C.RED}✘{C.RESET}  {msg}")


def fatal(msg: str):
    err(msg)
    sys.exit(1)


def header(msg: str):
    print(f"\n{C.BOLD}{C.CYAN}{'─' * 60}{C.RESET}")
    print(f"{C.BOLD}  {msg}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'─' * 60}{C.RESET}\n")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    result = input(f"  {C.YELLOW}?{C.RESET}  {prompt}{suffix}: ").strip()
    return result if result else default


def ask_yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    result = input(f"  {C.YELLOW}?{C.RESET}  {prompt} ({hint}): ").strip().lower()
    if not result:
        return default
    return result in ("y", "yes")


def ask_password(prompt: str) -> str:
    return getpass.getpass(f"  {C.YELLOW}?{C.RESET}  {prompt}: ")


def run(
    cmd: list[str], check: bool = True, capture: bool = True, **kwargs
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, **kwargs)


def run_live(cmd: list[str], check: bool = True) -> int:
    """Run a command with real-time stdout/stderr output. Returns exit code."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        print(f"    {C.DIM}{line.rstrip()}{C.RESET}")
    proc.wait()
    if check and proc.returncode != 0:
        fatal(f"Command failed (exit {proc.returncode}): {' '.join(cmd)}")
    return proc.returncode


# ── Step 1: Environment checks ───────────────────────────────────────────────


def check_not_root():
    if os.geteuid() == 0:
        fatal(
            "Do not run as root. This installer uses systemd --user services.\n"
            "     Run as your normal user: python3 install.py"
        )


def check_python() -> str:
    if sys.version_info < (3, 10):
        fatal(f"Python 3.10+ required, current: {sys.version}")
    try:
        import venv  # noqa: F401
    except ImportError:
        fatal(
            "Missing venv module. Install: sudo apt install python3-venv (Debian/Ubuntu) "
            "or sudo dnf install python3-devel (Fedora)"
        )
    ok(f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    return sys.executable


def check_systemd():
    if shutil.which("systemctl") is None:
        fatal("systemd not detected. This installer only supports systemd-based systems.")
    # Check that user session is available
    result = run(["systemctl", "--user", "status"], check=False)
    if result.returncode not in (0, 1, 3):
        # returncode 3 = "no units" which is fine
        warn("systemd --user may not be fully available. Continuing anyway.")
    ok("systemd --user available")


def check_lingering():
    """Enable lingering so user services start on boot without login."""
    user = os.environ.get("USER", getpass.getuser())
    result = run(["loginctl", "show-user", user, "--property=Linger"], check=False)
    if "Linger=yes" not in result.stdout:
        info(f"Enabling lingering for user {user} (needed for boot auto-start) ...")
        rc = run_live(["loginctl", "enable-linger", user], check=False)
        if rc != 0:
            warn(
                "Failed to enable lingering. Service may not auto-start on boot.\n"
                f"       Run manually: sudo loginctl enable-linger {user}"
            )
        else:
            ok("Lingering enabled")
    else:
        ok("Lingering already enabled")


def check_source():
    if not SOURCE_DIR.is_dir() or not (SOURCE_DIR / "main.py").is_file():
        fatal(
            f"kiro-gateway source not found at: {SOURCE_DIR}\n"
            f"     Make sure you cloned with: git clone --recurse-submodules ..."
        )
    ok(f"Source directory: {SOURCE_DIR}")


def step_check_env():
    header("Step 1/5 · Environment checks")
    check_not_root()
    ok(f"Running as user: {getpass.getuser()}")
    python_path = check_python()
    check_systemd()
    check_lingering()
    check_source()
    return python_path


# ── Step 2: Deploy code ──────────────────────────────────────────────────────


def step_deploy_code(python_path: str):
    header("Step 2/5 · Deploy code & dependencies")

    if INSTALL_DIR.exists():
        if not ask_yes(f"{INSTALL_DIR} already exists. Overwrite code? (.env will be preserved)"):
            info("Skipping code deployment")
            return

    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    dest_src = INSTALL_DIR / "kiro-gateway"
    if dest_src.exists():
        shutil.rmtree(dest_src)

    info("Copying source code ...")
    shutil.copytree(
        SOURCE_DIR,
        dest_src,
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".env", "debug_logs", "tests", ".github"
        ),
    )
    ok("Source code copied")

    info("Creating Python virtual environment ...")
    if VENV_DIR.exists():
        shutil.rmtree(VENV_DIR)
    run_live([python_path, "-m", "venv", str(VENV_DIR)])
    ok("Virtual environment created")

    pip = str(VENV_DIR / "bin" / "pip")
    req_file = dest_src / "requirements.txt"

    info("Installing pip dependencies ...")
    rc = run_live([pip, "install", "-r", str(req_file)], check=False)
    if rc != 0:
        fatal("Dependency installation failed. Check your network or pip mirror config.")
    ok("Dependencies installed")

    # Verify: can we import fastapi?
    py = str(VENV_DIR / "bin" / "python")
    result = run([py, "-c", "import fastapi; print(fastapi.__version__)"], check=False)
    if result.returncode != 0:
        fatal("Verification failed: cannot import fastapi")
    ok(f"Verified: fastapi {result.stdout.strip()}")

    # Patch: Q API endpoint (q.{region}.amazonaws.com) only exists in us-east-1,
    # but credentials files may contain other regions (e.g. ap-southeast-1).
    # The refresh URL must still use the credentials region (auth works per-region),
    # so we only pin api_host and q_host to us-east-1.
    auth_py = dest_src / "kiro" / "auth.py"
    if auth_py.exists():
        original = auth_py.read_text()
        old_block = (
            "            if 'region' in data:\n"
            "                self._region = data['region']\n"
            "                # Update URLs for new region\n"
            "                self._refresh_url = get_kiro_refresh_url(self._region)\n"
            "                self._api_host = get_kiro_api_host(self._region)\n"
            "                self._q_host = get_kiro_q_host(self._region)"
        )
        new_block = (
            "            if 'region' in data:\n"
            "                self._region = data['region']\n"
            "                # Update refresh URLs for credential region\n"
            "                self._refresh_url = get_kiro_refresh_url(self._region)\n"
            "                # Pin API host to us-east-1 (Q API only exists there)\n"
            "                self._api_host = get_kiro_api_host('us-east-1')\n"
            "                self._q_host = get_kiro_q_host('us-east-1')"
        )
        if old_block in original:
            auth_py.write_text(original.replace(old_block, new_block, 1))
            ok("Patched: API host pinned to us-east-1, refresh URL uses credential region")


# ── Step 3: Interactive configuration ─────────────────────────────────────────


def generate_api_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "kg-" + "".join(secrets.choice(alphabet) for _ in range(32))


def detect_credentials() -> dict:
    """
    Scan the current user's home for known Kiro credential files.
    Returns {"default_choice": "1"-"4", "detected_file": path_or_None}.
    Priority: Kiro IDE JSON > kiro-cli SQLite > AWS SSO cache.
    """
    home = Path.home()

    # Option 1: Kiro IDE JSON
    kiro_json = home / ".aws" / "sso" / "cache" / "kiro-auth-token.json"
    if kiro_json.is_file():
        return {"default_choice": "1", "detected_file": str(kiro_json)}

    # Option 3: kiro-cli SQLite
    for db_path in [
        home / ".local" / "share" / "kiro-cli" / "data.sqlite3",
        home / ".local" / "share" / "amazon-q" / "data.sqlite3",
    ]:
        if db_path.is_file():
            return {"default_choice": "3", "detected_file": str(db_path)}

    # Option 4: AWS SSO cache (any JSON with accessToken + refreshToken)
    sso_cache = home / ".aws" / "sso" / "cache"
    if sso_cache.is_dir():
        import json

        for f in sorted(sso_cache.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                if "accessToken" in data and "refreshToken" in data:
                    return {"default_choice": "4", "detected_file": str(f)}
            except (json.JSONDecodeError, OSError):
                continue

    # Nothing found — default to option 1 (recommended)
    return {"default_choice": "1", "detected_file": None}


def step_configure():
    header("Step 3/5 · Configure .env")

    if ENV_FILE.exists():
        if not ask_yes("Existing .env found. Reconfigure?", default=False):
            ok("Keeping existing configuration")
            return

    env_lines: list[str] = []

    # ── PROXY_API_KEY ──
    print()
    info("PROXY_API_KEY is the password YOU set to access the gateway (not a Kiro token)")
    auto_key = generate_api_key()
    info(f"Auto-generated key: {C.DIM}{auto_key}{C.RESET}")

    if ask_yes("Use the auto-generated key?"):
        api_key = auto_key
    else:
        while True:
            api_key = ask_password("Enter your PROXY_API_KEY")
            if len(api_key) >= 8:
                break
            warn("Key must be at least 8 characters")

    env_lines.append(f'PROXY_API_KEY="{api_key}"')
    ok("PROXY_API_KEY set")

    # ── Credential source ──
    detected = detect_credentials()
    default_choice = detected["default_choice"]
    detected_file = detected["detected_file"]

    print()
    if detected_file:
        ok(f"Detected credentials: {detected_file}")
    print(f"  {C.BOLD}Select Kiro credential source:{C.RESET}")

    tag = lambda n: f" {C.GREEN}← detected{C.RESET}" if n == default_choice else ""  # noqa: E731
    print(
        f"    {C.CYAN}1{C.RESET}  Kiro IDE credentials file (JSON)  — recommended, easiest{tag('1')}"
    )
    print(
        f"    {C.CYAN}2{C.RESET}  Refresh Token (manual)             — extracted from IDE traffic{tag('2')}"
    )
    print(
        f"    {C.CYAN}3{C.RESET}  kiro-cli SQLite database            — for kiro-cli users{tag('3')}"
    )
    print(
        f"    {C.CYAN}4{C.RESET}  AWS SSO cache file                  — enterprise SSO users{tag('4')}"
    )
    print()

    while True:
        choice = ask("Choose (1-4)", default_choice)
        if choice in ("1", "2", "3", "4"):
            break
        warn("Please enter 1-4")

    if choice == "1":
        default_creds = detected_file if default_choice == "1" and detected_file else ""
        if not default_creds:
            default_creds = "~/.aws/sso/cache/kiro-auth-token.json"
        creds_path = ask("Credentials file path", default_creds)
        abs_path = str(Path(creds_path).expanduser().resolve())
        if not Path(abs_path).exists():
            warn(
                f"File {abs_path} not found. It will be created automatically after Kiro IDE login."
            )
        env_lines.append(f'KIRO_CREDS_FILE="{abs_path}"')
        ok(f"Credential source: JSON file ({abs_path})")

    elif choice == "2":
        token = ask_password("Enter your REFRESH_TOKEN")
        if not token:
            fatal("REFRESH_TOKEN cannot be empty")
        env_lines.append(f'REFRESH_TOKEN="{token}"')
        ok("Credential source: Refresh Token")

    elif choice == "3":
        default_db = detected_file if default_choice == "3" and detected_file else ""
        if not default_db:
            default_db = "~/.local/share/kiro-cli/data.sqlite3"
        db_path = ask("kiro-cli database path", default_db)
        abs_path = str(Path(db_path).expanduser().resolve())
        if not Path(abs_path).exists():
            warn(f"File {abs_path} not found. Make sure you ran: kiro-cli login")
        env_lines.append(f'KIRO_CLI_DB_FILE="{abs_path}"')
        ok(f"Credential source: SQLite ({abs_path})")

    elif choice == "4":
        default_sso = detected_file if default_choice == "4" and detected_file else ""
        if not default_sso:
            default_sso = "~/.aws/sso/cache/"
        creds_path = ask("AWS SSO cache file path", default_sso)
        abs_path = str(Path(creds_path).expanduser().resolve())
        env_lines.append(f'KIRO_CREDS_FILE="{abs_path}"')
        ok(f"Credential source: AWS SSO ({abs_path})")

    # ── Optional settings ──
    print()
    if ask_yes("Configure advanced options? (port, proxy, etc.)", default=False):
        port = ask("Listen port", "8000")
        env_lines.append(f'SERVER_PORT="{port}"')

        host = ask("Listen address (127.0.0.1=local only, 0.0.0.0=all interfaces)", "127.0.0.1")
        env_lines.append(f'SERVER_HOST="{host}"')

        proxy = ask("VPN/Proxy URL (leave empty to skip)", "")
        if proxy:
            env_lines.append(f'VPN_PROXY_URL="{proxy}"')

        region = ask("AWS region", "us-east-1")
        if region != "us-east-1":
            env_lines.append(f'KIRO_REGION="{region}"')
    else:
        env_lines.append('SERVER_HOST="127.0.0.1"')
        env_lines.append('SERVER_PORT="8000"')

    ENV_FILE.write_text("\n".join(env_lines) + "\n")
    os.chmod(str(ENV_FILE), 0o600)

    content = ENV_FILE.read_text()
    if "PROXY_API_KEY" not in content:
        fatal("Verification failed: PROXY_API_KEY missing from .env")
    ok(".env written with permissions 600")


# ── Step 4: Install systemd user service ─────────────────────────────────────


def get_env_value(key: str, default: str) -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return default


def step_install_service():
    header("Step 4/5 · Install systemd user service")

    working_dir = INSTALL_DIR / "kiro-gateway"
    python_bin = VENV_DIR / "bin" / "python"

    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Kiro Gateway - Proxy API gateway for Kiro IDE
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        WorkingDirectory={working_dir}
        EnvironmentFile={ENV_FILE}
        ExecStart={python_bin} main.py
        Restart=on-failure
        RestartSec=5
        StartLimitIntervalSec=60
        StartLimitBurst=3

        # Logging
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier={SERVICE_NAME}

        [Install]
        WantedBy=default.target
    """)

    SERVICE_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_FILE.write_text(unit)
    ok(f"Service file written: {SERVICE_FILE}")

    run(["systemctl", "--user", "daemon-reload"])
    ok("systemctl --user daemon-reload")

    # Enable for boot auto-start
    run(["systemctl", "--user", "enable", SERVICE_NAME], check=False)
    verify = run(["systemctl", "--user", "is-enabled", SERVICE_NAME], check=False)
    if verify.stdout.strip() == "enabled":
        ok("Boot auto-start enabled (systemctl --user is-enabled = enabled)")
    else:
        warn(f"systemctl --user is-enabled returned: {verify.stdout.strip()}")
        fatal("Failed to enable boot auto-start.")

    # Start the service
    info("Starting service ...")
    run(["systemctl", "--user", "restart", SERVICE_NAME], check=False)

    info("Waiting for service to start ...")
    for attempt in range(10):
        time.sleep(1)
        result = run(["systemctl", "--user", "is-active", SERVICE_NAME], check=False)
        state = result.stdout.strip()
        if state == "active":
            ok("Service is running (active)")
            return
        if state == "failed":
            break
        print(f"    {C.DIM}state: {state} ({attempt + 1}/10){C.RESET}")

    warn("Service is not active. Recent logs:")
    run_live(["journalctl", "--user-unit", SERVICE_NAME, "-n", "30", "--no-pager"], check=False)
    fatal("Service failed to start. See logs above.")


# ── Step 5: Health check ─────────────────────────────────────────────────────


def step_health_check():
    header("Step 5/5 · Health check")

    host = get_env_value("SERVER_HOST", "127.0.0.1")
    port = get_env_value("SERVER_PORT", "8000")
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/health"

    import urllib.error
    import urllib.request

    info(f"Polling {url}")

    for i in range(15):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    ok(f"Health check passed (HTTP {resp.status})")
                    return True
        except urllib.error.URLError as e:
            print(f"    {C.DIM}attempt {i + 1}/15 — {e.reason}{C.RESET}")
        except Exception as e:
            print(f"    {C.DIM}attempt {i + 1}/15 — {e}{C.RESET}")
        time.sleep(1)

    print()
    warn(
        "Health check timed out. The service may still be initializing "
        "(first start loads model list)."
    )
    warn(f"Check manually later: curl {url}")
    return False


# ── Uninstall ─────────────────────────────────────────────────────────────────


def uninstall():
    header("Uninstall kiro-gateway")

    if not ask_yes("Are you sure you want to uninstall kiro-gateway?", default=False):
        info("Cancelled")
        return

    run(["systemctl", "--user", "stop", SERVICE_NAME], check=False)
    run(["systemctl", "--user", "disable", SERVICE_NAME], check=False)
    ok("Service stopped and disabled")

    if SERVICE_FILE.exists():
        SERVICE_FILE.unlink()
        run(["systemctl", "--user", "daemon-reload"])
        ok("Service file removed")

    keep_env = False
    if ENV_FILE.exists():
        keep_env = ask_yes("Keep .env config file? (useful for reinstall)", default=True)

    if INSTALL_DIR.exists():
        if keep_env and ENV_FILE.exists():
            env_backup = Path("/tmp/kiro-gateway-env-backup")
            shutil.copy2(ENV_FILE, env_backup)
            shutil.rmtree(INSTALL_DIR)
            INSTALL_DIR.mkdir(parents=True)
            shutil.copy2(env_backup, ENV_FILE)
            env_backup.unlink()
            ok(f"Install directory cleaned, .env preserved at {ENV_FILE}")
        else:
            shutil.rmtree(INSTALL_DIR)
            ok("Install directory removed")

    print()
    ok("Uninstall complete")


# ── Summary ───────────────────────────────────────────────────────────────────


def print_summary():
    host = get_env_value("SERVER_HOST", "127.0.0.1")
    port = get_env_value("SERVER_PORT", "8000")
    display_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}"

    print(f"""
{C.GREEN}{C.BOLD}{'═' * 60}
  ✅ kiro-gateway installation complete
{'═' * 60}{C.RESET}

  Endpoint:     {C.CYAN}{url}{C.RESET}
  Health check: {C.DIM}curl {url}/health{C.RESET}
  Config file:  {C.DIM}{ENV_FILE}{C.RESET}
  Auto-start:   {C.GREEN}enabled (starts on boot){C.RESET}

  {C.BOLD}Useful commands:{C.RESET}
    Status     systemctl --user status {SERVICE_NAME}
    Logs       journalctl --user-unit {SERVICE_NAME} -f
    Restart    systemctl --user restart {SERVICE_NAME}
    Edit cfg   nano {ENV_FILE} && systemctl --user restart {SERVICE_NAME}
""")


def hello():
    """Send a test message to the gateway and print the model's reply."""
    header("Hello — test model connectivity")

    if not ENV_FILE.exists():
        fatal(f"Config not found: {ENV_FILE}\n     Run python3 install.py first.")

    host = get_env_value("SERVER_HOST", "127.0.0.1")
    port = get_env_value("SERVER_PORT", "8000")
    api_key = get_env_value("PROXY_API_KEY", "")
    base = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}"

    if not api_key:
        fatal("PROXY_API_KEY not found in .env")

    # Check service is running
    result = run(["systemctl", "--user", "is-active", SERVICE_NAME], check=False)
    if result.stdout.strip() != "active":
        fatal(f"Service is not running. Start it first: systemctl --user start {SERVICE_NAME}")

    # List models
    import json
    import urllib.error
    import urllib.request

    info("Fetching available models ...")
    try:
        req = urllib.request.Request(
            f"{base}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            models = [m["id"] for m in data.get("data", [])]
    except Exception as e:
        fatal(f"Failed to list models: {e}")

    if not models:
        fatal("No models available.")

    ok(f"Found {len(models)} model(s)")
    for m in models[:10]:
        print(f"    {C.DIM}{m}{C.RESET}")
    if len(models) > 10:
        print(f"    {C.DIM}... and {len(models) - 10} more{C.RESET}")

    # Pick model — prefer a concrete model over auto-kiro (which may 400 on non-stream)
    concrete = [m for m in models if not m.startswith("auto")]
    default_model = concrete[0] if concrete else models[0]
    print()
    model = ask("Model to use", default_model)

    # Send hello
    info(f"Sending 'Hello' to {model} ...")
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Hello! Reply in one short sentence."}],
            "stream": False,
        }
    ).encode()

    try:
        req = urllib.request.Request(
            f"{base}/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        fatal(f"API error (HTTP {e.code}): {body}")
    except Exception as e:
        fatal(f"Request failed: {e}")

    # Extract reply
    try:
        reply = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        fatal(f"Unexpected response format: {json.dumps(data, indent=2)}")

    print()
    ok(f"Model: {model}")
    print(f"\n  {C.GREEN}💬 {reply}{C.RESET}\n")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print(f"\n{C.BOLD}  🚀 kiro-gateway installer{C.RESET}\n")

    if "--uninstall" in sys.argv:
        uninstall()
        return

    if "--hello" in sys.argv:
        hello()
        return

    python_path = step_check_env()
    step_deploy_code(python_path)
    step_configure()
    step_install_service()
    step_health_check()
    print_summary()


if __name__ == "__main__":
    main()
