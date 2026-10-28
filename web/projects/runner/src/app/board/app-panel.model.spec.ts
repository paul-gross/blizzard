import type { runnerApi } from 'fleet';

import { type BoardLane, type MachineChunkRow } from '../core/chunk-rows.model';
import type { MachineChunkStatus } from '../core/chunk-status';
import type { PanelLease } from '../core/panel-reads.model';
import { chunkLeasesFor, chunkStatusFor, escalationFor, rankedRows, showsChunkStatus } from './app-panel.model';

const OWNER = { runnerId: 'rn_1', name: 'r1', label: 'r1' };

function lease(overrides: Partial<PanelLease> = {}): PanelLease {
  return {
    lease_id: 'lease_1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: 1,
    environment_id: 'e1',
    workdir: '/ws/e1',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:00.000Z',
    state: 'running',
    display_status: 'running',
    closure_healthy: false,
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    harness_id: null,
    project_id: null,
    owner: OWNER,
    projectSlug: null,
    ...overrides,
  };
}

function escalation(chunkId: string, runnerId: string): runnerApi.EscalationView & { owner: { runnerId: string } } {
  return {
    chunk_id: chunkId,
    closed_at: '2026-07-16T11:30:00.000Z',
    epoch: 1,
    lease_id: 'lease_esc',
    node_id: 'nd_build',
    resume_command: `blizzard resume ${chunkId}`,
    owner: { runnerId },
    cause: null,
    effort: null,
    harness_id: null,
    harness_version: null,
    model: null,
    project_id: null,
    session_name: null,
    wrapped_takeover_command: null,
    closure: null,
  };
}

function row(
  chunkId: string,
  status: MachineChunkStatus = { label: 'RUNNING', tone: 'running' },
  overrides: { lane?: BoardLane; live?: PanelLease | null } = {},
): MachineChunkRow {
  const newest = lease({ chunk_id: chunkId, lease_id: `lease_${chunkId}` });
  return {
    lease: newest,
    leases: [newest],
    status,
    live: overrides.live === undefined ? newest : overrides.live,
    lane: overrides.lane ?? 'progress',
    projectName: null,
    projectColor: null,
  };
}

describe('rankedRows', () => {
  it('ranks rows lane by lane — needs human, in progress, done — keeping each lane in merged order', () => {
    const rows = [
      row('ch_done', undefined, { lane: 'done' }),
      row('ch_run1'),
      row('ch_needs', undefined, { lane: 'needs' }),
      row('ch_run2'),
    ];
    expect(rankedRows(rows).map((r) => r.lease.chunk_id)).toEqual(['ch_needs', 'ch_run1', 'ch_run2', 'ch_done']);
  });

  it('leaves its input untouched', () => {
    const rows = [row('ch_done', undefined, { lane: 'done' }), row('ch_needs', undefined, { lane: 'needs' })];
    rankedRows(rows);
    expect(rows.map((r) => r.lease.chunk_id)).toEqual(['ch_done', 'ch_needs']);
  });
});

describe('chunkLeasesFor / chunkStatusFor', () => {
  const rows = [row('ch_a'), row('ch_b', { label: 'STALE', tone: 'stale' })];

  it("resolves the selected chunk's attempts and status", () => {
    expect(chunkLeasesFor(rows, 'ch_b')).toBe(rows[1].leases);
    expect(chunkStatusFor(rows, 'ch_b')).toBe(rows[1].status);
  });

  it('resolves empty/null when nothing is selected', () => {
    expect(chunkLeasesFor(rows, null)).toEqual([]);
    expect(chunkStatusFor(rows, null)).toBeNull();
  });

  it('resolves empty/null for a chunk on none of the runners read', () => {
    expect(chunkLeasesFor(rows, 'ch_missing')).toEqual([]);
    expect(chunkStatusFor(rows, 'ch_missing')).toBeNull();
  });
});

describe('escalationFor', () => {
  const esc = escalation('ch_esc', 'rn_1');

  it("resolves the selected chunk's open escalation on its runner", () => {
    expect(escalationFor([escalation('ch_other', 'rn_1'), esc], 'rn_1', 'ch_esc')).toBe(esc);
  });

  it("ignores another runner's escalation for the same chunk id", () => {
    expect(escalationFor([escalation('ch_esc', 'rn_2')], 'rn_1', 'ch_esc')).toBeNull();
  });

  it('resolves null when nothing is selected, the runner is unknown, or none is open', () => {
    expect(escalationFor([esc], 'rn_1', null)).toBeNull();
    expect(escalationFor([esc], null, 'ch_esc')).toBeNull();
    expect(escalationFor([], 'rn_1', 'ch_esc')).toBeNull();
  });
});

describe('showsChunkStatus', () => {
  it('names the status once no worker is live', () => {
    expect(showsChunkStatus(row('ch_a', { label: 'COMPLETED', tone: 'done' }, { live: null }))).toBe(true);
  });

  it("hides the status while it only repeats the live worker's own state", () => {
    expect(showsChunkStatus(row('ch_a'))).toBe(false);
  });

  it("names a human fact that outranks the live worker's state", () => {
    expect(showsChunkStatus(row('ch_a', { label: 'WAITING · ASK', tone: 'waiting' }))).toBe(true);
  });
});
