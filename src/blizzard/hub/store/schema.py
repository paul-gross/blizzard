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
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from blizzard.foundation.store.utc import UtcDateTime

metadata = MetaData()

# --- Hub-unique columns ----------------------------------------------------------
# ``info={"hub_unique": True}`` marks a column whose value is unique across the whole hub, so a
# key holding it needs no ``tenant_id``: a hub-minted id, or a runner id the hub minted. An
# autoincrement surrogate is hub-unique without a mark, and so is a foreign key onto a marked column.

# --- The global list -----------------------------------------------------------
# Every table that carries no tenant key, each with why it is global. Every other table is
# tenant-owned. A name here may precede the revision that creates its table.

GLOBAL_TABLES: frozenset[str] = frozenset(
    {
        "tenants",  # the boundary itself
        "tenant_deletions",  # the boundary itself: the audit of a teardown outlives its tenant
        "users",  # identity is resolved before a tenant is
        "identities",  # identity is resolved before a tenant is
        "sessions",  # identity is resolved before a tenant is
        "auth_state",  # identity is resolved before a tenant is
        "auth_facts",  # the sign-in and security log of hub-level identities
        "superuser_bootstrap",  # the first claim of the hub administrator role
        "hub_admin_facts",  # who holds the hub administrator role, the role above every tenant
        "config_import_facts",  # the hub-level carry-over of a file-configured hub, read at startup
        "membership_facts",  # carries tenant_id as data, but is read hub-wide to list a person's tenants
        "hub_event_log",  # occurrences of the hub's own machinery, which concern no tenant
        "trace_cursor",  # trace export is hub configuration with one destination
        "egress_cursor",  # fact egress is hub configuration with one destination
    }
)

# --- The tenant boundary -------------------------------------------------------
# A tenant is named by its id alone; ``name`` is a display label, unique nowhere and never
# resolved. ``created_by`` is the creating user's id, or ``migration`` for the carried-over tenant.

tenants = Table(
    "tenants",
    metadata,
    Column("tenant_id", String, primary_key=True),  # ten_<ulid>
    Column("name", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String, nullable=False),
)

# The audit of one tenant's teardown, written when the tenant closes (``closed_at``); ``rows_removed``
# counts up as the drain removes rows, and ``deleted_at`` is recorded when the deletion completes —
# unset while it is underway. One per tenant: the row a drain locks, and the answer to a repeated
# delete. It outlives the tenant it names, so ``tenant_id`` carries no foreign key.
tenant_deletions = Table(
    "tenant_deletions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("closed_at", UtcDateTime, nullable=False),
    Column("deleted_at", UtcDateTime, nullable=True),
    Column("deleted_by", String, nullable=False),
    Column("rows_removed", Integer, nullable=True),
    UniqueConstraint("tenant_id", name="uq_tenant_deletions_tenant_id"),
)

# --- Workflow graphs (immutable definitions, reified) -------------------------

graphs = Table(
    "graphs",
    metadata,
    Column("graph_id", String, primary_key=True, info={"hub_unique": True}),  # gr_<ulid>
    Column("name", String, nullable=False),
    Column("entry_node_id", String, nullable=False),
    Column("definition_yaml", Text, nullable=False),  # the inlined source, for audit/re-export
    Column("created_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graphs_tenant_id"), nullable=False),
)
Index("ix_graphs_tenant_name", graphs.c.tenant_id, graphs.c.name)

graph_nodes = Table(
    "graph_nodes",
    metadata,
    Column("node_id", String, primary_key=True, info={"hub_unique": True}),  # nd_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_nodes_tenant_id"), nullable=False),
    # Null/false disallows proposed work items.
)
Index("ix_graph_nodes_tenant_graph_id", graph_nodes.c.tenant_id, graph_nodes.c.graph_id)

graph_choices = Table(
    "graph_choices",
    metadata,
    Column("choice_id", String, primary_key=True, info={"hub_unique": True}),  # cho_<ulid>
    Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
    # Null/false is ungated (#114).
    Column("requires_checks", Boolean, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_choices_tenant_id"), nullable=False),
)
Index("ix_graph_choices_tenant_node_id", graph_choices.c.tenant_id, graph_choices.c.node_id)

graph_edges = Table(
    "graph_edges",
    metadata,
    Column("edge_id", String, primary_key=True, info={"hub_unique": True}),
    Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
    Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
    Column("to_node_name", String, nullable=False),  # a node name, the reserved 'done', or 'graph:<name>' (#90)
    Column("prompt_addendum", Text, nullable=True),  # inlined arrival context
    # Cross-graph model override (#90); null keeps the current model.
    Column("to_graph_model", String, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_edges_tenant_id"), nullable=False),
)
Index("ix_graph_edges_tenant_from_node_id", graph_edges.c.tenant_id, graph_edges.c.from_node_id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_sessions_tenant_id"), nullable=False),
)
Index("ix_graph_sessions_tenant_graph_id", graph_sessions.c.tenant_id, graph_sessions.c.graph_id)

# Graph artifacts keyed by `(graph_id, name)`.
graph_artifacts = Table(
    "graph_artifacts",
    metadata,
    Column("graph_id", String, ForeignKey("graphs.graph_id"), primary_key=True),
    Column("name", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),  # authored `artifacts:` position — every read orders by it
    Column("content", Text, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_artifacts_tenant_id"), nullable=False),
)
Index("ix_graph_artifacts_tenant_graph_id", graph_artifacts.c.tenant_id, graph_artifacts.c.graph_id)

# --- Graph lifecycle facts (graph.retired / graph.enabled) -------------------

graph_lifecycle_facts = Table(
    "graph_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_lifecycle_facts_tenant_id"), nullable=False
    ),
)
Index("ix_graph_lifecycle_facts_tenant_graph_id", graph_lifecycle_facts.c.tenant_id, graph_lifecycle_facts.c.graph_id)

# Follow-latest policy: newest fact wins; null inherits the hub setting.
graph_policy_facts = Table(
    "graph_policy_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
    Column("follow_latest", Boolean, nullable=True),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_graph_policy_facts_tenant_id"), nullable=False
    ),
)
Index("ix_graph_policy_facts_tenant_id", graph_policy_facts.c.tenant_id)

# --- Projects (configured records inside a tenant; a hub-minted id, a slug named per tenant) ---
# A project is named by its id everywhere; ``slug`` is the handle people type, unique within the
# tenant and editable, and ``name`` a display label, unique nowhere and never resolved.

projects = Table(
    "projects",
    metadata,
    Column("project_id", String, nullable=False, info={"hub_unique": True}),  # proj_<ulid>
    Column("slug", String, nullable=False),
    Column("name", String, nullable=False),
    Column("description", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_projects_tenant_id"), nullable=False),
    PrimaryKeyConstraint("project_id", name="pk_projects"),
    UniqueConstraint("tenant_id", "slug", name="uq_projects_tenant_slug"),
)

# A slug a project moved away from; at most one project holds a given former slug.
project_former_slugs = Table(
    "project_former_slugs",
    metadata,
    Column("slug", String, nullable=False),
    Column(
        "project_id", String, ForeignKey("projects.project_id", name="fk_project_former_slugs_projects"), nullable=False
    ),
    Column("left_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_project_former_slugs_tenant_id"), nullable=False
    ),
    PrimaryKeyConstraint("tenant_id", "slug", name="pk_project_former_slugs"),
)

# --- Scopes (operator-authored buckets: a surrogate id, a slug named per project) ---

scopes = Table(
    "scopes",
    metadata,
    Column("scope_id", String, nullable=False, info={"hub_unique": True}),  # scp_<ulid>
    Column("slug", String, nullable=False),
    Column("description", Text, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_scopes_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_scopes_projects"), nullable=False),
    PrimaryKeyConstraint("scope_id", name="pk_scopes"),
    UniqueConstraint("project_id", "slug", name="uq_scopes_project_slug"),
)

# A scope by slug across the tenant's projects, for the reads that name no project.
Index("ix_scopes_tenant_slug", scopes.c.tenant_id, scopes.c.slug)

# Scope retire/enable facts: append-only, newest wins.
scope_lifecycle_facts = Table(
    "scope_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("scope_id", String, ForeignKey("scopes.scope_id", name="fk_scope_lifecycle_facts_scopes"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_scope_lifecycle_facts_tenant_id"), nullable=False
    ),
)
Index("ix_scope_lifecycle_facts_tenant_id", scope_lifecycle_facts.c.tenant_id)

# --- Secrets (write-only credentials, sealed under a hub-key generation) ---

secrets = Table(
    "secrets",
    metadata,
    Column("name", String, nullable=False),
    Column("ciphertext", Text, nullable=False),  # base64 AES-256-GCM output
    Column("nonce", Text, nullable=False),  # base64 96-bit nonce, fresh per write
    Column("key_id", String, nullable=False),  # the hub-key generation that sealed it
    Column("revision", Integer, nullable=False),
    Column("replaced_at", UtcDateTime, nullable=False),
    Column("replaced_by", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_secrets_tenant_id"), nullable=False),
    PrimaryKeyConstraint("tenant_id", "name", name="pk_secrets"),
)

# Secret retire/enable facts: append-only, newest wins.
secret_lifecycle_facts = Table(
    "secret_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String, nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_secret_lifecycle_facts_tenant_id"), nullable=False
    ),
    ForeignKeyConstraint(
        ["tenant_id", "name"], ["secrets.tenant_id", "secrets.name"], name="fk_secret_lifecycle_facts_secrets"
    ),
)
Index("ix_secret_lifecycle_facts_tenant_id", secret_lifecycle_facts.c.tenant_id)

