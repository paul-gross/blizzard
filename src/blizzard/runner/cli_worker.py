"""The identity a spawned worker acts under, and the calls it makes with it."""

from __future__ import annotations

import os
from dataclasses import dataclass

import click
import httpx

# The spawn environment a worker's identity is read from — never a flag, so no verb can
# name another chunk's work. The token may be absent; the runner then answers ``403``.
ENV_LEASE_ID = "BLIZZARD_LEASE_ID"
ENV_RUNNER_URL = "BLIZZARD_RUNNER_URL"
ENV_LEASE_TOKEN = "BLIZZARD_LEASE_TOKEN"
LEASE_TOKEN_HEADER = "X-Blizzard-Lease-Token"

# A hub-proxied read travels further than a runner-local write, so the two are bounded apart.
READ_TIMEOUT = 20.0
WRITE_TIMEOUT = 5.0


@dataclass(frozen=True)
class WorkerCall:
    """A spawned worker's ambient identity — the runner it reports to, and the lease it
    acts under (``""`` for a verb that names its own chunk instead)."""

    verb: str
    runner_url: str
    lease_id: str = ""
    lease_token: str | None = None

    @classmethod
    def of(cls, verb: str, *, lease: bool = True) -> WorkerCall:
        """This worker's identity — a hard error rather than the soft skip :meth:`hook` takes,
        so a lost read or write reaches the worker rather than passing silently."""
        runner_url = os.environ.get(ENV_RUNNER_URL)
        lease_id = os.environ.get(ENV_LEASE_ID)
        if not runner_url or (lease and not lease_id):
            wanted = f"{ENV_LEASE_ID}/{ENV_RUNNER_URL}" if lease else ENV_RUNNER_URL
            raise click.ClickException(f"{verb}: no {wanted} in the environment")
        return cls(verb, runner_url, lease_id or "", os.environ.get(ENV_LEASE_TOKEN))

    @classmethod
    def hook(cls, verb: str) -> WorkerCall | None:
        """The same identity for a worker *hook*, or ``None`` after saying so on stderr — a
        hook must never break the worker's tool call, so an absent identity skips."""
        runner_url = os.environ.get(ENV_RUNNER_URL)
        lease_id = os.environ.get(ENV_LEASE_ID)
        if not lease_id or not runner_url:
            click.echo(f"{verb}: no {ENV_LEASE_ID}/{ENV_RUNNER_URL} in the environment; skipping", err=True)
            return None
        return cls(verb, runner_url, lease_id, os.environ.get(ENV_LEASE_TOKEN))

    def leased(self, suffix: str) -> str:
        return f"/api/leases/{self.lease_id}/{suffix}"

    def get(
        self, path: str, *, failure: str, rejected: str | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        return self._call("get", path, failure=failure, rejected=rejected, params=params, timeout=READ_TIMEOUT)

    def post(
        self, path: str, *, failure: str, rejected: str | None = None, json_body: object | None = None
    ) -> httpx.Response:
        return self._call("post", path, failure=failure, rejected=rejected, json_body=json_body, timeout=WRITE_TIMEOUT)

    def soft_post(self, path: str, *, failure: str, json_body: object | None = None) -> None:
        try:
            self.post(path, failure=failure, json_body=json_body)
        except click.ClickException as exc:
            click.echo(f"{exc.message}; skipping", err=True)

    def _call(
        self,
        method: str,
        path: str,
        *,
        failure: str,
        timeout: float,
        rejected: str | None = None,
        json_body: object | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """One call, with this lease's token attached and a failure named as ``verb: failure``.

        A rejection the worker can act on (an unknown ``--repo``, naming the repos the env
        does list) carries its guidance in the body, so that is preferred over the bare status
        line; ``rejected`` names it differently from an unreachable runner where that helps."""
        kwargs: dict[str, object] = {"timeout": timeout, "headers": self._headers(), "params": params}
        if json_body is not None:
            kwargs["json"] = json_body
        try:
            resp = getattr(httpx, method)(f"{self.runner_url.rstrip('/')}{path}", **kwargs)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            named = rejected or failure
            raise click.ClickException(f"{self.verb}: {named} ({_problem_detail(exc.response) or exc})") from exc
        except httpx.HTTPError as exc:
            raise click.ClickException(f"{self.verb}: {failure} ({exc})") from exc

    def _headers(self) -> dict[str, str]:
        return {LEASE_TOKEN_HEADER: self.lease_token} if self.lease_token else {}


def _problem_detail(response: httpx.Response) -> str:
    """The ``detail`` string from a rejected call's JSON body, or ``""``."""
    try:
        body = response.json()
    except ValueError:
        return ""
    detail = body.get("detail") if isinstance(body, dict) else None
    return str(detail) if detail else ""
