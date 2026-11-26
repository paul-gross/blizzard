import type { UnknownHostValue } from 'fleet/core';
import type { runnerApi } from 'fleet/runner-shell';
import { openLookup } from 'fleet/shell';

import type { Lease, LeaseClosureReason, LeaseDisplayStatus, LeaseLiveness, LeaseState } from './lease.domain';

const unknown = (raw: string): UnknownHostValue => ({ kind: 'unknown', raw });

const stateOf = openLookup<runnerApi.LeaseView['state'], LeaseState>(
  {
    running: 'running',
    stale: 'stale',
    parked: 'parked',
    'backing-off': 'backing-off',
    spawning: 'spawning',
    exited: 'exited',
    closed: 'closed',
  },
  unknown,
);

const displayStatusOf = openLookup<runnerApi.LeaseDisplayStatus, LeaseDisplayStatus>(
  {
    'human-in-session': 'human-in-session',
    'needs-human': 'needs-human',
    'waiting-ask': 'waiting-ask',
    running: 'running',
    stale: 'stale',
    parked: 'parked',
    'backing-off': 'backing-off',
    spawning: 'spawning',
    exited: 'exited',
    closed: 'closed',
  },
  unknown,
);

const livenessOf = openLookup<runnerApi.LeaseLiveness, LeaseLiveness>({ live: 'live', ended: 'ended' }, () => 'unknown');

const closureReasonOf = openLookup<runnerApi.LeaseClosureReason, LeaseClosureReason>(
  {
    transitioned: 'transitioned',
    reaped: 'reaped',
    failed: 'failed',
    escalated: 'escalated',
    parked: 'parked',
    released: 'released',
    preempted: 'preempted',
  },
  unknown,
);

export function fromLeaseView(wire: runnerApi.LeaseView): Lease {
  return {
    lease_id: wire.lease_id,
    chunk_id: wire.chunk_id,
    graph_id: wire.graph_id,
    node_id: wire.node_id,
    node_name: wire.node_name,
    epoch: wire.epoch,
    session_id: wire.session_id,
    pid: wire.pid,
    environment_id: wire.environment_id,
    workdir: wire.workdir,
    project_id: wire.project_id,
    harness_id: wire.harness_id,
    harness_version: wire.harness_version,
    resolved_effort: wire.resolved_effort,
    resolved_model: wire.resolved_model,
    host_settings_revision: wire.host_settings_revision,
    runner_settings_revision: wire.runner_settings_revision,
    git_commits: wire.git_commits.map(({ branch, commit, declared_at, environment_id, repo }) => ({
      branch,
      commit,
      declared_at,
      environment_id,
      repo,
    })),
    created_at: wire.created_at,
    last_heartbeat_at: wire.last_heartbeat_at,
    stale_after_seconds: wire.stale_after_seconds,
    closed_at: wire.closed_at,
    closure_healthy: wire.closure_healthy,
    closure_reason: wire.closure_reason === null ? null : closureReasonOf(wire.closure_reason),
    display_status: displayStatusOf(wire.display_status),
    state: stateOf(wire.state),
    liveness: livenessOf(wire.liveness),
  };
}