# --- Work sources (configured records; secret_name references a secret, never a value) ---
# A work source is named by its id everywhere; ``slug`` is the handle people type, unique within the tenant, editable,
# and never ``hub``, and ``name`` a display label, unique nowhere and never resolved.

work_sources = Table(
    "work_sources",
    metadata,
    Column("work_source_id", String, nullable=False, info={"hub_unique": True}),  # wsrc_<ulid>
    Column("slug", String, nullable=False),
    Column("name", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("locator", String, nullable=False),
    Column("api_base", String, nullable=True),
    Column("web_base", String, nullable=True),
    Column("annotate", Boolean, nullable=False),
    Column("secret_name", String, nullable=True),
    Column("revision", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_work_sources_tenant_id"), nullable=False),
    PrimaryKeyConstraint("work_source_id", name="pk_work_sources"),
    UniqueConstraint("tenant_id", "slug", name="uq_work_sources_tenant_slug"),
    UniqueConstraint("tenant_id", "provider", "locator", name="uq_work_sources_tenant_provider_locator"),
    ForeignKeyConstraint(
        ["tenant_id", "secret_name"], ["secrets.tenant_id", "secrets.name"], name="fk_work_sources_secrets"
    ),
)

# A slug a work source moved away from; at most one work source holds a given former slug.
work_source_former_slugs = Table(
    "work_source_former_slugs",
    metadata,
    Column("slug", String, nullable=False),
    Column(
        "work_source_id",
        String,
        ForeignKey("work_sources.work_source_id", name="fk_work_source_former_slugs_work_sources"),
        nullable=False,
    ),
    Column("left_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_work_source_former_slugs_tenant_id"),
        nullable=False,
    ),
    PrimaryKeyConstraint("tenant_id", "slug", name="pk_work_source_former_slugs"),
)

# Work source retire/enable facts: append-only, newest wins.
work_source_lifecycle_facts = Table(
    "work_source_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("work_source_id", String, nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_work_source_lifecycle_facts_tenant_id"),
        nullable=False,
    ),
    ForeignKeyConstraint(
        ["work_source_id"],
        ["work_sources.work_source_id"],
        name="fk_work_source_lifecycle_facts_work_sources",
    ),
)
Index("ix_work_source_lifecycle_facts_tenant_id", work_source_lifecycle_facts.c.tenant_id)

# --- Repositories (configured records; secret_name references a secret, never a value) ---
# A repository is named by its id everywhere; ``slug`` is the handle people type, unique within the tenant and
# editable, and ``name`` a display label, unique nowhere and never resolved.

repositories = Table(
    "repositories",
    metadata,
    Column("repository_id", String, nullable=False, info={"hub_unique": True}),  # repo_<ulid>
    Column("slug", String, nullable=False),
    Column("name", String, nullable=False),
    Column("forge_api_url", String, nullable=False),
    Column("owner", String, nullable=False),
    Column("repo", String, nullable=False),
    Column("base_branch", String, nullable=False),
    Column("secret_name", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("created_by", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_repositories_tenant_id"), nullable=False),
    PrimaryKeyConstraint("repository_id", name="pk_repositories"),
    UniqueConstraint("tenant_id", "slug", name="uq_repositories_tenant_slug"),
    UniqueConstraint("tenant_id", "forge_api_url", "owner", "repo", name="uq_repositories_tenant_coordinate"),
    ForeignKeyConstraint(
        ["tenant_id", "secret_name"], ["secrets.tenant_id", "secrets.name"], name="fk_repositories_secrets"
    ),
)

# A slug a repository moved away from; at most one repository holds a given former slug.
repository_former_slugs = Table(
    "repository_former_slugs",
    metadata,
    Column("slug", String, nullable=False),
    Column(
        "repository_id",
        String,
        ForeignKey("repositories.repository_id", name="fk_repository_former_slugs_repositories"),
        nullable=False,
    ),
    Column("left_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_repository_former_slugs_tenant_id"),
        nullable=False,
    ),
    PrimaryKeyConstraint("tenant_id", "slug", name="pk_repository_former_slugs"),
)

# Repository retire/enable facts: append-only, newest wins.
repository_lifecycle_facts = Table(
    "repository_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("repository_id", String, nullable=False),
    Column("retired", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_repository_lifecycle_facts_tenant_id"),
        nullable=False,
    ),
    ForeignKeyConstraint(
        ["repository_id"],
        ["repositories.repository_id"],
        name="fk_repository_lifecycle_facts_repositories",
    ),
)
Index("ix_repository_lifecycle_facts_tenant_id", repository_lifecycle_facts.c.tenant_id)

# --- Project links (a project's work sources and the repositories it may land in) ---

# A project draws from a work source through a link: a configured record of its own, with a revision and
# change facts; unlinking is its retirement, and linked/unlinked derives from the newest fact.
project_source_links = Table(
    "project_source_links",
    metadata,
    Column(
        "project_id", String, ForeignKey("projects.project_id", name="fk_project_source_links_projects"), nullable=False
    ),
    Column("work_source_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_project_source_links_tenant_id"), nullable=False
    ),
    PrimaryKeyConstraint("project_id", "work_source_id", name="pk_project_source_links"),
    ForeignKeyConstraint(
        ["work_source_id"],
        ["work_sources.work_source_id"],
        name="fk_project_source_links_work_sources",
    ),
)
# Which projects link source X.
Index("ix_project_source_links_tenant_source", project_source_links.c.tenant_id, project_source_links.c.work_source_id)

project_source_link_facts = Table(
    "project_source_link_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False),
    Column("work_source_id", String, nullable=False),
    Column("linked", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_project_source_link_facts_tenant_id"),
        nullable=False,
    ),
    ForeignKeyConstraint(
        ["project_id", "work_source_id"],
        ["project_source_links.project_id", "project_source_links.work_source_id"],
        name="fk_project_source_link_facts_links",
    ),
)
# A link's newest fact.
Index(
    "ix_project_source_link_facts_tenant_link",
    project_source_link_facts.c.tenant_id,
    project_source_link_facts.c.project_id,
    project_source_link_facts.c.work_source_id,
    project_source_link_facts.c.id,
)

# A project may land in a repository through a link: a configured record of its own, with a revision and
# change facts; unlinking is its retirement, and linked/unlinked derives from the newest fact.
project_repository_links = Table(
    "project_repository_links",
    metadata,
    Column(
        "project_id",
        String,
        ForeignKey("projects.project_id", name="fk_project_repository_links_projects"),
        nullable=False,
    ),
    Column("repository_id", String, nullable=False),
    Column("revision", Integer, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_project_repository_links_tenant_id"),
        nullable=False,
    ),
    PrimaryKeyConstraint("project_id", "repository_id", name="pk_project_repository_links"),
    ForeignKeyConstraint(
        ["repository_id"], ["repositories.repository_id"], name="fk_project_repository_links_repositories"
    ),
)
# Which projects link repository X.
Index(
    "ix_project_repository_links_tenant_repository",
    project_repository_links.c.tenant_id,
    project_repository_links.c.repository_id,
)

project_repository_link_facts = Table(
    "project_repository_link_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False),
    Column("repository_id", String, nullable=False),
    Column("linked", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_project_repository_link_facts_tenant_id"),
        nullable=False,
    ),
    ForeignKeyConstraint(
        ["project_id", "repository_id"],
        ["project_repository_links.project_id", "project_repository_links.repository_id"],
        name="fk_project_repository_link_facts_links",
    ),
)
# A link's newest fact.
Index(
    "ix_project_repository_link_facts_tenant_link",
    project_repository_link_facts.c.tenant_id,
    project_repository_link_facts.c.project_id,
    project_repository_link_facts.c.repository_id,
    project_repository_link_facts.c.id,
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_config_changes_tenant_id"), nullable=False),
    # The project the changed record belongs to — a project, its links, a scope, a routine; null for a
    # tenant-wide record (a work source, a repository, a secret).
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_config_changes_projects"), nullable=True),
)
Index(
    "ix_config_changes_tenant_record",
    config_changes.c.tenant_id,
    config_changes.c.record_kind,
    config_changes.c.record_key,
    config_changes.c.id,
)
Index(
    "ix_config_changes_tenant_project_id", config_changes.c.tenant_id, config_changes.c.project_id, config_changes.c.id
)

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
    Column("routine_id", String, primary_key=True, info={"hub_unique": True}),  # rtn_<ulid>
    Column("name", String, nullable=False),
    Column("graph_name", String, nullable=False),  # a graph *name*, not a graph_id
    Column("default_scope_id", String, ForeignKey("scopes.scope_id", name="fk_routines_scopes"), nullable=False),
    # Nullable JSON model preferences and effort; empty means express none.
    Column("default_model", Text, nullable=True),
    Column("default_effort", String, nullable=True),
    # Nullable JSON harness preferences; empty means express none.
    Column("default_harnesses", Text, nullable=True),
    Column("created_at", UtcDateTime, nullable=False),
    Column("revision", Integer, nullable=False, server_default="1"),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_routines_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_routines_projects"), nullable=False),
    UniqueConstraint("project_id", "name", name="uq_routines_project_name"),
)

# A routine by name across the tenant's projects, for the reads that name no project.
Index("ix_routines_tenant_name", routines.c.tenant_id, routines.c.name)

# Mutable routine-to-scope membership.

