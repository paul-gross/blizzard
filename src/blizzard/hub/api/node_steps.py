"""The node-step wire bodies mapped at the fleet edge (``bzh:data-roles``): a runner's completion
and gate submission into the execution domain's models, and an envelope or an apply's outcome back
onto the wire."""

from __future__ import annotations

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.hub.domain.chunk.proposals import CreateItemProposal, ItemProposal, UpdateItemProposal
from blizzard.hub.domain.execution.envelope import Envelope
from blizzard.hub.domain.execution.submissions import CheckOutcome, Completion, CompletionArtifact, GateSubmission
from blizzard.wire.completion import (
    CompletionSubmission,
    CreateWorkItemProposal,
    SubmittedArtifact,
    UpdateWorkItemProposal,
    WorkItemProposal,
)
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import (
    ApplyResponse,
    EnvelopeArtifact,
    EnvelopeChoice,
    GraphArtifact,
    NodeConfig,
    NodeEnvelope,
)
from blizzard.wire.graph import ProducesEntry, RotatePolicyView


def completion_of(submission: CompletionSubmission) -> Completion:
    return Completion(
        choice=submission.choice,
        epoch=submission.epoch,
        runner_id=submission.runner_id,
        from_node_id=submission.from_node_id,
        check_results=tuple(CheckOutcome(command=c.command, passed=c.passed) for c in submission.check_results),
        artifacts=_artifacts(submission.artifacts),
        proposals=_proposals(submission.proposals),
        decision_id=submission.decision_id,
        route_token=submission.route_token,
        lease_id=submission.lease_id,
    )


def gate_submission_of(submission: DecisionSubmission) -> GateSubmission:
    return GateSubmission(
        from_node_id=submission.from_node_id,
        epoch=submission.epoch,
        runner_id=submission.runner_id,
        artifacts=_artifacts(submission.artifacts),
        proposals=_proposals(submission.proposals),
        route_token=submission.route_token,
        lease_id=submission.lease_id,
    )


def _artifacts(artifacts: list[SubmittedArtifact]) -> tuple[CompletionArtifact, ...]:
    return tuple(
        CompletionArtifact(
            name=a.name,
            kind=a.kind,
            forge=a.forge,
            repo=a.repo,
            branch_name=a.branch_name,
            commit_hash=a.commit_hash,
            content=a.content,
            attached=a.attached,
        )
        for a in artifacts
    )


def _proposals(proposals: list[WorkItemProposal]) -> tuple[ItemProposal, ...]:
    return tuple(_proposal(p) for p in proposals)


def _proposal(proposal: CreateWorkItemProposal | UpdateWorkItemProposal) -> ItemProposal:
    if isinstance(proposal, CreateWorkItemProposal):
        return CreateItemProposal(title=proposal.title, body=proposal.body, stated_priority=proposal.stated_priority)
    return UpdateItemProposal(source=proposal.source, ref=proposal.ref, evidence=proposal.evidence)


def apply_response(outcome: ApplyOutcome, *, detail: str | None, envelope: Envelope | None = None) -> ApplyResponse:
    return ApplyResponse(
        outcome=outcome, next_envelope=node_envelope(envelope) if envelope is not None else None, detail=detail
    )


def node_envelope(envelope: Envelope) -> NodeEnvelope:
    return NodeEnvelope(
        chunk_id=envelope.chunk.chunk_id,
        graph_id=envelope.chunk.graph_id,
        graph_name=envelope.graph.name,
        epoch=envelope.epoch,
        node=_node_config(envelope),
        prompt=envelope.prompt,
        judgement_prompt=envelope.judgement_prompt,
        work_refs=envelope.work_refs,
        artifacts=[
            EnvelopeArtifact(
                name=a.name,
                kind=a.kind,
                node_name=a.node_name,
                epoch=a.epoch,
                repo=a.repo,
                branch_name=a.branch_name,
                commit_hash=a.commit_hash,
                content=a.content,
            )
            for a in envelope.carried_artifacts
        ],
        graph_artifacts=[
            GraphArtifact(name=a.name, kind=ArtifactKind.ASSET, content=a.content) for a in envelope.graph.artifacts
        ],
    )


def _node_config(envelope: Envelope) -> NodeConfig:
    node = envelope.node
    session = envelope.session
    rotate = session.rotate
    return NodeConfig(
        node_id=node.node_id,
        node_name=node.name,
        executor=node.executor,
        session=envelope.session_mode,
        session_source=node.session_source,
        session_name=session.name,
        session_model=session.model,
        session_effort=session.effort,
        session_harnesses=session.harnesses,
        session_rotate=RotatePolicyView(
            max_context_tokens=rotate.max_context_tokens,
            max_transcript_bytes=rotate.max_transcript_bytes,
            max_invocations=rotate.max_invocations,
        )
        if rotate is not None
        else None,
        session_compaction_window=session.compaction_window,
        judged_by=node.judged_by,
        checks=list(node.checks),
        checks_cwd=node.checks_cwd,
        checks_timeout=node.checks_timeout,
        produces=[ProducesEntry(name=p.name, kind=p.kind) for p in node.produces],
        proposes_work_items=node.proposes_work_items,
        retries_max=node.retries_max,
        choices=[
            EnvelopeChoice(name=c.name, description=c.description, requires_checks=c.requires_checks)
            for c in node.choices
        ],
    )
