"""Helpers for the graphs api tests, shared with other test modules."""

from __future__ import annotations

GRAPH_YAML = """
name: tiny
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
        fail:
          description: it does not
          to: build
    retries:
      max: 1
      exhausted: escalate
"""
