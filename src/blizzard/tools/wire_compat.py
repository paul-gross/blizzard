"""Fail on a breaking change to the hub↔runner wire surface (bzh:fleet-wire-additive).

``classify_spec_diff`` lives in ``blizzard.tools.compat.surfaces.wire``; the rest resolves a
git or GitHub baseline and walks commits one first-parent step at a time, attributing
a break to its landing — acknowledged, not failed, by a Conventional Commits ``!``.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import click

from blizzard.tools.compat.gate import BREAKING_COMMIT_MARKER
from blizzard.tools.compat.surfaces.wire import classify_spec_diff

_SPEC_PATH = "openapi/hub.openapi.json"


class WireCompatError(RuntimeError):
    """A baseline could not be resolved, or a git/gh call needed to check it failed."""


# --- Git / gh plumbing ----------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise WireCompatError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _spec_at(ref: str, cwd: Path) -> dict:
    return json.loads(_run_git(["show", f"{ref}:{_SPEC_PATH}"], cwd))


def resolve_merge_base_baseline(against: str, cwd: Path) -> str:
    return _run_git(["merge-base", "HEAD", against], cwd).strip()


#: How many of the newest ``push.yml`` runs on ``master`` are searched for a successful one.
_DEPLOYED_SEARCH_DEPTH = 100

#: Resolution attempts before a disagreeing listing is declared stale, and the first backoff.
_DEPLOYED_ATTEMPTS = 4
_DEPLOYED_BACKOFF_SECONDS = 5.0


def _gh(args: list[str], cwd: Path) -> str:
    result = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd, timeout=30)
    if result.returncode != 0:
        raise WireCompatError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout or "[]"


def _newest_success(runs: list[tuple[str, str, str]]) -> str | None:
    """The head sha of the successful ``(sha, conclusion, created_at)`` run created last."""
    successes = [(created, sha) for sha, conclusion, created in runs if conclusion == "success"]
    return max(successes)[1] if successes else None


def _listed_baseline(repo: str, cwd: Path) -> str | None:
    out = _gh(
        [
            "run",
            "list",
            "--repo",
            repo,
            "--workflow",
            "push.yml",
            "--branch",
            "master",
            "--limit",
            str(_DEPLOYED_SEARCH_DEPTH),
            "--json",
            "headSha,conclusion,createdAt",
        ],
        cwd,
    )
    runs = [(r["headSha"], r.get("conclusion") or "", r.get("createdAt") or "") for r in json.loads(out)]
    return _newest_success(runs)


def _rest_baseline(repo: str, cwd: Path) -> str | None:
    out = _gh(
        ["api", f"repos/{repo}/actions/workflows/push.yml/runs?branch=master&per_page={_DEPLOYED_SEARCH_DEPTH}"],
        cwd,
    )
    payload = json.loads(out)
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    return _newest_success([(r["head_sha"], r.get("conclusion") or "", r.get("created_at") or "") for r in runs])


def resolve_deployed_baseline(repo: str, cwd: Path, *, sleep=time.sleep) -> str:
    """The head commit of the newest successful ``push.yml`` run on ``master`` — what the
    hosted hub's ``edge`` currently runs. Success is filtered here, not by ``--status``:
    GitHub answers ``branch`` and ``status`` together from a stale index.

    The run listing is itself sometimes answered from a stale index, weeks behind. The
    newest successful run by creation time is therefore cross-checked against the REST
    runs endpoint; a disagreement is retried with backoff and, if it never settles, fails
    rather than walking from a baseline that may be long deployed."""
    listed = rest = None
    for attempt in range(_DEPLOYED_ATTEMPTS):
        if attempt:
            sleep(_DEPLOYED_BACKOFF_SECONDS * 2 ** (attempt - 1))
        listed = _listed_baseline(repo, cwd)
        rest = _rest_baseline(repo, cwd)
        if listed is None and rest is None:
            raise WireCompatError(
                f"no successful push.yml run among the newest {_DEPLOYED_SEARCH_DEPTH} on {repo}@master"
            )
        if listed == rest:
            return listed  # type: ignore[return-value]
    raise WireCompatError(
        f"stale push.yml run listing for {repo}@master: `gh run list` resolved {listed} but the REST runs "
        f"endpoint resolved {rest} after {_DEPLOYED_ATTEMPTS} attempts; refusing to walk from either baseline"
    )


def _first_parent_steps(baseline: str, cwd: Path) -> list[str]:
    out = _run_git(["rev-list", "--first-parent", "--reverse", f"{baseline}..HEAD"], cwd)
    return [line for line in out.splitlines() if line]


def _is_acknowledged(prev: str, curr: str, cwd: Path) -> bool:
    landed = [line for line in _run_git(["rev-list", f"{prev}..{curr}"], cwd).splitlines() if line]
    for sha in landed:
        subject = _run_git(["log", "-1", "--format=%s", sha], cwd).strip()
        if BREAKING_COMMIT_MARKER.match(subject):
            return True
    return False


def check_history(baseline_commit: str, cwd: Path, *, echo=click.echo) -> bool:
    """Walk ``baseline_commit..HEAD`` first-parent; true iff every step is additive or
    acknowledged by a ``!``-marked landing. Checked net-first, skipping the per-step walk
    when the whole range is additive — see ``docs/ci.md`` for the revert recovery path.
    """
    steps = _first_parent_steps(baseline_commit, cwd)
    if not steps:
        echo(f"wire-compat: HEAD is already at baseline {baseline_commit[:9]}, nothing to check.")
        return True

    if not classify_spec_diff(_spec_at(baseline_commit, cwd), _spec_at(steps[-1], cwd)):
        echo(f"wire-compat: net change since baseline {baseline_commit[:9]} is additive, nothing to check.")
        return True

    ok = True
    prev = baseline_commit
    for curr in steps:
        violations = classify_spec_diff(_spec_at(prev, cwd), _spec_at(curr, cwd))
        if violations:
            acknowledged = _is_acknowledged(prev, curr, cwd)
            if acknowledged:
                echo(f"wire-compat: ACKNOWLEDGED breaking change at {curr[:9]}:")
            else:
                ok = False
                echo(f"wire-compat: UNACKNOWLEDGED breaking change at {curr[:9]}:")
            for v in violations:
                echo(f"  - {v}")
            if not acknowledged:
                echo("  Mark a commit subject the step lands with '!' (e.g. 'feat!: ...') to acknowledge.")
        prev = curr
    return ok


@click.command()
@click.option(
    "--baseline",
    type=click.Choice(["merge-base", "deployed"]),
    default="merge-base",
    show_default=True,
    help="merge-base: diff against the PR's merge-base with --against. deployed: diff "
    "against the last commit whose push.yml run succeeded (what edge currently runs).",
)
@click.option("--against", default="origin/master", show_default=True, help="merge-base mode's comparison ref.")
@click.option("--repo", default="paul-gross/blizzard", show_default=True, help="deployed mode's GitHub repo.")
@click.option(
    "--base-spec",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Diff two spec files directly, bypassing git and the ! acknowledgement walk.",
)
@click.option("--head-spec", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
def main(baseline: str, against: str, repo: str, base_spec: Path | None, head_spec: Path | None) -> None:
    if base_spec or head_spec:
        if not (base_spec and head_spec):
            raise click.UsageError("--base-spec and --head-spec must be given together")
        violations = classify_spec_diff(json.loads(base_spec.read_text()), json.loads(head_spec.read_text()))
        for v in violations:
            click.echo(f"  - {v}")
        raise SystemExit(1 if violations else 0)

    cwd = Path.cwd()
    baseline_commit = (
        resolve_merge_base_baseline(against, cwd) if baseline == "merge-base" else resolve_deployed_baseline(repo, cwd)
    )
    ok = check_history(baseline_commit, cwd)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
