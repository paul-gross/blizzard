import { runnerApi } from 'fleet';

import {
  activeLeases,
  chunkLeasesFor,
  chunksEmptyText,
  chunkStatusFor,
  escalationFor,
  type MachineChunkRow,
  machineChunkRows,
  visibleChunkRows,
} from './app-panel.model';

function lease(overrides: Partial<runnerApi.LeaseView> = {}): runnerApi.LeaseView {
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
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    ...overrides,
  };
}

function escalation(chunkId: string): runnerApi.EscalationView {
  return {
    chunk_id: chunkId,
    closed_at: '2026-07-16T11:30:00.000Z',
    epoch: 1,
    lease_id: 'lease_esc',
    node_id: 'nd_build',
    resume_command: `blizzard resume ${chunkId}`,
  };
}

function dashboard(facts: {
  escalations?: runnerApi.EscalationView[];
  takeoverChunkIds?: string[];
  askChunkIds?: string[];
}): runnerApi.DashboardView {
  return {
    asks: {
      items: (facts.askChunkIds ?? []).map((chunkId, i) => ({
        question_id: `q_${i}`,
        chunk_id: chunkId,
        lease_id: 'lease_ask',
        session_id: null,
        asked_at: '2026-07-16T11:50:00.000Z',
        question: 'Which branch?',
      })),
    },
    environments: { items: [] },
    escalations: { items: facts.escalations ?? [] },
    facts: { items: [] },
    fleet_summary: null,
    harness_health: { items: [] },
    runner: {
      runner_id: 'runner-local',
      workspace_id: 'workspace-local',
      pause: { local: false, hub: false, effective: false },
      capacities: { max_agents: 4, used: 1, free: 3 },
      hub: { endpoint: 'http://127.0.0.1:8421', reachable: true, last_contact_at: null, buffer_depth: 0 },
      last_tick_at: null,
    },
    subscriptions: { items: [] },
    takeovers: {
      items: (facts.takeoverChunkIds ?? []).map((chunkId, i) => ({
        takeover_id: `tk_${i}`,
        chunk_id: chunkId,
        held_since: '2026-07-16T11:40:00.000Z',
      })),
    },
  };
}

function row(chunkId: string, tone: MachineChunkRow['status']['tone']): MachineChunkRow {
  const newest = lease({ chunk_id: chunkId, lease_id: `lease_${chunkId}` });
  return { lease: newest, leases: [newest], status: { label: tone.toUpperCase(), tone } };
}

describe('activeLeases', () => {
  it('drops closed leases and keeps the rest in order', () => {
    const running = lease({ lease_id: 'l1', state: 'running' });
    const closed = lease({ lease_id: 'l2', state: 'closed' });
    const stale = lease({ lease_id: 'l3', state: 'stale' });
    expect(activeLeases([running, closed, stale])).toEqual([running, stale]);
  });
});

describe('chunksEmptyText', () => {
  it('names an empty machine', () => {
    expect(chunksEmptyText(0, 0)).toBe('NO CHUNKS ON THIS MACHINE');
  });

  it('names a single hidden chunk', () => {
    expect(chunksEmptyText(3, 2)).toBe('1 CHUNK HIDDEN BY THE FILTER — CHECK SHOW ALL');
  });

  it('pluralizes several hidden chunks', () => {
    expect(chunksEmptyText(3, 0)).toBe('3 CHUNKS HIDDEN BY THE FILTER — CHECK SHOW ALL');
  });
});

describe('machineChunkRows', () => {
  it('groups leases by chunk, newest lease first, attempts oldest → newest', () => {
    const a2 = lease({ lease_id: 'a2', chunk_id: 'ch_a', epoch: 2 });
    const b1 = lease({ lease_id: 'b1', chunk_id: 'ch_b', state: 'stale' });
    const a1 = lease({ lease_id: 'a1', chunk_id: 'ch_a', state: 'closed' });
    const rows = machineChunkRows([a2, b1, a1], undefined);
    expect(rows).toEqual([
      { lease: a2, leases: [a1, a2], status: { label: 'RUNNING', tone: 'running' } },
      { lease: b1, leases: [b1], status: { label: 'STALE', tone: 'stale' } },
    ]);
  });

  it('folds the dashboard escalations, takeovers, and asks into each status', () => {
    const rows = machineChunkRows(
      [lease({ chunk_id: 'ch_esc' }), lease({ chunk_id: 'ch_tko' }), lease({ chunk_id: 'ch_ask' })],
      dashboard({ escalations: [escalation('ch_esc')], takeoverChunkIds: ['ch_tko'], askChunkIds: ['ch_ask'] }),
    );
    expect(rows.map((r) => r.status)).toEqual([
      { label: 'NEEDS HUMAN', tone: 'needs' },
      { label: 'HUMAN IN SESSION', tone: 'takeover' },
      { label: 'WAITING · ASK', tone: 'waiting' },
    ]);
  });

  it('maps no leases to no rows', () => {
    expect(machineChunkRows([], dashboard({}))).toEqual([]);
  });

  it('reads a closed lease whose chunk transitioned as done', () => {
    const [only] = machineChunkRows(
      [lease({ state: 'closed', closure_reason: runnerApi.LeaseClosureReason.TRANSITIONED })],
      undefined,
    );
    expect(only.status.tone).toBe('done');
  });
});

describe('visibleChunkRows', () => {
  const rows = [row('ch_run', 'running'), row('ch_done', 'done'), row('ch_idle', 'idle'), row('ch_needs', 'needs')];

  it('returns every row when show-all is checked', () => {
    expect(visibleChunkRows(rows, true)).toBe(rows);
  });

  it('hides done and idle rows otherwise', () => {
    expect(visibleChunkRows(rows, false).map((r) => r.lease.chunk_id)).toEqual(['ch_run', 'ch_needs']);
  });
});

describe('chunkLeasesFor / chunkStatusFor', () => {
  const rows = [row('ch_a', 'running'), row('ch_b', 'stale')];

  it("resolves the selected chunk's attempts and status", () => {
    expect(chunkLeasesFor(rows, 'ch_b')).toBe(rows[1].leases);
    expect(chunkStatusFor(rows, 'ch_b')).toBe(rows[1].status);
  });

  it('resolves empty/null when nothing is selected', () => {
    expect(chunkLeasesFor(rows, null)).toEqual([]);
    expect(chunkStatusFor(rows, null)).toBeNull();
  });

  it('resolves empty/null for a chunk not on this machine', () => {
    expect(chunkLeasesFor(rows, 'ch_missing')).toEqual([]);
    expect(chunkStatusFor(rows, 'ch_missing')).toBeNull();
  });
});

describe('escalationFor', () => {
  const esc = escalation('ch_esc');

  it("resolves the selected chunk's open escalation", () => {
    expect(escalationFor(dashboard({ escalations: [escalation('ch_other'), esc] }), 'ch_esc')).toBe(esc);
  });

  it('resolves null when nothing is selected, none is open, or no read has resolved', () => {
    expect(escalationFor(dashboard({ escalations: [esc] }), null)).toBeNull();
    expect(escalationFor(dashboard({}), 'ch_esc')).toBeNull();
    expect(escalationFor(undefined, 'ch_esc')).toBeNull();
  });
});
