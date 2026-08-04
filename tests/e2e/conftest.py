"""Shared fixtures for the e2e browser tier (scenario 6).

Chromium is installed out of band (``uv run playwright install chromium``); when it is
absent the browser scenario **skips cleanly** rather than erroring.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    # Annotation-only: the module must stay importable on a machine that has never
    # installed Playwright (see the module docstring's clean-skip contract).
    from playwright.sync_api import ViewportSize


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
# web:shell-sweep — see blizzard-context:/verification/blizzard.md).
_WIDE_VIEWPORT: ViewportSize = {"width": 1400, "height": 900}
_NARROW_VIEWPORT: ViewportSize = {"width": 390, "height": 844}


@pytest.fixture(scope="session")
def wide_viewport() -> ViewportSize:
    """A desktop-width `Page` viewport — the wide end of the narrow-viewport tier rule."""
    return _WIDE_VIEWPORT.copy()


@pytest.fixture(scope="session")
def narrow_viewport() -> ViewportSize:
    """A ~390px phone-width `Page` viewport — the narrow end of the tier rule (issue #171)."""
    return _NARROW_VIEWPORT.copy()
