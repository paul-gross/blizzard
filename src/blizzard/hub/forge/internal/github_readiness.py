"""GitHub's read-only credential check: the reads landing and ingestion make, each answered as a check outcome, and
every write judged from the grant the credential exposes — a classic token's ``repo`` scope or an installation's
permission — never sent. Its client's transport refuses every write; an installation's token is minted through the
tenant's installation tokens, never this client."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import partial
from urllib.parse import quote

import httpx2

from blizzard.foundation.credential_check import CheckOutcome, UseKind
from blizzard.foundation.untyped import as_table
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.kernel.forge_refusals import ForgeMintRefused
from blizzard.hub.forge.auth import ForgeAuthorization
from blizzard.hub.forge.budget import SweepingGate
from blizzard.hub.forge.internal.github_grants import covers, variant_refusals
from blizzard.hub.forge.internal.github_refusals import CallCredential, classify_auth_refusal, diagnostic_of
from blizzard.hub.forge.internal.read_only_transport import ReadOnlyTransport
from blizzard.hub.forge.readiness import (
    ACCESS,
    MINT,
    CheckDeadline,
    CheckedUse,
    ICredentialProbe,
    OperationResult,
    ProbeAnswer,
    UseResult,
    mint_refused_answer,
    refusal_detail,
)

#: The classic token scope granting every repository write a check judges.
_REPO_SCOPE = "repo"
#: The GitHub permission each write a policy may require is granted by.
_WRITE_PERMISSIONS: Mapping[str, str] = {
    Capability.CHANGES_OPEN: "pull_requests",
    Capability.CHANGES_UPDATE_FROM_BASE: "pull_requests",
    Capability.CHANGES_MERGE_MERGE: "contents",
    Capability.CHANGES_MERGE_SQUASH: "contents",
    Capability.CHANGES_MERGE_REBASE: "contents",
    Capability.REFS_ADVANCE: "contents",
    Capability.REFS_CREATE: "contents",
    Capability.REFS_DELETE: "contents",
    Capability.BRANCHES_MERGE: "contents",
    Capability.CHECKS_RERUN: "actions",
}
#: The reads a repository is checked by, in the order they are made, after ``access``.
_READS: tuple[Capability, ...] = (
    Capability.REFS_READ,
    Capability.COMPARE,
    Capability.COMMITS_READ,
    Capability.CHANGES_READ,
    Capability.CHECKS_READ,
)
ISSUES_READ = "issues.read"
#: A work source's writes, judged on the ``issues`` permission.
ISSUE_WRITES: tuple[str, ...] = ("issues.label", "issues.comment", "issues.close")
_NOT_EXERCISED = "not exercised"


class _Stopped(Exception):
    """The check's deadline is spent or its budget turned it away: this use and every later one end unreached."""


class _Unreached(Exception):
    """The forge gave no answer to judge — a rate refusal, a 5xx, a timeout, or no connection: this use ends
    unreached."""


