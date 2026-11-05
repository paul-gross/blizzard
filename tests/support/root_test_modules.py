"""The basenames of every ``test_*.py`` directly under ``tests/`` that has not yet moved into a tier folder.

A new flat module is refused at collection (``tests/support/tier_layout.py``); a move slice deletes its entry here.
A ``.py`` file, not a data file, so the repo-read guard exempts it."""

from __future__ import annotations

ROOT_TEST_MODULES: frozenset[str] = frozenset(
    {
        "test_answer_chunk_end_race.py",
        "test_chunk_close_diagnostic_api.py",
        "test_chunk_landing_facts_api.py",
        "test_cli_entry.py",
        "test_cli_transcript_render.py",
        "test_configured_record_foundation.py",
        "test_controlled_clock.py",
        "test_daemon_port_zero.py",
        "test_escalation_answer_wake_atomicity.py",
        "test_escalation_completion_race.py",
        "test_fact_families.py",
        "test_forge_rate_dialect_guard.py",
        "test_gate_enforceability.py",
        "test_github_rate_dialect.py",
        "test_graph_progress.py",
        "test_host_lifecycle_versions.py",
        "test_host_roster_fencing.py",
        "test_hub_cli_create_name.py",
        "test_hub_cli_transcript.py",
        "test_hub_transaction_types.py",
        "test_hub_transactions.py",
        "test_invariant_checker_carry_over.py",
        "test_landing_order_race.py",
        "test_listening_file.py",
        "test_queue_ranking_race.py",
        "test_resolve_gate_closure_race.py",
        "test_runner_harness_opencode_v2_client.py",
        "test_runner_harness_opencode_v2_compatibility.py",
        "test_runner_harness_opencode_v2_corpus.py",
        "test_runner_harness_opencode_v2_driver.py",
        "test_runner_host_clock.py",
        "test_runner_identity_service.py",
        "test_runner_retire_fencing.py",
        "test_runner_transcript_cli.py",
        "test_tenant_resolution.py",
        "test_test_flow_control.py",
        "test_transcript_ingest_race.py",
        "test_vocabulary_seams.py",
    }
)
