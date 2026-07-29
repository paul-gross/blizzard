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


# The two viewport sizes a browser e2e scenario sizes its page against (issue #171's
# web:shell-sweep — see blizzard-context:/verification/blizzard.md): a wide desktop
# monitor, and a ~390px phone width, the narrow end of the range the mobile shell's
# bottom nav actually routes to.
_WIDE_VIEWPORT = {"width": 1400, "height": 900}
_NARROW_VIEWPORT = {"width": 390, "height": 844}


@pytest.fixture(scope="session")
def wide_viewport() -> dict[str, int]:
    """A desktop-width `Page` viewport — the wide end of the narrow-viewport tier rule."""
    return dict(_WIDE_VIEWPORT)


@pytest.fixture(scope="session")
def narrow_viewport() -> dict[str, int]:
    """A ~390px phone-width `Page` viewport — the narrow end of the tier rule every
    component reachable from the mobile shell's bottom nav is held to (issue #171)."""
    return dict(_NARROW_VIEWPORT)