routine_scopes = Table(
    "routine_scopes",
    metadata,
    Column("routine_id", String, ForeignKey("routines.routine_id"), nullable=False),
    Column("scope_id", String, ForeignKey("scopes.scope_id", name="fk_routine_scopes_scopes"), nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_routine_scopes_tenant_id"), nullable=False),
    PrimaryKeyConstraint("routine_id", "scope_id", name="pk_routine_scopes"),
)

Index("ix_routine_scopes_tenant_scope_id", routine_scopes.c.tenant_id, routine_scopes.c.scope_id)

# The routine's reversible retire/enable brake — scope_lifecycle_facts's own shape.
routine_lifecycle_facts = Table(
    "routine_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("routine_id", String, ForeignKey("routines.routine_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_routine_lifecycle_facts_tenant_id"),
        nullable=False,
    ),
)
Index("ix_routine_lifecycle_facts_tenant_id", routine_lifecycle_facts.c.tenant_id)

# --- Chunks and their work refs (chunk.minted) ------------------------------

chunks = Table(
    "chunks",
    metadata,
    Column("chunk_id", String, primary_key=True, info={"hub_unique": True}),  # ch_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunks_tenant_id"), nullable=False),
    # Set once at mint; every chunk-owned fact reaches its project through it.
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_chunks_projects"), nullable=False),
)
# (tenant_id, minted_at, chunk_id) for newest-first bounded reads since a timestamp.
Index("ix_chunks_tenant_minted_at_chunk_id", chunks.c.tenant_id, chunks.c.minted_at, chunks.c.chunk_id)
# The same, within one project — and the join a project's chunk-owned rows are read through.
Index(
    "ix_chunks_tenant_project_minted_at_chunk_id",
    chunks.c.tenant_id,
    chunks.c.project_id,
    chunks.c.minted_at,
    chunks.c.chunk_id,
)

