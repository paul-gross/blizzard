"""The hub store's SQLAlchemy metadata — the target for its Alembic tree.

Facts only, status derived (``bzh:facts-not-status``): every table records a thing that
definitely happened at a definite time, and no ``status`` column exists. Timestamps are
stamped by application code from the injected clock (``bzh:injected-clock``), never a
``server_default``. Portable-SQL surface only (``bzh:sql-portable``)."""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from blizzard.foundation.store.utc import UtcDateTime

metadata = MetaData()

# --- Workflow graphs (immutable definitions, reified) -------------------------

graphs = Table(
    "graphs",
    metadata,
    Column("graph_id", String, primary_key=True),  # gr_<ulid>
    Column("name", String, nullable=False),
    Column("entry_node_id", String, nullable=False),
    Column("definition_yaml", Text, nullable=False),  # the inlined source, for audit/re-export
    Column("created_at", UtcDateTime, nullable=False),
)
Index("ix_graphs_name", graphs.c.name)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("node_id", String, primary_key=True),  # nd_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("executor", String, nullable=False),  # runner | hub
    Column("prompt", Text, nullable=True),  # inlined text, never a path
    Column("judgement_prompt", Text, nullable=True),  # the verdict-elicitation prompt; null at a gate/hub node
    Column("session", String, nullable=False),  # resume | fresh
    # Target of ``session: resume:<name>``; null for bare resume/fresh.
    Column("session_source", String, nullable=True),
    Column("judged_by", String, nullable=False),  # worker | human
    Column("retries_max", Integer, nullable=True),
    Column("retries_exhausted", String, nullable=True),  # escalate
    Column("produces", Text, nullable=True),  # JSON list of artifact names; e.g. review's `review-findings`
    Column("checks", Text, nullable=True),  # JSON list of check commands, runner-run at worker exit (#114)
    # Null uses the env workdir / default check timeout (#114).
    Column("checks_cwd", String, nullable=True),
    Column("checks_timeout", Integer, nullable=True),
    # The kick-back cap (#64) — null accepts the fleet default (``graph.DEFAULT_BOUNCE_CAP``).
    Column("bounce_cap", Integer, nullable=True),
    # Hub commands (#65), JSON ``{command, name, produces}`` list.
    Column("run", Text, nullable=True),
    # Pending-poll cadence (#66); null uses the executor defaults.
    Column("poll_interval_seconds", Integer, nullable=True),
    Column("poll_timeout_seconds", Integer, nullable=True),
    # Null/false disallows proposed work items.
    Column("proposes_work_items", Boolean, nullable=True),
)
Index("ix_graph_nodes_graph_id", graph_nodes.c.graph_id)

graph_choices = Table(
    "graph_choices",
    metadata,
    Column("choice_id", String, primary_key=True),  # cho_<ulid>
    Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
    # Null/false is ungated (#114).
    Column("requires_checks", Boolean, nullable=True),
)
Index("ix_graph_choices_node_id", graph_choices.c.node_id)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("edge_id", String, primary_key=True),
    Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
    Column("to_node_name", String, nullable=False),  # a node name, the reserved 'done', or 'graph:<name>' (#90)
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context
    # Cross-graph model override (#90); null keeps the current model.
    Column("to_graph_model", String, nullable=True),
)
Index("ix_graph_edges_from_node_id", graph_edges.c.from_node_id)

