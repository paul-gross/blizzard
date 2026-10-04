"""OpenCode bundle composition, plugin collision checks and snapshot references."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from blizzard.runner.harness.bundle import EntryPoint, HarnessBundleError, HarnessComposition, HarnessLayout
from blizzard.runner.harness.opencode.shapes import OpenCodeShapeError, parse_worker_config

_FILE_SUBSTITUTION = re.compile(r"\{file:([^}]+)\}")
_JSONC_TOKEN = re.compile(r'("(?:\\.|[^"\\])*")|//[^\n]*|/\*.*?\*/', re.DOTALL)


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def file_substitutions(document: Mapping[str, Any]) -> tuple[str, ...]:
    """Every ``{file:<path>}`` reference in the document's string values."""
    return tuple(match.strip() for text in _strings(document) for match in _FILE_SUBSTITUTION.findall(text))


def content_with_snapshot_references(content: str, effective_dir: Path) -> str:
    """Resolve relative companions for the cwd-relative CONFIG_CONTENT loader."""
    document = json.loads(content)

    def resolve(value: Any) -> Any:
        if isinstance(value, str):

            def reference(match: re.Match[str]) -> str:
                path = match.group(1).strip()
                if Path(path).is_absolute() or path.startswith("~"):
                    return match.group(0)
                return "{file:" + str((effective_dir / path).resolve()) + "}"

            return _FILE_SUBSTITUTION.sub(reference, value)
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    return json.dumps(resolve(document), indent=2, sort_keys=True) + "\n" if file_substitutions(document) else content


OPENCODE_BUNDLE_LAYOUT = HarnessLayout(
    dirname="opencode",
    entry_points=(
        EntryPoint("opencode.json", companions=file_substitutions),
        EntryPoint("plugins", is_dir=True),
    ),
)


def plugin_identity(reference: str) -> str:
    """The package name or native filename that OpenCode registers as a plugin."""
    if reference.startswith("file:"):
        path = unquote(urlsplit(reference).path)
        return Path(path).stem.casefold()
    separator = reference.rfind("@")
    name = reference[:separator] if separator > 0 else reference
    return name.casefold()


def _plugins(document: Mapping[str, Any], source: Path) -> dict[str, Path]:
    raw = document.get("plugin", [])
    if not isinstance(raw, list) or any(not isinstance(item, str) or not item for item in raw):
        raise HarnessBundleError(source, "plugin must be an array of non-empty strings")
    found: dict[str, Path] = {}
    for item in raw:
        identity = plugin_identity(item)
        if identity in found:
            raise HarnessBundleError(source, f"duplicate plugin {identity!r} at {found[identity]} and {source}")
        found[identity] = source
    return found


def _directory_plugins(directory: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.suffix not in {".js", ".ts", ".mjs", ".mts"}:
                continue
            identity = path.stem.casefold()
            if identity in found:
                raise HarnessBundleError(path, f"duplicate plugin {identity!r} at {found[identity]} and {path}")
            found[identity] = path
    return found


def _merge_plugins(found: dict[str, Path], incoming: Mapping[str, Path]) -> None:
    for identity, path in incoming.items():
        if identity in found:
            raise HarnessBundleError(path, f"duplicate plugin {identity!r} at {found[identity]} and {path}")
        found[identity] = path


def ambient_plugin_sources(cwd: Path, env: Mapping[str, str]) -> dict[str, Path]:
    home = Path(env.get("HOME", str(Path.home())))
    user = Path(env.get("XDG_CONFIG_HOME", str(home / ".config"))) / "opencode"
    scopes = [(user, True)]
    for parent in reversed((cwd, *cwd.parents)):
        for config in (parent / "opencode.json", parent / "opencode.jsonc"):
            if config.is_file():
                scopes.append((parent, False))
                break
        scopes.append((parent / ".opencode", True))
    found: dict[str, Path] = {}
    for scope, reads_plugin_dirs in scopes:
        for config in (scope / "opencode.json", scope / "opencode.jsonc"):
            if config.is_file():
                try:
                    content = config.read_text(encoding="utf-8")
                    if config.suffix == ".jsonc":
                        content = _JSONC_TOKEN.sub(lambda match: match.group(1) or "", content)
                        content = re.sub(r",\s*(?=[}\]])", "", content)
                    document = json.loads(content)
                except (OSError, ValueError) as exc:
                    raise HarnessBundleError(config, f"cannot inspect plugin declarations: {exc}") from exc
                if not isinstance(document, dict):
                    raise HarnessBundleError(config, "must hold a JSON object")
                _merge_plugins(found, _plugins(document, config))
        if reads_plugin_dirs:
            for name in ("plugin", "plugins"):
                _merge_plugins(found, _directory_plugins(scope / name))
    return found


def check_ambient_plugins(effective_dir: Path, cwd: Path, env: Mapping[str, str]) -> None:
    document = json.loads((effective_dir / "opencode.json").read_text(encoding="utf-8"))
    found = _plugins(document, effective_dir / "opencode.json")
    _merge_plugins(found, _directory_plugins(effective_dir / "plugins"))
    _merge_plugins(found, ambient_plugin_sources(cwd, env))


def opencode_bundle_layout(worker_config_path: Path) -> HarnessLayout:
    """The layout, composing against the runner's worker config at ``worker_config_path``."""
    return replace(OPENCODE_BUNDLE_LAYOUT, compose=lambda composition: _compose(composition, worker_config_path))


def _compose(composition: HarnessComposition, worker_config_path: Path) -> None:
    """Compose inside staging, before the snapshot can be published."""
    source = composition.source_dir
    if not worker_config_path.is_file():
        raise HarnessBundleError(worker_config_path, "runner OpenCode worker config is missing")
    native = source / "opencode.json"
    document: dict[str, Any] = {}
    if native.is_file():
        document = json.loads(native.read_text(encoding="utf-8"))
    worker = json.loads(worker_config_path.read_text(encoding="utf-8"))
    try:
        parse_worker_config(worker)
    except OpenCodeShapeError as exc:
        raise HarnessBundleError(worker_config_path, str(exc)) from exc
    permission = document.get("permission", {})
    if not isinstance(permission, dict):
        raise HarnessBundleError(native, "permission must be an object")
    for name in worker["permission"]:
        if name in permission:
            raise HarnessBundleError(native, f"permission.{name} collides with runner-owned rule")
    operator_plugins = _plugins(document, native)
    _merge_plugins(operator_plugins, _directory_plugins(source / "plugins"))
    runner_plugins = _plugins(worker, worker_config_path)
    for identity, runner_path in runner_plugins.items():
        if identity in operator_plugins:
            operator_path = operator_plugins[identity]
            raise HarnessBundleError(
                operator_path, f"duplicate plugin {identity!r} at {operator_path} and {runner_path}"
            )
    _merge_plugins(operator_plugins, runner_plugins)
    document["permission"] = {**permission, **worker["permission"]}
    document["plugin"] = [*document.get("plugin", []), *worker["plugin"]]
    try:
        parse_worker_config(document)
    except OpenCodeShapeError as exc:
        raise HarnessBundleError(native, str(exc)) from exc
    destination = composition.staged_dir / "opencode.json"
    destination.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
