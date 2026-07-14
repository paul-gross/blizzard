"""Shared fixtures for the e2e browser tier (scenario 6).

The browser-driven scenario needs a real Chromium, installed out of band with
``uv run playwright install chromium``. When that binary is absent the whole
browser scenario **skips cleanly** rather than erroring, so ``mise run e2e`` still
runs the in-process scenarios on a machine that has never installed the browser.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def chromium_available() -> bool:
    """True when a launchable Playwright Chromium is installed (else the scenario skips)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - playwright is a declared dev dep
        return False
    try:
        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:  # pragma: no cover - driver missing / not installed
        return False