chunk_work_refs = Table(
    "chunk_work_refs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),  # ``hub``, or the work source's id
    Column("ref", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_work_refs_tenant_id"), nullable=False),
)
Index("ix_chunk_work_refs_tenant_chunk_id", chunk_work_refs.c.tenant_id, chunk_work_refs.c.chunk_id)
# Its (tenant_id, source) prefix also serves a source-only filter within the tenant.
Index(
    "ix_chunk_work_refs_tenant_source_ref",
    chunk_work_refs.c.tenant_id,
    chunk_work_refs.c.source,
    chunk_work_refs.c.ref,
)

# --- Keyed write locks ----------------------------------------------------------

# Lock-only rows: a decision whose race has no existing row to lock locks one of these
# instead (``bzh:store-exclusive-write``). Holds no state and is never read as a fact;
# rows are inserted once and never deleted. Keyed by the tenant: the keys are names a tenant chooses.
keyed_locks = Table(
    "keyed_locks",
    metadata,
    Column("namespace", String, nullable=False),
    Column("key", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_keyed_locks_tenant_id"), nullable=False),
    PrimaryKeyConstraint("tenant_id", "namespace", "key", name="pk_keyed_locks"),
)

# --- Movement record (transition.recorded) ------------------------------------

transitions = Table(
    "transitions",
    metadata,
    Column("transition_id", String, primary_key=True, info={"hub_unique": True}),  # tr_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_transitions_tenant_id"), nullable=False),
)
# Leading chunk_id also serves chunk-only filters.
Index("ix_transitions_tenant_chunk_id_epoch", transitions.c.tenant_id, transitions.c.chunk_id, transitions.c.epoch)

# Serves the per-chunk "superseded by a newer fact" check, which a (tenant_id, recorded_at) index walks tenant-wide.
Index(
    "ix_transitions_tenant_chunk_id_recorded_at",
    transitions.c.tenant_id,
    transitions.c.chunk_id,
    transitions.c.recorded_at,
)

# (tenant_id, recorded_at, transition_id) for one tenant's ordered reads.
Index(
    "ix_transitions_tenant_recorded_at_transition_id",
    transitions.c.tenant_id,
    transitions.c.recorded_at,
    transitions.c.transition_id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
Index("ix_transitions_recorded_at_transition_id", transitions.c.recorded_at, transitions.c.transition_id)

# Transitions into a given node.
Index("ix_transitions_tenant_to_node_id", transitions.c.tenant_id, transitions.c.to_node_id)

# --- Cross-graph migration record (chunk_migrations) ---------------
# Its own fact, never a ``transitions`` row (``bzh:migration-not-transition``).

chunk_migrations = Table(
    "chunk_migrations",
    metadata,
    Column("migration_id", String, primary_key=True, info={"hub_unique": True}),  # mg_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_migrations_tenant_id"), nullable=False),
)
Index("ix_chunk_migrations_tenant_chunk_id", chunk_migrations.c.tenant_id, chunk_migrations.c.chunk_id)
Index(
    "ix_chunk_migrations_tenant_chunk_id_recorded_at",
    chunk_migrations.c.tenant_id,
    chunk_migrations.c.chunk_id,
    chunk_migrations.c.recorded_at,
)
# (tenant_id, recorded_at, migration_id) for one tenant's ordered reads.
Index(
    "ix_chunk_migrations_tenant_recorded_at_migration_id",
    chunk_migrations.c.tenant_id,
    chunk_migrations.c.recorded_at,
    chunk_migrations.c.migration_id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
Index("ix_chunk_migrations_recorded_at_migration_id", chunk_migrations.c.recorded_at, chunk_migrations.c.migration_id)

# --- Artifacts (the chunk artifact store) --------------------------------------

artifacts = Table(
    "artifacts",
    metadata,
    Column("artifact_id", String, primary_key=True, info={"hub_unique": True}),  # art_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_artifacts_tenant_id"), nullable=False),
)
# Leading chunk_id also serves chunk-only artifact reads.
Index(
    "ix_artifacts_tenant_chunk_id_node_id_epoch",
    artifacts.c.tenant_id,
    artifacts.c.chunk_id,
    artifacts.c.node_id,
    artifacts.c.epoch,
)

# --- Findings and finding sets -----------------------------------
# Finding liveness and observation counts derive from append-only finding_facts.

findings = Table(
    "findings",
    metadata,
    Column("finding_id", String, primary_key=True, info={"hub_unique": True}),  # fin_<ulid>
    # Routine name; null for review-sourced findings.
    Column("routine_name", String, nullable=True),
    Column("scope_id", String, ForeignKey("scopes.scope_id", name="fk_findings_scopes"), nullable=False),
    # The scope's slug as the label the finding reads key on, resolved within the row's tenant.
    Column("scope_slug", String, nullable=False),
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
    Column(
        "raised_by_chunk_id",
        String,
        ForeignKey("chunks.chunk_id", name="fk_findings_raised_by_chunk_id"),
        nullable=True,
    ),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_findings_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_findings_projects"), nullable=False),
    CheckConstraint("source IN ('routine', 'review')", name="ck_findings_source"),
)

# A project's bucket by (routine_name, scope_slug), and its recurrence by (routine_name, class).
Index(
    "ix_findings_tenant_project_routine_scope",
    findings.c.tenant_id,
    findings.c.project_id,
    findings.c.routine_name,
    findings.c.scope_slug,
)
Index(
    "ix_findings_tenant_project_routine_class",
    findings.c.tenant_id,
    findings.c.project_id,
    findings.c.routine_name,
    findings.c.class_,
)
# A project's findings by (scope_slug, source), across routines.
Index(
    "ix_findings_tenant_project_scope_source",
    findings.c.tenant_id,
    findings.c.project_id,
    findings.c.scope_slug,
    findings.c.source,
)

# Append-only finding changes; first/last seen and counts derive here.

finding_facts = Table(
    "finding_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("finding_id", String, ForeignKey("findings.finding_id"), nullable=False),
    Column("kind", String, nullable=False),  # FACT_KINDS, src/blizzard/hub/domain/garden/findings/model.py
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("note", Text, nullable=True),  # gone's/delivered's/an exit's/reopened's note; null for add/observed
    # Who recorded a human-driven fact — null for a run-driven add/observed/gone.
    Column("actor", String, nullable=True),
    # Proposal answered by a delivered fact; null for hand/later resolutions.
    Column(
        "proposal_id",
        String,
        ForeignKey("garden_proposals.proposal_id", name="fk_finding_facts_proposal_id_garden_proposals"),
        nullable=True,
    ),
    # The absorbing finding, set only on a `superseded` fact.
    Column(
        "superseded_by",
        String,
        ForeignKey("findings.finding_id", name="fk_finding_facts_superseded_by_findings"),
        nullable=True,
    ),
    # Delivery set for add/observed/gone; null for exits and legacy facts.
    Column(
        "finding_set_id",
        String,
        ForeignKey("finding_sets.finding_set_id", name="fk_finding_facts_finding_set_id_finding_sets"),
        nullable=True,
    ),
    # Submission-local ref on add facts; null otherwise.
    Column("ref", String, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_finding_facts_tenant_id"), nullable=False),
    CheckConstraint(
        "kind IN ('add', 'observed', 'gone', 'delivered', 'resolved', 'gone-confirmed', 'wont-fix',"
        " 'not-a-finding', 'superseded', 'reopened')",
        name="ck_finding_facts_kind",
    ),
)

Index(
    "ix_finding_facts_tenant_finding_id_id", finding_facts.c.tenant_id, finding_facts.c.finding_id, finding_facts.c.id
)

# Facts by finding_set_id.
Index("ix_finding_facts_tenant_finding_set_id", finding_facts.c.tenant_id, finding_facts.c.finding_set_id)
Index("ix_finding_facts_tenant_proposal_id", finding_facts.c.tenant_id, finding_facts.c.proposal_id)

# One delivered set per artifact, holding scope, revisions and measurement.

finding_sets = Table(
    "finding_sets",
    metadata,
    Column("finding_set_id", String, primary_key=True, info={"hub_unique": True}),  # fins_<ulid>
    Column("artifact_id", String, ForeignKey("artifacts.artifact_id"), nullable=False, unique=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the run that delivered it
    Column("scope_id", String, ForeignKey("scopes.scope_id", name="fk_finding_sets_scopes"), nullable=False),
    # The scope's slug as the label the set's reads key on, resolved within the row's tenant.
    Column("scope_slug", String, nullable=False),
    # Routine name; a chunk's mixed-source pointers cannot identify it.
    Column("routine_name", String, nullable=False, server_default=""),
    Column("revisions", Text, nullable=False),  # JSON {repo: revision} (`bzh:sql-portable`)
    Column("measurement", Text, nullable=True),  # opaque, routine-strategy-defined; null when none was recorded
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_finding_sets_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_finding_sets_projects"), nullable=False),
)

Index("ix_finding_sets_tenant_chunk_id", finding_sets.c.tenant_id, finding_sets.c.chunk_id)
Index(
    "ix_finding_sets_tenant_project_routine_scope",
    finding_sets.c.tenant_id,
    finding_sets.c.project_id,
    finding_sets.c.routine_name,
    finding_sets.c.scope_slug,
)

# --- Garden proposals (distinct from work-item proposals) --------

garden_proposals = Table(
    "garden_proposals",
    metadata,
    Column("proposal_id", String, primary_key=True, info={"hub_unique": True}),  # gprop_<ulid>
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
    Column("source_artifact_id", String, nullable=True, info={"hub_unique": True}),
    Column("ref", String, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_garden_proposals_tenant_id"), nullable=False),
    Column(
        "project_id", String, ForeignKey("projects.project_id", name="fk_garden_proposals_projects"), nullable=False
    ),
    CheckConstraint("origin IN ('routine-run', 'operator')", name="ck_garden_proposals_origin"),
    CheckConstraint(
        "origin != 'routine-run' OR routine_name IS NOT NULL", name="ck_garden_proposals_routine_run_has_routine"
    ),
    CheckConstraint(
        "origin != 'operator' OR created_by IS NOT NULL", name="ck_garden_proposals_operator_has_created_by"
    ),
)

Index(
    "ix_garden_proposals_tenant_project_routine_class",
    garden_proposals.c.tenant_id,
    garden_proposals.c.project_id,
    garden_proposals.c.routine_name,
    garden_proposals.c.class_,
)
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
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_garden_proposal_findings_tenant_id"),
        nullable=False,
    ),
)

Index(
    "ix_garden_proposal_findings_tenant_finding_id",
    garden_proposal_findings.c.tenant_id,
    garden_proposal_findings.c.finding_id,
)
# (tenant_id, proposal_id, finding_id): one proposal's findings, already in finding order.
Index(
    "ix_garden_proposal_findings_tenant_proposal_id",
    garden_proposal_findings.c.tenant_id,
    garden_proposal_findings.c.proposal_id,
    garden_proposal_findings.c.finding_id,
)

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
    Column("source", String, nullable=True),  # the minted item's ``hub`` or source id; null when none
    Column("ref", String, nullable=True),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_garden_proposal_closures_tenant_id"),
        nullable=False,
    ),
    UniqueConstraint("proposal_id", name="uq_garden_proposal_closures_proposal_id"),
)

# Unique (tenant_id, source, ref): at most one closure per work item.
Index(
    "ix_garden_proposal_closures_tenant_source_ref",
    garden_proposal_closures.c.tenant_id,
    garden_proposal_closures.c.source,
    garden_proposal_closures.c.ref,
    unique=True,
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_lease_facts_tenant_id"), nullable=False),
)
# Leading chunk_id also serves chunk-only filters.
Index("ix_lease_facts_tenant_chunk_id_epoch", lease_facts.c.tenant_id, lease_facts.c.chunk_id, lease_facts.c.epoch)
# (tenant_id, minted_at) for one tenant's ordered reads.
Index("ix_lease_facts_tenant_minted_at", lease_facts.c.tenant_id, lease_facts.c.minted_at)
# The hub-wide twin: the egress backfill reads every open tenant's epochs minted in a window.
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_epoch_owners_tenant_id"), nullable=False),
    UniqueConstraint("chunk_id", "epoch", name="uq_epoch_owners_chunk_id_epoch"),
)
# (tenant_id, recorded_at, id) for one tenant's ordered reads.
Index("ix_epoch_owners_tenant_recorded_at_id", epoch_owners.c.tenant_id, epoch_owners.c.recorded_at, epoch_owners.c.id)
Index("ix_epoch_owners_tenant_chunk_id", epoch_owners.c.tenant_id, epoch_owners.c.chunk_id, epoch_owners.c.epoch)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
Index("ix_epoch_owners_recorded_at_id", epoch_owners.c.recorded_at, epoch_owners.c.id)

# --- Routes (route.created / route.released) ----------------------------------

route_created = Table(
    "route_created",
    metadata,
    Column("route_id", String, primary_key=True, info={"hub_unique": True}),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("runner_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    # The monotonic route-event tiebreak (see work.RouteHistory) — a
    # per-chunk counter shared with route_released.seq, assigned in real write order.
    Column("seq", Integer, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_route_created_tenant_id"), nullable=False),
)
# One chunk's routes, already in creation order.
Index(
    "ix_route_created_tenant_chunk_id",
    route_created.c.tenant_id,
    route_created.c.chunk_id,
    route_created.c.created_at,
    route_created.c.seq,
)
# Routes by runner_id.
# One runner's routed chunks, each once.
Index(
    "ix_route_created_tenant_runner_id", route_created.c.tenant_id, route_created.c.runner_id, route_created.c.chunk_id
)
# (tenant_id, created_at, route_id) for newest-first bounded reads since a timestamp.
Index(
    "ix_route_created_tenant_created_at_route_id",
    route_created.c.tenant_id,
    route_created.c.created_at,
    route_created.c.route_id,
)

route_environments = Table(
    "route_environments",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("route_id", String, ForeignKey("route_created.route_id"), nullable=False),
    Column("environment_id", String, nullable=False),  # opaque
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_route_environments_tenant_id"), nullable=False
    ),
)
Index("ix_route_environments_tenant_route_id", route_environments.c.tenant_id, route_environments.c.route_id)

route_released = Table(
    "route_released",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("released_at", UtcDateTime, nullable=False),
    # See route_created.seq — the same per-chunk counter, so a created/released pair
    # tied on timestamp is still totally ordered by real write order.
    Column("seq", Integer, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_route_released_tenant_id"), nullable=False),
)
# One chunk's releases, already in release order.
Index(
    "ix_route_released_tenant_chunk_id",
    route_released.c.tenant_id,
    route_released.c.chunk_id,
    route_released.c.released_at,
    route_released.c.seq,
)
# (tenant_id, released_at, id) for one tenant's ordered reads.
Index(
    "ix_route_released_tenant_released_at_id",
    route_released.c.tenant_id,
    route_released.c.released_at,
    route_released.c.id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_route_token_minted_tenant_id"), nullable=False
    ),
)
Index("ix_route_token_minted_tenant_chunk_id", route_token_minted.c.tenant_id, route_token_minted.c.chunk_id)

# --- Delivery landing facts (per-repo, then whole-chunk) ----------------------

delivery_repo_landed = Table(
    "delivery_repo_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("repo", String, nullable=False),
    Column("commit_hash", String, nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),
    # The id of the repository record the landing resolved to — null on a row landed before it was recorded.
    Column("repository_id", String, nullable=True),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_delivery_repo_landed_tenant_id"), nullable=False
    ),
)
Index("ix_delivery_repo_landed_tenant_chunk_id", delivery_repo_landed.c.tenant_id, delivery_repo_landed.c.chunk_id)

delivery_landed = Table(
    "delivery_landed",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("landed_at", UtcDateTime, nullable=False),  # terminal: all repos landed
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_delivery_landed_tenant_id"), nullable=False),
)
Index("ix_delivery_landed_tenant_chunk_id", delivery_landed.c.tenant_id, delivery_landed.c.chunk_id)

# --- Delivery closure facts (work_item_closures) -----------------
# One row per close-attempt outcome; `closed`/`gone` are terminal, `failed` is retried.

work_item_closures = Table(
    "work_item_closures",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),  # ``hub``, or the work source's id
    Column("ref", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("reason", String, nullable=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_work_item_closures_tenant_id"), nullable=False
    ),
    UniqueConstraint("chunk_id", "source", "ref", "outcome", name="uq_work_item_closures_chunk_source_ref_outcome"),
)
Index("ix_work_item_closures_tenant_id", work_item_closures.c.tenant_id)

# --- Close intent outbox (close_intents) ---------------------------------------
# One row per work ref a landing or completion owes a closure attempt; `retired_at` NULL is pending, never deleted.

close_intents = Table(
    "close_intents",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("source", String, nullable=False),  # ``hub``, or the work source's id
    Column("ref", String, nullable=False),
    Column("enqueued_at", UtcDateTime, nullable=False),
    Column("retired_at", UtcDateTime, nullable=True),  # null = pending; set when the drainer retires it
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_close_intents_tenant_id"), nullable=False),
    UniqueConstraint("chunk_id", "source", "ref", name="uq_close_intents_chunk_source_ref"),
)
Index("ix_close_intents_tenant_id", close_intents.c.tenant_id)
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
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_close_intent_attempts_tenant_id"), nullable=False
    ),
)
Index("ix_close_intent_attempts_tenant_intent_id", close_intent_attempts.c.tenant_id, close_intent_attempts.c.intent_id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_bounces_tenant_id"), nullable=False),
)
# (chunk_id, epoch) serves both a plain chunk_id filter and a per-attempt (chunk_id,
# epoch) anti-join, at no extra write cost over a single-column index.
Index(
    "ix_chunk_bounces_tenant_chunk_id_epoch", chunk_bounces.c.tenant_id, chunk_bounces.c.chunk_id, chunk_bounces.c.epoch
)