@dataclass
class _Run:
    """One check in progress: its client, authorization, gate and deadline, and what the reads have learned."""

    client: httpx2.Client
    authorization: ForgeAuthorization
    gate: SweepingGate
    deadline: CheckDeadline
    scopes: frozenset[str] | None = None
    minted: bool = False
    operations: list[OperationResult] = field(default_factory=list[OperationResult])

    @property
    def credential(self) -> CallCredential:
        authorization = self.authorization
        return CallCredential(authorization.credential_slug, authorization.binding, authorization.variant)

    def get(self, path: str, params: Mapping[str, str | int] | None = None) -> httpx2.Response:
        """One read, admitted by the gate and bounded by the deadline; its rate reading taught to the gate."""
        if self.deadline.spent() or self.gate.admit() is not None:
            raise _Stopped
        try:
            response = self.client.get(path, params=dict(params or {}), timeout=self.deadline.call_timeout())
        except httpx2.HTTPError as exc:
            raise _Unreached from exc
        self.minted = True
        self.gate.learn(response.headers)
        if response.status_code >= 400 and self.gate.refused(response.status_code, response.headers, _said(response)):
            raise _Unreached
        if response.status_code >= 500:
            raise _Unreached
        return response

    def read(
        self,
        operation: str,
        path: str,
        endpoint: str,
        locator: str,
        *,
        params: Mapping[str, str | int] | None = None,
        proven: Callable[[httpx2.Response], str | None] = lambda _response: None,
    ) -> httpx2.Response | None:
        """Make ``operation``'s read and record its outcome; the answer when it was 2xx."""
        response = self.get(path, params)
        if response.status_code < 300:
            self.operations.append(OperationResult(operation, CheckOutcome.PROVEN, proven(response)))
            return response
        self.operations.append(self._refused(operation, response, endpoint, locator))
        return None

    def write(self, operation: str, permission: str) -> None:
        """Judge ``operation``'s write from the grant, sending nothing."""
        if self.scopes is not None and _REPO_SCOPE in self.scopes:
            detail = f"granted by the token's {_REPO_SCOPE} scope; {_NOT_EXERCISED}"
            self.operations.append(OperationResult(operation, CheckOutcome.GRANTED, detail))
            return
        granted = self.authorization.granted_permissions or {}
        if covers(granted.get(permission), "write"):
            detail = f"granted by the installation's {permission}: {granted[permission]}; {_NOT_EXERCISED}"
            self.operations.append(OperationResult(operation, CheckOutcome.GRANTED, detail))
            return
        detail = f"no grant the credential exposes covers it; {_NOT_EXERCISED}"
        self.operations.append(OperationResult(operation, CheckOutcome.UNPROVEN, detail))

    def unsupported(self, operation: str, why: str) -> None:
        self.operations.append(OperationResult(operation, CheckOutcome.UNSUPPORTED, why))

    def learn_scopes(self, response: httpx2.Response) -> None:
        raw = response.headers.get("x-oauth-scopes")
        if raw is not None:
            self.scopes = frozenset(scope.strip() for scope in raw.split(",") if scope.strip())

    def taken(self) -> tuple[OperationResult, ...]:
        """The operations recorded so far, cleared for the next use."""
        taken, self.operations = tuple(self.operations), []
        return taken

    def _refused(self, operation: str, response: httpx2.Response, endpoint: str, locator: str) -> OperationResult:
        status, message = response.status_code, _said(response)
        documentation = _documentation(response)
        if status in (401, 403):
            classified = classify_auth_refusal(
                status,
                response.headers,
                message,
                documentation_url=documentation,
                operation=operation,
                endpoint=endpoint,
                repository=locator,
                credential=self.credential,
                withheld=operation in variant_refusals(self.authorization.variant),
            )
            capability = None if operation == ACCESS else operation
            detail = refusal_detail(classified, capability=capability)
            return OperationResult(operation, CheckOutcome.REFUSED, detail, classified.diagnostic)
        diagnostic = diagnostic_of(
            status,
            response.headers,
            message,
            documentation_url=documentation,
            operation=operation,
            endpoint=endpoint,
            repository=locator,
            credential=self.credential,
        )
        if status == 404:
            detail = f"{locator} was not found"
            if operation == ACCESS and self.authorization.grant_scoped:
                detail += ", or the installation does not include it"
        else:
            detail = f"the forge answered {status}"
        return OperationResult(operation, CheckOutcome.REFUSED, detail, diagnostic)