# Named sessions keyed by `(graph_id, name)`.
graph_sessions = Table(
    "graph_sessions",
    metadata,
    Column("graph_id", String, ForeignKey("graphs.graph_id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),  # authored `sessions:` position, display-only
    # Opaque JSON model preferences (``bzh:pluggable-seams``).
    Column("model", Text, nullable=True),
    Column("effort", String, nullable=True),  # a single aliased value; null declares none
    # Independent rotation bounds; null means unbounded.
    Column("rotate_max_context_tokens", Integer, nullable=True),
    Column("rotate_max_transcript_bytes", Integer, nullable=True),
    Column("rotate_max_invocations", Integer, nullable=True),
    # The compaction window, opaque like `effort`; null declares none.
    Column("compaction_window", String, nullable=True),
    # Ordered JSON harness preferences; null is unconstrained.
    Column("harnesses", Text, nullable=True),
)

# Graph artifacts keyed by `(graph_id, name)`.
graph_artifacts = Table(
    "graph_artifacts",
    metadata,
    Column("graph_id", String, ForeignKey("graphs.graph_id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),  # authored `artifacts:` position — every read orders by it
    Column("content", Text, nullable=False),
)

# --- Graph lifecycle facts (graph.retired / graph.enabled) -------------------

graph_lifecycle_facts = Table(
    "graph_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)
Index("ix_graph_lifecycle_facts_graph_id", graph_lifecycle_facts.c.graph_id)

# Follow-latest policy: newest fact wins; null inherits the hub setting.
graph_policy_facts = Table(
    "graph_policy_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("follow_latest", Boolean, nullable=True),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Scopes (operator-authored, slug-keyed buckets) ---

scopes = Table(
    "scopes",
    metadata,
    Column("slug", String, primary_key=True),
    Column("description", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
)

# Scope retire/enable facts: append-only, newest wins.
scope_lifecycle_facts = Table(
    "scope_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("slug", String, ForeignKey("scopes.slug"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Secrets (write-only credentials, sealed under a hub-key generation) ---

secrets = Table(
    "secrets",
    metadata,
    Column("name", String, primary_key=True),
    Column("ciphertext", Text, nullable=False),  # base64 AES-256-GCM output
    Column("nonce", Text, nullable=False),  # base64 96-bit nonce, fresh per write
    Column("key_id", String, nullable=False),  # the hub-key generation that sealed it
    Column("revision", Integer, nullable=False),
    Column("replaced_at", UtcDateTime, nullable=False),
    Column("replaced_by", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
)

# Secret retire/enable facts: append-only, newest wins.
secret_lifecycle_facts = Table(
    "secret_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, ForeignKey("secrets.name"), nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Work sources (configured records; secret_name references a secret, never a value) ---

work_sources = Table(
    "work_sources",
    metadata,
    Column("name", String, primary_key=True),
    Column("provider", String, nullable=False),
    Column("locator", String, nullable=False),
    Column("api_base", String, nullable=True),
    Column("web_base", String, nullable=True),
    Column("annotate", Boolean, nullable=False),
    Column("secret_name", String, ForeignKey("secrets.name"), nullable=True),
    Column("revision", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String, nullable=False),
    UniqueConstraint("provider", "locator", name="uq_work_sources_provider_locator"),
)

# Work source retire/enable facts: append-only, newest wins.
work_source_lifecycle_facts = Table(
    "work_source_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, ForeignKey("work_sources.name"), nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Repositories (configured records; secret_name references a secret, never a value) ---

repositories = Table(
    "repositories",
    metadata,
    Column("name", String, primary_key=True),
    Column("forge_api_url", String, nullable=False),
    Column("owner", String, nullable=False),
    Column("repo", String, nullable=False),
    Column("base_branch", String, nullable=False),
    Column("secret_name", String, ForeignKey("secrets.name"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String, nullable=False),
    UniqueConstraint("forge_api_url", "owner", "repo", name="uq_repositories_coordinate"),
)

# Repository retire/enable facts: append-only, newest wins.
repository_lifecycle_facts = Table(
    "repository_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, ForeignKey("repositories.name"), nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Configuration change log (one fact row per committed write to a configured record) ---

config_changes = Table(
    "config_changes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("actor", String, nullable=False),
    Column("door", String, nullable=False),  # board | cli | api | apply | migration
    Column("record_kind", String, nullable=False),
    Column("record_key", String, nullable=False),
    Column("revision", Integer, nullable=False),  # the record's revision as written
    Column("op", String, nullable=False),  # create | edit | retire | enable | replace
    Column("diff", Text, nullable=False),  # JSON [{field, old, new}]; never a secret value
    Column("apply_id", String, nullable=True),
)
Index("ix_config_changes_record", config_changes.c.record_kind, config_changes.c.record_key, config_changes.c.id)

# One append-only fact per carry-over of a file-configured hub into its records.
config_import_facts = Table(
    "config_import_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("imported_at", UtcDateTime, nullable=False),
    Column("actor", String, nullable=False),
    Column("read", Text, nullable=False),  # JSON {config_path, sources, variables}; names only, never a value
)

# --- Routines (mutable graph, scope and run defaults; surrogate id) ---

routines = Table(
    "routines",
    metadata,
    Column("routine_id", String, primary_key=True),  # rtn_<ulid>
    Column("name", String, nullable=False),
    Column("graph_name", String, nullable=False),  # a graph *name*, not a graph_id
    Column("default_scope_slug", String, ForeignKey("scopes.slug"), nullable=False),
    # Nullable JSON model preferences and effort; empty means express none.
    Column("default_model", Text, nullable=True),
    Column("default_effort", String, nullable=True),
    # Nullable JSON harness preferences; empty means express none.
    Column("default_harnesses", Text, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    UniqueConstraint("name", name="uq_routines_name"),
)

# Mutable routine-to-scope membership.

routine_scopes = Table(
    "routine_scopes",
    metadata,
    Column("routine_id", String, ForeignKey("routines.routine_id"), primary_key=True),
    Column("scope_slug", String, ForeignKey("scopes.slug"), primary_key=True),
)

Index("ix_routine_scopes_scope_slug", routine_scopes.c.scope_slug)

# The routine's reversible retire/enable brake — scope_lifecycle_facts's own shape.
routine_lifecycle_facts = Table(
    "routine_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("routine_id", String, ForeignKey("routines.routine_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# --- Chunks and their work refs (chunk.minted) ------------------------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True),  # ch_<ulid>
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),  # pinned at mint
    Column("minted_at", UtcDateTime, nullable=False),
    # Retained, unread legacy column; inserts use the migration's server default.
    Column("model", String, nullable=False),
    # Nullable JSON model preferences and effort; empty means express none.
    Column("default_model", Text, nullable=True),
    Column("default_effort", String, nullable=True),
    # Nullable JSON harness preferences; empty means express none.
    Column("default_harnesses", Text, nullable=True),
    # Next-transition migration intent, JSON; null while unset.
    Column("intended_migration", Text, nullable=True),
)
# (minted_at, chunk_id) for newest-first bounded reads since a timestamp.
Index("ix_chunks_minted_at_chunk_id", chunks.c.minted_at, chunks.c.chunk_id)

chunk_work_refs = Table(
    "chunk_work_refs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("ref", String, nullable=False),
)
Index("ix_chunk_work_refs_chunk_id", chunk_work_refs.c.chunk_id)
# Leading source column also serves source-only filters.
Index("ix_chunk_work_refs_source_ref", chunk_work_refs.c.source, chunk_work_refs.c.ref)

# --- Keyed write locks ----------------------------------------------------------

# Lock-only rows: a decision whose race has no existing row to lock locks one of these
# instead (``bzh:store-exclusive-write``). Holds no state and is never read as a fact;
# rows are inserted once and never deleted.
keyed_locks = Table(
    "keyed_locks",
    metadata,
    Column("namespace", String, primary_key=True),
    Column("key", String, primary_key=True),
)

# --- Movement record (transition.recorded) ------------------------------------

transitions = Table(
    "transitions",
    metadata,
    Column("transition_id", String, primary_key=True),  # tr_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    # Historical graph pin; not a FK to the chunk's mutable pin.
    Column("graph_id", String, nullable=False),
    Column("from_node_id", String, nullable=True),  # null on the first transition out of entry
    Column("to_node_id", String, nullable=False),  # a node_id, or 'done' terminal
    Column("choice_name", String, nullable=True),  # the judgement's selected choice
    Column("decision_id", String, nullable=True),  # gates only; shaped for P7
    Column("epoch", Integer, nullable=False),  # the fencing epoch checked against latest
    Column("runner_id", String, nullable=False),  # reporting author, or the hub coordinator
    Column("recorded_at", UtcDateTime, nullable=False),
)
# Leading chunk_id also serves chunk-only filters.
Index("ix_transitions_chunk_id_epoch", transitions.c.chunk_id, transitions.c.epoch)

# Newest-first bounded reads on the high-volume transition table.
Index("ix_transitions_recorded_at_transition_id", transitions.c.recorded_at, transitions.c.transition_id)

# Delivery-materialization candidates target the terminal node.
Index("ix_transitions_to_node_id", transitions.c.to_node_id)

# --- Cross-graph migration record (chunk_migrations) ---------------
# Its own fact, never a ``transitions`` row (``bzh:migration-not-transition``).

chunk_migrations = Table(
    "chunk_migrations",
    metadata,
    Column("migration_id", String, primary_key=True),  # mg_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("from_node_id", String, nullable=True),  # the node the migrating choice left
    Column("from_graph_id", String, nullable=False),  # the graph migrated out of
    Column("to_graph_id", String, nullable=False),  # the graph re-pinned to
    Column("landed_node_id", String, nullable=True),  # concrete landing node; null = target entry
    Column("choice_name", String, nullable=True),  # the triggering judgement choice
    Column("decision_id", String, nullable=True),  # gate migrations only — the decision this closes (#90)
    Column("model_after", String, nullable=True),  # the re-pinned model, or null (kept current)
    Column("epoch", Integer, nullable=False),  # the submitting fence; the natural-key third part
    Column("recorded_at", UtcDateTime, nullable=False),
    # Migration cause; null for legacy rows.
    Column("source", String, nullable=True),
)
Index("ix_chunk_migrations_chunk_id", chunk_migrations.c.chunk_id)
# (recorded_at, migration_id) for newest-first bounded reads since a timestamp.
Index("ix_chunk_migrations_recorded_at_migration_id", chunk_migrations.c.recorded_at, chunk_migrations.c.migration_id)

# --- Artifacts (the chunk artifact store) --------------------------------------

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
    Column("data", Text, nullable=False),  # '<branch>:<commit>' | raw content
    Column("repo", String, nullable=True),  # git_commit only
    Column("forge", String, nullable=True),  # git_commit only; null = legacy row
    Column("produced_at", UtcDateTime, nullable=False),
    # Durable write order, assigned under the chunk-row lock; not unique.
    Column("seq", Integer, nullable=False),
)
# Leading chunk_id also serves chunk-only artifact reads.
Index("ix_artifacts_chunk_id_node_id_epoch", artifacts.c.chunk_id, artifacts.c.node_id, artifacts.c.epoch)

# --- Findings and finding sets -----------------------------------
# Finding liveness and observation counts derive from append-only finding_facts.

findings = Table(
    "findings",
    metadata,
    Column("finding_id", String, primary_key=True),  # fin_<ulid>
    # Routine name; null for review-sourced findings.
    Column("routine_name", String, nullable=True),
    Column("scope_slug", String, ForeignKey("scopes.slug"), nullable=False),
    Column("class", String, key="class_", nullable=False),  # the deployment's own vocabulary; opaque to the hub
    Column("locus", String, nullable=False),  # a repo-relative path, optionally :line/::symbol; opaque to the hub
    Column("summary", Text, nullable=False),
    Column("introduced", String, nullable=True),  # best-effort blame commit; null when not resolvable
    # Authored instant, null when unresolved; never backfilled.
    Column("introduced_at", UtcDateTime, nullable=True),
    # Origin of the finding, not its liveness.
    Column("source", String, nullable=False, server_default="routine"),
    # Review severity; null for routine-sourced findings.
    Column("severity", String, nullable=True),
    # The chunk whose review raised this finding — null for a routine-sourced finding.
    Column("raised_by_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=True),
    CheckConstraint("source IN ('routine', 'review')", name="ck_findings_source"),
)

Index("ix_findings_routine_scope", findings.c.routine_name, findings.c.scope_slug)
Index("ix_findings_routine_class", findings.c.routine_name, findings.c.class_)
# Scope/source index includes review-sourced findings in garden reads.
Index("ix_findings_scope_source", findings.c.scope_slug, findings.c.source)

# Append-only finding changes; first/last seen and counts derive here.

finding_facts = Table(
    "finding_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("finding_id", String, ForeignKey("findings.finding_id"), nullable=False),
    Column("kind", String, nullable=False),  # FACT_KINDS, domain/garden/findings/model.py
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("note", Text, nullable=True),  # gone's/delivered's/an exit's/reopened's note; null for add/observed
    # Who recorded a human-driven fact — null for a run-driven add/observed/gone.
    Column("actor", String, nullable=True),
    # Proposal answered by a delivered fact; null for hand/later resolutions.
    Column("proposal_id", String, ForeignKey("garden_proposals.proposal_id"), nullable=True),
    # The absorbing finding, set only on a `superseded` fact.
    Column("superseded_by", String, ForeignKey("findings.finding_id"), nullable=True),
    # Delivery set for add/observed/gone; null for exits and legacy facts.
    Column("finding_set_id", String, ForeignKey("finding_sets.finding_set_id"), nullable=True),
    # Submission-local ref on add facts; null otherwise.
    Column("ref", String, nullable=True),
    CheckConstraint(
        "kind IN ('add', 'observed', 'gone', 'delivered', 'resolved', 'gone-confirmed', 'wont-fix',"
        " 'not-a-finding', 'superseded', 'reopened')",
        name="ck_finding_facts_kind",
    ),
)

Index("ix_finding_facts_finding_id_id", finding_facts.c.finding_id, finding_facts.c.id)

# Run detail reads facts by delivered set.
Index("ix_finding_facts_finding_set_id", finding_facts.c.finding_set_id)

# One delivered set per artifact, holding scope, revisions and measurement.

finding_sets = Table(
    "finding_sets",
    metadata,
    Column("finding_set_id", String, primary_key=True),  # fins_<ulid>
    Column("artifact_id", String, ForeignKey("artifacts.artifact_id"), nullable=False, unique=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the run that delivered it
    Column("scope_slug", String, ForeignKey("scopes.slug"), nullable=False),
    # Routine name; a chunk's mixed-source pointers cannot identify it.
    Column("routine_name", String, nullable=False, server_default=""),
    Column("revisions", Text, nullable=False),  # JSON {repo: revision} (`bzh:sql-portable`)
    Column("measurement", Text, nullable=True),  # opaque, routine-strategy-defined; null when none was recorded
)

Index("ix_finding_sets_chunk_id", finding_sets.c.chunk_id)
Index("ix_finding_sets_routine_scope", finding_sets.c.routine_name, finding_sets.c.scope_slug)

# --- Garden proposals (distinct from work-item proposals) --------

garden_proposals = Table(
    "garden_proposals",
    metadata,
    Column("proposal_id", String, primary_key=True),  # gprop_<ulid>
    # Mint-time origin, never inferred from routine_name.
    Column("origin", String, nullable=False, server_default="routine-run"),
    # Required for routine-run; optional for operator origin.
    Column("routine_name", String, nullable=True),
    # The identity that authored an `operator` proposal — null for `routine-run`.
    Column("created_by", String, nullable=True),
    Column("class", String, key="class_", nullable=False),  # the deployment's own taxonomy; opaque to the hub
    Column("title", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    # Delivery idempotence key; null outside delivery. No FK (SQLite drop-column).
    Column("source_artifact_id", String, nullable=True),
    Column("ref", String, nullable=True),
    CheckConstraint("origin IN ('routine-run', 'operator')", name="ck_garden_proposals_origin"),
    CheckConstraint(
        "origin != 'routine-run' OR routine_name IS NOT NULL", name="ck_garden_proposals_routine_run_has_routine"
    ),
    CheckConstraint(
        "origin != 'operator' OR created_by IS NOT NULL", name="ck_garden_proposals_operator_has_created_by"
    ),
)

Index("ix_garden_proposals_routine_class", garden_proposals.c.routine_name, garden_proposals.c.class_)
Index(
    "ux_garden_proposals_source_artifact_ref",
    garden_proposals.c.source_artifact_id,
    garden_proposals.c.ref,
    unique=True,
)

# Optional finding links as queryable joins (``bzh:sql-portable``).

garden_proposal_findings = Table(
    "garden_proposal_findings",
    metadata,
    Column("proposal_id", String, ForeignKey("garden_proposals.proposal_id"), primary_key=True),
    Column("finding_id", String, ForeignKey("findings.finding_id"), primary_key=True),
)

Index("ix_garden_proposal_findings_finding_id", garden_proposal_findings.c.finding_id)

# --- Garden proposal closures (terminal, unique per proposal) -----

garden_proposal_closures = Table(
    "garden_proposal_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("proposal_id", String, ForeignKey("garden_proposals.proposal_id"), nullable=False),
    Column("closure", String, nullable=False),  # passed | accepted
    Column("reason", String, nullable=True),
    Column("closed_by", String, nullable=False),
    Column("closed_at", UtcDateTime, nullable=False),
    Column("item_outcome", String, nullable=True),  # minted | declined; null on a pass
    Column("source", String, nullable=True),  # the minted item's pointer; null when none
    Column("ref", String, nullable=True),
    UniqueConstraint("proposal_id", name="uq_garden_proposal_closures_proposal_id"),
)

# Unique reverse item lookup for `find_by_item`.
Index(
    "ix_garden_proposal_closures_source_ref",
    garden_proposal_closures.c.source,
    garden_proposal_closures.c.ref,
    unique=True,
)

# --- Proposed work items (ride a node-step's completion, materialized at delivery) ----

work_item_proposals = Table(
    "work_item_proposals",
    metadata,
    Column("proposal_id", String, primary_key=True),  # wip_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # exact provenance
    Column("node_name", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("ordinal", Integer, nullable=False),  # authored-submission order, `graph_artifacts`-shaped
    Column("kind", String, nullable=False),  # create | update
    Column("data", Text, nullable=False),  # JSON object, kind-shaped
    Column("proposed_at", UtcDateTime, nullable=False),
    # Null legacy proposer materializes as unresolved.
    Column("runner_id", String, nullable=True),
)
Index("ix_work_item_proposals_chunk_id", work_item_proposals.c.chunk_id)

# --- Proposal materialization outcomes (terminal, once per proposal) ---

work_item_materializations = Table(
    "work_item_materializations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("proposal_id", String, ForeignKey("work_item_proposals.proposal_id"), nullable=False),
    Column("outcome", String, nullable=False),  # created | updated | unresolved
    Column("source", String, nullable=True),  # the resulting/targeted item's pointer; null when unresolved names none
    Column("ref", String, nullable=True),
    Column("reason", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    UniqueConstraint("proposal_id", name="uq_work_item_materializations_proposal_id"),
)

# --- Proposal strikes (exclude from future materialization sweeps) ---

work_item_strikes = Table(
    "work_item_strikes",
    metadata,
    Column("proposal_id", String, ForeignKey("work_item_proposals.proposal_id"), primary_key=True),
    Column("decision_id", String, ForeignKey("decisions.decision_id"), nullable=False),
    Column("struck_by", String, nullable=False),
    Column("struck_at", UtcDateTime, nullable=False),
)

# --- Lease facts (lease.minted, runner-reported) -------------------------------

lease_facts = Table(
    "lease_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the fence input the transition check consumes
    Column("runner_id", String, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
    Column("lease_id", String, nullable=True),  # the lease the mint named; null when it named none
)
# Leading chunk_id also serves chunk-only filters.
Index("ix_lease_facts_chunk_id_epoch", lease_facts.c.chunk_id, lease_facts.c.epoch)
# The events backfill's epochs-minted-in-a-window read.
Index("ix_lease_facts_minted_at", lease_facts.c.minted_at)

# --- Epoch owners (first owner wins; null runner_id means hub) -----
epoch_owners = Table(
    "epoch_owners",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("runner_id", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    UniqueConstraint("chunk_id", "epoch", name="uq_epoch_owners_chunk_id_epoch"),
)
# (recorded_at, id) for the trace sweep's closing-fact read since a timestamp.
Index("ix_epoch_owners_recorded_at_id", epoch_owners.c.recorded_at, epoch_owners.c.id)

# --- Routes (route.created / route.released) ----------------------------------

route_created = Table(
    "route_created",
    metadata,
    Column("route_id", String, primary_key=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("runner_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    # The monotonic route-event tiebreak (see work.RouteHistory) — a
    # per-chunk counter shared with route_released.seq, assigned in real write order.
    Column("seq", Integer, nullable=False),
)
Index("ix_route_created_chunk_id", route_created.c.chunk_id)
# A runner's held routes — retirement's by-runner holdings read.
Index("ix_route_created_runner_id", route_created.c.runner_id)
# (created_at, route_id) for newest-first bounded reads since a timestamp.
Index("ix_route_created_created_at_route_id", route_created.c.created_at, route_created.c.route_id)

route_environments = Table(
    "route_environments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String, ForeignKey("route_created.route_id"), nullable=False),
    Column("environment_id", String, nullable=False),  # opaque
)

route_released = Table(
    "route_released",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("released_at", UtcDateTime, nullable=False),
    # See route_created.seq — the same per-chunk counter, so a created/released pair
    # tied on timestamp is still totally ordered by real write order.
    Column("seq", Integer, nullable=False),
)
Index("ix_route_released_chunk_id", route_released.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index("ix_route_released_released_at_id", route_released.c.released_at, route_released.c.id)

# --- Route capability tokens (route_token_minted) ----------------
# Only the sha256 digest is persisted; ``seq`` shares the per-chunk route counter.
route_token_minted = Table(
    "route_token_minted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("token_hash", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("minted_at", UtcDateTime, nullable=False),
)
Index("ix_route_token_minted_chunk_id", route_token_minted.c.chunk_id)

# --- Delivery landing facts (per-repo, then whole-chunk) ----------------------

delivery_repo_landed = Table(
    "delivery_repo_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("commit_hash", String, nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),
)
Index("ix_delivery_repo_landed_chunk_id", delivery_repo_landed.c.chunk_id)

delivery_landed = Table(
    "delivery_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),  # terminal: all repos landed
)
Index("ix_delivery_landed_chunk_id", delivery_landed.c.chunk_id)

# --- Delivery closure facts (work_item_closures) -----------------
# One row per close-attempt outcome; `closed`/`gone` are terminal, `failed` is retried.

work_item_closures = Table(
    "work_item_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("ref", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("reason", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    UniqueConstraint("chunk_id", "source", "ref", "outcome", name="uq_work_item_closures_chunk_source_ref_outcome"),
)

# --- Close intent outbox (close_intents) ---------------------------------------
# One row per work ref a landing or completion owes a closure attempt; `retired_at` NULL is pending, never deleted.

close_intents = Table(
    "close_intents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),
    Column("ref", String, nullable=False),
    Column("enqueued_at", UtcDateTime, nullable=False),
    Column("retired_at", UtcDateTime, nullable=True),  # null = pending; set when the drainer retires it
    UniqueConstraint("chunk_id", "source", "ref", name="uq_close_intents_chunk_source_ref"),
)
# A pending-intents filter on `retired_at IS NULL` — sqlite indexes NULLs too, so a
# plain index still serves it.
Index("ix_close_intents_pending", close_intents.c.retired_at)

# --- Close-intent drain attempts (close_intent_attempts) ------------
# Append-only (`bzh:facts-not-status`): a terminal outcome retires the intent instead of writing a row here.

close_intent_attempts = Table(
    "close_intent_attempts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("intent_id", Integer, ForeignKey("close_intents.id"), nullable=False),
    Column("attempted_at", UtcDateTime, nullable=False),
    Column("outcome", String, nullable=False),  # skipped | failed
)
Index("ix_close_intent_attempts_intent_id", close_intent_attempts.c.intent_id)

# --- Delivery kick-backs (chunk_bounces — #64) --------------------------------
# Contention, not failure: consumes no node retry, natural-keyed ``(chunk_id, epoch)``.

chunk_bounces = Table(
    "chunk_bounces",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # the coordinator's hub_epoch — the natural key
    Column("cause", String, nullable=False),  # conflict | checks | master-moved
    Column("envelope", Text, nullable=False),  # JSON kick-back payload
    Column("recorded_at", UtcDateTime, nullable=False),
)
# (chunk_id, epoch) serves both a plain chunk_id filter and a per-attempt (chunk_id,
# epoch) anti-join, at no extra write cost over a single-column index.
Index("ix_chunk_bounces_chunk_id_epoch", chunk_bounces.c.chunk_id, chunk_bounces.c.epoch)

# --- The fleet-wide hub-execution serialization slot (#65) -------------------
# A live slot has ``released_at IS NULL``; one at a time, reclaimable past its TTL.

hub_exec_slot = Table(
    "hub_exec_slot",
    metadata,
    Column("slot_id", String, primary_key=True),  # hes_<ulid>
    Column("holder_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
    Column("released_at", UtcDateTime, nullable=True),  # null while live
)

# --- The generic hub command node's pending-poll attempts (#66) --------------
# One append-only row per poll attempt; pending-ness derives from them, never memory.

hub_node_poll = Table(
    "hub_node_poll",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("polled_at", UtcDateTime, nullable=False),
)
Index("ix_hub_node_poll_chunk_id", hub_node_poll.c.chunk_id)

# --- Readiness: the not-ready resting state and its promotion --------
# A chunk with no ``chunk_promoted`` row derives ``not_ready`` and is never claimed.

chunk_promoted = Table(
    "chunk_promoted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("promoted_at", UtcDateTime, nullable=False),  # not_ready -> ready
)
Index("ix_chunk_promoted_chunk_id", chunk_promoted.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index("ix_chunk_promoted_promoted_at_id", chunk_promoted.c.promoted_at, chunk_promoted.c.id)

# --- Facts that make the derivation precedence correct (shaped) -------------

chunk_stopped = Table(
    "chunk_stopped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("stopped_at", UtcDateTime, nullable=False),  # terminal operator abandonment
    # Who stopped it — nullable: a row predating the column reads back `None`.
    Column("stopped_by", String, nullable=True),
)
Index("ix_chunk_stopped_chunk_id", chunk_stopped.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index("ix_chunk_stopped_stopped_at_id", chunk_stopped.c.stopped_at, chunk_stopped.c.id)

# An operator's manual completion — outranks a ``chunk_stopped`` row recorded
# at or before it (``ChunkFacts._operator_completion_outranks_stop``), the motivating case.
chunk_completed = Table(
    "chunk_completed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("completed_at", UtcDateTime, nullable=False),
    Column("completed_by", String, nullable=False),
)
Index("ix_chunk_completed_chunk_id", chunk_completed.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index("ix_chunk_completed_completed_at_id", chunk_completed.c.completed_at, chunk_completed.c.id)

# The fact that makes an unacquired chunk ephemeral by deletion — a
# ``chunk_grouped``-shaped sibling; ``deleted_by`` is non-null, with no legacy row predating it.
chunk_deleted = Table(
    "chunk_deleted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("deleted_at", UtcDateTime, nullable=False),
    Column("deleted_by", String, nullable=False),
)
# Explicit id tie-break for portable newest-first reads.
Index("ix_chunk_deleted_deleted_at_id", chunk_deleted.c.deleted_at, chunk_deleted.c.id)

# --- Chunk dependency edges --------------------------------------
# One row per edge; ``released_at``/``released_by`` set once, together — never deleted.

chunk_dependencies = Table(
    "chunk_dependencies",
    metadata,
    Column("dependency_id", String, primary_key=True),  # dep_<ulid>
    Column("dependent_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("prerequisite_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("declared_at", UtcDateTime, nullable=False),
    Column("declared_by", String, nullable=False),
    Column("released_at", UtcDateTime, nullable=True),  # null while standing
    Column("released_by", String, nullable=True),
)
Index("ix_chunk_dependencies_dependent_chunk_id", chunk_dependencies.c.dependent_chunk_id)
Index("ix_chunk_dependencies_prerequisite_chunk_id", chunk_dependencies.c.prerequisite_chunk_id)

escalations = Table(
    "escalations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("epoch", Integer, nullable=False),  # closed by supersession, not a resolution
    Column("takeover_command", Text, nullable=False, server_default=""),  # the pasteable resume command
    # The runner-composed ``blizzard runner takeover`` invocation, beside the raw
    # harness-resume ``takeover_command``. Stored pre-composed; empty when none was.
    Column("wrapped_takeover_command", Text, nullable=False, server_default=""),
    # Set only when a gate's resolved choice migrated to an unresolvable target, so the
    # gate's decision derives closed here too. Null otherwise.
    Column("decision_id", String, nullable=True),
    Column("cause", Text, nullable=True),
    Column("detail", Text, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)
Index("ix_escalations_chunk_id", escalations.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index("ix_escalations_recorded_at_id", escalations.c.recorded_at, escalations.c.id)

# --- Usage facts (usage.recorded) --------------------------------
# One row per harness invocation. **Not** epoch-fenced: a zombie's spend is real spend.

usage_facts = Table(
    "usage_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),  # the row's own epoch — carried, never fenced against
    Column("runner_id", String, nullable=False),  # the reporting runner — audit/attribution only
    Column("kind", String, nullable=False),  # spawn | resume | judge
    Column("model", String, nullable=False),
    # The invocation's own recorded harness identity — nullable and
    # un-backfilled; NULL declares unknown, never a value.
    Column("harness_id", String, nullable=True),
    Column("harness_version", String, nullable=True),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_create_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=True),  # None = no billed figure for this invocation — never fabricated
    # A runner-side estimate, kept apart from `cost_usd` — nullable, un-backfilled; see
    # `docs/deployment/spend.md`'s "Estimated cost" section.
    Column("estimated_cost_usd", Float, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
)
Index("ix_usage_facts_chunk_id", usage_facts.c.chunk_id)
# The analytics spend-by-node grouping — otherwise a temp B-tree.
Index("ix_usage_facts_node_id", usage_facts.c.node_id)
# The spend read's range predicate, and the egress sweep's read of usage past a cursor position —
# one composite whose leading column serves both, so a second single-column index never competes.
Index("ix_usage_facts_recorded_at_id", usage_facts.c.recorded_at, usage_facts.c.id)

# --- Questions and answers (the ask/answer rendezvous) ----------------------
# Open exactly while no answer row exists; the answer is first-write-wins CAS on the PK.

questions = Table(
    "questions",
    metadata,
    Column("question_id", String, primary_key=True),  # qn_<ulid> (runner-minted)
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the parked chunk
    Column("node_id", String, nullable=True),  # the node the worker parked at
    Column("session_id", String, nullable=True),  # the dormant session to resume around the answer
    Column("harness_id", String, nullable=True),  # owner of session_id; null from an older peer without it
    Column("runner_id", String, nullable=False),  # the runner holding the session
    Column("epoch", Integer, nullable=False),  # the parked lease's fencing epoch
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] of offered choices (may be empty)
    Column("asked_at", UtcDateTime, nullable=False),  # reap clock stops for the chunk from here
)
Index("ix_questions_chunk_id", questions.c.chunk_id)
# (asked_at, question_id) for newest-first bounded reads since a timestamp.
Index("ix_questions_asked_at_question_id", questions.c.asked_at, questions.c.question_id)

question_answers = Table(
    "question_answers",
    metadata,
    # The primary key IS the question id: the CAS that makes answers first-write-wins —
    # a racing second insert collides and the loser reads back the winning row.
    Column("question_id", String, ForeignKey("questions.question_id"), primary_key=True),
    Column("answer", Text, nullable=False),  # the chosen option or free text, carried into the resume prompt
    Column("answered_by", String, nullable=False),  # who won the CAS
    Column("answered_at", UtcDateTime, nullable=False),
)
# (answered_at, question_id) for newest-first bounded reads since a timestamp; the
# PK IS the question id, so it also serves as the row's own tie-break column.
Index("ix_question_answers_answered_at_question_id", question_answers.c.answered_at, question_answers.c.question_id)

answer_deliveries = Table(
    "answer_deliveries",
    metadata,
    # answer.delivered (runner-minted): the resume-with-answer executed. Board detail
    # only — the chunk's status already flipped to running at question.answered.
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String, ForeignKey("questions.question_id"), nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("delivered_at", UtcDateTime, nullable=False),
)

# --- Human gates: decisions and their resolutions -------------
# Resolved-ness derives: a decision with a resolution row is resolved.

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", String, primary_key=True),  # dec_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # the gate node awaiting the decision
    Column("node_name", String, nullable=False),  # the node's name — what runner gate-config matches
    Column("epoch", Integer, nullable=False),  # the parked step's fence; stale decisions rejected
    Column("choices", Text, nullable=False),  # JSON list of {name, description} — the buttons
    Column("submitted_at", UtcDateTime, nullable=False),
    # The runner whose configuration imposed this gate — a fact of the write; `NULL` when the graph declared it.
    Column("imposed_by_runner_id", String, nullable=True),
)
Index("ix_decisions_chunk_id", decisions.c.chunk_id)
# (submitted_at, decision_id) for newest-first bounded reads since a timestamp.
Index("ix_decisions_submitted_at_decision_id", decisions.c.submitted_at, decisions.c.decision_id)

decision_resolutions = Table(
    "decision_resolutions",
    metadata,
    # decision_id is the PK — the first write wins the CAS; a second resolution is
    # rejected and told who already resolved (like an answer).
    Column("decision_id", String, ForeignKey("decisions.decision_id"), primary_key=True),
    Column("choice", String, nullable=False),  # the picked choice name — routes the resolving transition
    Column("resolved_by", String, nullable=False),
    Column("resolved_at", UtcDateTime, nullable=False),
)
# (resolved_at, decision_id) for newest-first bounded reads since a timestamp; the
# PK IS the decision id, so it also serves as the row's own tie-break column.
Index(
    "ix_decision_resolutions_resolved_at_decision_id",
    decision_resolutions.c.resolved_at,
    decision_resolutions.c.decision_id,
)

# --- Requeue facts (close needs_human by supersession) ------------------------
# `escalation_superseded` owns which facts close one; there is no resolution fact.

requeues = Table(
    "requeues",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("requeued_at", UtcDateTime, nullable=False),  # supersedes an earlier escalation
)
Index("ix_requeues_chunk_id", requeues.c.chunk_id)
# (requeued_at, id) for newest-first bounded reads since a timestamp, portable
# across sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the
# rowid as a tie-break).
Index("ix_requeues_requeued_at_id", requeues.c.requeued_at, requeues.c.id)

# An operator's forced move of a chunk onto a node, now — a movement fact of
# its own, never a transition: nothing judged it and no edge was taken.
chunk_restarts = Table(
    "chunk_restarts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    # The graph ``to_node_id`` belongs to — the target's for a cross-graph move (#371), so a
    # later re-pin cannot strand it, exactly as for a transition.
    Column("graph_id", String, nullable=False),
    Column("from_node_id", String, nullable=True),  # the node left behind; null before the first move
    # ``from_node_id``'s own graph, set only when the move crossed one (#371) — null otherwise,
    # and on every row predating the column, where ``graph_id`` names both ends.
    Column("from_graph_id", String, nullable=True),
    Column("to_node_id", String, nullable=False),  # the node forced onto
    Column("epoch", Integer, nullable=False),  # the fresh fence that preempts the live attempt
    # Set when the move superseded an open gate decision, so that decision derives closed here.
    Column("decision_id", String, nullable=True),
    Column("restarted_by", String, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)
Index("ix_chunk_restarts_chunk_id", chunk_restarts.c.chunk_id)
# (recorded_at, id) for newest-first bounded reads since a timestamp, portable
# across sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the
# rowid as a tie-break).
Index("ix_chunk_restarts_recorded_at_id", chunk_restarts.c.recorded_at, chunk_restarts.c.id)

# --- Chunk pause facts (chunk.paused / chunk.resumed) -----------
# An operator-level brake over one chunk: append-only, newest-fact-wins.

chunk_pause_facts = Table(
    "chunk_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)
Index("ix_chunk_pause_facts_chunk_id", chunk_pause_facts.c.chunk_id)
# (set_at, id) for newest-first bounded reads since a timestamp, portable across
# sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the rowid as
# a tie-break).
Index("ix_chunk_pause_facts_set_at_id", chunk_pause_facts.c.set_at, chunk_pause_facts.c.id)

# --- Store-and-forward high-water mark (per-runner idempotency) ---------------
# The greatest per-runner seq already applied; a fact at or below it is re-acked, not applied.

runner_high_water = Table(
    "runner_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),  # greatest applied per-runner seq
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Queue shaping: ready-queue ordering ----------------------
# A chunk's effective position is its newest fact, else its ``minted_at`` as a unix stamp.

queue_positions = Table(
    "queue_positions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("position", Float, nullable=False),  # lower sorts earlier; newest fact per chunk wins
    Column("set_at", UtcDateTime, nullable=False),
)
# Live-set reads look positions up by candidate chunk id (`bzh:live-set-read`).
Index("ix_queue_positions_chunk_id", queue_positions.c.chunk_id)

# --- Queue shaping: grouping (chunk.grouped) -----------------------------------
# A grouped chunk is EPHEMERAL: removed from every listing, deriving no status at all.

chunk_grouped = Table(
    "chunk_grouped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the merged-away chunk
    Column("grouped_into", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the survivor
    Column("grouped_at", UtcDateTime, nullable=False),
)
# (grouped_at, id) for newest-first bounded reads since a timestamp, portable
# across sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the
# rowid as a tie-break).
Index("ix_chunk_grouped_grouped_at_id", chunk_grouped.c.grouped_at, chunk_grouped.c.id)

# --- The fleet registry (runner.added / registered / paused / resumed) --------
# A row is inserted when a runner is added and refreshed in place by each registration.

runner_registrations = Table(
    "runner_registrations",
    metadata,
    # The hub-minted id — the runner's one identity, and every runner-id column's value.
    Column("runner_id", String, primary_key=True),
    # The display name the runner last registered with (or was added under) — not unique,
    # so deliberately unindexed: nothing looks a runner up by it.
    Column("name", String, nullable=False),
    Column("added_at", UtcDateTime, nullable=False),
    Column("added_by", String, nullable=True),  # who added it — null when the hub recorded no one
    # The workspace binding, first registration, and latest contact — all null for a runner
    # that was added but has never registered.
    Column("workspace_id", String, nullable=True),
    Column("registered_at", UtcDateTime, nullable=True),
    Column("last_seen_at", UtcDateTime, nullable=True),  # liveness derives from this
    # The hub-minted bearer token's sha256 hex digest — nullable (an
    # unenrolled runner has none), indexed for the reverse token lookup.
    Column("token_hash", Text, nullable=True, index=True),
    # The runner's configured environment-pool size — nullable when the runner
    # reports none. Refreshed in place on each re-registration.
    Column("env_capacity", Integer, nullable=True),
    # The runner's own browser-reachable base URL — nullable: a runner that
    # registers none cannot be a federation target. Refreshed in place.
    Column("public_url", Text, nullable=True),
    # The runner's allowed redirect URIs, JSON `list[str]` — exact-matched
    # against a presented `redirect_uri` before a JWT is minted (the open-redirect guard).
    Column("redirect_uris", Text, nullable=True),
    # The runner's reported capability snapshot, JSON `list[dict]`, one per harness binding.
    Column("capabilities", Text, nullable=True),
    # The runner's declared subscription roster, JSON `list[dict]` — unlike `capabilities`,
    # `NULL` (no roster reported) is kept distinct from `[]` (declared none).
    Column("subscriptions", Text, nullable=True),
    # The node names the runner declared it holds for a human decision, JSON `list[str]`; `NULL` reads as none.
    Column("gates", Text, nullable=True),
)

runner_pause_facts = Table(
    "runner_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
)

# Retirement — ``retired`` derives from the newest row; reinstate is a ``retired=False`` fact.
runner_lifecycle_facts = Table(
    "runner_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# Every revoked token hash, never cleared — a revoked token is refused, not merely unresolved.
runner_token_revocations = Table(
    "runner_token_revocations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("token_hash", Text, nullable=False, index=True),
    Column("revoked_at", UtcDateTime, nullable=False),
    Column("revoked_by", String, nullable=False),
)

# The runner's *own* brake, as reported to us — a separate table because the
# hub only ever reads it. No ForeignKey: a fact can arrive before its registration does.

runner_local_pause_facts = Table(
    "runner_local_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, nullable=False),
    Column("paused", Boolean, nullable=False),  # locally_paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),  # the runner's clock, off the fact's payload
    Column("set_by", String, nullable=False),
    # The composed cause string off the fact's payload — nullable, since a
    # manual pause carries none.
    Column("reason", Text, nullable=True),
)

# The runner's latest sampled external-usage snapshot, one row per declared subscription
# — advisory. No ForeignKey: a raise would stall the rail.
runner_external_usage = Table(
    "runner_external_usage",
    metadata,
    Column("runner_id", String, primary_key=True),
    # The runner-unique join key within `runner_id` — a row predating declared
    # subscriptions backfills to the legacy Anthropic slug.
    Column("slug", String, primary_key=True),
    # The declaration's operator-facing label, reported alongside `slug`.
    Column("name", String, nullable=False),
    Column("sampled_at", UtcDateTime, nullable=False),
    # JSON array of {window, utilization_pct, resets_at, window_seconds} — rewritten
    # wholesale on every sample, never queried by its members.
    Column("windows", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# The runner's newest external-usage *miss* per slug — sibling to `runner_external_usage`, no FK.
runner_external_usage_misses = Table(
    "runner_external_usage_misses",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("slug", String, primary_key=True),
    # The declaration's operator-facing label, reported alongside `slug`.
    Column("name", String, nullable=False),
    Column("missed_at", UtcDateTime, nullable=False),
    # The sampler's own closed-set miss reason (e.g. `credential_lapsed`) — never a
    # token, a refresh token, or a path.
    Column("reason", String, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- The identity spine: users, provider identities, sessions -----
# ``role`` is a coarse tag expanded through a static map, never a stored permission list.

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),  # usr_<ulid>
    Column("username", String, nullable=False, unique=True),
    Column("display_name", String, nullable=False),
    Column("email", String, nullable=True),
    Column("role", String, nullable=False),  # blizzard.auth_core.Role value
    Column("created_at", UtcDateTime, nullable=False),
)

# A partial unique index — dialect-keyed rather than raw ``text()``, staying inside
# SQLAlchemy's portable DDL surface (``bzh:sql-portable``).
Index(
    "uq_users_email",
    users.c.email,
    unique=True,
    sqlite_where=users.c.email.isnot(None),
    postgresql_where=users.c.email.isnot(None),
)

# One row per (provider, subject) a user has linked (#92). ``handle`` is the provider's
# own display name at last link, refreshed on a later login.
identities = Table(
    "identities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider_name", String, nullable=False),
    Column("subject", String, nullable=False),  # the provider's own stable subject id
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("handle", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    UniqueConstraint("provider_name", "subject", name="uq_identities_provider_subject"),
)

# A hub session, resolved by its **hashed** id; the plaintext is minted once and never
# stored. Sliding expiry: refreshed in place on resolve, once past `touch_granularity`.
sessions = Table(
    "sessions",
    metadata,
    Column("id_hash", String, primary_key=True),
    Column("user_id", String, ForeignKey("users.id"), nullable=False, index=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("last_seen_at", UtcDateTime, nullable=False),
)

# --- The provider-login seam: single-use state, non-chunk auth facts ----

# A single-use ``state``, read-and-deleted in one call, so a replayed value
# can never resolve twice. Expiry is checked at read, never swept.
auth_state = Table(
    "auth_state",
    metadata,
    Column("state", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("provider_name", String, nullable=False),
    Column("return_to", String, nullable=False),
    Column("code_challenge", String, nullable=True),  # reserved for #96's PKCE public client
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
    Column("user_id", String, nullable=True),  # cli_login rows only — see note above
)

# The append-only, non-chunk auth/security event log (``bzh:facts-not-status``) — these
# events concern no single chunk.
auth_facts = Table(
    "auth_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("detail", Text, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)

# --- The superuser bootstrap lifecycle ---------------------------------------------
# A **singleton** row, so a config change naming a different email can still demote.
superuser_bootstrap = Table(
    "superuser_bootstrap",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String, nullable=False),
    Column("claimed_user_id", String, ForeignKey("users.id"), nullable=True),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Operational event log (event_log) ---------------------------
# ``chunk_id``/``runner_id`` are nullable — runner-scoped/hub-authored, respectively. ``detail`` is opaque JSON.

event_log = Table(
    "event_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("severity", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("runner_id", String, nullable=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=True),
    Column("lease_id", String, nullable=True),
    Column("node_name", String, nullable=True),
    Column("message", Text, nullable=False),
    Column("detail", Text, nullable=True),
)

# The read's own sort key (newest-first) — indexed so ordering never scans the table.
Index("ix_event_log_recorded_at", event_log.c.recorded_at)

# --- Trace export cursor (trace_cursor) ---------------------------------------
# Append-only, one row per advancing sweep; the newest row is the position.

trace_cursor = Table(
    "trace_cursor",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("position_at", UtcDateTime, nullable=False),  # the told step's closing-fact time
    Column("chunk_id", String, nullable=False),  # empty, so no foreign key, on an opening row
    Column("epoch", Integer, nullable=False),
    Column("decision_id", String, nullable=False),  # empty for runner and hub steps
    Column("span_count", Integer, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)
# The newest-row read's own sort key.
Index("ix_trace_cursor_recorded_at_id", trace_cursor.c.recorded_at, trace_cursor.c.id)

# --- Fact egress cursor (egress_cursor) ----------------------------------------
# Append-only, one row per advancing pass; a dataset's newest row is its position.

egress_cursor = Table(
    "egress_cursor",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("dataset", String, nullable=False),
    Column(
        "position_at", UtcDateTime, nullable=True
    ),  # the last written step's closing-fact time, or the events source's time; null for invocations
    Column("chunk_id", String, nullable=True),
    Column("epoch", Integer, nullable=True),
    Column("decision_id", String, nullable=True),  # empty for runner and hub steps
    Column("usage_recorded_at", UtcDateTime, nullable=False),
    Column("usage_id", Integer, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("files", Text, nullable=False),  # JSON list of the placed paths, manifest last
    Column("recorded_at", UtcDateTime, nullable=False),
    # The events position after ``position_at``; a drop's version is empty. Null for the other datasets.
    Column("segment_id", String, nullable=True),
    Column("extractor_version", String, nullable=True),
)
Index(
    "ix_egress_cursor_dataset_recorded_at_id", egress_cursor.c.dataset, egress_cursor.c.recorded_at, egress_cursor.c.id
)

# --- Transcript segments (epic:transcripts) ----------------------
# One row per shipped record, append-only; the natural key dedupes re-offers.

transcript_segments = Table(
    "transcript_segments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment_id", String, nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("spawn_generation", Integer, nullable=False),
    Column("runner_id", String, nullable=False),
    Column("turn_range_start", Integer, nullable=False),
    Column("turn_range_end", Integer, nullable=False),
    # True on the one record that closes the segment out — never inferred from a
    # transition, since a tail may land after the step's completion (product plan).
    Column("final", Boolean, nullable=False),
    # A cap rejection: no content, no codec; `rejection_reason` is non-null iff
    # `rejected`.
    Column("rejected", Boolean, nullable=False),
    Column("rejection_reason", String, nullable=True),
    # Raw, uncompressed turn bytes as received — the budget currency for both caps,
    # regardless of `rejected`.
    Column("byte_count", Integer, nullable=False),
    Column("codec", String, nullable=True),  # e.g. "zlib"; null iff rejected
    Column("content", LargeBinary, nullable=True),  # compressed turns JSON; null iff rejected
    # The harness family that produced the source transcript. This is deliberately
    # separate from its observed version and the normalizer version below.
    Column("harness_id", String, nullable=True),
    Column("normalizer_version", String, nullable=False),
    Column("harness_version", String, nullable=True),
    # Frozen at the runner's segment open; nullable, no backfill.
    Column("model", String, nullable=True),
    Column("effort", String, nullable=True),
    # The worker's working directory, frozen at the runner's segment open; nullable, no backfill.
    Column("spawn_cwd", String, nullable=True),
    # The runner's OWN cap declaration, distinct from `rejected` above; nullable, no backfill.
    Column("record_truncated", Boolean, nullable=True),
    # Re-ship only: the segment this replaces — dropped from a bounded per-lease read.
    Column("supersedes", String, nullable=True),
    # Hub-stamped receipt instant — the rolling 24h window anchors here, never on the runner's.
    Column("received_at", UtcDateTime, nullable=False),
    # A per-record fingerprint of `(turn_range_start, rejected, content)`,
    # so a bulk candidacy read detects a content change without reading `content` at all.
    Column("content_digest", String, nullable=False),
    UniqueConstraint("segment_id", "turn_range_start", name="uq_transcript_segments_segment_turn_start"),
)

Index("ix_transcript_segments_chunk_id", transcript_segments.c.chunk_id)
Index("ix_transcript_segments_runner_received_at", transcript_segments.c.runner_id, transcript_segments.c.received_at)
Index("ix_transcript_segments_segment_id", transcript_segments.c.segment_id)
# A visible-segment read's `NOT IN (SELECT supersedes ...)` anti-join.
Index("ix_transcript_segments_supersedes", transcript_segments.c.supersedes)
# (final, chunk_id): a visible-segment read's outer `WHERE final = TRUE`, `final`
# leading because it is the more selective predicate; `chunk_id` second lets an `IN
# (chunks)` probe and a `DISTINCT` ride the same index.
Index("ix_transcript_segments_final_chunk_id", transcript_segments.c.final, transcript_segments.c.chunk_id)

# --- Transcript lane high-water mark (own table, not runner_high_water) --------
# `runner_high_water` belongs to the fact lane; a second lane sharing it would collide.

transcript_high_water = Table(
    "transcript_high_water",
    metadata,
    Column("runner_id", String, primary_key=True),
    Column("seq", Integer, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
)

# --- Derived transcript events — one row per occurrence, re-derivable ---
# from `transcript_segments` at any later extractor version (`bzh:facts-not-status`: an
# immutable observation computed from already-durable rows, never a status).

transcript_events = Table(
    "transcript_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment_id", String, nullable=False),
    # The extractor version that produced this row — a bump re-derives history
    # while leaving earlier-version rows untouched.
    Column("extractor_version", String, nullable=False),
    Column("kind", String, nullable=False),
    # This event's location in the segment's turn tree: "N" for a main-lane turn,
    # "N.M" one sidechain deep, "N.M.K" two, and so on.
    Column("turn_path", String, nullable=False),
    # Disambiguates more than one event of the same kind at the same `turn_path` — 0 for
    # every extractor today, kept general for one that could ever multi-match a turn.
    Column("occurrence", Integer, nullable=False),
    Column("payload", Text, nullable=False),  # JSON object, kind-shaped (`bzh:sql-portable`)
    # `payload`'s filterable projection — principal subject and
    # invoking tool; `None` for a kind with no single natural subject, never guessed.
    Column("subject", String, nullable=True),
    Column("tool", String, nullable=True),
    # Denormalized node-step context, stamped at derive time.
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("spawn_generation", Integer, nullable=False),
    Column("graph_id", String, nullable=False),
    Column("depth", Integer, nullable=False),  # 0 main lane; nesting depth otherwise
    Column("agent_type", String, nullable=True),  # nearest-enclosing sidechain's; None at depth 0
    # The segment's own frozen provenance, stamped once per derivation call.
    Column("harness_id", String, nullable=True),
    Column("harness_version", String, nullable=True),
    Column("model", String, nullable=True),
    Column("effort", String, nullable=True),
    # The turn's own instant, never the hub's receipt instant; nullable for an
    # untimed turn.
    Column("occurred_at", UtcDateTime, nullable=True),
    UniqueConstraint(
        "segment_id",
        "extractor_version",
        "kind",
        "turn_path",
        "occurrence",
        name="uq_transcript_events_natural_key",
    ),
)

Index("ix_transcript_events_chunk_id", transcript_events.c.chunk_id)
Index("ix_transcript_events_segment_id", transcript_events.c.segment_id)
Index("ix_transcript_events_subject", transcript_events.c.subject)
Index("ix_transcript_events_tool", transcript_events.c.tool)
# (extractor_version, id) for a per-extractor cursor read: `extractor_version = ? AND
# id > cursor ORDER BY id`.
Index("ix_transcript_events_extractor_version_id", transcript_events.c.extractor_version, transcript_events.c.id)

# --- Per-segment derivation marker — replaced, never appended: what a segment's ---
# most recent derivation at a given extractor version saw, when, and whether it was complete.

transcript_event_derivations = Table(
    "transcript_event_derivations",
    metadata,
    Column("segment_id", String, primary_key=True),
    Column("extractor_version", String, primary_key=True),
    # A fingerprint of the segment's stored content as of this derivation — compared
    # against the segment's current fingerprint to detect a content change (a rejected
    # record later accepted, a late record landing) that the sweep must re-derive over.
    Column("content_fingerprint", String, nullable=False),
    Column("derived_at", UtcDateTime, nullable=False),
    Column("event_count", Integer, nullable=False),
    # False when the segment held a content hole (a rejected record) at derivation time —
    # declared, never silently indistinguishable from a session that read nothing.
    Column("complete", Boolean, nullable=False),
)
# The events egress's markers-past-a-position read, in its cursor order.
Index(
    "ix_transcript_event_derivations_derived_at_segment_id_extractor_version",
    transcript_event_derivations.c.derived_at,
    transcript_event_derivations.c.segment_id,
    transcript_event_derivations.c.extractor_version,
)

# --- Transcript segment drops — append-only: one row per time a segment's derived events and
# markers were deleted. A segment dropped, derived again, and dropped again leaves two rows,
# so ``segment_id`` is not unique. ``chunk_id`` carries no foreign key: a segment leaves the
# visible set precisely when its chunk row is missing, and the row records the segment's
# identity at the moment of a past event.

transcript_event_drops = Table(
    "transcript_event_drops",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("segment_id", String, nullable=False),
    Column("chunk_id", String, nullable=False),
    Column("epoch", Integer, nullable=False),
    Column("spawn_generation", Integer, nullable=False),
    Column("dropped_at", UtcDateTime, nullable=False),
)
Index(
    "ix_transcript_event_drops_dropped_at_segment_id",
    transcript_event_drops.c.dropped_at,
    transcript_event_drops.c.segment_id,
)
# The events backfill's drops-of-these-epochs read.
Index("ix_transcript_event_drops_chunk_id_epoch", transcript_event_drops.c.chunk_id, transcript_event_drops.c.epoch)

# --- Work items (hub-owned work items) ---------------------------
# A mutable entity row, not a fact table: title/body/edited_at change in place, and
# closure is recorded on the row itself (nullable ``closed_at`` + ``closure``) rather
# than a separate append-only table, because there is exactly one current state to read
# back — never a history of edits (``bzh:facts-not-status``, Recorded position).

work_items = Table(
    "work_items",
    metadata,
    Column("work_item_id", String, primary_key=True),  # wi_<ulid>
    Column("source", String, nullable=False),  # the WorkRef.source that owns this item ("hub")
    Column("ref", String, nullable=False),  # the WorkRef.ref, allocated from work_item_sequence
    Column("title", String, nullable=False),
    Column("body", Text, nullable=False),
    # Discriminator + one JSON payload read whole, matching artifacts.kind/data and
    # transcript_events.kind/payload (`bzh:sql-portable`) — a hub user by id, or the
    # fleet itself.
    Column("author_kind", String, nullable=False),  # user | fleet
    Column("author_payload", Text, nullable=False),  # JSON object, author_kind-shaped
    # A plain, comment-documented value set rather than a DB enum (`bzh:sql-portable`):
    # low | normal | high. Null — the author stated none.
    Column("stated_priority", String, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("edited_at", UtcDateTime, nullable=False),
    # Unset while open. Set together, once, when the item closes.
    Column("closed_at", UtcDateTime, nullable=True),
    Column("closure", String, nullable=True),  # delivered | withdrawn
    # A routine run's own recorded values, nullable — unindexed: the pair's
    # actual read path is `finding_sets(routine_name, scope_slug)`, not this table.
    Column("routine_name", String, nullable=True),
    Column("scope_slug", String, nullable=True),
    Column("run_mode", String, nullable=True),
    UniqueConstraint("source", "ref", name="uq_work_items_source_ref"),
)

Index("ix_work_items_source", work_items.c.source)

# --- Work item runs (a run's identity) -----------------------
# What routine, scope, and mode a work item's run is executing under — minted together
# with the work item, read back through a chunk's first work ref (`routine_name`, not a
# surrogate `routine_id`, the `findings.routine_name` shape).

work_item_runs = Table(
    "work_item_runs",
    metadata,
    Column("work_item_id", String, ForeignKey("work_items.work_item_id"), primary_key=True),
    Column("routine_name", String, nullable=False),
    Column("scope_slug", String, ForeignKey("scopes.slug"), nullable=False),
    Column("mode", String, nullable=False),
)

# A per-source allocation counter, one row per source, so ``ref`` allocation never
# reads ``MAX(ref)+1`` (two concurrent first allocations on an empty source would both
# compute 1) — every source gets a pre-existing row instead (`bzh:sql-portable`).

work_item_sequence = Table(
    "work_item_sequence",
    metadata,
    Column("source", String, primary_key=True),
    Column("next_ref", Integer, nullable=False),
)

# --- Forge annotation memory (forge_annotation_facts) --------------------------
# Append-only, one row per entry to or exit from the annotated set; the newest says whether a clear is owed.

forge_annotation_facts = Table(
    "forge_annotation_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_name", String, nullable=False),
    Column("annotating", Boolean, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
)

Index("ix_forge_annotation_facts_source_name_id", forge_annotation_facts.c.source_name, forge_annotation_facts.c.id)