# --- The hub-execution serialization slot, one per tenant --------------------
# A live slot has ``released_at IS NULL``; one at a time per tenant, reclaimable past its TTL.

hub_exec_slot = Table(
    "hub_exec_slot",
    metadata,
    Column("slot_id", String, primary_key=True, info={"hub_unique": True}),  # hes_<ulid>
    Column("holder_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),
    Column("acquired_at", UtcDateTime, nullable=False),
    Column("released_at", UtcDateTime, nullable=True),  # null while live
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_hub_exec_slot_tenant_id"), nullable=False),
)
Index("ix_hub_exec_slot_tenant_id", hub_exec_slot.c.tenant_id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_hub_node_poll_tenant_id"), nullable=False),
)
Index("ix_hub_node_poll_tenant_chunk_id", hub_node_poll.c.tenant_id, hub_node_poll.c.chunk_id)

# --- Readiness: the not-ready resting state, its promotion and demotion --------
# A chunk is currently promoted when its newest ``chunk_promoted`` row is later than its newest
# ``chunk_demoted`` row; any other chunk derives ``not_ready`` and is never claimed.

chunk_promoted = Table(
    "chunk_promoted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("promoted_at", UtcDateTime, nullable=False),  # not_ready -> ready
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_promoted_tenant_id"), nullable=False),
)
Index("ix_chunk_promoted_tenant_chunk_id", chunk_promoted.c.tenant_id, chunk_promoted.c.chunk_id)
# Explicit id tie-break for portable newest-first reads.
Index(
    "ix_chunk_promoted_tenant_promoted_at_id",
    chunk_promoted.c.tenant_id,
    chunk_promoted.c.promoted_at,
    chunk_promoted.c.id,
)

chunk_demoted = Table(
    "chunk_demoted",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("demoted_at", UtcDateTime, nullable=False),  # ready -> not_ready
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_demoted_tenant_id"), nullable=False),
)
Index("ix_chunk_demoted_tenant_chunk_id", chunk_demoted.c.tenant_id, chunk_demoted.c.chunk_id)
Index(
    "ix_chunk_demoted_tenant_demoted_at_id",
    chunk_demoted.c.tenant_id,
    chunk_demoted.c.demoted_at,
    chunk_demoted.c.id,
)

# --- Facts that make the derivation precedence correct (shaped) -------------

chunk_stopped = Table(
    "chunk_stopped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("stopped_at", UtcDateTime, nullable=False),  # terminal operator abandonment
    # Who stopped it — nullable: a row predating the column reads back `None`.
    Column("stopped_by", String, nullable=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_stopped_tenant_id"), nullable=False),
)
Index("ix_chunk_stopped_tenant_chunk_id", chunk_stopped.c.tenant_id, chunk_stopped.c.chunk_id)
# (tenant_id, stopped_at, id) for one tenant's ordered reads.
Index(
    "ix_chunk_stopped_tenant_stopped_at_id",
    chunk_stopped.c.tenant_id,
    chunk_stopped.c.stopped_at,
    chunk_stopped.c.id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_completed_tenant_id"), nullable=False),
)
Index("ix_chunk_completed_tenant_chunk_id", chunk_completed.c.tenant_id, chunk_completed.c.chunk_id)
# (tenant_id, completed_at, id) for one tenant's ordered reads.
Index(
    "ix_chunk_completed_tenant_completed_at_id",
    chunk_completed.c.tenant_id,
    chunk_completed.c.completed_at,
    chunk_completed.c.id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_deleted_tenant_id"), nullable=False),
)
# Explicit id tie-break for portable newest-first reads.
Index(
    "ix_chunk_deleted_tenant_deleted_at_id",
    chunk_deleted.c.tenant_id,
    chunk_deleted.c.deleted_at,
    chunk_deleted.c.id,
)

# --- Chunk dependency edges --------------------------------------
# One row per edge; ``released_at``/``released_by`` set once, together — never deleted.

chunk_dependencies = Table(
    "chunk_dependencies",
    metadata,
    Column("dependency_id", String, primary_key=True, info={"hub_unique": True}),  # dep_<ulid>
    Column("dependent_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("prerequisite_chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("declared_at", UtcDateTime, nullable=False),
    Column("declared_by", String, nullable=False),
    Column("released_at", UtcDateTime, nullable=True),  # null while standing
    Column("released_by", String, nullable=True),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_dependencies_tenant_id"), nullable=False
    ),
)
Index(
    "ix_chunk_dependencies_tenant_dependent_chunk_id",
    chunk_dependencies.c.tenant_id,
    chunk_dependencies.c.dependent_chunk_id,
)
Index(
    "ix_chunk_dependencies_tenant_prerequisite_chunk_id",
    chunk_dependencies.c.tenant_id,
    chunk_dependencies.c.prerequisite_chunk_id,
)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_escalations_tenant_id"), nullable=False),
)
Index("ix_escalations_tenant_chunk_id", escalations.c.tenant_id, escalations.c.chunk_id)
# (tenant_id, recorded_at, id) for one tenant's ordered reads.
Index("ix_escalations_tenant_recorded_at_id", escalations.c.tenant_id, escalations.c.recorded_at, escalations.c.id)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_usage_facts_tenant_id"), nullable=False),
)
Index("ix_usage_facts_tenant_chunk_id", usage_facts.c.tenant_id, usage_facts.c.chunk_id)
# The analytics spend-by-node grouping — otherwise a temp B-tree.
Index("ix_usage_facts_tenant_node_id", usage_facts.c.tenant_id, usage_facts.c.node_id)
# (tenant_id, recorded_at, id) for one tenant's ordered reads.
Index("ix_usage_facts_tenant_recorded_at_id", usage_facts.c.tenant_id, usage_facts.c.recorded_at, usage_facts.c.id)
# The hub-wide twin: the egress sweep pages every open tenant's usage in this order.
Index("ix_usage_facts_recorded_at_id", usage_facts.c.recorded_at, usage_facts.c.id)

# --- Questions and answers (the ask/answer rendezvous) ----------------------
# Open exactly while no answer row exists; the answer is first-write-wins CAS on the PK.

questions = Table(
    "questions",
    metadata,
    Column("question_id", String, nullable=False),  # qn_<ulid> (runner-minted), unique within its tenant
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the parked chunk
    Column("node_id", String, nullable=True),  # the node the worker parked at
    Column("session_id", String, nullable=True),  # the dormant session to resume around the answer
    Column("harness_id", String, nullable=True),  # owner of session_id; null from an older peer without it
    Column("runner_id", String, nullable=False),  # the runner holding the session
    Column("epoch", Integer, nullable=False),  # the parked lease's fencing epoch
    Column("question", Text, nullable=False),
    Column("options", Text, nullable=False),  # JSON list[str] of offered choices (may be empty)
    Column("asked_at", UtcDateTime, nullable=False),  # reap clock stops for the chunk from here
    # The asking chunk's project, taken from its row in the write's own transaction.
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_questions_projects"), nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_questions_tenant_id"), nullable=False),
    PrimaryKeyConstraint("tenant_id", "question_id", name="pk_questions"),
)
Index("ix_questions_tenant_chunk_id", questions.c.tenant_id, questions.c.chunk_id)
# (tenant_id, asked_at, question_id) for newest-first bounded reads since a timestamp.
Index(
    "ix_questions_tenant_asked_at_question_id",
    questions.c.tenant_id,
    questions.c.asked_at,
    questions.c.question_id,
)
# (tenant_id, project_id, asked_at, question_id) for one project's ordered reads.
Index(
    "ix_questions_tenant_project_asked_at_question_id",
    questions.c.tenant_id,
    questions.c.project_id,
    questions.c.asked_at,
    questions.c.question_id,
)

question_answers = Table(
    "question_answers",
    metadata,
    # The primary key IS the question's key: the CAS that makes answers first-write-wins —
    # a racing second insert collides and the loser reads back the winning row.
    Column("question_id", String, nullable=False),
    Column("answer", Text, nullable=False),  # the chosen option or free text, carried into the resume prompt
    Column("answered_by", String, nullable=False),  # who won the CAS
    Column("answered_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_question_answers_tenant_id"), nullable=False),
    PrimaryKeyConstraint("tenant_id", "question_id", name="pk_question_answers"),
    ForeignKeyConstraint(
        ["tenant_id", "question_id"],
        ["questions.tenant_id", "questions.question_id"],
        name="fk_question_answers_questions",
    ),
)
# (tenant_id, answered_at, question_id) for newest-first bounded reads since a timestamp;
# the question id is the row's own tie-break column within the tenant.
Index(
    "ix_question_answers_tenant_answered_at_question_id",
    question_answers.c.tenant_id,
    question_answers.c.answered_at,
    question_answers.c.question_id,
)

answer_deliveries = Table(
    "answer_deliveries",
    metadata,
    # answer.delivered (runner-minted): the resume-with-answer executed. Board detail
    # only — the chunk's status already flipped to running at question.answered.
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("question_id", String, nullable=False),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("delivered_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_answer_deliveries_tenant_id"), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "question_id"],
        ["questions.tenant_id", "questions.question_id"],
        name="fk_answer_deliveries_questions",
    ),
)
Index("ix_answer_deliveries_tenant_question_id", answer_deliveries.c.tenant_id, answer_deliveries.c.question_id)

