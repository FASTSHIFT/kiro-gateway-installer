#!/usr/bin/env python3
"""
Run installer integration tests inside a systemd-enabled Docker container.
Safe to run locally — uses Docker, never touches your host systemd.

Usage:
    python3 tests/run_tests.py          # Run tests
    python3 tests/run_tests.py --keep   # Keep container after tests (for debugging)
"""

import argparse
import os
import subprocess
import sys
import time

IMAGE_NAME = "kiro-gateway-installer-test"
CONTAINER_NAME = "kiro-gw-test"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True, check=check)


def main():
    parser = argparse.ArgumentParser(description="Run installer tests in Docker")
    parser.add_argument(
        "--keep", action="store_true", help="Keep container after tests for debugging"
    )
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)

    # Check Docker is available
    r = subprocess.run("docker info", shell=True, capture_output=True)
    if r.returncode != 0:
        print("ERROR: Docker is not available. Please install Docker first.")
        sys.exit(1)

    # Clean up any previous test container
    subprocess.run(f"docker rm -f {CONTAINER_NAME}", shell=True, capture_output=True)

    try:
        # Build the test image
        print("\n=== Building test image ===")
        run(f"docker build -f tests/Dockerfile.systemd -t {IMAGE_NAME} .")

        # Start container with systemd as PID 1
        # --privileged is needed for systemd to work inside Docker
        # --cgroupns=host is needed on newer Docker versions
        print("\n=== Starting systemd container ===")
        run(
            f"docker run -d "
            f"--name {CONTAINER_NAME} "
            f"--privileged "
            f"--cgroupns=host "
            f"-v /sys/fs/cgroup:/sys/fs/cgroup:rw "
            f"{IMAGE_NAME}"
        )

        # Wait for systemd to boot
        print("\n=== Waiting for systemd to boot ===")
        for _i in range(30):
            r = subprocess.run(
                f"docker exec {CONTAINER_NAME} systemctl is-system-running",
                shell=True,
                capture_output=True,
                text=True,
            )
            state = r.stdout.strip()
            if state in ("running", "degraded"):
                print(f"  systemd state: {state}")
                break
            time.sleep(1)
        else:
            print(f"  WARNING: systemd state after 30s: {state}")

        # Initialize git submodule inside container (needed for source check)
        print("\n=== Ensuring submodule is present ===")
        run(f"docker exec {CONTAINER_NAME} ls /workspace/kiro-gateway/main.py", check=False)

        # Run the tests
        print("\n=== Running tests ===")
        result = subprocess.run(
            f"docker exec {CONTAINER_NAME} python3 -m pytest /workspace/tests/test_install.py -v --tb=short",
            shell=True,
        )

        sys.exit(result.returncode)

    finally:
        if not args.keep:
            print("\n=== Cleaning up ===")
            subprocess.run(f"docker rm -f {CONTAINER_NAME}", shell=True, capture_output=True)
        else:
            print(f"\n=== Container kept: docker exec -it {CONTAINER_NAME} bash ===")


if __name__ == "__main__":
    main()
