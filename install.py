#!/usr/bin/env python3
"""
kiro-gateway interactive installer
Deploys kiro-gateway as a systemd service with auto-verification and security hardening.

Usage:
    sudo python3 install.py              # Install
    sudo python3 install.py --uninstall  # Uninstall
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

INSTALL_DIR = Path("/opt/kiro-gateway")
SERVICE_NAME = "kiro-gateway"
SERVICE_FILE = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
SERVICE_USER = "kiro-gateway"
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


# ── Step 1: Environment checks ───────────────────────────────────────────────


def check_root():
    if os.geteuid() != 0:
        fatal("Please run with sudo: sudo python3 install.py")


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
    ok("systemd available")


def check_source():
    if not SOURCE_DIR.is_dir() or not (SOURCE_DIR / "main.py").is_file():
        fatal(
            f"kiro-gateway source not found at: {SOURCE_DIR}\n"
            f"     Make sure you cloned with: git clone --recurse-submodules ..."
        )
    ok(f"Source directory: {SOURCE_DIR}")


def step_check_env():
    header("Step 1/6 · Environment checks")
    check_root()
    ok("Root privileges")
    python_path = check_python()
    check_systemd()
    check_source()
    return python_path


# ── Step 2: Create service user ───────────────────────────────────────────────


def step_create_user():
    header("Step 2/6 · Create service user")
    try:
        run(["id", SERVICE_USER])
        ok(f"User {SERVICE_USER} already exists")
    except subprocess.CalledProcessError:
        info(f"Creating system user {SERVICE_USER} ...")
        run(
            [
                "useradd",
                "--system",
                "--shell",
                "/usr/sbin/nologin",
                "--home-dir",
                str(INSTALL_DIR),
                "--no-create-home",
                SERVICE_USER,
            ]
        )
        run(["id", SERVICE_USER])
        ok(f"User {SERVICE_USER} created")


# ── Step 3: Deploy code ──────────────────────────────────────────────────────


def step_deploy_code(python_path: str):
    header("Step 3/6 · Deploy code & dependencies")

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
    run([python_path, "-m", "venv", str(VENV_DIR)])
    ok("Virtual environment created")

    pip = str(VENV_DIR / "bin" / "pip")
    req_file = dest_src / "requirements.txt"

    info("Installing pip dependencies (this may take a moment) ...")
    result = run([pip, "install", "-r", str(req_file)], check=False)
    if result.returncode != 0:
        err("pip install failed:")
        print(result.stderr)
        fatal("Dependency installation failed. Check your network or pip mirror config.")
    ok("Dependencies installed")

    # Verify: can we import fastapi?
    py = str(VENV_DIR / "bin" / "python")
    result = run([py, "-c", "import fastapi; print(fastapi.__version__)"], check=False)
    if result.returncode != 0:
        fatal("Verification failed: cannot import fastapi")
    ok(f"Verified: fastapi {result.stdout.strip()}")

    run(["chown", "-R", f"{SERVICE_USER}:{SERVICE_USER}", str(INSTALL_DIR)])
    run(["chmod", "750", str(INSTALL_DIR)])
    ok("Directory permissions set")


# ── Step 4: Interactive configuration ─────────────────────────────────────────


def generate_api_key() -> str:
    alphabet = string.ascii_letters + string.digits
    return "kg-" + "".join(secrets.choice(alphabet) for _ in range(32))


def step_configure():
    header("Step 4/6 · Configure .env")

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
    print()
    print(f"  {C.BOLD}Select Kiro credential source:{C.RESET}")
    print(f"    {C.CYAN}1{C.RESET}  Kiro IDE credentials file (JSON)  — recommended, easiest")
    print(
        f"    {C.CYAN}2{C.RESET}  Refresh Token (manual)             — extracted from IDE traffic"
    )
    print(f"    {C.CYAN}3{C.RESET}  kiro-cli SQLite database            — for kiro-cli users")
    print(f"    {C.CYAN}4{C.RESET}  AWS SSO cache file                  — enterprise SSO users")
    print()

    while True:
        choice = ask("Choose (1-4)", "1")
        if choice in ("1", "2", "3", "4"):
            break
        warn("Please enter 1-4")

    if choice == "1":
        default_creds = "~/.aws/sso/cache/kiro-auth-token.json"
        creds_path = ask("Credentials file path", default_creds)
        expanded = Path(creds_path).expanduser()
        if not expanded.exists():
            warn(
                f"File {expanded} not found. It will be created automatically after Kiro IDE login."
            )
        env_lines.append(f'KIRO_CREDS_FILE="{creds_path}"')
        ok(f"Credential source: JSON file ({creds_path})")

    elif choice == "2":
        token = ask_password("Enter your REFRESH_TOKEN")
        if not token:
            fatal("REFRESH_TOKEN cannot be empty")
        env_lines.append(f'REFRESH_TOKEN="{token}"')
        ok("Credential source: Refresh Token")

    elif choice == "3":
        default_db = "~/.local/share/kiro-cli/data.sqlite3"
        db_path = ask("kiro-cli database path", default_db)
        expanded = Path(db_path).expanduser()
        if not expanded.exists():
            warn(f"File {expanded} not found. Make sure you ran: kiro-cli login")
        env_lines.append(f'KIRO_CLI_DB_FILE="{db_path}"')
        ok(f"Credential source: SQLite ({db_path})")

    elif choice == "4":
        creds_path = ask("AWS SSO cache file path", "~/.aws/sso/cache/")
        env_lines.append(f'KIRO_CREDS_FILE="{creds_path}"')
        ok(f"Credential source: AWS SSO ({creds_path})")

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
    run(["chown", f"{SERVICE_USER}:{SERVICE_USER}", str(ENV_FILE)])
    run(["chmod", "600", str(ENV_FILE)])

    content = ENV_FILE.read_text()
    if "PROXY_API_KEY" not in content:
        fatal("Verification failed: PROXY_API_KEY missing from .env")
    ok(".env written with permissions 600")


# ── Step 5: Install systemd service ──────────────────────────────────────────


def get_env_value(key: str, default: str) -> str:
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return default


def step_install_service():
    header("Step 5/6 · Install systemd service")

    working_dir = INSTALL_DIR / "kiro-gateway"
    python_bin = VENV_DIR / "bin" / "python"

    unit = textwrap.dedent(f"""\
        [Unit]
        Description=Kiro Gateway - Proxy API gateway for Kiro IDE
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        User={SERVICE_USER}
        Group={SERVICE_USER}
        WorkingDirectory={working_dir}
        EnvironmentFile={ENV_FILE}
        ExecStart={python_bin} main.py
        Restart=on-failure
        RestartSec=5
        StartLimitIntervalSec=60
        StartLimitBurst=3

        # Security hardening
        NoNewPrivileges=true
        ProtectSystem=strict
        ProtectHome=read-only
        PrivateTmp=true
        ReadWritePaths={INSTALL_DIR}

        # Logging
        StandardOutput=journal
        StandardError=journal
        SyslogIdentifier={SERVICE_NAME}

        [Install]
        WantedBy=multi-user.target
    """)

    SERVICE_FILE.write_text(unit)
    ok(f"Service file written: {SERVICE_FILE}")

    run(["systemctl", "daemon-reload"])
    ok("systemctl daemon-reload")

    # Enable for boot auto-start
    result = run(["systemctl", "enable", SERVICE_NAME], check=False)
    # Verify enable actually worked
    verify = run(["systemctl", "is-enabled", SERVICE_NAME], check=False)
    if verify.stdout.strip() == "enabled":
        ok("Boot auto-start enabled (systemctl is-enabled = enabled)")
    else:
        warn(f"systemctl is-enabled returned: {verify.stdout.strip()}")
        fatal("Failed to enable boot auto-start. Check systemd configuration.")

    # Start the service
    info("Starting service ...")
    run(["systemctl", "restart", SERVICE_NAME], check=False)

    time.sleep(2)
    result = run(["systemctl", "is-active", SERVICE_NAME], check=False)
    if result.stdout.strip() == "active":
        ok("Service is running (active)")
    else:
        warn("Service is not active. Fetching logs ...")
        log_result = run(["journalctl", "-u", SERVICE_NAME, "-n", "20", "--no-pager"], check=False)
        print(log_result.stdout)
        fatal("Service failed to start. See logs above.")


# ── Step 6: Health check ─────────────────────────────────────────────────────


def step_health_check():
    header("Step 6/6 · Health check")

    host = get_env_value("SERVER_HOST", "127.0.0.1")
    port = get_env_value("SERVER_PORT", "8000")
    url = f"http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}/health"

    info(f"Waiting for service: {url}")

    for i in range(15):
        try:
            import urllib.request

            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    ok(f"Health check passed (HTTP {resp.status})")
                    return True
        except Exception:
            pass
        time.sleep(1)
        print(f"  {C.DIM}  Waiting ... ({i+1}/15){C.RESET}", end="\r")

    print()
    warn(
        "Health check timed out. The service may still be initializing (first start loads model list)."
    )
    warn(f"Check manually later: curl {url}")
    return False


# ── Uninstall ─────────────────────────────────────────────────────────────────


def uninstall():
    header("Uninstall kiro-gateway")
    check_root()

    if not ask_yes("Are you sure you want to uninstall kiro-gateway?", default=False):
        info("Cancelled")
        return

    run(["systemctl", "stop", SERVICE_NAME], check=False)
    run(["systemctl", "disable", SERVICE_NAME], check=False)
    ok("Service stopped and disabled")

    if SERVICE_FILE.exists():
        SERVICE_FILE.unlink()
        run(["systemctl", "daemon-reload"])
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

    if ask_yes("Remove system user kiro-gateway?", default=True):
        run(["userdel", SERVICE_USER], check=False)
        ok("User removed")

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
    Status     sudo systemctl status {SERVICE_NAME}
    Logs       sudo journalctl -u {SERVICE_NAME} -f
    Restart    sudo systemctl restart {SERVICE_NAME}
    Edit cfg   sudo nano {ENV_FILE} && sudo systemctl restart {SERVICE_NAME}
""")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    print(f"\n{C.BOLD}  🚀 kiro-gateway installer{C.RESET}\n")

    if "--uninstall" in sys.argv:
        uninstall()
        return

    python_path = step_check_env()
    step_create_user()
    step_deploy_code(python_path)
    step_configure()
    step_install_service()
    step_health_check()
    print_summary()


if __name__ == "__main__":
    main()