# --- Human gates: decisions and their resolutions -------------
# Resolved-ness derives: a decision with a resolution row is resolved.

decisions = Table(
    "decisions",
    metadata,
    Column("decision_id", String, primary_key=True, info={"hub_unique": True}),  # dec_<ulid>
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("node_id", String, nullable=False),  # the gate node awaiting the decision
    Column("node_name", String, nullable=False),  # the node's name — what runner gate-config matches
    Column("epoch", Integer, nullable=False),  # the parked step's fence; stale decisions rejected
    Column("choices", Text, nullable=False),  # JSON list of {name, description} — the buttons
    Column("submitted_at", UtcDateTime, nullable=False),
    # The runner whose configuration imposed this gate — a fact of the write; `NULL` when the graph declared it.
    Column("imposed_by_runner_id", String, nullable=True),
    # The deciding chunk's project, taken from its row in the write's own transaction.
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_decisions_projects"), nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_decisions_tenant_id"), nullable=False),
)
# (tenant_id, chunk_id, submitted_at, decision_id): one chunk's decisions, already in submission order.
Index(
    "ix_decisions_tenant_chunk_id",
    decisions.c.tenant_id,
    decisions.c.chunk_id,
    decisions.c.submitted_at,
    decisions.c.decision_id,
)
# (tenant_id, submitted_at, decision_id) for one tenant's ordered reads.
Index(
    "ix_decisions_tenant_submitted_at_decision_id",
    decisions.c.tenant_id,
    decisions.c.submitted_at,
    decisions.c.decision_id,
)
# (tenant_id, project_id, submitted_at, decision_id) for one project's ordered reads.
Index(
    "ix_decisions_tenant_project_submitted_at_decision_id",
    decisions.c.tenant_id,
    decisions.c.project_id,
    decisions.c.submitted_at,
    decisions.c.decision_id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_decision_resolutions_tenant_id"), nullable=False
    ),
)
# (tenant_id, resolved_at, decision_id) for newest-first bounded reads since a timestamp; the
# PK IS the decision id, so it also serves as the row's own tie-break column.
Index(
    "ix_decision_resolutions_tenant_resolved_at_decision_id",
    decision_resolutions.c.tenant_id,
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_requeues_tenant_id"), nullable=False),
)
Index("ix_requeues_tenant_chunk_id", requeues.c.tenant_id, requeues.c.chunk_id)
# (tenant_id, requeued_at, id) for newest-first bounded reads since a timestamp, portable
# across sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the
# rowid as a tie-break).
Index("ix_requeues_tenant_requeued_at_id", requeues.c.tenant_id, requeues.c.requeued_at, requeues.c.id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_restarts_tenant_id"), nullable=False),
)
Index("ix_chunk_restarts_tenant_chunk_id", chunk_restarts.c.tenant_id, chunk_restarts.c.chunk_id)
Index(
    "ix_chunk_restarts_tenant_chunk_id_recorded_at",
    chunk_restarts.c.tenant_id,
    chunk_restarts.c.chunk_id,
    chunk_restarts.c.recorded_at,
)
# (tenant_id, recorded_at, id) for one tenant's ordered reads.
Index(
    "ix_chunk_restarts_tenant_recorded_at_id",
    chunk_restarts.c.tenant_id,
    chunk_restarts.c.recorded_at,
    chunk_restarts.c.id,
)
# The hub-wide twin: the trace sweep's closing-candidates read pages every open tenant's rows in this order.
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_pause_facts_tenant_id"), nullable=False),
)
Index("ix_chunk_pause_facts_tenant_chunk_id", chunk_pause_facts.c.tenant_id, chunk_pause_facts.c.chunk_id)
# (tenant_id, set_at, id) for newest-first bounded reads since a timestamp, portable across
# sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the rowid as
# a tie-break).
Index(
    "ix_chunk_pause_facts_tenant_set_at_id",
    chunk_pause_facts.c.tenant_id,
    chunk_pause_facts.c.set_at,
    chunk_pause_facts.c.id,
)

# --- Store-and-forward high-water mark (per-runner idempotency) ---------------
# The greatest per-runner seq already applied; a fact at or below it is re-acked, not applied.

runner_high_water = Table(
    "runner_high_water",
    metadata,
    Column("runner_id", String, primary_key=True, info={"hub_unique": True}),
    Column("seq", Integer, nullable=False),  # greatest applied per-runner seq
    Column("updated_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_runner_high_water_tenant_id"), nullable=False),
)
Index("ix_runner_high_water_tenant_id", runner_high_water.c.tenant_id)

# --- Queue shaping: ready-queue ordering ----------------------
# A chunk's effective position is its newest fact, else its ``minted_at`` as a unix stamp.

queue_positions = Table(
    "queue_positions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
    Column("position", Float, nullable=False),  # lower sorts earlier; newest fact per chunk wins
    Column("set_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_queue_positions_tenant_id"), nullable=False),
)
# Live-set reads look positions up by candidate chunk id (`bzh:live-set-read`).
Index("ix_queue_positions_tenant_chunk_id", queue_positions.c.tenant_id, queue_positions.c.chunk_id)

# --- Queue shaping: grouping (chunk.grouped) -----------------------------------
# A grouped chunk is EPHEMERAL: removed from every listing, deriving no status at all.

chunk_grouped = Table(
    "chunk_grouped",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the merged-away chunk
    Column("grouped_into", String, ForeignKey("chunks.chunk_id"), nullable=False),  # the survivor
    Column("grouped_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_chunk_grouped_tenant_id"), nullable=False),
)
# (tenant_id, grouped_at, id) for newest-first bounded reads since a timestamp, portable
# across sqlite and postgres (`bzh:sql-portable`; only sqlite implicitly appends the
# rowid as a tie-break).
Index(
    "ix_chunk_grouped_tenant_grouped_at_id",
    chunk_grouped.c.tenant_id,
    chunk_grouped.c.grouped_at,
    chunk_grouped.c.id,
)

# --- Hosts (a tenant-scoped record above runners, with a bearer credential of its own) ---
# A row is inserted when a host is added and refreshed in place by each report. Retirement is a lifecycle fact.

hosts = Table(
    "hosts",
    metadata,
    # The hub-minted id — the host's one identity.
    Column("host_id", String, nullable=False, info={"hub_unique": True}),  # hs_<ulid>
    # The operator's label. Unique among the tenant's live hosts, which liveness derives, so no constraint holds it.
    Column("name", String, nullable=False),
    Column("added_at", UtcDateTime, nullable=False),
    Column("added_by", String, nullable=True),
    # All null for a host that was added but has never reported.
    Column("registered_at", UtcDateTime, nullable=True),
    Column("last_seen_at", UtcDateTime, nullable=True),  # liveness derives from this
    # The panel's canonical origin, as reported.
    Column("url", Text, nullable=True),
    # The panel's sign-in callbacks, JSON `list[str]`, replaced whole on each report.
    Column("redirect_uris", Text, nullable=True),
    # JSON `list[dict]` of `{workspace_id, env_capacity}`, replaced whole on each report.
    Column("workspaces", Text, nullable=True),
    # The subscription roster, JSON `list[dict]` of `{slug, name, provider}`, replaced whole on each report.
    Column("subscriptions", Text, nullable=True),
    # The bearer token's sha256 hex digest — null while none is enrolled.
    Column("token_hash", Text, nullable=True, index=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_hosts_tenant_id"), nullable=False),
    PrimaryKeyConstraint("host_id", name="pk_hosts"),
)
Index("ix_hosts_tenant_id", hosts.c.tenant_id)

# Retirement — ``retired`` derives from the newest row.
host_lifecycle_facts = Table(
    "host_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("host_id", String, ForeignKey("hosts.host_id", name="fk_host_lifecycle_facts_hosts"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_host_lifecycle_facts_tenant_id"), nullable=False
    ),
)
Index("ix_host_lifecycle_facts_tenant_id", host_lifecycle_facts.c.tenant_id)

# Every revoked host token hash, never cleared — a revoked token is refused, not merely unresolved.
host_token_revocations = Table(
    "host_token_revocations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("host_id", String, ForeignKey("hosts.host_id", name="fk_host_token_revocations_hosts"), nullable=False),
    Column("token_hash", Text, nullable=False, index=True),
    Column("revoked_at", UtcDateTime, nullable=False),
    Column("revoked_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_host_token_revocations_tenant_id"),
        nullable=False,
    ),
)
Index("ix_host_token_revocations_tenant_id", host_token_revocations.c.tenant_id)

# --- The fleet registry (runner.added / registered / paused / resumed) --------
# A row is inserted when a runner is added and refreshed in place by each registration.

runner_registrations = Table(
    "runner_registrations",
    metadata,
    # The hub-minted id — the runner's one identity, and every runner-id column's value.
    Column("runner_id", String, primary_key=True, info={"hub_unique": True}),
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
    # The projects the runner serves, JSON `list[dict]` of `{entry, project_id}`: each entry as declared and the
    # project it resolved to; `NULL` reads as serving nothing.
    Column("projects", Text, nullable=True),
    # The host the runner stands under; null for a runner that stands alone.
    Column("host_id", String, ForeignKey("hosts.host_id", name="fk_runner_registrations_hosts"), nullable=True),
    # The bindings the runner last declared, JSON `list[dict]` of `{project, workspace_id, agents}`; `NULL` (none sent)
    # is kept distinct from `[]`. The flags against the host's report are derived on read, never stored.
    Column("bindings", Text, nullable=True),
    # The slug of the host subscription the bindings spend; null when the runner named none.
    Column("subscription", Text, nullable=True),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_runner_registrations_tenant_id"), nullable=False
    ),
)
Index("ix_runner_registrations_tenant_id", runner_registrations.c.tenant_id)
Index("ix_runner_registrations_tenant_host_id", runner_registrations.c.tenant_id, runner_registrations.c.host_id)

