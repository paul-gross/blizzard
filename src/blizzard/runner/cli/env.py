from __future__ import annotations

# The runtime root the dir-taking verbs resolve, highest to lowest: an explicit `--dir`, then
# `BZ_RUNNER_DIR`, then the cwd (issue #39). Selectable, not shareable — the store is single-writer.
ENV_RUNNER_DIR = "BZ_RUNNER_DIR"
DEFAULT_DIR = "."
