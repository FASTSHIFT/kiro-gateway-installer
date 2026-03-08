#!/usr/bin/env bash
# Format and lint all Python files in the project.
#
# Usage:
#   ./format.sh          # Auto-format + lint check
#   ./format.sh --check  # Check only (no modifications, for CI)

set -euo pipefail

CHECK_ONLY=false
if [[ "${1:-}" == "--check" ]]; then
    CHECK_ONLY=true
fi

# Ensure tools are installed
for tool in ruff black; do
    if ! command -v "$tool" &>/dev/null; then
        echo "Installing $tool ..."
        pip install "$tool" --quiet
    fi
done

echo "black $(black --version | head -1)"
echo "ruff  $(ruff version)"
echo

TARGETS="install.py tests/test_install.py tests/run_tests.py"

if $CHECK_ONLY; then
    echo "=== black (check) ==="
    black --check --diff $TARGETS

    echo "=== ruff (check) ==="
    ruff check $TARGETS
else
    echo "=== black (format) ==="
    black $TARGETS

    echo "=== ruff (fix) ==="
    ruff check --fix $TARGETS

    echo "=== ruff (verify) ==="
    ruff check $TARGETS
fi

echo "✔ All good"
