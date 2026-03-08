# kiro-gateway-installer

Interactive installer that deploys [kiro-gateway](https://github.com/jwadow/kiro-gateway) as a systemd service — with auto-verification at every step.

## What it does

kiro-gateway is a proxy gateway for the Kiro API, providing OpenAI/Anthropic-compatible endpoints so you can use Claude models through tools like Claude Code, Cursor, Cline, etc.

Manually deploying it as a systemd service involves checking Python versions, setting up venv, writing .env configs (4 credential options), crafting unit files, managing permissions... any mistake means a broken service and painful debugging.

This installer handles all of that interactively.

## Installation flow

```
install.py
├── 1. Environment checks
│   ├── Root privileges
│   ├── Python >= 3.10 + venv module
│   ├── systemd available
│   └── Source code present (git submodule)
│
├── 2. Create service user
│   ├── System user kiro-gateway (nologin)
│   └── ✅ Verify: id kiro-gateway
│
├── 3. Deploy code
│   ├── Copy to /opt/kiro-gateway
│   ├── Create Python venv + install deps
│   └── ✅ Verify: import fastapi succeeds
│
├── 4. Interactive .env configuration
│   ├── Auto-generate or set PROXY_API_KEY
│   ├── Choose credential source (4 options with guidance)
│   ├── Optional: port, proxy, region
│   └── ✅ Verify: .env exists with required fields
│
├── 5. Install systemd service
│   ├── Generate unit file with security hardening
│   ├── daemon-reload → enable → start
│   ├── ✅ Verify: is-enabled = enabled (boot auto-start)
│   └── ✅ Verify: is-active = active
│
└── 6. Health check
    ├── Poll /health endpoint (up to 15s)
    └── ✅ Verify: HTTP 200
```

## Security

- Dedicated system user `kiro-gateway` with nologin shell
- `.env` file permissions `600` (owner-only read)
- Install directory permissions `750`
- systemd hardening: `ProtectSystem=strict`, `ProtectHome=read-only`, `NoNewPrivileges=true`, `PrivateTmp=true`
- Default listen on `127.0.0.1` (local only)

## Usage

### Install

```bash
git clone --recurse-submodules https://github.com/YOUR_USERNAME/kiro-gateway-installer.git
cd kiro-gateway-installer
sudo python3 install.py
```

### Uninstall

```bash
sudo python3 install.py --uninstall
```

### Management commands

```bash
# Status
sudo systemctl status kiro-gateway

# Logs
sudo journalctl -u kiro-gateway -f

# Restart
sudo systemctl restart kiro-gateway

# Edit config and restart
sudo nano /opt/kiro-gateway/.env
sudo systemctl restart kiro-gateway
```

## Testing

Tests run inside a systemd-enabled Docker container — your local system is never touched.

### Run locally

```bash
python3 tests/run_tests.py          # Run tests
python3 tests/run_tests.py --keep   # Keep container for debugging
```

Requires Docker. The script builds an Ubuntu 24.04 image with systemd, runs the full install/uninstall cycle inside it, and verifies every step.

### What's tested

- File creation (source, venv, .env, service file)
- .env permissions (600) and content
- venv has fastapi installed
- Service user exists with nologin shell
- systemd service is enabled (boot auto-start verified)
- Service file contains security hardening directives
- Full uninstall cleans up everything

### CI

Tests run automatically on push/PR via GitHub Actions (`.github/workflows/test.yml`).

## File layout

```
/opt/kiro-gateway/
├── kiro-gateway/           # Source code
├── venv/                   # Python virtual environment
└── .env                    # Configuration (permissions 600)

/etc/systemd/system/kiro-gateway.service
```

## License

This installer is MIT licensed. kiro-gateway itself is AGPL-3.0.
