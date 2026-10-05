"""The node-step wire bodies mapped at the runner's hub client (``bzh:data-roles``): the envelope
and the apply's reply into the runner's domain models, and a completion or gate submission onto
the wire."""

from __future__ import annotations

from blizzard.runner.node_steps.envelope import (
    CarriedArtifact,
    Choice,
    Envelope,
    EnvelopeNode,
    GraphArtifact,
    ProducesSpec,
    RotateBounds,
)
from blizzard.runner.node_steps.submissions import (
    ApplyReply,
    CheckVerdict,
    Completion,
    CompletionArtifact,
    GateSubmission,
)
from blizzard.wire.completion import CheckResult, CompletionSubmission, SubmittedArtifact
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeConfig, NodeEnvelope


def envelope_of(wire: NodeEnvelope) -> Envelope:
    return Envelope(
        chunk_id=wire.chunk_id,
        graph_id=wire.graph_id,
        graph_name=wire.graph_name,
        epoch=wire.epoch,
        node=_node_of(wire.node),
        prompt=wire.prompt,
        judgement_prompt=wire.judgement_prompt,
        work_refs=[dict(ref) for ref in wire.work_refs],
        artifacts=[
            CarriedArtifact(
                name=a.name,
                kind=a.kind,
                node_name=a.node_name,
                epoch=a.epoch,
                repo=a.repo,
                branch_name=a.branch_name,
                commit_hash=a.commit_hash,
                content=a.content,
            )
            for a in wire.artifacts
        ],
        graph_artifacts=[GraphArtifact(name=a.name, kind=a.kind, content=a.content) for a in wire.graph_artifacts],
    )


def _node_of(wire: NodeConfig) -> EnvelopeNode:
    rotate = wire.session_rotate
    return EnvelopeNode(
        node_id=wire.node_id,
        node_name=wire.node_name,
        executor=wire.executor,
        session=wire.session,
        judged_by=wire.judged_by,
        session_source=wire.session_source,
        session_name=wire.session_name,
        session_model=list(wire.session_model),
        session_effort=wire.session_effort,
        session_harnesses=list(wire.session_harnesses),
        session_rotate=RotateBounds(
            max_context_tokens=rotate.max_context_tokens,
            max_transcript_bytes=rotate.max_transcript_bytes,
            max_invocations=rotate.max_invocations,
        )
        if rotate is not None
        else None,
        session_compaction_window=wire.session_compaction_window,
        checks=list(wire.checks),
        checks_cwd=wire.checks_cwd,
        checks_timeout=wire.checks_timeout,
        produces=[ProducesSpec(name=p.name, kind=p.kind) for p in wire.produces],
        proposes_work_items=wire.proposes_work_items,
        retries_max=wire.retries_max,
        choices=[
            Choice(name=c.name, description=c.description, requires_checks=c.requires_checks) for c in wire.choices
        ],
    )


def apply_reply_of(wire: ApplyResponse) -> ApplyReply:
    return ApplyReply(
        outcome=wire.outcome,
        next_envelope=envelope_of(wire.next_envelope) if wire.next_envelope is not None else None,
        detail=wire.detail,
    )


def completion_submission(completion: Completion) -> CompletionSubmission:
    return CompletionSubmission(
        choice=completion.choice,
        epoch=completion.epoch,
        runner_id=completion.runner_id,
        from_node_id=completion.from_node_id,
        check_results=[CheckResult(command=c.command, passed=c.passed) for c in completion.check_results],
        artifacts=[_submitted(a) for a in completion.artifacts],
        decision_id=completion.decision_id,
        route_token=completion.route_token,
        lease_id=completion.lease_id,
    )


def completion_of(wire: CompletionSubmission) -> Completion:
    return Completion(
        choice=wire.choice,
        epoch=wire.epoch,
        runner_id=wire.runner_id,
        from_node_id=wire.from_node_id,
        check_results=[CheckVerdict(command=c.command, passed=c.passed) for c in wire.check_results],
        artifacts=[_artifact_of(a) for a in wire.artifacts],
        decision_id=wire.decision_id,
        route_token=wire.route_token,
        lease_id=wire.lease_id,
    )


def decision_submission(gate: GateSubmission) -> DecisionSubmission:
    return DecisionSubmission(
        from_node_id=gate.from_node_id,
        epoch=gate.epoch,
        runner_id=gate.runner_id,
        artifacts=[_submitted(a) for a in gate.artifacts],
        route_token=gate.route_token,
        lease_id=gate.lease_id,
    )


def gate_submission_of(wire: DecisionSubmission) -> GateSubmission:
    return GateSubmission(
        from_node_id=wire.from_node_id,
        epoch=wire.epoch,
        runner_id=wire.runner_id,
        artifacts=[_artifact_of(a) for a in wire.artifacts],
        route_token=wire.route_token,
        lease_id=wire.lease_id,
    )


def _submitted(artifact: CompletionArtifact) -> SubmittedArtifact:
    return SubmittedArtifact(
        name=artifact.name,
        kind=artifact.kind,
        forge=artifact.forge,
        repo=artifact.repo,
        branch_name=artifact.branch_name,
        commit_hash=artifact.commit_hash,
        content=artifact.content,
        attached=artifact.attached,
    )


def _artifact_of(wire: SubmittedArtifact) -> CompletionArtifact:
    return CompletionArtifact(
        name=wire.name,
        kind=wire.kind,
        forge=wire.forge,
        repo=wire.repo,
        branch_name=wire.branch_name,
        commit_hash=wire.commit_hash,
        content=wire.content,
        attached=wire.attached,
    )