class GitHubReadinessProbe:
    """Checks a GitHub credential through a client whose transport sends only reads; ``transport`` is the transport
    it wraps, the network's when ``None``."""

    def __init__(self, *, transport: httpx2.BaseTransport | None = None) -> None:
        self._transport = transport

    def check(
        self,
        authorization: ForgeAuthorization,
        uses: Sequence[CheckedUse],
        *,
        api_base: str,
        gate: SweepingGate,
        deadline: CheckDeadline,
    ) -> ProbeAnswer:
        granted = authorization.granted_permissions
        transport = ReadOnlyTransport(self._transport or httpx2.HTTPTransport())
        results: list[UseResult] = []
        with httpx2.Client(
            base_url=api_base.rstrip("/"),
            auth=authorization.auth,
            transport=transport,
            headers={"Accept": "application/vnd.github+json"},
        ) as client:
            run = _Run(client, authorization, gate, deadline)
            for index, use in enumerate(uses):
                try:
                    results.append(use.answered(self._use(run, use)))
                except _Stopped:
                    results.extend((use.unreached(run.taken()), *(later.unreached() for later in uses[index + 1 :])))
                    break
                except _Unreached:
                    results.append(use.unreached(run.taken()))
                except ForgeMintRefused as exc:
                    return mint_refused_answer(exc.classified, uses, granted)
        mint = OperationResult(MINT, CheckOutcome.PROVEN) if authorization.grant_scoped and run.minted else None
        return ProbeAnswer(mint, granted, tuple(results))

    def _use(self, run: _Run, use: CheckedUse) -> tuple[OperationResult, ...]:
        if use.kind == UseKind.REPOSITORY:
            self._repository(run, use)
        else:
            self._work_source(run, use)
        return run.taken()

    def _repository(self, run: _Run, use: CheckedUse) -> None:
        repo = f"/repos/{use.locator}"
        base = use.base_branch or ""
        access = run.read(ACCESS, repo, "GET /repos/{owner}/{repo}", use.locator)
        if access is not None:
            run.learn_scopes(access)
        tip: str | None = None
        for capability in (read for read in _READS if read in use.capabilities):
            if capability not in run.authorization.offered:
                run.unsupported(capability, "the credential's authorization does not offer it; no call made")
                continue
            ref = tip or base
            match capability:
                case Capability.REFS_READ:
                    answer = run.read(
                        capability,
                        f"{repo}/git/ref/heads/{quote(base)}",
                        "GET /repos/{owner}/{repo}/git/ref/heads/{branch}",
                        use.locator,
                        proven=lambda r: f"{base}@{_tip(r)[:7]}" if _tip(r) else None,
                    )
                    tip = _tip(answer) if answer is not None else None
                case Capability.COMPARE:
                    run.read(
                        capability,
                        f"{repo}/compare/{quote(ref)}...{quote(ref)}",
                        "GET /repos/{owner}/{repo}/compare/{basehead}",
                        use.locator,
                    )
                case Capability.COMMITS_READ:
                    run.read(
                        capability,
                        f"{repo}/commits/{quote(ref)}",
                        "GET /repos/{owner}/{repo}/commits/{ref}",
                        use.locator,
                    )
                case Capability.CHANGES_READ:
                    run.read(
                        capability,
                        f"{repo}/pulls",
                        "GET /repos/{owner}/{repo}/pulls",
                        use.locator,
                        params={"state": "open", "base": base, "per_page": 1},
                    )
                case Capability.CHECKS_READ:
                    run.read(
                        capability,
                        f"{repo}/commits/{quote(ref)}/check-runs",
                        "GET /repos/{owner}/{repo}/commits/{ref}/check-runs",
                        use.locator,
                        params={"per_page": 1},
                        proven=partial(_check_runs, base=base, tip=tip),
                    )
                case _:
                    raise AssertionError(f"no read is made for {capability}")
        for capability in sorted(c for c in use.capabilities if c not in _READS):
            if capability not in run.authorization.offered:
                run.unsupported(capability, "the credential's authorization does not offer it; no call made")
            else:
                run.write(capability, _WRITE_PERMISSIONS[capability])

    def _work_source(self, run: _Run, use: CheckedUse) -> None:
        granted = run.authorization.granted_permissions
        if run.authorization.grant_scoped and not covers((granted or {}).get("issues"), "read"):
            run.unsupported(ISSUES_READ, "the installation is not granted issues; no call made")
        else:
            answer = run.read(
                ISSUES_READ,
                f"/repos/{use.locator}/issues",
                "GET /repos/{owner}/{repo}/issues",
                use.locator,
                params={"state": "all", "per_page": 1},
            )
            if answer is not None:
                run.learn_scopes(answer)
        for operation in ISSUE_WRITES:
            run.write(operation, "issues")


def _tip(response: httpx2.Response) -> str:
    target = as_table((as_table(_body(response)) or {}).get("object")) or {}
    sha = target.get("sha")
    return str(sha) if sha else ""


def _check_runs(response: httpx2.Response, base: str, tip: str | None) -> str:
    count = (as_table(_body(response)) or {}).get("total_count", 0)
    at = f"{base}@{tip[:7]}" if tip else base
    return f"{count} check runs on {at}: the read is proven, not that checks run"


def _body(response: httpx2.Response) -> object:
    try:
        return response.json()
    except ValueError:
        return None


def _said(response: httpx2.Response) -> str:
    body = as_table(_body(response))
    return str(body.get("message") or "") if body else response.text


def _documentation(response: httpx2.Response) -> str | None:
    body = as_table(_body(response))
    url = body.get("documentation_url") if body else None
    return str(url) if url else None


_conforms: type[ICredentialProbe] = GitHubReadinessProbe
