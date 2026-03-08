# kiro-gateway-installer

Interactive installer that deploys [kiro-gateway](https://github.com/jwadow/kiro-gateway) as a systemd user service — with auto-verification at every step.

No root/sudo required. Runs as your normal user.

## What it does

kiro-gateway is a proxy gateway for the Kiro API, providing OpenAI/Anthropic-compatible endpoints so you can use Claude models through tools like Claude Code, Cursor, Cline, etc.

Manually deploying it as a systemd service involves checking Python versions, setting up venv, writing .env configs (4 credential options), crafting unit files... any mistake means a broken service and painful debugging.

This installer handles all of that interactively.

## Installation flow

```
install.py (no sudo needed)
├── 1. Environment checks
│   ├── Not running as root
│   ├── Python >= 3.10 + venv module
│   ├── systemd --user available
│   ├── Lingering enabled (for boot auto-start)
│   └── Source code present (git submodule)
│
├── 2. Deploy code
│   ├── Copy to ~/.local/share/kiro-gateway
│   ├── Create Python venv + install deps (live output)
│   └── ✅ Verify: import fastapi succeeds
│
├── 3. Interactive .env configuration
│   ├── Auto-detect existing credential files on disk
│   ├── Auto-generate or set PROXY_API_KEY
│   ├── Choose credential source (4 options, auto-detected default)
│   ├── All paths expanded to absolute (no ~ issues)
│   ├── Optional: port, proxy, region
│   └── ✅ Verify: .env exists with required fields
│
├── 4. Install systemd user service
│   ├── Generate unit file → ~/.config/systemd/user/
│   ├── daemon-reload → enable → start
│   ├── ✅ Verify: is-enabled = enabled (boot auto-start)
│   └── ✅ Verify: is-active = active (with live status)
│
└── 5. Health check
    ├── Poll /health endpoint (up to 15s, shows each attempt)
    └── ✅ Verify: HTTP 200
```

## Usage

### Install

```bash
git clone --recurse-submodules https://github.com/FASTSHIFT/kiro-gateway-installer.git
cd kiro-gateway-installer
python3 install.py
```

### Uninstall

```bash
python3 install.py --uninstall
```

### Management commands

```bash
# Status
systemctl --user status kiro-gateway

# Logs
journalctl --user-unit kiro-gateway -f

# Restart
systemctl --user restart kiro-gateway

# Edit config and restart
nano ~/.local/share/kiro-gateway/.env
systemctl --user restart kiro-gateway
```

## File layout

```
~/.local/share/kiro-gateway/
├── kiro-gateway/           # Source code
├── venv/                   # Python virtual environment
└── .env                    # Configuration (permissions 600)

~/.config/systemd/user/kiro-gateway.service
```

## Testing

Tests run inside a systemd-enabled Docker container — your local system is never touched.

```bash
python3 tests/run_tests.py          # Run tests
python3 tests/run_tests.py --keep   # Keep container for debugging
```

Requires Docker. Tests cover: file creation, .env permissions, venv integrity, service enable/start, path expansion (no ~ in .env), and clean uninstall.

CI runs automatically on push/PR via GitHub Actions.

## License

This installer is MIT licensed. kiro-gateway itself is AGPL-3.0.
