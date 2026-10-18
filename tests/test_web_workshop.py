"""The workshop's wiring, which the web scripts cannot see from inside `web/`.

Its stylesheets match the hub build's, no tsconfig that compiles production code takes a story, and the `fleet`
project carries the two Storybook targets in the shape the workshop needs.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_WEB = repo_root() / "web"
_FLEET = _WEB / "projects" / "fleet"
_STORIES_GLOB = "**/*.stories.ts"
_CSS_IMPORT = re.compile(r"""^import\s+['"]([^'"]+\.css)['"];?\s*$""", re.MULTILINE)
_COMMENT_LINE = re.compile(r"^\s*/\*.*\*/\s*$", re.MULTILINE)


def _fleet_targets() -> dict:
    project = json.loads((_WEB / "angular.json").read_text())["projects"]["fleet"]
    return project.get("architect") or project["targets"]


def _tsconfig_excludes(path: str) -> list[str]:
    # tsconfigs open with whole-line `/* … */` comments, which json cannot parse; a glob's own `/*` sits inside a string.
    return json.loads(_COMMENT_LINE.sub("", (_WEB / path).read_text())).get("exclude", [])


def _resolved(path: str, *, relative_to: str) -> str:
    return str((_WEB / relative_to / path).resolve().relative_to(_WEB.resolve()))


def test_workshop_stylesheets_match_the_hub_build_styles() -> None:
    hub_styles = json.loads((_WEB / "angular.json").read_text())["projects"]["hub"]
    hub_styles = (hub_styles.get("architect") or hub_styles["targets"])["build"]["options"]["styles"]
    assert hub_styles, "the hub build lists no styles — the comparison below would be a green no-op"
    preview = (_FLEET / ".storybook" / "preview.ts").read_text()
    imports = [
        # `node_modules/` sheets are named by package in `preview.ts`, by path in `angular.json`.
        f"node_modules/{spec}" if not spec.startswith(".") else _resolved(spec, relative_to="projects/fleet/.storybook")
        for spec in _CSS_IMPORT.findall(preview)
    ]
    assert imports == hub_styles, "preview.ts must import, in order, exactly the stylesheets the hub build lists"


@pytest.mark.parametrize(
    "tsconfig",
    ["projects/fleet/tsconfig.lib.json", "projects/hub/tsconfig.app.json", "projects/runner/tsconfig.app.json"],
)
def test_production_tsconfigs_exclude_stories(tsconfig: str) -> None:
    assert _STORIES_GLOB in _tsconfig_excludes(tsconfig), f"{tsconfig} would compile stories into a bundle"


def test_fleet_carries_the_storybook_targets() -> None:
    targets = _fleet_targets()
    serve, build = targets["storybook"], targets["build-storybook"]
    assert serve["builder"] == "@storybook/angular-vite:start-storybook"
    assert build["builder"] == "@storybook/angular-vite:build-storybook"
    assert serve["options"]["zoneless"] is True
    assert build["options"]["zoneless"] is True
    assert build["options"]["enableProdMode"] is False
