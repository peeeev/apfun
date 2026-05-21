"""Idempotent Playwright + Chromium readiness check.

The host Dockerfile (per feedback 014) bakes `playwright install --with-deps
chromium` into the image, so a freshly-built container is ready out of the
box. This script exists for two reasons:

- Operator readiness check: run it after a container rebuild to confirm the
  Chromium binary is reachable for the node user before kicking off any
  ingest jobs.
- Local-dev escape hatch: if the host hasn't been rebuilt or someone is
  developing outside the canonical container, this script can re-run
  `playwright install chromium` to repair the install.

Exits 0 when Chromium launches cleanly; non-zero otherwise with an actionable
message.

Usage::

    uv run python scripts/setup_playwright.py
    uv run python scripts/setup_playwright.py --install   # force re-install
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys


def _check_launch() -> tuple[bool, str]:
    """Try launching headless Chromium briefly. Returns (ok, message)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return False, f"playwright not importable: {exc}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True, "Chromium launched and closed cleanly."
    except Exception as exc:  # noqa: BLE001 — surface whatever playwright reports
        return False, f"Chromium launch failed: {type(exc).__name__}: {exc}"


def _force_install() -> int:
    """Run `playwright install chromium`. Returns the subprocess exit code."""
    if shutil.which("playwright") is None:
        print(
            "`playwright` CLI not found on PATH. Run `uv add playwright` first.",
            file=sys.stderr,
        )
        return 2
    print("Running: playwright install chromium")
    return subprocess.call(["playwright", "install", "chromium"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Force `playwright install chromium` before the launch check.",
    )
    args = parser.parse_args()

    if args.install:
        rc = _force_install()
        if rc != 0:
            return rc

    ok, message = _check_launch()
    if ok:
        print(f"✓ {message}")
        return 0
    print(f"✗ {message}", file=sys.stderr)
    print(
        "\nTry: uv run python scripts/setup_playwright.py --install",
        file=sys.stderr,
    )
    print(
        "If running inside the dev container, the host Dockerfile should already "
        "have done `playwright install --with-deps chromium` per orchestrator "
        "feedback 014. Confirm with: docker exec apfun-funnel playwright --version",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
