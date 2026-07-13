"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): every table here records a
thing that definitely happened at a definite time; no ``status`` column exists,
and the derivations over these rows live in :mod:`blizzard.hub.domain.work` (D-004).
Timestamps are stamped by application code from the injected clock, never a
``server_default=func.now()`` (``bzh:injected-clock``). Portable-SQL surface only
(``bzh:sql-portable``): the same DDL runs on sqlite and postgres.

The walking-skeleton tables (P6) carry ONE chunk ingest -> claim -> commit ->
deliver -> land end to end. Tables the thin slice does not yet exercise
(``chunk_stopped``, ``escalations``) are present because the status-derivation
*precedence* is only correct with them — a seam shaped, not dead weight. Ask/answer
and gate/decision tables are P7 (ORCHESTRATION.md); ``transitions.decision_id`` is
already carried, un-constrained, so they bolt on without reshaping.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)

metadata = MetaData()

# --- Workflow graphs (immutable definitions, reified — D-033/D-071) ---------

graphs = Table(
    "graphs",
    metadata,
    Column("graph_id", String, primary_key=True),  # gr_<ulid>
    Column("name", String, nullable=False),
    Column("entry_node_id", String, nullable=False),
    Column("definition_yaml", Text, nullable=False),  # the inlined source, for audit/re-export
    Column("created_at", DateTime, nullable=False),
)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("node_id", String, primary_key=True),  # nd_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("executor", String, nullable=False),  # runner | hub
    Column("prompt", Text, nullable=True),  # inlined text, never a path (D-033)
    Column("judgement_prompt", Text, nullable=True),  # the verdict-elicitation prompt (D-038); null at a gate/hub node
    Column("session", String, nullable=False),  # resume | fresh
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("mode", String, nullable=True),  # deliver hub node: merge-to-main | open-pr
)

graph_choices = Table(
    "graph_choices",
    metadata,
    Column("choice_id", String, primary_key=True),  # cho_<ulid>
    Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
    Column("to_node_name", String, nullable=False),  # a node name, or the reserved 'done'
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context (D-038)
)

# --- Chunks and their PM pointers (chunk.minted — D-024/D-047) --------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid> (D-075)
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", DateTime, nullable=False),
)

chunk_pm_pointers = Table(
    "chunk_pm_pointers",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("provider", String, nullable=False),
    Column("url", String, nullable=False),
)

# --- Movement record (transition.recorded — D-027/D-036) --------------------

transitions = Table(
    "transitions",
    metadata,
    Column("transition_id", String, primary_key=True),  # tr_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only (D-045); shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest (D-007)
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator (D-079)
    Column("recorded_at", DateTime, nullable=False),
)

# --- Artifacts (the chunk artifact store — D-036) ---------------------------

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String, primary_key=True),  # art_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # exact provenance
    Column("node_name", String, nullable=False),  # the {node} store-key component (name, not id)
    Column("epoch", Integer, nullable=False),
    Column("name", String, nullable=False),  # the {artifact-name} store-key component
    Column("kind", String, nullable=False),  # git_commit | asset
    Column("data", Text, nullable=False),  # '<branch>:<commit>' | raw content (D-036)
    Column("repo", String, nullable=True),  # git_commit only
    Column("produced_at", DateTime, nullable=False),
)

# --- Lease facts (lease.minted, runner-reported — D-044) --------------------

lease_facts = Table(
    "lease_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the fence input the transition check consumes (D-007)
    Column("runner_id", String, nullable=False),
    Column("minted_at", DateTime, nullable=False),
)

# --- Routes (route.created / route.released — D-021/D-080/D-088) ------------

route_created = Table(
    "route_created",
    metadata,
    Column("route_id", String, primary_key=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("runner_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("created_at", DateTime, nullable=False),
)

route_environments = Table(
    "route_environments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String, ForeignKey("route_created.route_id"), nullable=False),
    Column("environment_id", String, nullable=False),  # opaque (D-021)
)

route_released = Table(
    "route_released",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("released_at", DateTime, nullable=False),
)

# --- Delivery landing facts (per-repo, then whole-chunk — D-030/D-091) ------

delivery_repo_landed = Table(
    "delivery_repo_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("commit_hash", String, nullable=False),
    Column("landed_at", DateTime, nullable=False),
)

delivery_landed = Table(
    "delivery_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("landed_at", DateTime, nullable=False),  # terminal: all repos landed (D-030)
)

# --- Facts that make the derivation precedence correct (shaped) -------------

chunk_stopped = Table(
    "chunk_stopped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("stopped_at", DateTime, nullable=False),  # terminal operator abandonment (D-067)
)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by a later lease mint, not a resolution (D-067)
    Column("recorded_at", DateTime, nullable=False),
)
