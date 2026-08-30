"""The three things a node declares about its step: where it runs, who judges its exit,
and how its session is minted. Both daemons read this vocabulary; the :class:`Node`
entity it describes is hub policy and stays in ``hub/domain/graph.py``."""

from __future__ import annotations

from enum import StrEnum


class Executor(StrEnum):
    """Where a node's step runs."""

    RUNNER = "runner"
    HUB = "hub"


class JudgedBy(StrEnum):
    """Who issues a node's exit judgement — the structural gate marker."""

    WORKER = "worker"
    HUMAN = "human"


class SessionMode(StrEnum):
    """Per-node session freshness."""

    RESUME = "resume"
    FRESH = "fresh"