runner_pause_facts = Table(
    "runner_pause_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("paused", Boolean, nullable=False),  # paused derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),  # who flipped it — recorded on the fact
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_runner_pause_facts_tenant_id"), nullable=False
    ),
)
Index("ix_runner_pause_facts_tenant_id", runner_pause_facts.c.tenant_id)

# Retirement — ``retired`` derives from the newest row; reinstate is a ``retired=False`` fact.
runner_lifecycle_facts = Table(
    "runner_lifecycle_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("retired", Boolean, nullable=False),  # retired derives from the newest fact
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_runner_lifecycle_facts_tenant_id"), nullable=False
    ),
)
Index("ix_runner_lifecycle_facts_tenant_id", runner_lifecycle_facts.c.tenant_id)

# Every revoked token hash, never cleared — a revoked token is refused, not merely unresolved.
runner_token_revocations = Table(
    "runner_token_revocations",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
    Column("token_hash", Text, nullable=False, index=True),
    Column("revoked_at", UtcDateTime, nullable=False),
    Column("revoked_by", String, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_runner_token_revocations_tenant_id"),
        nullable=False,
    ),
)
Index("ix_runner_token_revocations_tenant_id", runner_token_revocations.c.tenant_id)

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
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_runner_local_pause_facts_tenant_id"),
        nullable=False,
    ),
)
Index("ix_runner_local_pause_facts_tenant_id", runner_local_pause_facts.c.tenant_id)

# The runner's latest sampled external-usage snapshot, one row per declared subscription
# — advisory. No ForeignKey on runner_id: a raise would stall the rail.
runner_external_usage = Table(
    "runner_external_usage",
    metadata,
    Column("runner_id", String, nullable=False, info={"hub_unique": True}),
    # The runner-unique join key within `runner_id` — a row predating declared
    # subscriptions backfills to the legacy Anthropic slug.
    Column("slug", String, nullable=False),
    # The declaration's operator-facing label, reported alongside `slug`.
    Column("name", String, nullable=False),
    Column("sampled_at", UtcDateTime, nullable=False),
    # JSON array of {window, utilization_pct, resets_at, window_seconds} — rewritten
    # wholesale on every sample, never queried by its members.
    Column("windows", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_runner_external_usage_tenant_id"), nullable=False
    ),
    PrimaryKeyConstraint("runner_id", "slug", name="pk_runner_external_usage"),
)
Index("ix_runner_external_usage_tenant_id", runner_external_usage.c.tenant_id)

# The runner's newest external-usage *miss* per slug — sibling to `runner_external_usage`, no FK on runner_id.
runner_external_usage_misses = Table(
    "runner_external_usage_misses",
    metadata,
    Column("runner_id", String, primary_key=True, info={"hub_unique": True}),
    Column("slug", String, primary_key=True),
    # The declaration's operator-facing label, reported alongside `slug`.
    Column("name", String, nullable=False),
    Column("missed_at", UtcDateTime, nullable=False),
    # The sampler's own closed-set miss reason (e.g. `credential_lapsed`) — never a
    # token, a refresh token, or a path.
    Column("reason", String, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_runner_external_usage_misses_tenant_id"),
        nullable=False,
    ),
)
Index("ix_runner_external_usage_misses_tenant_id", runner_external_usage_misses.c.tenant_id)

# --- The host's fact lane ------------------------------------------------------
# A host's own facts ride a lane of their own, de-duplicated against a mark of the host's own — never the runner's
# `runner_high_water`. No ForeignKey on host_id in any of the three: the upsert must not depend on a row it cannot
# already see, as the runner usage tables.

host_high_water = Table(
    "host_high_water",
    metadata,
    Column("host_id", String, nullable=False, info={"hub_unique": True}),
    Column("seq", Integer, nullable=False),  # greatest applied per-host seq
    Column("updated_at", UtcDateTime, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_host_high_water_tenant_id"), nullable=False),
    PrimaryKeyConstraint("host_id", name="pk_host_high_water"),
)
Index("ix_host_high_water_tenant_id", host_high_water.c.tenant_id)

# The host's newest sampled external-usage snapshot per subscription slug — advisory, refreshed in place.
host_external_usage = Table(
    "host_external_usage",
    metadata,
    Column("host_id", String, nullable=False, info={"hub_unique": True}),
    Column("slug", String, nullable=False),
    # The declaration's operator-facing label, reported alongside `slug`.
    Column("name", String, nullable=False),
    Column("sampled_at", UtcDateTime, nullable=False),
    # JSON array of {window, utilization_pct, resets_at, window_seconds} — rewritten wholesale on every sample.
    Column("windows", Text, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_host_external_usage_tenant_id"), nullable=False
    ),
    PrimaryKeyConstraint("host_id", "slug", name="pk_host_external_usage"),
)
Index("ix_host_external_usage_tenant_id", host_external_usage.c.tenant_id)

# The host's newest external-usage *miss* per slug — sibling to `host_external_usage`.
host_external_usage_misses = Table(
    "host_external_usage_misses",
    metadata,
    Column("host_id", String, nullable=False, info={"hub_unique": True}),
    Column("slug", String, nullable=False),
    Column("name", String, nullable=False),
    Column("missed_at", UtcDateTime, nullable=False),
    # The sampler's own closed-set miss reason — never a token, a refresh token, or a path.
    Column("reason", String, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_host_external_usage_misses_tenant_id"),
        nullable=False,
    ),
    PrimaryKeyConstraint("host_id", "slug", name="pk_host_external_usage_misses"),
)
Index("ix_host_external_usage_misses_tenant_id", host_external_usage_misses.c.tenant_id)

# --- The identity spine: users, provider identities, sessions -----
# An account carries no role: what a user may do is their memberships and the hub administrator facts.

users = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),  # usr_<ulid>
    Column("username", String, nullable=False, unique=True),
    Column("display_name", String, nullable=False),
    Column("email", String, nullable=True),
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
    # The sha256 hex of the invitation token a provider_login row was started with; NULL for every other sign-in.
    Column("invitation_token_hash", String, nullable=True),
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

# --- Memberships and hub administrators ----------------------------------------------
# Both are facts (``bzh:facts-not-status``): the newest row per key is in force. A membership grants one user one
# role in one tenant, and a ``NULL`` role revokes it; ``tenant_id`` is data here, since a person's memberships are
# read hub-wide, before any tenant is known. ``set_by`` names the granting user's id, or ``migration``,
# ``bootstrap``, or — for a membership granted under ``auth.mode = "none"`` — ``operator``.

membership_facts = Table(
    "membership_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_membership_facts_tenant_id"), nullable=False),
    Column("user_id", String, ForeignKey("users.id", name="fk_membership_facts_user_id"), nullable=False),
    Column("role", String, nullable=True),  # guest | contributor | admin; NULL revokes
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

# One membership's newest fact is a seek on ``(tenant_id, user_id)``, and a tenant's members a walk of its
# ``tenant_id`` range; a person's memberships across tenants seek on ``user_id``.
Index(
    "ix_membership_facts_tenant_user", membership_facts.c.tenant_id, membership_facts.c.user_id, membership_facts.c.id
)
Index("ix_membership_facts_user_id", membership_facts.c.user_id)

hub_admin_facts = Table(
    "hub_admin_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String, ForeignKey("users.id", name="fk_hub_admin_facts_user_id"), nullable=False),
    Column("granted", Boolean, nullable=False),
    Column("set_at", UtcDateTime, nullable=False),
    Column("set_by", String, nullable=False),
)

Index("ix_hub_admin_facts_user_id", hub_admin_facts.c.user_id)

# --- Invitations -----------------------------------------------------------------
# One invitation admits one email address into one tenant with one role. ``email`` is stored
# lowercased; ``token_hash`` is the sha256 of the link's token, which is never stored.

invitations = Table(
    "invitations",
    metadata,
    Column("invitation_id", String, primary_key=True, info={"hub_unique": True}),  # inv_<ulid>
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_invitations_tenant_id"), nullable=False),
    Column("email", String, nullable=False),
    Column("role", String, nullable=False),  # blizzard.auth_core.Role value
    Column("token_hash", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", UtcDateTime, nullable=False),
    Column("expires_at", UtcDateTime, nullable=False),
)

Index("ix_invitations_tenant_id", invitations.c.tenant_id)
Index("ix_invitations_token_hash", invitations.c.token_hash)

# What became of an invitation (``bzh:facts-not-status``): ``accepted`` with the accepting
# user, or ``revoked`` with the revoking admin. At most one per invitation — it is used once.
invitation_facts = Table(
    "invitation_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_invitation_facts_tenant_id"), nullable=False),
    Column(
        "invitation_id",
        String,
        ForeignKey("invitations.invitation_id", name="fk_invitation_facts_invitation_id"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("actor_user_id", String, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    UniqueConstraint("invitation_id", name="uq_invitation_facts_invitation_id"),
    CheckConstraint("kind IN ('accepted', 'revoked')", name="ck_invitation_facts_kind"),
)

Index("ix_invitation_facts_tenant_id", invitation_facts.c.tenant_id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_event_log_tenant_id"), nullable=False),
)

# The read's own sort key (newest-first) — indexed so ordering never scans the table.
Index("ix_event_log_tenant_recorded_at", event_log.c.tenant_id, event_log.c.recorded_at)

# The hub's own occurrences — its exports, startup, migration check, and tenant teardowns —
# which concern no tenant, chunk, or runner. Read only by hub administrators.
hub_event_log = Table(
    "hub_event_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column("severity", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("message", Text, nullable=False),
    Column("detail", Text, nullable=True),
)

Index("ix_hub_event_log_recorded_at", hub_event_log.c.recorded_at)
# The exports' newest-of-a-kind reads (their failure and latch) — each a seek to one kind's newest row.
Index("ix_hub_event_log_kind_recorded_at", hub_event_log.c.kind, hub_event_log.c.recorded_at)

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
    # The events position's tenant: null for the other datasets, and for an events position recorded without one,
    # which stands past every tenant at its time, segment and version.
    Column("events_tenant_id", String, nullable=True),
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
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_transcript_segments_tenant_id"), nullable=False
    ),
    UniqueConstraint(
        "tenant_id", "segment_id", "turn_range_start", name="uq_transcript_segments_tenant_segment_turn_start"
    ),
)

# (tenant_id, chunk_id, segment_id, turn_range_start): one chunk's segments, already in reading order.
Index(
    "ix_transcript_segments_tenant_chunk_id",
    transcript_segments.c.tenant_id,
    transcript_segments.c.chunk_id,
    transcript_segments.c.segment_id,
    transcript_segments.c.turn_range_start,
)
Index(
    "ix_transcript_segments_tenant_runner_received_at",
    transcript_segments.c.tenant_id,
    transcript_segments.c.runner_id,
    transcript_segments.c.received_at,
)
# A visible-segment read's `NOT IN (SELECT supersedes ...)` anti-join.
Index("ix_transcript_segments_tenant_supersedes", transcript_segments.c.tenant_id, transcript_segments.c.supersedes)
# (final, chunk_id): the hub-wide visible-segment read's outer `WHERE final = TRUE`,
# `final` leading because it is the more selective predicate; `chunk_id` second lets an
# `IN (chunks)` probe and a `DISTINCT` ride the same index.
Index("ix_transcript_segments_final_chunk_id", transcript_segments.c.final, transcript_segments.c.chunk_id)

# --- Transcript lane high-water mark (own table, not runner_high_water) --------
# `runner_high_water` belongs to the fact lane; a second lane sharing it would collide.

transcript_high_water = Table(
    "transcript_high_water",
    metadata,
    Column("runner_id", String, primary_key=True, info={"hub_unique": True}),
    Column("seq", Integer, nullable=False),
    Column("updated_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_transcript_high_water_tenant_id"), nullable=False
    ),
)
Index("ix_transcript_high_water_tenant_id", transcript_high_water.c.tenant_id)

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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_transcript_events_tenant_id"), nullable=False),
    UniqueConstraint(
        "tenant_id",
        "segment_id",
        "extractor_version",
        "kind",
        "turn_path",
        "occurrence",
        name="uq_transcript_events_tenant_natural_key",
    ),
)

Index("ix_transcript_events_tenant_chunk_id", transcript_events.c.tenant_id, transcript_events.c.chunk_id)
Index("ix_transcript_events_tenant_subject", transcript_events.c.tenant_id, transcript_events.c.subject)
Index("ix_transcript_events_tenant_tool", transcript_events.c.tenant_id, transcript_events.c.tool)
# (extractor_version, id) for a per-extractor cursor read: `extractor_version = ? AND
# id > cursor ORDER BY id`.
Index("ix_transcript_events_extractor_version_id", transcript_events.c.extractor_version, transcript_events.c.id)

# --- Per-segment derivation marker — replaced, never appended: what a segment's ---
# most recent derivation at a given extractor version saw, when, and whether it was complete.

transcript_event_derivations = Table(
    "transcript_event_derivations",
    metadata,
    Column("segment_id", String, nullable=False),
    Column("extractor_version", String, nullable=False),
    # A fingerprint of the segment's stored content as of this derivation — compared
    # against the segment's current fingerprint to detect a content change (a rejected
    # record later accepted, a late record landing) that the sweep must re-derive over.
    Column("content_fingerprint", String, nullable=False),
    Column("derived_at", UtcDateTime, nullable=False),
    Column("event_count", Integer, nullable=False),
    # False when the segment held a content hole (a rejected record) at derivation time —
    # declared, never silently indistinguishable from a session that read nothing.
    Column("complete", Boolean, nullable=False),
    Column(
        "tenant_id",
        String,
        ForeignKey("tenants.tenant_id", name="fk_transcript_event_derivations_tenant_id"),
        nullable=False,
    ),
    PrimaryKeyConstraint("tenant_id", "segment_id", "extractor_version", name="pk_transcript_event_derivations"),
)
# (tenant_id, derived_at, segment_id, extractor_version) for one tenant's ordered reads past a position.
Index(
    "ix_transcript_event_derivations_tenant_derived_at",
    transcript_event_derivations.c.tenant_id,
    transcript_event_derivations.c.derived_at,
    transcript_event_derivations.c.segment_id,
    transcript_event_derivations.c.extractor_version,
)
# The hub-wide twin: the egress sweep pages every open tenant's markers in this order.
Index(
    "ix_transcript_event_derivations_egress_order",
    transcript_event_derivations.c.derived_at,
    transcript_event_derivations.c.segment_id,
    transcript_event_derivations.c.extractor_version,
    transcript_event_derivations.c.tenant_id,
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
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_transcript_event_drops_tenant_id"), nullable=False
    ),
)
# (tenant_id, dropped_at, segment_id) for one tenant's ordered reads.
Index(
    "ix_transcript_event_drops_tenant_dropped_at_segment_id",
    transcript_event_drops.c.tenant_id,
    transcript_event_drops.c.dropped_at,
    transcript_event_drops.c.segment_id,
)
# The hub-wide twin: the egress sweep pages every open tenant's drops in this order.
Index(
    "ix_transcript_event_drops_egress_order",
    transcript_event_drops.c.dropped_at,
    transcript_event_drops.c.segment_id,
    transcript_event_drops.c.tenant_id,
)
# Drops by (chunk_id, epoch).
Index(
    "ix_transcript_event_drops_tenant_chunk_id_epoch",
    transcript_event_drops.c.tenant_id,
    transcript_event_drops.c.chunk_id,
    transcript_event_drops.c.epoch,
)

# --- Work items (hub-owned work items) ---------------------------
# A mutable entity row, not a fact table: title/body/edited_at change in place, and
# closure is recorded on the row itself (nullable ``closed_at`` + ``closure``) rather
# than a separate append-only table, because there is exactly one current state to read
# back — never a history of edits (``bzh:facts-not-status``, Recorded position).

work_items = Table(
    "work_items",
    metadata,
    Column("work_item_id", String, primary_key=True, info={"hub_unique": True}),  # wi_<ulid>
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
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_work_items_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_work_items_projects"), nullable=False),
    UniqueConstraint("tenant_id", "source", "ref", name="uq_work_items_tenant_source_ref"),
)

Index("ix_work_items_tenant_source", work_items.c.tenant_id, work_items.c.source)

# --- Work item runs (a run's identity) -----------------------
# What routine, scope, and mode a work item's run is executing under — minted together
# with the work item, read back through a chunk's first work ref (`routine_name`, not a
# surrogate `routine_id`, the `findings.routine_name` shape).

work_item_runs = Table(
    "work_item_runs",
    metadata,
    Column("work_item_id", String, ForeignKey("work_items.work_item_id"), primary_key=True),
    Column("routine_name", String, nullable=False),
    Column("scope_id", String, ForeignKey("scopes.scope_id", name="fk_work_item_runs_scopes"), nullable=False),
    Column("mode", String, nullable=False),
    Column("tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_work_item_runs_tenant_id"), nullable=False),
    Column("project_id", String, ForeignKey("projects.project_id", name="fk_work_item_runs_projects"), nullable=False),
)
Index("ix_work_item_runs_tenant_id", work_item_runs.c.tenant_id)

# A per-source allocation counter, one row per source, so ``ref`` allocation never
# reads ``MAX(ref)+1`` (two concurrent first allocations on an empty source would both
# compute 1) — every source gets a pre-existing row instead (`bzh:sql-portable`).

work_item_sequence = Table(
    "work_item_sequence",
    metadata,
    Column("source", String, nullable=False),
    Column("next_ref", Integer, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_work_item_sequence_tenant_id"), nullable=False
    ),
    PrimaryKeyConstraint("tenant_id", "source", name="pk_work_item_sequence"),
)

# --- Forge annotation memory (forge_annotation_facts) --------------------------
# Append-only, one row per entry to or exit from the annotated set; the newest says whether a clear is owed.

forge_annotation_facts = Table(
    "forge_annotation_facts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("work_source_id", String, nullable=False),
    Column("annotating", Boolean, nullable=False),
    Column("recorded_at", UtcDateTime, nullable=False),
    Column(
        "tenant_id", String, ForeignKey("tenants.tenant_id", name="fk_forge_annotation_facts_tenant_id"), nullable=False
    ),
)

Index(
    "ix_forge_annotation_facts_tenant_work_source_id_id",
    forge_annotation_facts.c.tenant_id,
    forge_annotation_facts.c.work_source_id,
    forge_annotation_facts.c.id,
)
